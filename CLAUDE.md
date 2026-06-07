# CLAUDE.md

Project guidance for Claude Code. These rules **override** default behavior.

---

## Rule 1 — Always end with a recap

Every response that does work must end with a recap block:

```
### Recap
**Initial prompt:** <verbatim or close paraphrase of what I asked>
**Changes made this time:** <bulleted list of concrete edits/files touched>
```

- Restate the **initial prompt** so the intent is never lost across iterations.
- List the **changes made this time** — actual files/functions touched, not vague summaries.
- If nothing changed (pure question/answer), say so explicitly instead of inventing changes.

## Rule 2 — Commit means commit *and* push

If I say "commit this", "push this", "save this", or anything similar — **do both**: stage, commit, then push. Never stop at a local commit.

- **Always split into multiple logical commits when it makes sense.** One giant commit is confusing. Group by intent (e.g. "refactor X", "fix bug Y", "add tests for Z") so history is readable and reviewable.
- Write clear, conventional commit messages (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`).
- If on the default branch, branch first when appropriate, otherwise push to the current branch.
- Confirm the push succeeded; report the branch and commit count.

## Rule 3 — Security audit after every prompt

At the end of each of my prompts that touches code, **run the `code-auditor` agent** to check for major security problems before considering the task done. Surface anything it finds; fix critical issues immediately.

---

## High-level recommendations

- **Understand before editing.** Read the surrounding code and match its style, naming, and idioms rather than imposing new patterns.
- **Smallest change that works.** Prefer minimal, targeted edits over broad rewrites unless a refactor is explicitly requested.
- **Verify, don't assume.** When a fix is claimed "done", it should be tested or at least reasoned through — never report success on untested code.
- **Surface trade-offs.** If there are meaningful design choices, mention them briefly rather than silently picking one.
- **Keep secrets out of the repo.** Never commit API keys, tokens, `.env` files, or credentials. Use `.env.example` for shared config shape.
- **Flag risky/irreversible actions** (deletes, force-pushes, schema migrations, outward-facing calls) before doing them.

## Low-level defaults (apply automatically)

- Keep imports sorted and remove unused ones.
- Strip trailing whitespace; end files with a single newline.
- Match existing indentation (tabs vs spaces) and line-length conventions of the file.
- Prefer descriptive variable names over abbreviations.
- Add type hints / types where the surrounding code already uses them.
- Don't leave commented-out dead code or stray `print`/`console.log` debugging statements.
- Update or add docstrings/comments only where they add value — match existing comment density.
