"""FastAPI backend for the AgentBreaker web demo.

Serves the React frontend and exposes three endpoints:
  GET  /api/scenarios            -> the attack list
  GET  /api/stream?...           -> a live SSE feed of one agent running one scenario
  POST /api/decision             -> approve/deny a Breaker Agent escalation

A "RUN" in the UI opens two SSE streams (prompt + breaker) under one session id.
Each stream spawns a worker thread that drives the agent loop (engine.run_agent)
and pushes events onto an asyncio.Queue via the event loop. Escalations block the
worker thread until /api/decision resolves them (or a timeout denies them).
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import re as _re
import secrets
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response

# Session IDs flow into file paths (audit log) and dict keys — restrict to
# safe characters so a crafted value can't cause path traversal.
_SESSION_RE = _re.compile(r'^[A-Za-z0-9_-]{1,64}$')


def _validate_session(session: str) -> None:
    if not _SESSION_RE.match(session):
        raise HTTPException(status_code=400, detail="invalid session id")


from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel
from rich.console import Console
from sse_starlette.sse import EventSourceResponse

# Local backend modules + the harness in src/ (engine sets the src path too).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (  # noqa: E402
    BASIC_AUTH_REALM,
    ESCALATION_TIMEOUT_SECONDS,
    FILE_VIEW_MAX_BYTES,
    FRONTEND_DIR,
    HEALTH_PATH,
    HOST,
    PORT,
    REPO_ROOT,
    STATIC_CACHE_CONTROL,
)
import engine  # noqa: E402
import scenarios  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import audit as audit_module  # noqa: E402
import reset_env  # noqa: E402
from identity import CapabilityToken, Scope, generate_token, revoke_token  # noqa: E402
from intercepter import InterceptContext  # noqa: E402
from settings import (  # noqa: E402
    AGENT_NAME,
    DEFAULT_SCOPE_TOOLS,
    IDENTITY_TOKEN_TTL_SECONDS,
    PRINCIPAL_NAME,
    SUBAGENT_MAX_DEPTH,
    TESTING_ENV_DIRNAME,
)

load_dotenv()

# The one directory the file explorer is allowed to show.
TESTING_ENV = (REPO_ROOT / TESTING_ENV_DIRNAME).resolve()

# Active runs, keyed by (session_id, agent). Decisions look a run up here.
RUNS: dict[tuple[str, str], "Run"] = {}
# Persisted conversation history per (session_id, agent), so a session is a real
# multi-turn chat: each new message continues from the stored messages.
HISTORY: dict[tuple[str, str], list] = {}
# Sessions where the operator has revoked the capability token. Checked before
# each new Run so revocation survives turn boundaries (new turns would otherwise
# re-issue a fresh root token that is not in _REVOKED).
REVOKED_SESSIONS: set[tuple[str, str]] = set()
# Latest capability token issued per (session_id, agent). Kept so the operator can
# revoke a session even after its run finished: RUNS only holds a run while it is
# actively streaming (the SSE generator pops it on completion), so revoke must not
# depend on a live run being present.
SESSION_TOKENS: dict[tuple[str, str], CapabilityToken] = {}


class Run:
    """One agent turn: a worker thread feeding an asyncio.Queue, continuing the
    session's stored history with `task` (the new user message)."""

    def __init__(self, session: str, agent: str, task: str, history: list,
                 email_replies: list, loop: asyncio.AbstractEventLoop, api_key: str) -> None:
        self.session = session
        self.agent = agent
        self.task = task
        self.history = history
        self.messages: list = []  # built by the engine; persisted on clean completion
        self.loop = loop
        self.queue: asyncio.Queue = asyncio.Queue()
        self.client = engine.anthropic.Anthropic(api_key=api_key)
        self.system = engine.build_system_prompt(include_rules=(agent == "prompt"))
        # Issue a capability token for the Breaker Agent and wire it into the
        # intercept context so tier 0 validates identity before any policy runs.
        if agent == "breaker":
            self.token = generate_token(
                agent_name=AGENT_NAME,
                principal=PRINCIPAL_NAME,
                scope=Scope(
                    tools=DEFAULT_SCOPE_TOOLS,
                    bash_allowed=True,
                    max_depth=SUBAGENT_MAX_DEPTH,
                ),
                ttl_seconds=IDENTITY_TOKEN_TTL_SECONDS,
            )
            self.intercept_ctx = InterceptContext(token=self.token, session_id=session)
        else:
            self.token = None
            self.intercept_ctx = None
        # A throwaway console so the policy core's spinner/Live has somewhere to go.
        self.null_console = Console(file=io.StringIO(), force_terminal=False)
        self._replies = list(email_replies)
        self._pending: dict[str, threading.Event] = {}
        self._decisions: dict[str, bool] = {}
        # Set when the SSE client goes away: wakes any parked escalation and stops
        # the agent loop between steps so a disconnected run can't keep working.
        self.stop = threading.Event()

    # -- event emission (worker thread -> event loop) --
    def emit(self, event_type: str, **fields) -> None:
        self._put({"type": event_type, "agent": self.agent, **fields})

    def _put(self, item) -> None:
        self.loop.call_soon_threadsafe(self.queue.put_nowait, item)

    # -- scripted email replies --
    def next_reply(self) -> str:
        if self._replies:
            return self._replies.pop(0)
        from config import DEFAULT_EMAIL_REPLY
        return DEFAULT_EMAIL_REPLY

    # -- escalation: block the worker until the browser answers --
    def ask(self, call_id: str, tool: str, tool_input: dict, reason: str) -> bool:
        gate = threading.Event()
        self._pending[call_id] = gate           # register BEFORE emitting (close the race)
        self.emit("tool_escalated", tool=tool, call_id=call_id, reason=reason,
                  params=engine._display_params(tool, tool_input))
        gate.wait(timeout=ESCALATION_TIMEOUT_SECONDS)
        self._pending.pop(call_id, None)
        # Honor a decision however we woke (set, early, or timeout); no decision
        # (timeout or client gone) is a deny — fail closed.
        return bool(self._decisions.pop(call_id, False))

    def resolve(self, call_id: str, approve: bool) -> None:
        self._decisions[call_id] = bool(approve)
        gate = self._pending.get(call_id)
        if gate is not None:
            gate.set()

    def cancel(self) -> None:
        """Client left: stop the loop and wake any parked escalation (→ deny)."""
        self.stop.set()
        for gate in list(self._pending.values()):
            gate.set()

    # -- worker thread entrypoint --
    def start(self) -> None:
        threading.Thread(target=self._execute, daemon=True).start()

    def _execute(self) -> None:
        try:
            engine.run_agent(self)
            if not self.stop.is_set() and self.messages:  # commit the turn to history
                HISTORY[(self.session, self.agent)] = list(self.messages)
        except Exception as exc:  # never let a worker die silently
            self.emit("error", message=f"engine error: {exc!r}")
        finally:
            self.emit("done")
            self._put(None)  # sentinel: closes the SSE generator


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Seed a clean, deterministic testing_env so scenarios replay identically.
    try:
        reset_env.reset()
    except Exception as exc:  # pragma: no cover — surfaced at startup, non-fatal
        print(f"[warning] could not reset testing_env: {exc}")
    yield


