# AgentBreaker Web Demo — Vault Boundary

A single-page interactive demo built to the **Vault Boundary** design: a dark
landing page that makes the argument ("agents shouldn't hold the keys") and then
*proves* it with a live, side-by-side demo. The **Prompt Agent** (prompt-rule
guardrails) and the **Breaker Agent** (enforced policy interceptor) run the same
injected task *simultaneously*, and you watch their tool calls — and their two
context inspectors — diverge in real time.

```
              Agents shouldn't hold the keys.   (hero + flow diagram)
              ─────────────  the live demo  ─────────────
┌─────────────────────────┐   ┌─────────────────────────┐
│      Prompt Agent       │   │      Breaker Agent      │
│  the old way · rules    │   │  the new way · access   │
│       in the prompt     │   │       at runtime        │
│  context inspector ▸    │   │  context inspector ▸    │
│   …event feed streams…  │   │  capability token panel │
│                         │   │   …event feed streams…  │
└─────────────────────────┘   └─────────────────────────┘
       Proof & how it works · Why it's different · Run it yourself
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
4. Open <http://127.0.0.1:8010> (set the port in `backend/config.py`).

On startup the backend reseeds `testing_env/` (synthetic Helios-Chat product with
decoy secrets) so every replay starts from the same state.

## Using it

- **Attack chips** — pick Attack 1–4 (defaults to Attack 4, the decisive case).
- **The task textarea** — pre-filled with the selected attack; type to override it
  with a custom task sent to both agents.
- **▶ Run both** — fires both agents at once on the same task; each column streams
  its own feed and flips its context inspector (clean/green vs exposed/red).
- **Capability token panel** (Breaker column) — shows the real scoped token;
  **Revoke token** hits `/api/revoke`, **Verify audit chain** hits
  `/api/audit/{session}/verify` and reports the verified length.
- **Approve / Block** buttons appear on the Breaker side when a call escalates.
- **Sandbox files** — expand to browse the real `testing_env/` tree; click a file
  to read it.

## Event types → design boxes (per column)

Colour encodes **good vs. bad outcome**, not the literal allow/block action: a
blocked attack is a *win* and reads **green** with a shield 🛡️.

| event            | rendered as                                            |
|------------------|--------------------------------------------------------|
| `user_message`   | violet TASK box (typewriter)                           |
| `thinking`       | spinner (while the model works)                        |
| `tool_call`      | neutral tool box · `call_api` is a blue **broker** box with a "🔑 key never in context" badge |
| `tool_allowed`   | green ALLOWED — or a red **leak** box when an unenforced Prompt-Agent call moved/exposed secrets (`danger`) |
| `tool_blocked`   | green **block** box + shield 🛡️ + reason               |
| `tool_escalated` | amber box + Approve / Block buttons                    |
| `tool_result`    | collapsible output (bash stdout / reply)               |
| `agent_response` | neutral answer card (typewriter)                       |
| `context_scan`   | flips the column's context-inspector strip             |
| `identity_*`     | updates / revokes the capability-token panel           |

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
  frontend/             # React via esm.sh + htm, no build step
    index.html          # loads the app + Google Fonts
    app.js              # shell: demo state + SSE wiring; composes the page
    sections.js         # static marketing sections (nav, hero, beats, proof, …)
    demo.js             # the live-demo section (columns, inspector, token panel)
    feed.js             # maps backend SSE events -> design "box" styles
    explorer.js         # the Sandbox files tree (real /api/files) + viewer
    markdown.js         # tiny markdown -> React + typewriter
    ui.js               # shared React/htm/hooks setup
    styles.css          # Vault Boundary design system (dark theme)
```

The frontend is plain React loaded from a CDN (no `npm`/build step), so the whole
demo runs with just the Python backend. It's a single scrolling page built to the
**Vault Boundary** design handoff; the live-demo section streams the real backend.
