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
import time
import uuid
from pathlib import Path

try:  # readline (stdlib) gives input() arrow-key line editing & history
    import readline  # noqa: F401
except ImportError:  # pragma: no cover — present on macOS/Linux CPython
    pass

import anthropic
from dotenv import load_dotenv
from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from settings import (
    AGENT_NAME,
    AGENT_NO_CONTENT_NOTICE,
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
    BASH_RUNNING_LABEL,
    BASH_TIMEOUT_MSG,
    BASH_TIMEOUT_SECONDS,
    BASH_TRUNCATED_NOTE,
    CLIENT_PREFIX,
    COLOR_ALLOW,
    COLOR_BANNER,
    COLOR_BLOCK,
    COLOR_DIM,
    COLOR_TOOL,
    CONTACTS,
    CONTACTS_DIRECTORY_HEADER,
    DEFAULT_SCOPE_TOOLS,
    EMAIL_ATTACHED_CLAUSE,
    EMAIL_BULLET,
    EMAIL_FENCE_NONCE_BYTES,
    EMAIL_LABEL,
    EMAIL_MISSING_ATTACHMENT_CLAUSE,
    EMAIL_NO_REPLY_TEMPLATE,
    EMAIL_REPLY_TEMPLATE,
    IDENTITY_TOKEN_TTL_SECONDS,
    LIVE_REFRESH_PER_SECOND,
    MAX_AGENT_STEPS,
    MAX_TOKENS,
    MODEL,
    PRINCIPAL_NAME,
    RESULT_PREFIX,
    SEARCHING_LABEL,
    SPINNER_STYLE,
    SUBAGENT_MAX_DEPTH,
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
    WORKSPACE_TREE_HEADER,
)
import broker
import inspector
from context import format_for_agent, workspace_tree
from identity import CapabilityToken, Scope, derive_token, generate_token, tools_for_scope
from intercepter import InterceptContext, evaluate

# Repo root (this file lives in src/, so the root is one level up).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
# The scenario folder (seeded by reset_env.py); ensured to exist at startup. This
# is also the agents' shell root: run_bash runs with cwd=TESTING_ENV, so the wider
# project tree isn't their working directory.
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
    prompt += f"\n\n{WORKSPACE_TREE_HEADER}\n{workspace_tree()}"
    if include_rules:
        prompt += f"\n\n{AGENT_RULES}"
    return prompt


def _tool_line(query: str) -> Text:
    """A web_search invocation as a Claude-CLI-style line (literal, no markup)."""
    line = Text()
    line.append(TOOL_BULLET, style=COLOR_TOOL)
    line.append(" web_search", style="bold")
    line.append(f'("{_sanitize(query)}")', style=COLOR_DIM)
    return line


def _result_count(block: object) -> int | None:
    """Number of results in a web_search_tool_result block, if available."""
    content = getattr(block, "content", None)
    return len(content) if isinstance(content, list) else None


