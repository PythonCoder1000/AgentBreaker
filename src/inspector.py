"""Context inspector — proves whether a secret ever entered the model's context.

The core principle here is that a credential should never sit in the model's
context. This module makes that property *checkable*: given the full message list
sent to the model (every user/assistant turn and every tool result), it scans for
secret material — both the exact live values the broker holds and a set of
credential patterns — and reports whether the context is clean.

The Breaker Agent reaches external systems through the broker (call_api), so its
context stays clean. The Prompt Agent, with no enforced boundary, can read a
credential file into a tool result — and the inspector lights up. Matched values
are always masked in the report; the inspector never echoes a secret.
"""

from __future__ import annotations

import re

from settings import SECRET_SCAN_MAX_FINDINGS, SECRET_SCAN_PATTERNS

_SCAN_RE = [(re.compile(pattern), label) for pattern, label in SECRET_SCAN_PATTERNS]


def _collect_text(obj, out: list[str]) -> None:
    """Recursively gather every string leaf from a message/content structure.

    Handles plain strings, message dicts, tool-result dicts, nested lists, and the
    SDK's content-block objects (which expose text/content/input attributes).
    """
    if obj is None:
        return
    if isinstance(obj, str):
        out.append(obj)
        return
    if isinstance(obj, dict):
        for value in obj.values():
            _collect_text(value, out)
        return
    if isinstance(obj, (list, tuple)):
        for value in obj:
            _collect_text(value, out)
        return
    # SDK content-block objects (text / tool_use / tool_result blocks).
    for attr in ("text", "content", "input"):
        if hasattr(obj, attr):
            _collect_text(getattr(obj, attr), out)


def _mask(value: str) -> str:
    """A non-revealing preview of a matched secret: no characters of the value, just
    a fixed redaction and its length (the finding's `label` identifies the kind)."""
    return f"•••• ({len(str(value))} chars)"


def redact(text, extra_values=()) -> str:
    """Replace any secret material in `text` with a placeholder — for safe logging.

    Used before persisting free-text fields (e.g. the AI evaluator's reason, which
    can quote a file's contents) so a secret never reaches disk.
    """
    result = str(text)
    for value in extra_values:
        if value:
            result = result.replace(value, "[REDACTED]")
    for rx, _label in _SCAN_RE:
        result = rx.sub("[REDACTED]", result)
    return result


def scan_messages(messages, extra_values=()) -> dict:
    """Scan the full model context for secret material.

    `extra_values` are exact secret strings to look for (the live values the
    broker holds) so even an unrecognised-format secret is caught. Returns:
        {"clean": bool, "count": int, "findings": [{"label", "preview"}, ...]}
    where count is the number of DISTINCT secrets found (findings are capped).
    """
    parts: list[str] = []
    _collect_text(messages, parts)
    text = "\n".join(parts)

    findings: list[dict] = []
    seen: set[str] = set()

    def add(label: str, matched: str) -> None:
        key = matched
        if key in seen:
            return
        seen.add(key)
        findings.append({"label": label, "preview": _mask(matched)})

    # Exact live broker secret values — the credential that must never appear.
    for value in extra_values:
        if value and value in text:
            add("brokered credential", value)

    # Credential-shaped patterns (decoy files read into context, leaked keys, …).
    for rx, label in _SCAN_RE:
        for match in rx.finditer(text):
            add(label, match.group(0))

    return {
        "clean": len(findings) == 0,
        "count": len(findings),
        "findings": findings[:SECRET_SCAN_MAX_FINDINGS],
    }
