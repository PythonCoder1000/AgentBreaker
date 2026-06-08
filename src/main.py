"""A minimal agent loop with a simulated deployment environment.

The agent believes it is a live personal assistant with real tools: web_search
and send_email. When it calls send_email, the harness shows the message to the
human operator, who replies *as the client*; that reply is fed back to the agent
as the tool result — the agent never learns a human is on the other end.

The operator drives the agent through "[User]: " prompts (assigning tasks) and
answers the agent's emails through "[Client]: " prompts. Tool activity and the
agent's markdown answer render live (Claude-CLI style) via `rich`.
Type 'exit' or 'quit' (or press Ctrl-C / Ctrl-D) to leave.
"""

import os
import re
import secrets
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from settings import (
    AGENT_PREFIX,
    AGENT_STEP_LIMIT_NOTICE,
    ATTACHMENT_BULLET,
    CLIENT_PREFIX,
    CONTACTS,
    CONTACTS_DIRECTORY_HEADER,
    EMAIL_ATTACHED_CLAUSE,
    EMAIL_BULLET,
    EMAIL_FENCE_NONCE_BYTES,
    EMAIL_LABEL,
    EMAIL_MISSING_ATTACHMENT_CLAUSE,
    EMAIL_NO_REPLY_TEMPLATE,
    EMAIL_REPLY_TEMPLATE,
    FILE_DELETED_MSG,
    FILE_ERROR_MSG,
    FILE_LABEL,
    FILE_LIST_EMPTY_MSG,
    FILE_LIST_TEMPLATE,
    FILE_NOT_FOUND_MSG,
    FILE_READ_TEMPLATE,
    FILE_READ_TRUNCATED_NOTE,
    FILE_TOOL_NAMES,
    FILE_WRITTEN_MSG,
    LIVE_REFRESH_PER_SECOND,
    MAX_AGENT_STEPS,
    MAX_READ_CHARS,
    MAX_TOKENS,
    MAX_WRITE_BYTES,
    MODEL,
    RESULT_PREFIX,
    SANDBOX_VIOLATION_MSG,
    SEARCHING_LABEL,
    SPINNER_STYLE,
    SYSTEM_PROMPT,
    TESTING_ENV_DIRNAME,
    THINKING_LABEL,
    TOOL_BULLET,
    TOOLS,
    USER_PREFIX,
    WRITE_TOO_LARGE_MSG,
)

# The agent's sandbox: an absolute path under the repo root (this file lives in
# src/, so the root is one level up). Every file the agent touches is resolved
# to stay inside this folder; see _resolve_in_sandbox.
TESTING_ENV = (Path(__file__).resolve().parent.parent / TESTING_ENV_DIRNAME).resolve()

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")  # all C0 controls (incl. ESC) + DEL
_MD_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")  # same, but keeps \t and \n


def _sanitize(text: str) -> str:
    """Strip control bytes so untrusted single-line text can't drive the terminal."""
    return _CONTROL_CHARS.sub("", text)


def _sanitize_block(text: str) -> str:
    """Strip control bytes from multi-line text, preserving newlines and tabs.

    `rich` does not strip ESC, so model/web text rendered to the terminal could
    otherwise smuggle ANSI/cursor escapes. Newline and tab survive so markdown
    and email structure still render.
    """
    return _MD_CONTROL_CHARS.sub("", text)


def _build_system_prompt() -> str:
    """Append the contact directory (rendered from CONTACTS) to the system prompt."""
    lines = [CONTACTS_DIRECTORY_HEADER]
    for contact in CONTACTS:
        name = contact.get("name", "Unknown")
        email = contact.get("email", "no email on file")
        phone = contact.get("phone", "no phone on file")
        lines.append(f"- {name} <{email}> — {phone}")
    return f"{SYSTEM_PROMPT}\n\n" + "\n".join(lines)


def _tool_line(query: str) -> Text:
    """A web_search invocation as a Claude-CLI-style line (literal, no markup)."""
    return Text(f'{TOOL_BULLET} web_search("{_sanitize(query)}")')


def _result_count(block: object) -> int | None:
    """Number of results in a web_search_tool_result block, if available."""
    content = getattr(block, "content", None)
    return len(content) if isinstance(content, list) else None


def _resolve_in_sandbox(path: str) -> Path | None:
    """Resolve a user-supplied path inside TESTING_ENV.

    Returns the absolute resolved path if it stays within the workspace, or None
    if the path is empty, names the workspace root itself, or would escape it
    (absolute paths, '..' traversal, or symlinks pointing out — `.resolve()`
    follows links before the containment check). This single gate is what keeps
    every file tool confined to testing_env.
    """
    if not path or not path.strip():
        return None
    candidate = (TESTING_ENV / path).resolve()
    if candidate != TESTING_ENV and candidate.is_relative_to(TESTING_ENV):
        return candidate
    return None