app = FastAPI(title="AgentBreaker Demo", lifespan=lifespan)

# Optional HTTP Basic Auth gate. Active only when both env vars are set, so local
# dev stays open but a public deployment can require a password — important here
# because the Prompt Agent has real shell/file tools and no enforced policy, and
# every run spends Anthropic credits.
_AUTH_USER = os.getenv("BASIC_AUTH_USER")
_AUTH_PASS = os.getenv("BASIC_AUTH_PASS")
_AUTH_ENABLED = bool(_AUTH_USER and _AUTH_PASS)

# Make the security posture impossible to miss in the logs: a public deploy that
# forgets the creds (or sets only one) would otherwise run silently wide open.
if _AUTH_ENABLED:
    print("[auth] HTTP Basic Auth ENABLED — requests require a username + password.", flush=True)
elif _AUTH_USER or _AUTH_PASS:
    print("[auth] WARNING: only one of BASIC_AUTH_USER / BASIC_AUTH_PASS is set — "
          "auth is DISABLED. Set BOTH to require a login.", flush=True)
else:
    print("[auth] WARNING: no BASIC_AUTH_USER / BASIC_AUTH_PASS set — running OPEN "
          "(no password). Fine for local dev; set both before any public deploy.", flush=True)


def _auth_ok(header: str) -> bool:
    """Constant-time check of an `Authorization: Basic …` header against the env creds."""
    # Scheme token is case-insensitive per RFC 7617.
    if header[:6].lower() != "basic ":
        return False
    try:
        user, _, pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")
    except (ValueError, UnicodeDecodeError):
        return False
    # Compare on bytes (compare_digest rejects non-ASCII str); evaluate both fields
    # so timing doesn't leak which one failed.
    user_ok = secrets.compare_digest(user.encode("utf-8"), (_AUTH_USER or "").encode("utf-8"))
    pass_ok = secrets.compare_digest(pw.encode("utf-8"), (_AUTH_PASS or "").encode("utf-8"))
    return user_ok and pass_ok


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    # No creds configured → open (local dev). Health check is always exempt so the
    # platform can probe liveness without a password.
    if _AUTH_ENABLED and request.url.path != HEALTH_PATH:
        if not _auth_ok(request.headers.get("authorization", "")):
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": f'Basic realm="{BASIC_AUTH_REALM}"'},
            )
    return await call_next(request)


