# AgentBreaker

A workspace for probing, stress-testing, and red-teaming AI agents.

## Overview

AgentBreaker is intended for **authorized** testing of AI agent behavior: finding
failure modes, robustness gaps, and unsafe responses so they can be fixed. Use it
only against systems you own or have explicit permission to test.

## Getting started

```bash
# clone
git clone https://github.com/PythonCoder1000/AgentBreaker.git
cd AgentBreaker

# install deps (uses uv + the committed uv.lock)
uv sync --extra web        # or: pip install -e ".[web]"

# add your key
cp .env.template .env       # then edit .env and paste your ANTHROPIC_API_KEY
```

## Running the web demo

```bash
uv run uvicorn app:app --app-dir web/backend --reload
# open http://127.0.0.1:8010
```

The demo runs both agents side by side on the same task: the **Prompt Agent**
(guardrails written into its system prompt) vs. the **Breaker Agent** (no rules in
its prompt — every tool call is routed through an enforced policy layer).

## Deploying

The web demo is a single long-lived process (in-memory sessions + SSE streams),
so it needs a real web service, **not** a static host or serverless function.
A [Render](https://render.com) blueprint is included (`render.yaml`).

> **⚠️ Before you make it public:** there is no auth by default, every run spends
> Anthropic credits, and the Prompt Agent has real shell/file tools with no
> enforced policy — so an open URL lets anyone burn your key or drive that agent's
> shell (including reading secrets out of the container's environment). Always set
> the Basic Auth vars below for a public deploy.

1. Push this repo to GitHub.
2. On Render, **New → Blueprint**, point it at the repo. Render reads `render.yaml`.
3. Set the env vars (they are **not** committed):
   - `ANTHROPIC_API_KEY` — your key.
   - `BASIC_AUTH_USER` and `BASIC_AUTH_PASS` — set **both** to require a login.
     Leave them unset only for a throwaway URL you take down immediately.
4. Deploy. Render builds with `uv sync --extra web` and serves with
   `uvicorn app:app --host 0.0.0.0 --port $PORT`. `/healthz` is the (unauthenticated)
   liveness probe.

Free-tier note: the instance spins down after ~15 min idle (slow first request)
and in-memory chat history resets on restart — fine for a demo.

## Project structure

```
AgentBreaker/
├── CLAUDE.md          # Working rules for Claude Code in this repo
├── README.md          # You are here
├── pyproject.toml     # Deps (core + the `web` extra) — installed via uv
├── render.yaml        # Render.com deploy blueprint
├── src/               # The red-team harness (agent loop + policy interceptor)
├── testing_env/       # Sandboxed workspace the agents act in (seeded at startup)
└── web/
    ├── backend/       # FastAPI app: SSE streaming, scenarios, escalation API
    └── frontend/      # No-build React (htm + ESM) UI served as static files
```

## Conventions

- Keep secrets out of the repo — `.env` is gitignored; `.env.template` is the
  committed, secret-free shape. Copy it to `.env` and fill in your
  `ANTHROPIC_API_KEY` (and the optional Basic Auth vars).
- Commits are split into small, logical units; commit messages follow the
  conventional style (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`).

## License

TBD.