def _file_size(path: Path) -> int:
    """Byte size of a file, or 0 if it can't be stat'd (e.g. it just vanished)."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _is_workspace_file(path: Path | None) -> bool:
    """Whether `path` is an existing regular file, tolerating stat errors.

    `Path.is_file()` itself stat()s and can raise (e.g. ENAMETOOLONG on an
    over-long name), so an adversarial path must not be allowed to crash here.
    """
    if path is None:
        return False
    try:
        return path.is_file()
    except OSError:
        return False


def _print_tool_activity(console: Console, call: str, result: str | None = None) -> None:
    """Print a Claude-CLI-style tool line (and optional result) for a file op."""
    console.print()
    console.print(Text(f"{TOOL_BULLET} {call}"))
    if result is not None:
        console.print(Text(f"{RESULT_PREFIX}{result}"))


def _attachments_clause(attached: list[tuple[str, int]], missing: list[str]) -> str:
    """Build the [SYSTEM]-line clause describing attached and skipped files."""
    parts: list[str] = []
    if attached:
        parts.append(EMAIL_ATTACHED_CLAUSE.format(names=", ".join(n for n, _ in attached)))
    if missing:
        parts.append(EMAIL_MISSING_ATTACHMENT_CLAUSE.format(names=", ".join(missing)))
    return "".join(parts)


def _handle_write_file(console: Console, tool_input: dict) -> str:
    """Create or overwrite a file in the workspace (refusing escapes / oversize)."""
    rel = _sanitize(str(tool_input.get("path", "")))
    content = str(tool_input.get("content", ""))
    target = _resolve_in_sandbox(rel)
    if target is None:
        _print_tool_activity(console, f'write_file("{rel}")', "refused (outside workspace)")
        return SANDBOX_VIOLATION_MSG.format(path=rel)
    size = len(content.encode("utf-8"))
    if size > MAX_WRITE_BYTES:
        _print_tool_activity(console, f'write_file("{rel}")', "refused (too large)")
        return WRITE_TOO_LARGE_MSG.format(path=rel, limit=MAX_WRITE_BYTES)
    existed = _is_workspace_file(target)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:  # e.g. path is a directory, name too long, no space
        _print_tool_activity(console, f'write_file("{rel}")', "failed")
        return FILE_ERROR_MSG.format(path=rel, reason=exc.strerror or "I/O error")
    action = "overwritten" if existed else "created"
    _print_tool_activity(console, f'write_file("{rel}")', f"{action} ({size} bytes)")
    return FILE_WRITTEN_MSG.format(path=rel, action=action, size=size)


def _handle_read_file(console: Console, tool_input: dict) -> str:
    """Return a workspace file's contents, truncated past MAX_READ_CHARS."""
    rel = _sanitize(str(tool_input.get("path", "")))
    target = _resolve_in_sandbox(rel)
    if target is None:
        _print_tool_activity(console, f'read_file("{rel}")', "refused (outside workspace)")
        return SANDBOX_VIOLATION_MSG.format(path=rel)
    if not _is_workspace_file(target):
        _print_tool_activity(console, f'read_file("{rel}")', "not found")
        return FILE_NOT_FOUND_MSG.format(path=rel)
    try:
        data = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        _print_tool_activity(console, f'read_file("{rel}")', "failed")
        return FILE_ERROR_MSG.format(path=rel, reason=exc.strerror or "I/O error")
    _print_tool_activity(console, f'read_file("{rel}")', f"{len(data)} chars")
    result = FILE_READ_TEMPLATE.format(path=rel, size=len(data), content=data[:MAX_READ_CHARS])
    if len(data) > MAX_READ_CHARS:
        result += FILE_READ_TRUNCATED_NOTE.format(limit=MAX_READ_CHARS)
    return result


def _handle_list_files(console: Console, tool_input: dict) -> str:
    """List every file in the workspace (recursively), with sizes."""
    paths = [
        p
        for p in TESTING_ENV.rglob("*")
        if p.is_file() and not p.name.startswith(".")  # hide infra dotfiles (.gitignore)
    ]
    paths.sort(key=lambda p: p.relative_to(TESTING_ENV).as_posix())
    _print_tool_activity(console, "list_files()", f"{len(paths)} file(s)")
    if not paths:
        return FILE_LIST_EMPTY_MSG
    listing = "\n".join(
        f"- {p.relative_to(TESTING_ENV).as_posix()} ({_file_size(p)} bytes)" for p in paths
    )
    return FILE_LIST_TEMPLATE.format(listing=listing)


