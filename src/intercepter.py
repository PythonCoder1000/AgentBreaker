"""Policy evaluator for Breaker Agent tool calls.

Only the Breaker Agent is routed here (see main.stream_turn); the Prompt Agent
never passes through this module. evaluate() judges each tool call in priority
order:
  1. hard-logic blocks      — instant, deterministic
  2. hard-logic escalations — instant; ask the operator
  3. AI semantic blocks      — one Claude call covering all semantic rules
  4. AI escalations          — same call

It returns None to ALLOW the call (the real tool then runs) or a block-message
string (the tool result the agent sees) to DENY it.

(web_search is a server-side tool executed by Anthropic's infrastructure, so it
never reaches the client-side dispatch and is not evaluated here.)
"""

from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass, field

import anthropic
from rich.console import Console
from rich.text import Text

from settings import (
    ATTACHMENT_BLOCKED_BASENAMES,
    ATTACHMENT_BLOCKED_COMPONENTS,
    ATTACHMENT_BLOCKED_PREFIXES,
    ATTACHMENT_BLOCKED_SUFFIXES,
    ATTACHMENTS_ROOT,
    BASH_BLOCK_PATTERNS,
    BASH_MODIFY_PATTERNS,
    COMPANY_DOMAIN,
    EMAIL_ADDRESS_RE,
    EMAIL_BODY_BLOCK_PATTERNS,
    ESCALATE_MAX_TOOL_CALLS_PER_TURN,
    INTERCEPT_AI_MAX_TOKENS,
    INTERCEPT_AI_MODEL,
    INTERCEPT_AI_SYSTEM,
    INTERCEPT_BLOCK_TEMPLATE,
    INTERCEPT_DENIED_REASON,
    INTERCEPT_ESCALATE_PROMPT,
    INTERCEPT_ESCALATE_TEMPLATE,
    INTERCEPT_FAIL_OPEN,
    INTERCEPT_RECENT_CALLS_KEPT,
    MAX_ATTACHMENTS,
    MAX_EMAIL_RECIPIENTS,
    RESULT_PREFIX,
    TOOL_BULLET,
)

# Pre-compile the command-pattern lists once. Case-insensitive so a secret can't
# be reached by varying case (e.g. .ENV, Credentials.txt) on a case-insensitive FS.
_BASH_BLOCK = [(re.compile(p, re.IGNORECASE), reason) for p, reason in BASH_BLOCK_PATTERNS]
_BASH_MODIFY = [re.compile(p, re.IGNORECASE) for p in BASH_MODIFY_PATTERNS]
_EMAIL_BODY_BLOCK = [(re.compile(p, re.IGNORECASE), reason) for p, reason in EMAIL_BODY_BLOCK_PATTERNS]
_EMAIL_ADDRESS = re.compile(EMAIL_ADDRESS_RE)
_ALLOWED_DOMAIN = "@" + COMPANY_DOMAIN.lower()


@dataclass
class InterceptContext:
    """Session/turn state the evaluator reasons over for the stateful policies."""

    task: str = ""  # the current user task (set each turn by main)
    tool_calls_this_turn: int = 0
    bash_history: list[str] = field(default_factory=list)  # executed bash commands
    recent_calls: list[str] = field(default_factory=list)  # short summaries for the AI

    def record(self, tool_name: str, tool_input: dict, executed: bool) -> None:
        """Log a call: keep a short summary, and remember executed bash commands."""
        self.recent_calls.append(_summarize_call(tool_name, tool_input))
        del self.recent_calls[:-INTERCEPT_RECENT_CALLS_KEPT]
        if executed and tool_name == "run_bash":
            self.bash_history.append(str(tool_input.get("command", "")))


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def evaluate(
    client: anthropic.Anthropic,
    console: Console,
    context: InterceptContext,
    tool_name: str,
    tool_input: dict,
) -> str | None:
    """Judge one Breaker Agent tool call. Return None to allow, else a block msg."""
    # 1. Hard-logic blocks (instant).
    reason = _hard_block(tool_name, tool_input)
    if reason:
        return _block(console, tool_name, reason)

    # 2. Hard-logic escalations (instant; ask the operator).
    reason = _hard_escalation(tool_name, tool_input, context)
    if reason and not _ask_operator(console, reason):
        return _block(console, tool_name, f"{INTERCEPT_DENIED_REASON} ({reason})")

    # 3 + 4. AI semantic blocks and escalations (one Claude call).
    decision, ai_reason = _ai_evaluate(client, console, context, tool_name, tool_input)
    if decision == "block":
        return _block(console, tool_name, ai_reason or "failed AI policy review")
    if decision == "escalate" and not _ask_operator(console, ai_reason or "flagged by AI policy"):
        return _block(console, tool_name, f"{INTERCEPT_DENIED_REASON} ({ai_reason})")

    return None  # allowed


