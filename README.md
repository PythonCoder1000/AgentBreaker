# AgentBreaker

A workspace for probing, stress-testing, and red-teaming AI agents.

> ⚠️ This README is an initial scaffold — update the sections below as the project takes shape.

## Overview

AgentBreaker is intended for **authorized** testing of AI agent behavior: finding
failure modes, robustness gaps, and unsafe responses so they can be fixed. Use it
only against systems you own or have explicit permission to test.

## Getting started

```bash
# clone
git clone https://github.com/PythonCoder1000/AgentBreaker.git
cd AgentBreaker

# (set up your environment here — e.g. a virtualenv)
# python -m venv .venv && source .venv/bin/activate
# pip install -r requirements.txt
```

## Project structure

```
AgentBreaker/
├── CLAUDE.md     # Working rules for Claude Code in this repo
├── README.md     # You are here
└── .gitignore
```

## Conventions

- Keep secrets out of the repo — use a local `.env` (already gitignored) and
  commit a `.env.example` describing the expected variables.
- Commits are split into small, logical units; commit messages follow the
  conventional style (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`).

## License

TBD.
