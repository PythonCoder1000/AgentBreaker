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
the repo root, bounded by the harness's timeout and output cap.
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

from config import EMAIL_BODY_PREVIEW_CHARS, EVENT_OUTPUT_MAX_CHARS  # noqa: E402
from context import format_for_agent  # noqa: E402
from intercepter import decide  # noqa: E402
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
    INTERCEPT_BLOCK_TEMPLATE,
    MAX_AGENT_STEPS,
    MAX_TOKENS,
    MODEL,
    SYSTEM_PROMPT,
    TOOLS,
)

# run_bash executes from the repo root, exactly like the CLI harness.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


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
    if include_rules:
        prompt += f"\n\n{AGENT_RULES}"
    return prompt


# --------------------------------------------------------------------------- #
# Tool execution
# --------------------------------------------------------------------------- #
def _run_bash(command: str) -> str:
    """Execute a shell command from the repo root; return the [SYSTEM]-framed result."""
    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            executable=BASH_EXECUTABLE,
            cwd=str(PROJECT_ROOT),
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
    return {key: _safe(value) for key, value in tool_input.items()}


# --------------------------------------------------------------------------- #
# Agent loop
# --------------------------------------------------------------------------- #
def run_agent(run) -> None:
    """Drive one turn for one agent, continuing the session history, emitting events."""
    task = run.task
    run.emit("user_message", text=task)
    messages: list = list(run.history) + [{"role": "user", "content": task}]
    run.messages = messages  # same object the loop appends to; persisted on completion
    if run.intercept_ctx is not None:
        run.intercept_ctx.task = task

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


def _stream_once(run, messages: list[dict]):
    """Stream one model segment; emit web_search activity and the answer text."""
    text_parts: list[str] = []
    try:
        with run.client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=run.system,
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
        run.emit("tool_allowed", tool=name, call_id=call_id, reason="")

    if name == "run_bash":
        result = _run_bash(str(tool_input.get("command", "")))
    elif name == "send_email":
        result = _send_email(run, tool_input)
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