@app.get(HEALTH_PATH)
async def health() -> dict:
    return {"ok": True}


@app.get("/api/scenarios")
async def list_scenarios() -> list[dict]:
    return scenarios.list_public()


def _build_tree(path: Path, depth: int = 0) -> list[dict]:
    """Directory listing of `path` (dirs first, then files), confined to testing_env."""
    if depth > 12:  # runaway guard
        return []
    nodes: list[dict] = []
    try:
        children = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except OSError:
        return nodes
    for child in children:
        # Hide the workspace's own infra .gitignore so the product structure is clean.
        if depth == 0 and child.name == ".gitignore":
            continue
        if child.is_dir() and not child.is_symlink():
            nodes.append({"name": child.name, "type": "dir", "children": _build_tree(child, depth + 1)})
        else:
            try:
                size = child.stat().st_size
            except OSError:
                size = 0
            nodes.append({"name": child.name, "type": "file", "size": size})
    return nodes


@app.get("/api/files")
async def list_files() -> dict:
    """The testing_env file tree (read-only) for the frontend file explorer."""
    if not TESTING_ENV.is_dir():
        return {"name": TESTING_ENV_DIRNAME, "type": "dir", "children": []}
    return {"name": TESTING_ENV_DIRNAME, "type": "dir", "children": _build_tree(TESTING_ENV)}


@app.get("/api/file")
async def read_file(path: str) -> dict:
    """Return the text contents of a single file inside testing_env (read-only).

    `path` is relative to testing_env. It is resolved and confined to that
    directory (symlinks included, since we compare the resolved real path) so the
    explorer can never be used to read arbitrary files on the host.
    """
    try:
        target = (TESTING_ENV / path).resolve()
    except OSError:
        raise HTTPException(status_code=400, detail="bad path")
    if target != TESTING_ENV and not target.is_relative_to(TESTING_ENV):
        raise HTTPException(status_code=403, detail="outside testing_env")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="not a file")
    size = target.stat().st_size
    with target.open("rb") as fh:          # read at most the cap, never the whole file
        raw = fh.read(FILE_VIEW_MAX_BYTES)
    try:
        content, binary = raw.decode("utf-8"), False
    except UnicodeDecodeError:
        content, binary = "", True
    return {
        "path": path,
        "size": size,
        "truncated": size > FILE_VIEW_MAX_BYTES,
        "binary": binary,
        "content": content,
    }


@app.get("/api/stream")
async def stream(agent: str, session: str, scenario: str | None = None, message: str | None = None):
    _validate_session(session)
    if agent not in ("prompt", "breaker"):
        raise HTTPException(status_code=400, detail="agent must be 'prompt' or 'breaker'")
    if (session, agent) in REVOKED_SESSIONS:
        raise HTTPException(status_code=403, detail="session token has been revoked")
    if message and message.strip():     # a free-text chat message wins over a preset
        task, replies = message, []
    elif scenario:
        sc = scenarios.get_scenario(scenario)
        if sc is None:
            raise HTTPException(status_code=404, detail=f"unknown scenario {scenario!r}")
        task, replies = sc.task, sc.email_replies
    else:
        raise HTTPException(status_code=400, detail="provide a message or a scenario")
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY is not set")

    key = (session, agent)
    previous = RUNS.get(key)
    if previous is not None:            # same session+agent reconnecting: stop the old one
        previous.cancel()
    history = HISTORY.get(key, [])      # continue the session's conversation
    run = Run(session, agent, task, history, replies, asyncio.get_running_loop(), api_key)
    RUNS[key] = run
    if getattr(run, "token", None) is not None:
        SESSION_TOKENS[key] = run.token  # so the token stays revocable after the run ends
    run.start()

    async def generator():
        try:
            while True:
                event = await run.queue.get()
                if event is None:
                    break
                yield {"data": json.dumps(event)}
        finally:
            run.cancel()                # signal the worker if the client left mid-run
            if RUNS.get(key) is run:    # don't clobber a newer run under the same key
                RUNS.pop(key, None)

    return EventSourceResponse(generator())


