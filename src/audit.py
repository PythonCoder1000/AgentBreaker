"""Structured, tamper-evident per-session audit log.

Every tool call evaluated by the interceptor — and every brokered credential
access — is appended here as a JSONL record capturing who called what, which
policy tier decided, and the verdict. This makes the authorization story
reproducible after the fact: you can answer "who authorized this?" and "what did
this agent do?" from the log alone.

The records are hash-linked into a keyed chain: each record carries its sequence
number (`seq`), the hash of the record before it (`prev_hash`), and its own
content hash (`hash`) — an HMAC under a per-process key. Because the chain is
keyed, an attacker with write access to the log file cannot edit, forge, insert,
reorder, or delete an interior record without the per-process secret and have the
chain still verify. verify_chain() proves all of that, turning "here is a log"
into a verifiable accountability receipt.

Two honest caveats: the key is process-local (like the capability-token signing
key in identity.py), so a restarted process issues a fresh key and cannot verify a
previous process's log; and truncation of the most recent record(s) leaves a
shorter-but-internally-valid prefix, which can only be caught against an external
anchor (e.g. a remembered record count) — verify_chain returns the verified
length so a caller can compare it to what it expected.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from pathlib import Path

from inspector import redact
from settings import AUDIT_LOG_DIR

# Repo root is two levels above src/
_LOG_ROOT = Path(__file__).resolve().parent.parent / AUDIT_LOG_DIR

_lock = threading.Lock()

# Per-process key for the audit chain HMAC. A record's hash cannot be recomputed
# (so the chain cannot be forged or silently edited) without this secret, which
# never leaves the process — the same model used for token signing in identity.py.
_AUDIT_KEY: bytes = os.urandom(32)

# The hash a session's first record links back to (no predecessor).
GENESIS_HASH = "0" * 64


def _log_path(session_id: str) -> Path:
    return _LOG_ROOT / f"session_{session_id}.jsonl"


def _compute_hash(record_without_hash: dict) -> str:
    """Keyed HMAC-SHA-256 over the canonical JSON of a record (its `hash` excluded)."""
    payload = json.dumps(record_without_hash, sort_keys=True, ensure_ascii=False)
    return hmac.new(_AUDIT_KEY, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _tail(log_path: Path) -> tuple[str, int]:
    """The (`hash`, count) of the existing log — the predecessor hash and the next
    sequence number — or (GENESIS, 0) if the log is missing/empty/unreadable."""
    last = GENESIS_HASH
    count = 0
    try:
        with log_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    last = json.loads(line).get("hash", last)
                    count += 1
                except json.JSONDecodeError:
                    continue
    except OSError:
        return GENESIS_HASH, 0
    return last, count


def log_event(
    session_id: str,
    token_id: str | None,
    agent_name: str,
    tool_name: str,
    input_summary: str,
    tier: str,
    decision: str,
    reason: str,
    task: str,
    files_read: list[str],
) -> None:
    """Append one decision to the session's hash-linked audit log (non-fatal)."""
    log_path = _log_path(session_id)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            # Read the prior record's hash + the record count, and append under the
            # same lock so the chain stays consistent even with concurrent
            # agents/sub-agents.
            prev_hash, seq = _tail(log_path)
            record = {
                "ts": time.time(),
                "seq": seq,
                "session_id": session_id,
                "token_id": token_id,
                "agent_name": agent_name,
                "tool": tool_name,
                # Redact any secret material before it reaches disk — the AI
                # evaluator's reason can quote a file's contents, and a command /
                # email body can carry a key.
                "input_summary": redact(input_summary)[:300],
                "tier": tier,
                "decision": decision,
                "reason": redact(reason),
                "task": redact(task)[:300],
                "files_read": files_read,
                "prev_hash": prev_hash,
            }
            record["hash"] = _compute_hash(record)
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
    except OSError:
        pass  # audit failure must never interrupt the agent loop


def read_log(session_id: str) -> list[dict]:
    """Return all audit records for a session, oldest first."""
    log_path = _log_path(session_id)
    records: list[dict] = []
    try:
        with log_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except OSError:
        pass
    return records


def verify_chain(session_id: str) -> dict:
    """Recompute the keyed chain and report whether the log is intact.

    Returns {"ok", "length", "broken_at", "reason"}. ok is False — with the index
    of the first bad record in broken_at — if any record's keyed hash does not
    match, its prev_hash does not link to the record before it, or its seq is not
    contiguous. Because the hash is keyed (HMAC under a per-process secret), an
    edit, a forgery, an insertion, a reorder, or an interior deletion is detected.

    The one thing a backward-linked chain cannot catch from the file alone is
    truncation of the most recent record(s): the surviving prefix is still valid.
    `length` is returned precisely so a caller holding an expected count can detect
    that out of band.
    """
    records = read_log(session_id)
    prev = GENESIS_HASH
    for index, record in enumerate(records):
        body = {key: value for key, value in record.items() if key != "hash"}
        if record.get("hash") != _compute_hash(body):
            return {"ok": False, "length": len(records), "broken_at": index,
                    "reason": "record content hash mismatch"}
        if record.get("prev_hash") != prev:
            return {"ok": False, "length": len(records), "broken_at": index,
                    "reason": "chain link mismatch"}
        if record.get("seq") != index:
            return {"ok": False, "length": len(records), "broken_at": index,
                    "reason": "sequence number mismatch"}
        prev = record["hash"]
    return {"ok": True, "length": len(records), "broken_at": None, "reason": "intact"}