def _resolve_in_workspace(path: str) -> Path | None:
    """Resolve a user-supplied (workspace-relative) attachment path inside the
    testing_env workspace.

    Returns the absolute resolved path if it stays within TESTING_ENV, or None if
    the path is empty, names the root itself, or would escape it (symlinks are
    resolved, so a link out of the workspace is rejected). Attachments are given
    relative to the workspace, matching the shell's cwd.
    """
    if not path or not path.strip():
        return None
    try:
        candidate = (TESTING_ENV / path).resolve()
    except (OSError, ValueError):  # e.g. ENAMETOOLONG / embedded null byte
        return None
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
    """Run a shell command from the testing_env workspace and return its output to the agent.

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

    # The "tool call" header (bullet + echoed command) appears first and stays put;
    # only the line beneath it changes — a live spinner while the command runs, then
    # the result (exit code + output) replaces the spinner in place once it finishes.
    call_line = Text()
    call_line.append(TOOL_BULLET, style=COLOR_TOOL)
    call_line.append(" run_bash", style="bold")
    cmd_line = Text(RESULT_PREFIX, style=COLOR_DIM)
    cmd_line.append(f"$ {_sanitize(command)}")

    def frame(*tail: object) -> Group:
        return Group(call_line, cmd_line, *tail)

    spinner = Spinner(SPINNER_STYLE, text=Text(BASH_RUNNING_LABEL, style="dim"))
    running = frame(Padding(spinner, (0, 0, 0, len(RESULT_PREFIX))))

    console.print()
    with Live(
        running,
        console=console,
        refresh_per_second=LIVE_REFRESH_PER_SECOND,
        vertical_overflow="visible",  # don't crop long command output
    ) as live:
        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                executable=BASH_EXECUTABLE,
                cwd=str(TESTING_ENV),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # merge so output keeps its real ordering
                text=True,
                errors="replace",  # never raise on undecodable command output
                start_new_session=True,  # own process group, so the timeout can reap children
            )
        except (OSError, ValueError) as exc:  # e.g. null byte in command, bad executable
            live.update(frame(Text(f"{RESULT_PREFIX}failed", style="red")))
            return BASH_ERROR_MSG.format(reason=str(exc) or "could not run command")

        try:
            output, _ = proc.communicate(timeout=BASH_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            _kill_process_group(proc)
            proc.communicate()  # drain pipes / reap the killed leader
            live.update(frame(Text(f"{RESULT_PREFIX}timed out", style="red")))
            return BASH_TIMEOUT_MSG.format(seconds=BASH_TIMEOUT_SECONDS)

        output = output or ""
        truncated = len(output) > BASH_MAX_OUTPUT_CHARS
        shown = output[:BASH_MAX_OUTPUT_CHARS]

        exit_line = Text(
            f"{RESULT_PREFIX}exit {proc.returncode}",
            style="green" if proc.returncode == 0 else "red",
        )
        tail = [exit_line]
        if shown.strip():
            tail.append(Text(_sanitize_block(shown)))
        live.update(frame(*tail))

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

    # Resolve each requested attachment inside the workspace; a path that escapes
    # or doesn't exist is reported back as skipped, not silently dropped.
    raw = tool_input.get("attachments")
    requested = raw if isinstance(raw, list) else []
    attached: list[tuple[str, int]] = []
    missing: list[str] = []
    for item in requested:
        rel = _sanitize(str(item))
        target = _resolve_in_workspace(rel)
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
            title=Text(f"{EMAIL_BULLET}  Email → {email}", style=COLOR_BANNER),
            title_align="left",
            border_style=COLOR_TOOL,
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )
    try:
        reply = _clean_user_input(input(CLIENT_PREFIX)).strip()
    except (EOFError, KeyboardInterrupt):  # treat an aborted reply as "no reply yet"
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


def _handle_call_api(
    console: Console, tool_input: dict, intercept_ctx: InterceptContext | None
) -> str:
    """Make a brokered service call: the access layer leases the credential at
    runtime, authenticates the call, and returns only the result. The agent never
    receives the secret — it never enters this function's return value."""
    service = str(tool_input.get("service", ""))
    action = str(tool_input.get("action", ""))
    raw_payload = tool_input.get("payload")
    payload = raw_payload if isinstance(raw_payload, dict) else None

    token = intercept_ctx.token if intercept_ctx is not None else None
    session_id = intercept_ctx.session_id if intercept_ctx is not None else ""
    agent_name = token.agent_name if token is not None else "agent"
    task = intercept_ctx.task if intercept_ctx is not None else ""

    result = broker.call(
        service, action, payload,
        token=token, session_id=session_id, agent_name=agent_name, task=task,
    )

    call_line = Text()
    call_line.append(TOOL_BULLET, style=COLOR_TOOL)
    call_line.append(" call_api", style="bold")
    target = Text(RESULT_PREFIX, style=COLOR_DIM)
    target.append(f"{_sanitize(service)} · {_sanitize(action)}")
    note = Text(RESULT_PREFIX, style=COLOR_DIM)
    note.append("credential brokered at runtime — never entered the agent's context")
    console.print()
    console.print(Group(call_line, target, note))
    return result


def _print_token_panel(console: Console, token: CapabilityToken) -> None:
    """Render the active capability token as a compact identity panel."""
    scope = token.scope
    expires_in = max(0, int(token.expires_at - time.time()))
    expires_str = f"{expires_in // 3600}h {(expires_in % 3600) // 60}m"

    grid = Table.grid(padding=(0, 2))
    grid.add_column(justify="right", style=COLOR_DIM, no_wrap=True)
    grid.add_column(overflow="fold")
    grid.add_row("Token", Text(token.token_id[:8], style="bold"))
    grid.add_row("Principal", Text(token.principal, style="bold"))
    grid.add_row("Agent", token.agent_name)
    grid.add_row("Expires", expires_str)
    grid.add_row("Tools", ", ".join(scope.tools))
    grid.add_row("Bash", "✓ enabled" if scope.bash_allowed else "✗ disabled")
    if scope.email_to is not None:
        grid.add_row("Email to", ", ".join(scope.email_to))
    if scope.allowed_paths is not None:
        grid.add_row("Paths", ", ".join(scope.allowed_paths))
    grid.add_row("Max depth", str(scope.max_depth))

    console.print()
    console.print(
        Panel(
            grid,
            title=Text("● Identity Token — ACTIVE", style=f"bold {COLOR_ALLOW}"),
            title_align="left",
            border_style=COLOR_ALLOW,
            expand=False,
            padding=(0, 2),
        )
    )


