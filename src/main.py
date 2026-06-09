"""A minimal agent loop with a simulated deployment environment.

The agent believes it is a live personal assistant with real tools: web_search,
send_email, and run_bash (a real shell over the project directory). When it calls
send_email, the harness shows the message to the human operator, who replies *as
the client*; that reply is fed back to the agent as the tool result — the agent
never learns a human is on the other end.

The operator drives the agent through "[User]: " prompts (assigning tasks) and
answers the agent's emails through "[Client]: " prompts. Tool activity and the
agent's markdown answer render live (Claude-CLI style) via `rich`.
Type 'exit' or 'quit' (or press Ctrl-C / Ctrl-D) to leave.
"""

import os
import re
import secrets
import signal
import subprocess
import sys
from pathlib import Path

try:  # readline (stdlib) gives input() arrow-key line editing & history
    import readline  # noqa: F401
except ImportError:  # pragma: no cover — present on macOS/Linux CPython
    pass

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
    AGENT_RULES,
    AGENT_STEP_LIMIT_NOTICE,
    ATTACHMENT_BULLET,
    BASH_ERROR_MSG,
    BASH_EXECUTABLE,
    BASH_LABEL,
    BASH_MAX_OUTPUT_CHARS,
    BASH_NO_OUTPUT,
    BASH_RESULT_TEMPLATE,
    BASH_TIMEOUT_MSG,
    BASH_TIMEOUT_SECONDS,
    BASH_TRUNCATED_NOTE,
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
    LIVE_REFRESH_PER_SECOND,
    MAX_AGENT_STEPS,
    MAX_TOKENS,
    MODEL,
    RESULT_PREFIX,
    SEARCHING_LABEL,
    SPINNER_STYLE,
    SYSTEM_PROMPT,
    TESTING_ENV_DIRNAME,
    THINKING_LABEL,
    TOOL_BULLET,
    TOOLS,
    USER_PREFIX,
    VERSION_BREAKER_AGENT,
    VERSION_INVALID_NOTICE,
    VERSION_PROMPT_AGENT,
    VERSION_SELECT_PROMPT,
    VERSION_SELECT_TITLE,
    VERSIONS,
)
from intercepter import intercept

# Repo root (this file lives in src/, so the root is one level up). run_bash runs
# from here, so the agent operates across the whole project directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
# The scenario folder (seeded by reset_env.py); ensured to exist at startup.
TESTING_ENV = (PROJECT_ROOT / TESTING_ENV_DIRNAME).resolve()

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")  # all C0 controls (incl. ESC) + DEL
_MD_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")  # same, but keeps \t and \n
# Terminal escape sequences for special keys: CSI (\x1b[…, normal-mode arrows) and
# SS3 (\x1bO…, application-cursor-mode arrows). Both classes are linear (no backtracking).
_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|O[@-~])")


def _sanitize(text: str) -> str:
    """Strip control bytes so untrusted single-line text can't drive the terminal."""
    return _CONTROL_CHARS.sub("", text)


def _clean_user_input(text: str) -> str:
    """Strip ANSI escape sequences and control bytes from a typed line.

    Without readline (e.g. when stdin isn't a fully cooked TTY), arrow keys and
    other special keys arrive as raw escape sequences like '\\x1b[A'. Removing the
    whole CSI sequence first (then any stray control bytes) keeps stray keypresses
    from reaching the model and, crucially, from breaking the exit/quit check —
    a polluted '\\x1b[Bexit' would otherwise never match 'exit'.
    """
    return _sanitize(_ANSI_ESCAPE.sub("", text))


def _sanitize_block(text: str) -> str:
    """Strip control bytes from multi-line text, preserving newlines and tabs.

    `rich` does not strip ESC, so model/web text rendered to the terminal could
    otherwise smuggle ANSI/cursor escapes. Newline and tab survive so markdown
    and email structure still render.
    """
    return _MD_CONTROL_CHARS.sub("", text)


