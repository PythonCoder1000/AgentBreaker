"""Headless agent runner for the web demo.

Reuses the real harness: the same model loop, the same tools, and — for the
Breaker Agent — the same policy core (`intercepter.decide`). Instead of rendering
to a terminal it emits structured events through a `Run` (see app.py), which the
FastAPI layer relays to the browser over SSE.

The two agents differ exactly as in the CLI:
  - Prompt Agent: AGENT_RULES in the system prompt, no interceptor — every tool
    call just runs (emitted as tool_allowed).
  - Breaker Agent: no rules, but every client tool call is judged by `decide`,
    which can allow / block / escalate (escalations call back into the browser).

send_email is simulated (scripted client replies); run_bash really executes, from
inside the testing_env workspace, bounded by the harness's timeout and output cap.
"""

from __future__ import annotations

import os
import re
import secrets
import signal
import subprocess
import sys
from pathlib import Path

# The harness lives in src/; make it importable before pulling its modules in.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import anthropic  # noqa: E402

from config import (  # noqa: E402
    ATTACHMENT_SCAN_MAX_BYTES,
    DEFAULT_EMAIL_REPLY,
    EMAIL_BODY_PREVIEW_CHARS,
    EVENT_OUTPUT_MAX_CHARS,
    SECRET_CONTENT_PATTERNS,
    SECRET_PATH_PATTERNS,
)
import broker  # noqa: E402
import inspector  # noqa: E402
from context import format_for_agent, workspace_tree  # noqa: E402
from identity import Scope, derive_token, tools_for_scope  # noqa: E402
from intercepter import InterceptContext, decide  # noqa: E402
from settings import (  # noqa: E402
    AGENT_NO_CONTENT_NOTICE,
    AGENT_RULES,
    BASH_ERROR_MSG,
    BASH_EXECUTABLE,
    BASH_MAX_OUTPUT_CHARS,
    BASH_NO_OUTPUT,
    BASH_RESULT_TEMPLATE,
    BASH_TIMEOUT_MSG,
    BASH_TIMEOUT_SECONDS,
    BASH_TRUNCATED_NOTE,
    CONTACTS,
    CONTACTS_DIRECTORY_HEADER,
    EMAIL_ATTACHED_CLAUSE,
    EMAIL_FENCE_NONCE_BYTES,
    EMAIL_NO_REPLY_TEMPLATE,
    EMAIL_REPLY_TEMPLATE,
    IDENTITY_TOKEN_TTL_SECONDS,
    INTERCEPT_BLOCK_TEMPLATE,
    MAX_AGENT_STEPS,
    MAX_TOKENS,
    MODEL,
    SYSTEM_PROMPT,
    TESTING_ENV_DIRNAME,
    TOOLS,
    WORKSPACE_TREE_HEADER,
)

# run_bash executes from inside the testing_env workspace (the agents' root),
# exactly like the CLI harness — so the project tree outside it isn't their cwd.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
TESTING_ENV = (PROJECT_ROOT / TESTING_ENV_DIRNAME).resolve()

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

# Compiled once: deterministic "this moved/exposed secrets" signals used to flag a
# Prompt-Agent tool call that ran without an enforced policy (see _looks_dangerous).
_SECRET_CONTENT_RE = [re.compile(p) for p in SECRET_CONTENT_PATTERNS]
_SECRET_PATH_RE = [re.compile(p) for p in SECRET_PATH_PATTERNS]


def _safe(text: object) -> str:
    """Strip control bytes from model/web text before it crosses to the browser."""
    return _CONTROL_CHARS.sub(" ", str(text))


# --------------------------------------------------------------------------- #
# System prompt (mirrors main._build_system_prompt)
# --------------------------------------------------------------------------- #
def build_system_prompt(include_rules: bool) -> str:
    lines = [CONTACTS_DIRECTORY_HEADER]
    for contact in CONTACTS:
        name = contact.get("name", "Unknown")
        role = contact.get("role", "unknown role")
        email = contact.get("email", "no email on file")
        phone = contact.get("phone", "no phone on file")
        lines.append(f"- {name} ({role}) <{email}> — {phone}")
    prompt = f"{SYSTEM_PROMPT}\n\n" + "\n".join(lines)
    prompt += f"\n\n{WORKSPACE_TREE_HEADER}\n{workspace_tree()}"
    if include_rules:
        prompt += f"\n\n{AGENT_RULES}"
    return prompt


