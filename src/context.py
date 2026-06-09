"""File-context provider for Breaker Agent tool calls.

This is the single place that decides which files a tool call involves and loads
their contents. The agent itself never does this loading — context does, then
returns the files. The same loaded context serves two consumers:

  1. the policy evaluator (intercepter.evaluate) — to judge the call on what the
     files actually contain, and
  2. the main Breaker Agent — the contents are appended to the tool result so the
     agent receives the files it touched without having to read them separately.

(web_search is a server-side tool and never reaches client-side dispatch, so it
has no file context here.)
"""

from __future__ import annotations

import glob as _glob
import os
import re
from pathlib import Path

from settings import (
    AGENT_CONTEXT_FILE_TEMPLATE,
    AGENT_CONTEXT_HEADER,
    INTERCEPT_FILE_CONTEXT_CHARS,
    INTERCEPT_MAX_FILES_READ,
    INTERCEPT_MAX_GLOB_MATCHES,
)

# Repo root (this file lives in src/), used to resolve the files a call involves.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# A path-like token in a shell command (quotes/operators act as separators). Glob
# metacharacters are part of the token so a globbed path (secret*/x, .e*) is
# captured whole instead of fragmenting into tokens that resolve to nothing.
_PATH_TOKEN = re.compile(r"[A-Za-z0-9_./@+~*?\[\]-]+")
# Glob metacharacters that mean a token must be expanded against the filesystem.
_GLOB_META = re.compile(r"[*?\[]")


def gather_file_context(tool_name: str, tool_input: dict) -> list[tuple[str, str]]:
    """Read the existing files referenced by this call. Returns (path, content) pairs."""
    # Attachments must stay inside the project tree (a symlink planted under
    # testing_env mustn't smuggle an out-of-tree file's contents back to the
    # agent); run_bash reads anywhere, so grounding on those files is unconfined.
    confine = tool_name == "send_email"
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for rel in involved_paths(tool_name, tool_input):
        if rel in seen:
            continue
        seen.add(rel)
        resolved = resolve(rel, confine=confine)
        content = read_file_safe(resolved) if resolved is not None else None
        if content is not None:
            out.append((rel, content))
        if len(out) >= INTERCEPT_MAX_FILES_READ:
            break
    return out


def format_for_agent(file_context: list[tuple[str, str]]) -> str:
    """Render gathered files as a block to append to the agent's tool result."""
    if not file_context:
        return ""
    parts = [AGENT_CONTEXT_HEADER]
    for path, content in file_context:
        parts.append(AGENT_CONTEXT_FILE_TEMPLATE.format(path=path, content=content))
    return "".join(parts)


def involved_paths(tool_name: str, tool_input: dict) -> list[str]:
    if tool_name == "send_email":
        return attachments(tool_input)
    if tool_name == "run_bash":
        return command_paths(str(tool_input.get("command", "")))
    return []


def command_paths(command: str) -> list[str]:
    """Path-like tokens in a shell command, with globs expanded to real files.

    A token is kept if it looks like a path (contains '/', a file extension, or a
    glob metacharacter). Glob patterns are expanded against the project tree so a
    pattern like 'testing_env/secret*/credentials.json' or '.e*' surfaces the real
    file it would read — otherwise it fragments into tokens that match nothing,
    blinding both the sensitive-path block and the AI evaluator.
    """
    paths: list[str] = []
    for token in _PATH_TOKEN.findall(command):
        if token.startswith("-"):  # a flag, not a path
            continue
        token = os.path.expanduser(token)  # ~ -> $HOME, so the real target is seen
        has_glob = bool(_GLOB_META.search(token))
        if not ("/" in token or has_glob or re.search(r"\.[A-Za-z0-9]+$", token)):
            continue
        if has_glob:
            paths.extend(_expand_glob(token))
        else:
            paths.append(token)
    return paths


def _expand_glob(pattern: str) -> list[str]:
    """Expand a shell glob against the project tree → project-relative paths of the
    real files it matches (capped). Falls back to the literal pattern when nothing
    matches, so a sensitive-looking pattern is still surfaced to the policy check.
    """
    try:
        matches = sorted(_glob.glob(str(PROJECT_ROOT / pattern)))
    except (OSError, ValueError):
        return [pattern]
    rels: list[str] = []
    for match in matches[:INTERCEPT_MAX_GLOB_MATCHES]:
        try:
            rels.append(os.path.relpath(Path(match).resolve(), PROJECT_ROOT))
        except (OSError, ValueError):
            continue
    return rels or [pattern]


def attachments(tool_input: dict) -> list[str]:
    raw = tool_input.get("attachments")
    return [str(item) for item in raw] if isinstance(raw, list) else []


def resolve(rel: str, confine: bool = False) -> Path | None:
    """Resolve a referenced path to an absolute path.

    With confine=True the result must stay within PROJECT_ROOT *after* symlinks
    are resolved — used for email attachments so a symlink can't escape the tree.
    run_bash leaves confine=False: the shell can read anywhere, so grounding the
    evaluator on those same files is correct.
    """
    try:
        path = Path(rel)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        resolved = path.resolve()
    except (OSError, ValueError):  # ValueError: embedded null byte in the path
        return None
    if confine and not resolved.is_relative_to(PROJECT_ROOT):
        return None
    return resolved


def read_file_safe(path: Path) -> str | None:
    """Read up to INTERCEPT_FILE_CONTEXT_CHARS of a file; None if not a readable file."""
    try:
        if not path.is_file():
            return None
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            data = handle.read(INTERCEPT_FILE_CONTEXT_CHARS + 1)
    except (OSError, ValueError):  # ValueError: embedded null byte in the path
        return None
    if len(data) > INTERCEPT_FILE_CONTEXT_CHARS:
        return data[:INTERCEPT_FILE_CONTEXT_CHARS] + "\n...[truncated]"
    return data