# --------------------------------------------------------------------------- #
# Operator-facing helpers
# --------------------------------------------------------------------------- #
def _block(console: Console, tool_name: str, reason: str) -> str:
    """Print a block line and return the message the agent sees as its result."""
    message = INTERCEPT_BLOCK_TEMPLATE.format(reason=reason)
    console.print()
    console.print(Text(f"{TOOL_BULLET} {tool_name} (blocked)", style="yellow"))
    console.print(Text(f"{RESULT_PREFIX}{message}", style="red"))
    return message


def _ask_operator(console: Console, reason: str) -> bool:
    """Pause and ask the operator to approve an escalated action (default: no)."""
    console.print()
    console.print(Text(INTERCEPT_ESCALATE_TEMPLATE.format(reason=reason), style="yellow"))
    try:
        answer = input(INTERCEPT_ESCALATE_PROMPT).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in {"y", "yes"}


# --------------------------------------------------------------------------- #
# Hard-logic blocks
# --------------------------------------------------------------------------- #
def _hard_block(tool_name: str, tool_input: dict) -> str | None:
    if tool_name == "send_email":
        return _email_hard_block(tool_input)
    if tool_name == "run_bash":
        return _bash_hard_block(tool_input)
    return None


def _email_hard_block(tool_input: dict) -> str | None:
    recipients = _recipients(tool_input.get("email", ""))
    if not recipients:
        return "no recipient specified"
    if len(recipients) > MAX_EMAIL_RECIPIENTS:
        return f"more than {MAX_EMAIL_RECIPIENTS} recipients"
    for addr in recipients:
        if not _EMAIL_ADDRESS.match(addr):
            return f"malformed recipient address: {addr}"
        if not addr.lower().endswith(_ALLOWED_DOMAIN):
            return f"recipient outside @{COMPANY_DOMAIN}: {addr}"

    attachments = _attachments(tool_input)
    if len(attachments) > MAX_ATTACHMENTS:
        return f"more than {MAX_ATTACHMENTS} attachments"
    for path in attachments:
        reason = _attachment_blocked(path)
        if reason:
            return reason

    body = str(tool_input.get("message", ""))
    for pattern, reason in _EMAIL_BODY_BLOCK:
        if pattern.search(body):
            return reason
    return None


def _attachment_blocked(path: str) -> str | None:
    norm = posixpath.normpath(str(path).replace("\\", "/"))
    parts = norm.split("/")
    base = parts[-1]
    base_l = base.lower()  # match case-insensitively (case-insensitive FS)
    if norm.startswith("/") or ".." in parts or parts[0].lower() != ATTACHMENTS_ROOT.lower():
        return f"attachment outside {ATTACHMENTS_ROOT}/: {path}"
    if any(part.lower() in ATTACHMENT_BLOCKED_COMPONENTS for part in parts):
        return f"attachment in a secrets path: {path}"
    if base_l in ATTACHMENT_BLOCKED_BASENAMES:
        return f"blocked attachment: {base}"
    if base_l.startswith(ATTACHMENT_BLOCKED_PREFIXES):
        return f"blocked credentials attachment: {base}"
    if base_l.endswith(ATTACHMENT_BLOCKED_SUFFIXES):
        return f"blocked key/cert attachment: {base}"
    return None


def _bash_hard_block(tool_input: dict) -> str | None:
    command = str(tool_input.get("command", ""))
    for pattern, reason in _BASH_BLOCK:
        if pattern.search(command):
            return reason
    return None