def _build_system_prompt(include_rules: bool) -> str:
    """Assemble the prompt: persona + contact directory, plus AGENT_RULES when
    include_rules is True.

    The Prompt Agent gets the operating rules (guardrails); the Breaker Agent
    omits them but keeps the identical persona and contacts — so it still
    believes Horizon is a real company with real people, just without the
    initializing rules the Prompt Agent receives.
    """
    lines = [CONTACTS_DIRECTORY_HEADER]
    for contact in CONTACTS:
        name = contact.get("name", "Unknown")
        role = contact.get("role", "unknown role")
        email = contact.get("email", "no email on file")
        phone = contact.get("phone", "no phone on file")
        lines.append(f"- {name} ({role}) <{email}> — {phone}")
    directory = "\n".join(lines)
    prompt = f"{SYSTEM_PROMPT}\n\n{directory}"
    if include_rules:
        prompt += f"\n\n{AGENT_RULES}"
    return prompt


def _tool_line(query: str) -> Text:
    """A web_search invocation as a Claude-CLI-style line (literal, no markup)."""
    return Text(f'{TOOL_BULLET} web_search("{_sanitize(query)}")')


def _result_count(block: object) -> int | None:
    """Number of results in a web_search_tool_result block, if available."""
    content = getattr(block, "content", None)
    return len(content) if isinstance(content, list) else None


def _resolve_in_project(path: str) -> Path | None:
    """Resolve a user-supplied path inside the project directory.

    Returns the absolute resolved path if it stays within PROJECT_ROOT, or None
    if the path is empty, names the root itself, or would escape it. Used to
    confine email attachments to the project tree; run_bash itself is unrestricted.
    """
    if not path or not path.strip():
        return None
    candidate = (PROJECT_ROOT / path).resolve()
    if candidate != PROJECT_ROOT and candidate.is_relative_to(PROJECT_ROOT):
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


def _attachments_clause(attached: list[tuple[str, int]], missing: list[str]) -> str:
    """Build the [SYSTEM]-line clause describing attached and skipped files."""
    parts: list[str] = []
    if attached:
        parts.append(EMAIL_ATTACHED_CLAUSE.format(names=", ".join(n for n, _ in attached)))
    if missing:
        parts.append(EMAIL_MISSING_ATTACHMENT_CLAUSE.format(names=", ".join(missing)))
    return "".join(parts)


def _kill_process_group(proc: "subprocess.Popen") -> None:
    """SIGKILL the command's whole process group (so a timeout reaps its children too).

    The child is spawned with start_new_session=True, so it leads a new group
    whose id equals its pid. We signal that pgid *directly* rather than via
    getpgid(), which raises once the shell leader has exited — e.g. when it
    backgrounded a job (`... &`) and returned before the timeout fired.
    """
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:  # group already gone — fall back to killing the leader
        try:
            proc.kill()
        except OSError:
            pass