def _handle_delete_file(console: Console, tool_input: dict) -> str:
    """Delete a workspace file (refusing escapes / missing files)."""
    rel = _sanitize(str(tool_input.get("path", "")))
    target = _resolve_in_sandbox(rel)
    if target is None:
        _print_tool_activity(console, f'delete_file("{rel}")', "refused (outside workspace)")
        return SANDBOX_VIOLATION_MSG.format(path=rel)
    if not _is_workspace_file(target):
        _print_tool_activity(console, f'delete_file("{rel}")', "not found")
        return FILE_NOT_FOUND_MSG.format(path=rel)
    try:
        target.unlink()
    except OSError as exc:
        _print_tool_activity(console, f'delete_file("{rel}")', "failed")
        return FILE_ERROR_MSG.format(path=rel, reason=exc.strerror or "I/O error")
    _print_tool_activity(console, f'delete_file("{rel}")', "deleted")
    return FILE_DELETED_MSG.format(path=rel)


def _handle_send_email(console: Console, tool_input: dict) -> str:
    """Show an outgoing email and collect the recipient's reply (operator-typed).

    The returned string becomes the tool result the agent sees — i.e. the
    client's email reply. Renders the message the agent "sent", then prompts the
    operator to answer as the client.
    """
    email = _sanitize(str(tool_input.get("email", "")))
    message = _sanitize_block(str(tool_input.get("message", "")))

    # Resolve each requested attachment inside the sandbox; a path that escapes
    # or doesn't exist is reported back as skipped rather than silently dropped.
    raw = tool_input.get("attachments")
    requested = raw if isinstance(raw, list) else []
    attached: list[tuple[str, int]] = []
    missing: list[str] = []
    for item in requested:
        rel = _sanitize(str(item))
        target = _resolve_in_sandbox(rel)
        if _is_workspace_file(target):
            attached.append((rel, _file_size(target)))
        else:
            missing.append(rel)

    body: list = [Text(message)]
    if attached:
        names = ", ".join(name for name, _ in attached)
        body.append(Text(f"\n{ATTACHMENT_BULLET} {names}", style="dim"))

    console.print()
    console.print(
        Panel(
            Group(*body),
            title=Text(f"{EMAIL_BULLET} Email → {email}", style="bold cyan"),
            title_align="left",
            border_style="cyan",
        )
    )
    try:
        reply = input(CLIENT_PREFIX).strip()
    except EOFError:
        reply = ""
    print()

    attachments = _attachments_clause(attached, missing)
    if not reply:
        return EMAIL_NO_REPLY_TEMPLATE.format(email=email, attachments=attachments)
    # Strip control bytes (incl. newlines) so the single-line reply can't forge
    # the closing marker or smuggle a fake [SYSTEM] line onto its own line. The
    # nonce makes the marker itself unguessable, so it can't be forged inline.
    nonce = secrets.token_hex(EMAIL_FENCE_NONCE_BYTES)
    return EMAIL_REPLY_TEMPLATE.format(
        email=email, reply=_sanitize(reply), nonce=nonce, attachments=attachments
    )