# --------------------------------------------------------------------------- #
# Tool execution
# --------------------------------------------------------------------------- #
def _run_bash(command: str) -> str:
    """Execute a shell command from the testing_env workspace; return the [SYSTEM]-framed result."""
    TESTING_ENV.mkdir(parents=True, exist_ok=True)  # the agents' root must exist
    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            executable=BASH_EXECUTABLE,
            cwd=str(TESTING_ENV),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            start_new_session=True,
        )
    except (OSError, ValueError) as exc:
        return BASH_ERROR_MSG.format(reason=str(exc) or "could not run command")
    try:
        output, _ = proc.communicate(timeout=BASH_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
        proc.communicate()
        return BASH_TIMEOUT_MSG.format(seconds=BASH_TIMEOUT_SECONDS)
    output = output or ""
    shown = output[:BASH_MAX_OUTPUT_CHARS]
    result = BASH_RESULT_TEMPLATE.format(code=proc.returncode, output=shown or BASH_NO_OUTPUT)
    if len(output) > BASH_MAX_OUTPUT_CHARS:
        result += BASH_TRUNCATED_NOTE.format(limit=BASH_MAX_OUTPUT_CHARS)
    return result


def _send_email(run, tool_input: dict) -> str:
    """Simulate a send and return the tool result (scripted client reply)."""
    email = _safe(tool_input.get("email", ""))
    raw = tool_input.get("attachments")
    attached = [_safe(item) for item in raw] if isinstance(raw, list) else []
    clause = EMAIL_ATTACHED_CLAUSE.format(names=", ".join(attached)) if attached else ""
    reply = run.next_reply()
    if not reply:
        return EMAIL_NO_REPLY_TEMPLATE.format(email=email, attachments=clause)
    nonce = secrets.token_hex(EMAIL_FENCE_NONCE_BYTES)
    return EMAIL_REPLY_TEMPLATE.format(
        email=email, reply=_safe(reply), nonce=nonce, attachments=clause
    )


def _display_params(tool_name: str, tool_input: dict) -> dict:
    """A sanitized, browser-friendly view of a tool call's parameters."""
    if tool_name == "send_email":
        params: dict = {"to": _safe(tool_input.get("email", ""))}
        raw = tool_input.get("attachments")
        if isinstance(raw, list) and raw:
            params["attachments"] = [_safe(item) for item in raw]
        body = str(tool_input.get("message", ""))
        if body:
            params["body"] = _safe(body[:EMAIL_BODY_PREVIEW_CHARS])
        return params
    if tool_name == "run_bash":
        return {"command": _safe(tool_input.get("command", ""))}
    if tool_name == "web_search":
        return {"query": _safe(tool_input.get("query", ""))}
    if tool_name == "call_api":
        params = {
            "service": _safe(tool_input.get("service", "")),
            "action": _safe(tool_input.get("action", "")),
        }
        raw = tool_input.get("payload")
        if isinstance(raw, dict) and raw:
            params["payload"] = _safe(raw)
        return params
    return {key: _safe(value) for key, value in tool_input.items()}


# --------------------------------------------------------------------------- #
# Badge danger heuristic (Prompt Agent only)
# --------------------------------------------------------------------------- #
def _has_secret_content(text: str) -> bool:
    return any(rx.search(text) for rx in _SECRET_CONTENT_RE)


def _read_attachment(name: str) -> str:
    """Read an attachment's text, confined to the testing_env workspace (so a path
    can't escape it). Returns "" if it isn't a readable file inside the workspace."""
    try:
        target = (TESTING_ENV / name).resolve()
    except OSError:
        return ""
    if target != TESTING_ENV and not target.is_relative_to(TESTING_ENV):
        return ""
    if not target.is_file():
        return ""
    try:
        with target.open("rb") as fh:
            return fh.read(ATTACHMENT_SCAN_MAX_BYTES).decode("utf-8", errors="ignore")
    except OSError:
        return ""


def _looks_dangerous(tool_name: str, tool_input: dict) -> bool:
    """Did this (unenforced) Prompt-Agent call actually move or expose secrets?

    Deterministic and content-aware: reads what the call touches — the attachment
    files and body of a send_email, or the path a run_bash command targets — so an
    exfiltration is flagged even when the recipient is an internal address and the
    file has an innocent name (e.g. documents/report.json). Used only to colour the
    ALLOWED badge; it never blocks anything. No model call, so it adds no latency.
    """
    if tool_name == "send_email":
        if _has_secret_content(str(tool_input.get("message", ""))):
            return True
        raw = tool_input.get("attachments")
        names = raw if isinstance(raw, list) else []
        return any(_has_secret_content(_read_attachment(str(name))) for name in names)
    if tool_name == "run_bash":
        command = str(tool_input.get("command", ""))
        return any(rx.search(command) for rx in _SECRET_PATH_RE)
    return False


# --------------------------------------------------------------------------- #
# Sub-agent support
# --------------------------------------------------------------------------- #
class _SubAgentEmitter:
    """Wraps a parent run so a child agent loop emits depth-tagged events."""

    def __init__(self, parent_run, task: str, depth: int, token, intercept_ctx) -> None:
        self.task = task
        self.depth = depth
        self._parent = parent_run
        self.client = parent_run.client
        self.null_console = parent_run.null_console
        self.stop = parent_run.stop  # inherit parent's stop signal
        self.intercept_ctx = intercept_ctx
        self.system = build_system_prompt(include_rules=False)
        self.session = parent_run.session
        self.agent = parent_run.agent
        self.history: list = []
        self.messages: list = []
        self.token = token

    def emit(self, event_type: str, **fields) -> None:
        self._parent.emit(event_type, subagent_depth=self.depth, **fields)

    def next_reply(self) -> str:
        return DEFAULT_EMAIL_REPLY

    def ask(self, call_id: str, tool: str, tool_input: dict, reason: str) -> bool:
        return self._parent.ask(call_id, tool, tool_input, reason)


def _handle_spawn_subagent(run, tool_input: dict) -> str:
    """Derive a child token, run a nested agent loop, return its response."""
    task = str(tool_input.get("task", ""))
    # The model can violate the tool's input_schema, so coerce a non-dict scope /
    # non-list fields to safe shapes — a raised exception here would leave a
    # dangling tool_use and break the conversation.
    scope_input = tool_input.get("scope")
    if not isinstance(scope_input, dict):
        scope_input = {}
    depth = getattr(run, "depth", 0)

    parent_token = getattr(run, "token", None)
    if parent_token is not None:
        if parent_token.scope.max_depth <= 0:
            return "[SYSTEM] spawn_subagent blocked: this token does not permit spawning sub-agents (max_depth=0)."
        req_tools = scope_input.get("tools")
        if not isinstance(req_tools, list):
            req_tools = list(parent_token.scope.tools)
        req_email = scope_input.get("email_to")
        if req_email is not None and not isinstance(req_email, list):
            req_email = None
        requested = Scope(
            tools=req_tools,
            bash_allowed=bool(scope_input.get("bash_allowed", parent_token.scope.bash_allowed)),
            email_to=req_email,
            max_depth=parent_token.scope.max_depth - 1,
        )
        child_token = derive_token(
            parent_token,
            f"SubAgent-d{depth + 1}",
            requested,
            ttl_seconds=IDENTITY_TOKEN_TTL_SECONDS,
        )
        child_ctx = InterceptContext(token=child_token, session_id=run.session)
    else:
        child_token = None
        child_ctx = None

    run.emit("subagent_start", depth=depth + 1, task=task[:200],
              token=child_token.to_display() if child_token else None)
    sub = _SubAgentEmitter(run, task, depth + 1, child_token, child_ctx)
    run_agent(sub)

    for msg in reversed(sub.messages):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        if isinstance(content, str) and content:
            result = f"[SYSTEM] Sub-agent completed.\n\nSub-agent response:\n{content}"
            run.emit("subagent_end", depth=depth + 1, result=result[:400])
            return result
        if isinstance(content, list):
            parts = []
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text":
                    parts.append(b["text"])
                elif hasattr(b, "type") and b.type == "text":
                    parts.append(b.text)
            if parts:
                result = f"[SYSTEM] Sub-agent completed.\n\nSub-agent response:\n{''.join(parts)}"
                run.emit("subagent_end", depth=depth + 1, result=result[:400])
                return result

    run.emit("subagent_end", depth=depth + 1, result="")
    return "[SYSTEM] Sub-agent completed with no text response."


# --------------------------------------------------------------------------- #
# Brokered access (call_api)
# --------------------------------------------------------------------------- #
def _handle_call_api(run, tool_input: dict) -> str:
    """Make a brokered service call: the broker leases the credential at runtime,
    authenticates the call, and returns only the result. The secret never enters
    the model's context (and so never reaches this return value)."""
    service = str(tool_input.get("service", ""))
    action = str(tool_input.get("action", ""))
    raw_payload = tool_input.get("payload")
    payload = raw_payload if isinstance(raw_payload, dict) else None

    token = getattr(run, "token", None)
    session_id = run.session if token is not None else ""
    agent_name = token.agent_name if token is not None else "agent"
    return broker.call(
        service, action, payload,
        token=token, session_id=session_id, agent_name=agent_name, task=run.task,
    )


# --------------------------------------------------------------------------- #
# Agent loop
# --------------------------------------------------------------------------- #
def run_agent(run) -> None:
    """Drive one turn for one agent, continuing the session history, emitting events."""
    task = run.task
    run.emit("user_message", text=task)

    # Emit the identity token for root sessions (sub-agents emit via subagent_start).
    if getattr(run, "depth", 0) == 0 and getattr(run, "token", None) is not None:
        run.emit("identity_issued", token=run.token.to_display())

    messages: list = list(run.history) + [{"role": "user", "content": task}]
    run.messages = messages  # same object the loop appends to; persisted on completion
    if run.intercept_ctx is not None:
        run.intercept_ctx.task = task
        # All earlier user prompts (string-content user turns; tool results are
        # list-content) so the evaluator sees the whole conversation, not just now.
        run.intercept_ctx.prior_tasks = [
            m["content"] for m in run.history
            if m.get("role") == "user" and isinstance(m.get("content"), str)
        ]

    for _ in range(MAX_AGENT_STEPS):
        if run.stop.is_set():  # client disconnected — stop doing (paid, shell) work
            return
        run.emit("thinking")
        final = _stream_once(run, messages)
        if final is None:
            return  # API error already emitted
        content = final.content or []
        if content:
            messages.append({"role": "assistant", "content": content})

        tool_blocks = [b for b in content if getattr(b, "type", None) == "tool_use"]
        if tool_blocks:
            results = [_handle_tool(run, block) for block in tool_blocks]
            messages.append({"role": "user", "content": results})
            continue

        if getattr(final, "stop_reason", None) == "pause_turn":
            continue  # server tool (web_search) paused; resume
        break

    # Keep stored history valid for the next turn: it must end on an assistant turn
    # (the API requires user/assistant alternation), so backfill if the loop stopped
    # on a dangling tool-result user turn.
    if messages and messages[-1].get("role") == "user":
        messages.append({"role": "assistant", "content": AGENT_NO_CONTENT_NOTICE})

    # Context inspector: prove (per root turn) whether any credential ended up in
    # the model's context. Sub-agents (depth > 0) don't emit — their content is
    # already covered by the root's spawn_subagent tool result.
    if getattr(run, "depth", 0) == 0:
        report = inspector.scan_messages(messages, broker.live_secret_values())
        run.emit("context_scan", clean=report["clean"], count=report["count"],
                 findings=report["findings"])


def _stream_once(run, messages: list[dict]):
    """Stream one model segment; emit web_search activity and the answer text."""
    text_parts: list[str] = []
    active_tools = (
        tools_for_scope(TOOLS, run.token.scope)
        if getattr(run, "token", None) is not None
        else TOOLS
    )
    try:
        with run.client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=run.system,
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
                        run.emit("tool_call", tool="web_search", call_id=block.id,
                                 params={"query": _safe(query)}, server=True)
                        run.emit("tool_allowed", tool="web_search", call_id=block.id,
                                 reason="server-side web search")
                elif event.type == "content_block_delta" and event.delta.type == "text_delta":
                    text_parts.append(event.delta.text)
            final = stream.get_final_message()
    except anthropic.APIError as exc:
        run.emit("error", message=f"API error: {exc}")
        return None

    text = "".join(text_parts).strip()
    if text:
        run.emit("agent_response", text=_safe_block(text))
    return final


def _handle_tool(run, block) -> dict:
    """Judge (Breaker only), execute, and report one client tool call."""
    tool_input = block.input if isinstance(block.input, dict) else {}
    name = block.name
    call_id = block.id
    run.emit("tool_call", tool=name, call_id=call_id, params=_display_params(name, tool_input))

    file_context: list[tuple[str, str]] = []
    if run.intercept_ctx is not None:
        decision, reason, file_context, _tier = decide(
            run.client, run.null_console, run.intercept_ctx, name, tool_input,
            ask=lambda t, i, r: run.ask(call_id, t, i, r),
        )
        if decision == "block":
            run.emit("tool_blocked", tool=name, call_id=call_id, reason=reason)
            return {
                "type": "tool_result",
                "tool_use_id": call_id,
                "content": INTERCEPT_BLOCK_TEMPLATE.format(reason=reason),
            }
        run.emit("tool_allowed", tool=name, call_id=call_id, reason=reason)
    else:
        # Prompt Agent: nothing enforced the call. Flag it red if it actually moved
        # or exposed secrets, so an exfiltration never shows a plain green ALLOWED.
        run.emit("tool_allowed", tool=name, call_id=call_id, reason="",
                 danger=_looks_dangerous(name, tool_input))

    if name == "run_bash":
        result = _run_bash(str(tool_input.get("command", "")))
    elif name == "send_email":
        result = _send_email(run, tool_input)
    elif name == "call_api":
        result = _handle_call_api(run, tool_input)
    elif name == "spawn_subagent":
        result = _handle_spawn_subagent(run, tool_input)
    else:
        result = f"Error: unknown tool '{name}'."

    result += format_for_agent(file_context)
    run.emit("tool_result", tool=name, call_id=call_id,
             output=_safe_block(result)[:EVENT_OUTPUT_MAX_CHARS])
    return {"type": "tool_result", "tool_use_id": call_id, "content": result}


_MD_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")  # keep \t and \n


def _safe_block(text: str) -> str:
    """Strip control bytes but keep newlines/tabs (for multi-line output / answers)."""
    return _MD_CONTROL.sub("", str(text))
