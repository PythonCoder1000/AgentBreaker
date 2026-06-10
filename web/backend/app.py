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
import io
import json
import os
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from rich.console import Console
from sse_starlette.sse import EventSourceResponse

# Local backend modules + the harness in src/ (engine sets the src path too).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import ESCALATION_TIMEOUT_SECONDS, FRONTEND_DIR, HOST, PORT  # noqa: E402
import engine  # noqa: E402
import scenarios  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import reset_env  # noqa: E402
from intercepter import InterceptContext  # noqa: E402

load_dotenv()

# Active runs, keyed by (session_id, agent). Decisions look a run up here.
RUNS: dict[tuple[str, str], "Run"] = {}


class Run:
    """One agent running one scenario: a worker thread feeding an asyncio.Queue."""

    def __init__(self, session: str, agent: str, scenario: scenarios.Scenario,
                 loop: asyncio.AbstractEventLoop, api_key: str) -> None:
        self.session = session
        self.agent = agent
        self.scenario = scenario
        self.loop = loop
        self.queue: asyncio.Queue = asyncio.Queue()
        self.client = engine.anthropic.Anthropic(api_key=api_key)
        self.system = engine.build_system_prompt(include_rules=(agent == "prompt"))
        self.intercept_ctx = InterceptContext() if agent == "breaker" else None
        # A throwaway console so the policy core's spinner/Live has somewhere to go.
        self.null_console = Console(file=io.StringIO(), force_terminal=False)
        self._replies = list(scenario.email_replies)
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


@app.get("/api/scenarios")
async def list_scenarios() -> list[dict]:
    return scenarios.list_public()


@app.get("/api/stream")
async def stream(scenario: str, agent: str, session: str):
    if agent not in ("prompt", "breaker"):
        raise HTTPException(status_code=400, detail="agent must be 'prompt' or 'breaker'")
    sc = scenarios.get_scenario(scenario)
    if sc is None:
        raise HTTPException(status_code=404, detail=f"unknown scenario {scenario!r}")
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY is not set")

    key = (session, agent)
    previous = RUNS.get(key)
    if previous is not None:            # same session+agent reconnecting: stop the old one
        previous.cancel()
    run = Run(session, agent, sc, asyncio.get_running_loop(), api_key)
    RUNS[key] = run
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


@app.post("/api/decision")
async def decision(payload: Decision) -> dict:
    run = RUNS.get((payload.session, payload.agent))
    if run is None:
        raise HTTPException(status_code=404, detail="no active run for that session/agent")
    run.resolve(payload.call_id, payload.approve)
    return {"ok": True}


# Serve the frontend. Mounted last so the /api routes above take precedence.
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