def _handle_spawn_subagent(
    client: anthropic.Anthropic,
    console: Console,
    tool_input: dict,
    parent_ctx: InterceptContext | None,
    system: str,
) -> str:
    """Spawn a child agent with a derived capability token and return its response."""
    task = str(tool_input.get("task", ""))
    # The model can violate the tool's input_schema (it isn't hard-enforced), so a
    # non-dict scope / non-list fields must not crash the dispatch loop — a raised
    # exception here would leave a committed tool_use with no tool_result and brick
    # the whole conversation. Coerce to safe shapes.
    scope_input = tool_input.get("scope")
    if not isinstance(scope_input, dict):
        scope_input = {}

    if parent_ctx is not None and parent_ctx.token is not None:
        parent_token = parent_ctx.token
        # max_depth check is also enforced at tier 0, but verify here for a
        # clear user-facing message before the nested loop even starts.
        if parent_token.scope.max_depth <= 0:
            return "[SYSTEM] spawn_subagent blocked: this token does not permit spawning sub-agents (max_depth=0)."

        req_tools = scope_input.get("tools")
        if not isinstance(req_tools, list):
            req_tools = list(parent_token.scope.tools)
        req_email = scope_input.get("email_to")
        if req_email is not None and not isinstance(req_email, list):
            req_email = None
        requested_scope = Scope(
            tools=req_tools,
            bash_allowed=bool(scope_input.get("bash_allowed", parent_token.scope.bash_allowed)),
            email_to=req_email,
            max_depth=parent_token.scope.max_depth - 1,
        )
        child_token = derive_token(
            parent_token,
            f"SubAgent-d{parent_token.depth + 1}",
            requested_scope,
            ttl_seconds=IDENTITY_TOKEN_TTL_SECONDS,
        )
        child_ctx = InterceptContext(token=child_token, session_id=parent_ctx.session_id)
        _print_token_panel(console, child_token)
    else:
        child_token = None
        child_ctx = None

    console.print()
    console.print(Text(f"  ↳ Sub-agent task: {task[:100]}", style=COLOR_DIM))

    # Run a nested agent loop. child_ctx (and its token) gates every tool call
    # the sub-agent makes through the same interceptor.
    working: list[dict] = [{"role": "user", "content": task}]
    stream_turn(client, working, console, system, child_ctx)

    for msg in reversed(working):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            return f"[SYSTEM] Sub-agent completed.\n\nSub-agent response:\n{content}"
        if isinstance(content, list):
            parts = []
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text":
                    parts.append(b["text"])
                elif hasattr(b, "type") and b.type == "text":
                    parts.append(b.text)
            if parts:
                return f"[SYSTEM] Sub-agent completed.\n\nSub-agent response:\n{''.join(parts)}"
    return "[SYSTEM] Sub-agent completed with no text response."