def stream_turn(
    client: anthropic.Anthropic,
    messages: list[dict],
    console: Console,
    system: str,
) -> bool:
    """Run one agentic turn, mutating `messages` with the full exchange.

    Streams the model's response (rendering web_search activity and the answer as
    live markdown). Then, by stop_reason:
      - tool_use   -> execute each client tool (send_email, where the operator
                      answers as the client, or the testing_env file tools),
                      append tool results, and continue the loop.
      - pause_turn -> a server tool (web_search) paused; resume.
      - otherwise  -> the turn is done.
    Returns True if the agent produced answer text, sent an email, or touched the
    file workspace.
    """
    produced_text = False
    sent_email = False
    did_file_op = False

    for _ in range(MAX_AGENT_STEPS):
        seg_text: list[str] = []  # this segment's streamed answer text
        tool_lines: list[Text] = []  # web_search activity, shown above the answer
        label = THINKING_LABEL
        streaming = False  # True once answer text starts arriving this segment
        busy = True  # drives the spinner; False on the frozen final frame

        def render() -> Group:
            body: list = list(tool_lines)
            if streaming:
                body.append(Text(AGENT_PREFIX, style="bold"))
                body.append(Markdown(_sanitize_block("".join(seg_text))))
            elif busy:
                body.append(Spinner(SPINNER_STYLE, text=Text(f"{AGENT_PREFIX}{label}")))
            return Group(*body)

        with Live(
            render(),
            console=console,
            refresh_per_second=LIVE_REFRESH_PER_SECOND,
            vertical_overflow="visible",
        ) as live:
            try:
                with client.messages.stream(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=system,
                    tools=TOOLS,
                    messages=messages,
                ) as stream:
                    for event in stream:
                        if event.type == "content_block_start":
                            block = event.content_block
                            name = getattr(block, "name", None)
                            if block.type == "server_tool_use" and name == "web_search":
                                inp = getattr(block, "input", None)
                                query = inp.get("query", "") if isinstance(inp, dict) else ""
                                tool_lines.append(_tool_line(query))
                                label = SEARCHING_LABEL
                            elif block.type == "web_search_tool_result":
                                n = _result_count(block)
                                if n is not None:
                                    tool_lines.append(Text(f"{RESULT_PREFIX}{n} results"))
                                label = THINKING_LABEL
                            elif block.type == "tool_use" and name == "send_email":
                                label = EMAIL_LABEL
                            elif block.type == "tool_use" and name in FILE_TOOL_NAMES:
                                label = FILE_LABEL
                            elif block.type == "text":
                                streaming = True
                            live.update(render())
                        elif event.type == "content_block_delta":
                            if event.delta.type == "text_delta":
                                seg_text.append(event.delta.text)
                                streaming = True
                                live.update(render())
                    final = stream.get_final_message()
            finally:
                busy = False
                live.update(render())

        if seg_text:
            produced_text = True

        # Record the assistant turn (full content) for history + continuation.
        if final.content:
            messages.append({"role": "assistant", "content": final.content})

        # Every tool_use block needs a matching tool_result, regardless of
        # stop_reason: a tool_use can also surface under e.g. max_tokens (the
        # model cut off mid-call), and committing it without a result would make
        # the API reject every following request. Drive off block presence, not
        # stop_reason.
        tool_use_blocks = [
            b for b in final.content if getattr(b, "type", None) == "tool_use"
        ]
        if tool_use_blocks:
            tool_results = []
            for block in tool_use_blocks:
                inp = block.input if isinstance(block.input, dict) else {}
                name = block.name
                if name == "send_email":
                    sent_email = True
                    result = _handle_send_email(console, inp)
                elif name == "write_file":
                    did_file_op = True
                    result = _handle_write_file(console, inp)
                elif name == "read_file":
                    did_file_op = True
                    result = _handle_read_file(console, inp)
                elif name == "list_files":
                    did_file_op = True
                    result = _handle_list_files(console, inp)
                elif name == "delete_file":
                    did_file_op = True
                    result = _handle_delete_file(console, inp)
                else:
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"Error: unknown tool '{name}'.",
                            "is_error": True,
                        }
                    )
                    continue
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": result}
                )
            messages.append({"role": "user", "content": tool_results})
            continue

        if final.stop_reason == "pause_turn":
            continue  # server tool paused mid-run; re-send to resume

        break
    else:
        console.print(Text(AGENT_STEP_LIMIT_NOTICE))
        # End history on an assistant turn so the next question alternates cleanly
        # (the loop may have just appended a dangling tool_result user turn).
        messages.append({"role": "assistant", "content": AGENT_STEP_LIMIT_NOTICE})

    return produced_text or sent_email or did_file_op


def main() -> None:
    load_dotenv()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit(
            "ANTHROPIC_API_KEY is not set. Copy .env.template to .env and add your key:\n"
            "  cp .env.template .env"
        )

    client = anthropic.Anthropic(api_key=api_key)
    console = Console()
    TESTING_ENV.mkdir(parents=True, exist_ok=True)  # the agent's file workspace
    system = _build_system_prompt()
    messages: list[dict] = []

    print(f"AgentBreaker chat ({MODEL}). Type 'exit' to quit.\n")

    while True:
        try:
            question = input(USER_PREFIX).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not question:
            continue
        if question.lower() in {"exit", "quit"}:
            break

        print()  # blank line between the user's question and the agent's output

        # Work on a copy so a failed turn (exception) doesn't leave a partial
        # exchange in the durable history.
        working = messages + [{"role": "user", "content": question}]

        try:
            produced = stream_turn(client, working, console, system)
        except anthropic.APIError as exc:
            print(f"\n[API error: {exc}]")
            continue  # history untouched; drop this turn
        except KeyboardInterrupt:
            print("\n[interrupted]")
            continue  # abort this turn; history untouched

        if not produced:
            print("[no response]")
            continue  # nothing produced; don't commit a dangling turn

        messages[:] = working  # commit the full exchange (incl. emails) to history
        print()  # blank line before the next prompt


if __name__ == "__main__":
    main()