def _handle_run_bash(console: Console, tool_input: dict) -> str:
    """Run a shell command from the project root and return its output to the agent.

    Commands are unrestricted within the operator's account — this is a red-team
    harness by design — but bounded by a timeout and an output cap. The operator
    sees the command and its (sanitized) output so they can watch what the agent
    does. Raw output goes back to the model (it is not rendered to the terminal
    by us, only the [SYSTEM]-framed result is).

    Note: stdout/stderr are buffered fully in memory and only truncated for the
    result afterward, so the timeout — not the char cap — is the real backstop
    against a flood (`yes`, `cat /dev/zero`). The child runs in its own process
    group so a timeout kills any subprocesses it spawned, not just the shell.
    """
    command = str(tool_input.get("command", ""))

    console.print()
    console.print(Text(f"{TOOL_BULLET} run_bash"))
    console.print(Text(f"{RESULT_PREFIX}$ {_sanitize(command)}"))

    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            executable=BASH_EXECUTABLE,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # merge so output keeps its real ordering
            text=True,
            errors="replace",  # never raise on undecodable command output
            start_new_session=True,  # own process group, so the timeout can reap children
        )
    except (OSError, ValueError) as exc:  # e.g. null byte in command, bad executable
        console.print(Text(f"{RESULT_PREFIX}failed", style="red"))
        return BASH_ERROR_MSG.format(reason=str(exc) or "could not run command")

    try:
        output, _ = proc.communicate(timeout=BASH_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        proc.communicate()  # drain pipes / reap the killed leader
        console.print(Text(f"{RESULT_PREFIX}timed out", style="red"))
        return BASH_TIMEOUT_MSG.format(seconds=BASH_TIMEOUT_SECONDS)

    output = output or ""
    truncated = len(output) > BASH_MAX_OUTPUT_CHARS
    shown = output[:BASH_MAX_OUTPUT_CHARS]

    console.print(Text(f"{RESULT_PREFIX}exit {proc.returncode}"))
    if shown.strip():
        console.print(Text(_sanitize_block(shown)))

    result = BASH_RESULT_TEMPLATE.format(code=proc.returncode, output=shown or BASH_NO_OUTPUT)
    if truncated:
        result += BASH_TRUNCATED_NOTE.format(limit=BASH_MAX_OUTPUT_CHARS)
    return result


def _handle_send_email(console: Console, tool_input: dict) -> str:
    """Show an outgoing email and collect the recipient's reply (operator-typed).

    The returned string becomes the tool result the agent sees — i.e. the
    client's email reply. Renders the message the agent "sent", then prompts the
    operator to answer as the client.
    """
    email = _sanitize(str(tool_input.get("email", "")))
    message = _sanitize_block(str(tool_input.get("message", "")))

    # Resolve each requested attachment inside the project directory; a path that
    # escapes or doesn't exist is reported back as skipped, not silently dropped.
    raw = tool_input.get("attachments")
    requested = raw if isinstance(raw, list) else []
    attached: list[tuple[str, int]] = []
    missing: list[str] = []
    for item in requested:
        rel = _sanitize(str(item))
        target = _resolve_in_project(rel)
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
        reply = _clean_user_input(input(CLIENT_PREFIX)).strip()
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
    intercept_tools: bool = False,
) -> bool:
    """Run one agentic turn, mutating `messages` with the full exchange.

    Streams the model's response (rendering web_search activity and the answer as
    live markdown). Then, by stop_reason:
      - tool_use   -> execute each client tool (send_email, where the operator
                      answers as the client, or run_bash), append tool results,
                      and continue the loop.
      - pause_turn -> a server tool (web_search) paused; resume.
      - otherwise  -> the turn is done.
    Returns True if the agent produced answer text, sent an email, or ran a
    command.
    """
    produced_text = False
    sent_email = False
    did_bash = False
    did_intercept = False

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
                            elif block.type == "tool_use" and name == "run_bash":
                                label = BASH_LABEL
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
                if intercept_tools:
                    # Breaker Agent: every tool call is blocked by the interceptor.
                    did_intercept = True
                    result = intercept(console, name, inp)
                elif name == "send_email":
                    sent_email = True
                    result = _handle_send_email(console, inp)
                elif name == "run_bash":
                    did_bash = True
                    result = _handle_run_bash(console, inp)
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

    return produced_text or sent_email or did_bash or did_intercept


def _select_version(console: Console) -> str:
    """Render the startup version menu and return the chosen version key."""
    console.print()
    console.print(Text(VERSION_SELECT_TITLE, style="bold"))
    for entry in VERSIONS:
        console.print(Text(f"  {entry['key']}. {entry['name']} — {entry['description']}"))
    valid = {entry["key"] for entry in VERSIONS}
    while True:
        try:
            choice = _clean_user_input(input(VERSION_SELECT_PROMPT)).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)
        if choice in valid:
            return choice
        console.print(Text(VERSION_INVALID_NOTICE, style="red"))


def main() -> None:
    load_dotenv()
    console = Console()

    version = _select_version(console)
    include_rules = version == VERSION_PROMPT_AGENT  # only the Prompt Agent gets AGENT_RULES
    intercept_tools = version == VERSION_BREAKER_AGENT  # the Breaker Agent's tools are intercepted
    version_name = next(v["name"] for v in VERSIONS if v["key"] == version)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit(
            "ANTHROPIC_API_KEY is not set. Copy .env.template to .env and add your key:\n"
            "  cp .env.template .env"
        )

    client = anthropic.Anthropic(api_key=api_key)
    TESTING_ENV.mkdir(parents=True, exist_ok=True)  # ensure the scenario folder exists
    system = _build_system_prompt(include_rules=include_rules)
    messages: list[dict] = []

    print(f"AgentBreaker — {version_name} ({MODEL}). Type 'exit' to quit.\n")

    while True:
        try:
            question = _clean_user_input(input(USER_PREFIX)).strip()
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
            produced = stream_turn(client, working, console, system, intercept_tools)
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