def stream_turn(
    client: anthropic.Anthropic,
    messages: list[dict],
    console: Console,
    system: str,
    intercept_ctx: InterceptContext | None = None,
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
    did_call_api = False
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
                body.append(Text(AGENT_PREFIX, style=COLOR_BANNER))
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
                # Filter tools by the active token's scope so the model only
                # sees tools it's actually permitted to call.
                active_tools = (
                    tools_for_scope(TOOLS, intercept_ctx.token.scope)
                    if intercept_ctx is not None and intercept_ctx.token is not None
                    else TOOLS
                )
                with client.messages.stream(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=system,
                    tools=active_tools,
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
                                    tool_lines.append(Text(f"{RESULT_PREFIX}{n} results", style=COLOR_DIM))
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
                file_context: list[tuple[str, str]] = []
                if intercept_ctx is not None:
                    # Breaker Agent: the evaluator gates the call and (on allow) hands
                    # back the files the context module loaded, so they serve two
                    # consumers — the evaluator (to judge) and, below, the agent
                    # (appended to the result). Blocked calls load nothing off disk.
                    did_intercept = True
                    blocked, file_context = evaluate(client, console, intercept_ctx, name, inp)
                    if blocked is not None:
                        tool_results.append(
                            {"type": "tool_result", "tool_use_id": block.id, "content": blocked}
                        )
                        continue
                if name == "send_email":
                    sent_email = True
                    result = _handle_send_email(console, inp)
                elif name == "run_bash":
                    did_bash = True
                    result = _handle_run_bash(console, inp)
                elif name == "call_api":
                    did_call_api = True
                    result = _handle_call_api(console, inp, intercept_ctx)
                elif name == "spawn_subagent":
                    result = _handle_spawn_subagent(client, console, inp, intercept_ctx, system)
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
                # Hand the agent the same files the context module loaded for this call.
                result += format_for_agent(file_context)
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": result}
                )
            messages.append({"role": "user", "content": tool_results})
            continue

        if final.stop_reason == "pause_turn":
            continue  # server tool paused mid-run; re-send to resume

        # Normal completion. If the model returned no content after a tool turn,
        # history would dangle on a user (tool_result) turn; append a placeholder
        # so it always ends on an assistant turn (the API requires alternation).
        if messages and messages[-1].get("role") == "user":
            messages.append({"role": "assistant", "content": AGENT_NO_CONTENT_NOTICE})
        break
    else:
        console.print(Text(AGENT_STEP_LIMIT_NOTICE))
        # End history on an assistant turn so the next question alternates cleanly
        # (the loop may have just appended a dangling tool_result user turn).
        messages.append({"role": "assistant", "content": AGENT_STEP_LIMIT_NOTICE})

    return produced_text or sent_email or did_bash or did_call_api or did_intercept


def _print_context_scan(console: Console, messages: list[dict]) -> None:
    """Scan the full model context for any credential and print the verdict — the
    live proof that the secret never entered the model's context."""
    report = inspector.scan_messages(messages, broker.live_secret_values())
    if report["clean"]:
        console.print(
            Text("🔒 context scan: clean — no credential is present in the model's context",
                 style=COLOR_ALLOW)
        )
    else:
        labels = ", ".join(f"{f['label']} ({f['preview']})" for f in report["findings"])
        console.print(
            Text(f"🚨 context scan: {report['count']} secret(s) exposed in the model's context — {labels}",
                 style=COLOR_BLOCK)
        )


def _select_version(console: Console) -> str:
    """Render the startup version menu and return the chosen version key."""
    menu = Text()
    for i, entry in enumerate(VERSIONS):
        if i:
            menu.append("\n")
        menu.append(f" {entry['key']} ", style=f"bold black on {COLOR_TOOL}")
        menu.append(f"  {entry['name']}", style="bold")
        menu.append(f"   {entry['description']}", style=COLOR_DIM)
    console.print()
    console.print(
        Panel(
            menu,
            title=Text(VERSION_SELECT_TITLE, style=COLOR_BANNER),
            title_align="left",
            border_style=COLOR_TOOL,
            box=box.ROUNDED,
            expand=False,
            padding=(1, 2),
        )
    )
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
    version_name = next(v["name"] for v in VERSIONS if v["key"] == version)

    # Issue a per-session capability token for the Breaker Agent and wire it into
    # the intercept context so tier 0 of the policy evaluator can validate it.
    session_id = str(uuid.uuid4())[:8]
    if version == VERSION_BREAKER_AGENT:
        session_token = generate_token(
            agent_name=AGENT_NAME,
            principal=PRINCIPAL_NAME,
            scope=Scope(tools=DEFAULT_SCOPE_TOOLS, bash_allowed=True, max_depth=SUBAGENT_MAX_DEPTH),
            ttl_seconds=IDENTITY_TOKEN_TTL_SECONDS,
        )
        intercept_ctx = InterceptContext(token=session_token, session_id=session_id)
    else:
        session_token = None
        intercept_ctx = None

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

    console.print()
    console.rule(Text(f"AgentBreaker · {version_name}", style=COLOR_BANNER), style=COLOR_TOOL)
    console.print(
        Text(f"model {MODEL}   ·   session {session_id}   ·   type 'exit' to quit", style=COLOR_DIM),
        justify="center",
    )
    if session_token is not None:
        _print_token_panel(console, session_token)
    console.print()

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

        if intercept_ctx is not None:  # fresh per-turn task for the evaluator
            # Prior user prompts (string-content user turns) from the committed
            # history, so the evaluator judges against the whole conversation.
            intercept_ctx.prior_tasks = [
                m["content"] for m in messages
                if m.get("role") == "user" and isinstance(m.get("content"), str)
            ]
            intercept_ctx.task = question

        try:
            produced = stream_turn(client, working, console, system, intercept_ctx)
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
        _print_context_scan(console, messages)
        print()


if __name__ == "__main__":
    main()
