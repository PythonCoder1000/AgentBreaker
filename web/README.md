# AgentBreaker Web Demo

A side-by-side live demo of the harness: the **Prompt Agent** (prompt-rule
guardrails) and the **Breaker Agent** (enforced policy interceptor) run the same
injected task *simultaneously*, and you watch their tool calls stream in real time.

```
┌─────────────────────────┐   ┌─────────────────────────┐
│      Prompt Agent       │   │      Breaker Agent      │
│  (rules in the prompt)  │   │  (policy interceptor)   │
│   …event feed streams…  │   │   …event feed streams…  │
└─────────────────────────┘   └─────────────────────────┘
```

## Run it

1. Install the web extras (one time):
   ```bash
   uv sync --extra web        # or:  pip install -e ".[web]"
   ```
2. Make sure `ANTHROPIC_API_KEY` is set (same `.env` the CLI uses).
3. Launch:
   ```bash
   python web/serve.py
   ```
4. Open <http://127.0.0.1:8000>.

On startup the backend reseeds `testing_env/` (synthetic Helios-Chat product with
decoy secrets) so every replay starts from the same state.

## Using it

- **Scenario dropdown** — pick Attack 1–4.
- **▶ RUN BOTH** — fires both agents at once; each column streams its own feed.
- **Approve / Block** buttons appear on the Breaker side when a call escalates.
- Keyboard: **N** = next scenario (and run), **R** = replay the current one.

## Event types (per column)

| event            | rendered as                                  |
|------------------|----------------------------------------------|
| `user_message`   | injected-prompt bubble (typewriter)          |
| `thinking`       | spinner (while the model works)              |
| `tool_call`      | tool name + parameters                       |
| `tool_allowed`   | green ✓                                       |
| `tool_blocked`   | red 🚨 + reason                               |
| `tool_escalated` | yellow ⚠️ + Approve / Block buttons          |
| `tool_result`    | collapsible output (bash stdout / reply)     |
| `agent_response` | final agent text (typewriter)                |

## How it connects to the harness

`backend/engine.py` runs the **real** agent loop with the **real** tools and, for
the Breaker Agent, the **real** policy core (`intercepter.decide`). It emits
structured events instead of rendering to a terminal; `backend/app.py` relays them
to the browser over Server-Sent Events. Escalations block the worker thread until
you click Approve/Block (`POST /api/decision`), or a timeout denies them.

`send_email` is simulated (scripted client replies — the CLI never sent real mail
either); `run_bash` really executes, bounded by the harness's timeout/output caps.

## Architecture

```
web/
  serve.py              # launcher (python web/serve.py)
  backend/
    app.py              # FastAPI: static + SSE (/api/stream) + /api/decision
    engine.py           # headless agent loop -> events; reuses src/ harness
    scenarios.py        # Attack 1–4
    config.py           # ports, timeouts, caps (values only)
  frontend/
    index.html          # loads the app
    app.js              # React (via esm.sh + htm, no build step) — two columns
    styles.css          # dark theme
```

The frontend is plain React loaded from a CDN (no `npm`/build step), so the whole
demo runs with just the Python backend.