class Decision(BaseModel):
    session: str
    agent: str
    call_id: str
    approve: bool


class ResetRequest(BaseModel):
    session: str


@app.post("/api/reset")
async def reset_session(req: ResetRequest) -> dict:
    """Start a new conversation: stop any live runs and drop the stored history."""
    for agent in ("prompt", "breaker"):
        key = (req.session, agent)
        run = RUNS.get(key)
        if run is not None:
            run.cancel()
        HISTORY.pop(key, None)
        REVOKED_SESSIONS.discard(key)
        SESSION_TOKENS.pop(key, None)
    return {"ok": True}


@app.post("/api/decision")
async def decision(payload: Decision) -> dict:
    run = RUNS.get((payload.session, payload.agent))
    if run is None:
        raise HTTPException(status_code=404, detail="no active run for that session/agent")
    run.resolve(payload.call_id, payload.approve)
    return {"ok": True}


class RevokeRequest(BaseModel):
    session: str
    agent: str


@app.post("/api/revoke")
async def revoke_agent(req: RevokeRequest) -> dict:
    """Revoke the capability token for an agent session.

    Works whether or not a run is currently streaming: RUNS only holds a run while
    it is live, so the token is also looked up from SESSION_TOKENS once the run has
    ended. All subsequent tool calls from this session (and any sub-agents it has
    spawned) are blocked at tier 0, and — because each new turn issues a fresh root
    token — the session is marked revoked so future turns are blocked too. If the
    run is still live, its SSE stream receives an identity_revoked event.
    """
    _validate_session(req.session)
    key = (req.session, req.agent)
    run = RUNS.get(key)
    token = getattr(run, "token", None) if run is not None else None
    if token is None:
        token = SESSION_TOKENS.get(key)  # the run has ended; use the last-issued token
    if token is None:
        raise HTTPException(status_code=404, detail="no token has been issued for that session/agent")
    revoke_token(token.token_id)
    REVOKED_SESSIONS.add(key)
    if run is not None:
        run.emit("identity_revoked", token_id=token.token_id[:8])
    return {"ok": True, "revoked_token": token.token_id[:8]}


@app.get("/api/audit/{session}")
async def get_audit_log(session: str) -> list[dict]:
    """Return the structured audit log for a session (all tool call decisions)."""
    _validate_session(session)
    return audit_module.read_log(session)


@app.get("/api/audit/{session}/verify")
async def verify_audit_log(session: str) -> dict:
    """Verify the session's hash-linked audit chain (tamper-evidence check).

    Returns {"ok", "length", "broken_at", "reason"}: ok is True only if every
    record's content hash matches and links to its predecessor — so a tampered,
    reordered, or dropped record is provably detected.
    """
    _validate_session(session)
    return audit_module.verify_chain(session)


class SPAStaticFiles(StaticFiles):
    """Serve the frontend assets with revalidation forced, and fall back to
    index.html for unknown client-side routes.

    Two behaviours layered on Starlette's StaticFiles:
      - Cache-Control: StaticFiles sends none, so browsers fall back to heuristic
        caching and may keep running a stale ES module after an edit. We set
        "no-cache" (revalidate, not "don't store") so the ETag still yields cheap
        304s but a changed file is always picked up on reload.
      - SPA fallback: StaticFiles 404s any path without a matching file, so a
        direct load or refresh of a client route (e.g. /chat) would break. We
        serve index.html for a 404 on a route-shaped path so the single-page app
        boots and renders it. We do NOT mask a missing /api call (those are real
        routes handled before this mount) or a missing static asset (a path whose
        last segment has a file extension) — those keep their honest 404.
    """

    async def get_response(self, path: str, scope) -> Response:
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            last_segment = path.rsplit("/", 1)[-1]
            is_route = not path.startswith("api/") and "." not in last_segment
            if exc.status_code == 404 and is_route:
                response = await super().get_response("index.html", scope)
            else:
                raise
        response.headers["Cache-Control"] = STATIC_CACHE_CONTROL
        return response


# Serve the frontend. Mounted last so the /api routes above take precedence.
app.mount("/", SPAStaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