# --------------------------------------------------------------------------- #
# Hard-logic escalations
# --------------------------------------------------------------------------- #
def _hard_escalation(tool_name: str, tool_input: dict, context: InterceptContext) -> str | None:
    if context.tool_calls_this_turn > ESCALATE_MAX_TOOL_CALLS_PER_TURN:
        return f"more than {ESCALATE_MAX_TOOL_CALLS_PER_TURN} tool calls this turn"
    if tool_name == "send_email":
        if len(_recipients(tool_input.get("email", ""))) > 1:
            return "emailing more than one person at once"
        for path in _attachments(tool_input):
            if not _was_read(path, context):
                return f"attaching a file not read this session: {path}"
    if tool_name == "run_bash":
        if any(p.search(str(tool_input.get("command", ""))) for p in _BASH_MODIFY):
            return "bash command modifies files (not just reads)"
    return None


def _was_read(path: str, context: InterceptContext) -> bool:
    """Whether the attachment's path/basename appears in an executed bash command."""
    base = posixpath.basename(str(path))
    return any(path in cmd or (base and base in cmd) for cmd in context.bash_history)


# --------------------------------------------------------------------------- #
# AI semantic evaluation (one call covers all semantic blocks + escalations)
# --------------------------------------------------------------------------- #
def _ai_evaluate(
    client: anthropic.Anthropic,
    console: Console,
    context: InterceptContext,
    tool_name: str,
    tool_input: dict,
) -> tuple[str, str]:
    """Return (decision, reason) where decision is allow|block|escalate."""
    fallback = ("allow", "") if INTERCEPT_FAIL_OPEN else ("block", "policy evaluator unavailable")
    try:
        response = client.messages.create(
            model=INTERCEPT_AI_MODEL,
            max_tokens=INTERCEPT_AI_MAX_TOKENS,
            system=INTERCEPT_AI_SYSTEM,
            messages=[{"role": "user", "content": _ai_user_message(context, tool_name, tool_input)}],
        )
    except anthropic.APIError as exc:
        console.print(Text(f"{RESULT_PREFIX}[policy evaluator error: {exc}]", style="red"))
        return fallback

    text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
    parsed = _parse_verdict(text)
    return parsed if parsed is not None else fallback


def _ai_user_message(context: InterceptContext, tool_name: str, tool_input: dict) -> str:
    lines = [f"Original task: {context.task or '(unknown)'}"]
    if context.recent_calls:
        lines.append("Recent tool calls (oldest first):")
        lines += [f"  - {call}" for call in context.recent_calls]
    lines.append("New tool call to judge:")
    if tool_name == "send_email":
        lines.append("  tool: send_email")
        lines.append(f"  recipients: {tool_input.get('email', '')}")
        lines.append(f"  attachments: {_attachments(tool_input)}")
        lines.append("  body:")
        lines.append(_indent(str(tool_input.get("message", ""))))
    elif tool_name == "run_bash":
        lines.append("  tool: run_bash")
        lines.append(f"  command: {tool_input.get('command', '')}")
    else:
        lines.append(f"  tool: {tool_name}")
        lines.append(f"  input: {tool_input}")
    return "\n".join(lines)


def _parse_verdict(text: str) -> tuple[str, str] | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    decision = str(data.get("decision", "")).strip().lower()
    if decision not in {"allow", "block", "escalate"}:
        return None
    return decision, str(data.get("reason", "")).strip()


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _recipients(email_field: object) -> list[str]:
    """Split a recipient field into individual addresses (comma/semicolon-separated)."""
    return [addr.strip() for addr in re.split(r"[;,]", str(email_field)) if addr.strip()]


def _attachments(tool_input: dict) -> list[str]:
    raw = tool_input.get("attachments")
    return [str(item) for item in raw] if isinstance(raw, list) else []


def _summarize_call(tool_name: str, tool_input: dict) -> str:
    if tool_name == "send_email":
        return f"send_email to {tool_input.get('email', '')} ({len(_attachments(tool_input))} attachments)"
    if tool_name == "run_bash":
        return f"run_bash: {str(tool_input.get('command', ''))[:120]}"
    return tool_name


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line for line in text.splitlines()) or prefix
