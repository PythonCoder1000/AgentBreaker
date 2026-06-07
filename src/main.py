"""A minimal agent loop: the user asks a question, the agent answers.

Keeps a running conversation history so follow-up questions have context.
While the agent works, a "[Agent]: Thinking..." spinner runs and each tool the
agent uses is printed underneath it; the answer is rendered as live markdown as
it streams (Claude-CLI style), via `rich`.
Type 'exit' or 'quit' (or press Ctrl-C / Ctrl-D) to leave.
"""

import os
import re
import sys

import anthropic
from dotenv import load_dotenv
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.spinner import Spinner
from rich.text import Text

from settings import (
    AGENT_PREFIX,
    LIVE_REFRESH_PER_SECOND,
    MAX_TOKENS,
    MAX_TOOL_CONTINUATIONS,
    MODEL,
    RESULT_PREFIX,
    SEARCHING_LABEL,
    SPINNER_STYLE,
    SYSTEM_PROMPT,
    THINKING_LABEL,
    TOOL_BULLET,
    TOOLS,
    TRUNCATION_NOTICE,
    USER_PREFIX,
)

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")  # all C0 controls (incl. ESC) + DEL
_MD_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")  # same, but keeps \t and \n


def _sanitize(text: str) -> str:
    """Strip control bytes so untrusted text can't drive the terminal.

    Search queries are model-generated and can be steered by adversarial page
    content returned from an earlier search, so a query echoed to the TTY could
    otherwise smuggle ANSI/cursor escapes. Used for single-line labels — newline
    and tab are stripped too.
    """
    return _CONTROL_CHARS.sub("", text)


def _sanitize_markdown(text: str) -> str:
    """Strip control bytes from answer text before rendering it as markdown.

    `rich` only strips a handful of control codes (not ESC), so adversarial web
    content that steers the model into emitting a raw escape could otherwise
    drive the terminal. Newline and tab are preserved so markdown structure
    (lists, code blocks, paragraphs) still renders.
    """
    return _MD_CONTROL_CHARS.sub("", text)


def _tool_line(query: str) -> Text:
    """A web_search invocation as a Claude-CLI-style line (literal, no markup)."""
    return Text(f'{TOOL_BULLET} web_search("{_sanitize(query)}")')


def _result_count(block: object) -> int | None:
    """Number of results in a web_search_tool_result block, if available."""
    content = getattr(block, "content", None)
    return len(content) if isinstance(content, list) else None


def stream_turn(
    client: anthropic.Anthropic, messages: list[dict], console: Console
) -> str:
    """Stream one user turn and return the assistant's text.

    A single `rich` Live region shows, top to bottom: each web_search the agent
    ran (with a results count), then either a spinner (while working) or the
    answer rendered as markdown that updates live as text streams.

    `web_search` is a server-side tool, so the streamed text already reflects the
    results. If the server-side loop pauses (`stop_reason == "pause_turn"`), the
    partial assistant turn is appended and re-sent to resume, up to
    MAX_TOOL_CONTINUATIONS times. `messages` is a working list the caller owns.
    """
    answer_parts: list[str] = []
    tool_lines: list[Text] = []  # persistent activity shown above the answer
    label = THINKING_LABEL
    streaming = False  # True once the answer's text starts arriving
    working = True  # False once the turn is done (drops the spinner)

    def render() -> Group:
        body: list = list(tool_lines)
        if streaming:
            body.append(Text(AGENT_PREFIX, style="bold"))
            body.append(Markdown(_sanitize_markdown("".join(answer_parts))))
        elif working:
            body.append(
                Spinner(SPINNER_STYLE, text=Text(f"{AGENT_PREFIX}{label}"))
            )
        return Group(*body)

    with Live(
        render(),
        console=console,
        refresh_per_second=LIVE_REFRESH_PER_SECOND,
        vertical_overflow="visible",
    ) as live:
        try:
            for _ in range(MAX_TOOL_CONTINUATIONS + 1):
                with client.messages.stream(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=SYSTEM_PROMPT,
                    tools=TOOLS,
                    messages=messages,
                ) as stream:
                    for event in stream:
                        if event.type == "content_block_start":
                            block = event.content_block
                            if (
                                block.type == "server_tool_use"
                                and getattr(block, "name", None) == "web_search"
                            ):
                                inp = getattr(block, "input", None)
                                query = (
                                    inp.get("query", "")
                                    if isinstance(inp, dict)
                                    else ""
                                )
                                tool_lines.append(_tool_line(query))
                                label = SEARCHING_LABEL
                            elif block.type == "web_search_tool_result":
                                n = _result_count(block)
                                if n is not None:
                                    tool_lines.append(Text(f"{RESULT_PREFIX}{n} results"))
                                label = THINKING_LABEL
                            elif block.type == "text":
                                streaming = True
                            live.update(render())
                        elif event.type == "content_block_delta":
                            if event.delta.type == "text_delta":
                                answer_parts.append(event.delta.text)
                                live.update(render())
                    final = stream.get_final_message()

                if final.stop_reason != "pause_turn":
                    break
                # Resume: re-send with the paused assistant content appended.
                messages.append({"role": "assistant", "content": final.content})
            else:
                # Loop ran out while still paused — the answer is cut short.
                tool_lines.append(Text(TRUNCATION_NOTICE))
        finally:
            # Drop the spinner from the final, frozen frame (success or error).
            working = False
            live.update(render())

    return "".join(answer_parts)


def main() -> None:
    load_dotenv()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit(
            "ANTHROPIC_API_KEY is not set. Copy .env.template to .env and add your key:\n"
            "  cp .env.template .env"
        )

    client = anthropic.Anthropic(api_key=api_key)
    console = Console()
    messages: list[dict] = []

    print(f"AgentBreaker chat ({MODEL}). Type 'exit' to quit.\n")

    while True:
        try:
            question = input(USER_PREFIX).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not question:
            continue
        if question.lower() in {"exit", "quit"}:
            break

        print()  # blank line between the user's question and the agent's output

        # Work on a copy so a server-tool `pause_turn` (which appends partial
        # assistant blocks) doesn't leak into the durable text history.
        working = messages + [{"role": "user", "content": question}]

        try:
            answer = stream_turn(client, working, console)
        except anthropic.APIError as exc:
            print(f"\n[API error: {exc}]")
            continue  # history untouched; drop this turn
        except KeyboardInterrupt:
            print("\n[interrupted]")
            continue  # abort this turn; history untouched
        if not answer:
            # No text came back (empty refusal, hit max_tokens before any text,
            # etc.). Appending an empty assistant turn would make the API reject
            # every following request, so drop this turn instead.
            print("[no response]")
            continue

        print()  # blank line before the next prompt
        messages.append({"role": "user", "content": question})
        messages.append({"role": "assistant", "content": answer})


if __name__ == "__main__":
    main()
