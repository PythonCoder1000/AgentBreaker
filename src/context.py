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

import re
from pathlib import Path

from settings import (
    AGENT_CONTEXT_FILE_TEMPLATE,
    AGENT_CONTEXT_HEADER,
    INTERCEPT_FILE_CONTEXT_CHARS,
    INTERCEPT_MAX_FILES_READ,
)

# Repo root (this file lives in src/), used to resolve the files a call involves.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# A path-like token in a shell command (quotes/operators act as separators).
_PATH_TOKEN = re.compile(r"[A-Za-z0-9_./@+-]+")


def gather_file_context(tool_name: str, tool_input: dict) -> list[tuple[str, str]]:
    """Read the existing files referenced by this call. Returns (path, content) pairs."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for rel in involved_paths(tool_name, tool_input):
        if rel in seen:
            continue
        seen.add(rel)
        resolved = resolve(rel)
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
    """Path-like tokens in a shell command (not flags; must look like a file path)."""
    paths = []
    for token in _PATH_TOKEN.findall(command):
        if token.startswith("-"):  # a flag, not a path
            continue
        if "/" in token or re.search(r"\.[A-Za-z0-9]+$", token):
            paths.append(token)
    return paths


def attachments(tool_input: dict) -> list[str]:
    raw = tool_input.get("attachments")
    return [str(item) for item in raw] if isinstance(raw, list) else []


def resolve(rel: str) -> Path | None:
    try:
        path = Path(rel)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.resolve()
    except (OSError, ValueError):  # ValueError: embedded null byte in the path
        return None


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
