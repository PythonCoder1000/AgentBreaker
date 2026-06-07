"""A minimal agent loop: the user asks a question, the agent answers.

Keeps a running conversation history so follow-up questions have context.
While the agent works, a "[Agent]: Thinking..." spinner runs, and each tool the
agent uses is printed underneath it (Claude-CLI style).
Type 'exit' or 'quit' (or press Ctrl-C / Ctrl-D) to leave.
"""

import itertools
import os
import re
import sys
import threading
import time

import anthropic
from dotenv import load_dotenv

from settings import (
    AGENT_PREFIX,
    MAX_TOKENS,
    MAX_TOOL_CONTINUATIONS,
    MODEL,
    RESULT_PREFIX,
    SEARCHING_LABEL,
    SPINNER_FRAMES,
    SPINNER_INTERVAL,
    SYSTEM_PROMPT,
    THINKING_LABEL,
    TOOL_BULLET,
    TOOLS,
    TRUNCATION_NOTICE,
    USER_PREFIX,
)

_CLEAR_LINE = "\r\033[K"  # carriage return + clear-to-end-of-line
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")  # C0 controls (incl. ESC) + DEL


def _sanitize(text: str) -> str:
    """Strip control bytes so untrusted text can't drive the terminal.

    Search queries are model-generated and can be steered by adversarial page
    content returned from an earlier search, so a query echoed to the TTY could
    otherwise smuggle ANSI/cursor escapes. Used for single-line labels — newline
    and tab are stripped too.
    """
    return _CONTROL_CHARS.sub("", text)


class Spinner:
    """A single-line 'label …' spinner driven by a background thread.

    Animates only on a TTY; under a pipe it's a no-op so logs stay clean.
    `start()` replaces any running spinner; `stop()` clears the line and joins
    the thread, so callers can safely print immediately afterward.
    """

    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self._enabled = sys.stdout.isatty()
        self._stop: threading.Event | None = None
        self._thread: threading.Thread | None = None

    def start(self, label: str) -> None:
        self.stop()
        if not self._enabled:
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._spin, args=(label,), daemon=True)
        self._thread.start()

    def _spin(self, label: str) -> None:
        assert self._stop is not None
        for frame in itertools.cycle(SPINNER_FRAMES):
            if self._stop.is_set():
                break
            print(f"\r{self._prefix}{label} {frame}", end="", flush=True)
            time.sleep(SPINNER_INTERVAL)

    def stop(self) -> None:
        if self._stop is None:
            return
        self._stop.set()
        try:
            if self._thread is not None:
                self._thread.join()
        finally:
            # Always erase the spinner line and reset, even if join() is
            # interrupted (e.g. a second Ctrl-C), so the terminal isn't left dirty.
            print(_CLEAR_LINE, end="", flush=True)
            self._stop = None
            self._thread = None


def _tool_line(query: str) -> str:
    """Render a web_search invocation as a Claude-CLI-style line."""
    return f'{TOOL_BULLET} web_search("{_sanitize(query)}")'


def _result_count(block: object) -> int | None:
    """Number of results in a web_search_tool_result block, if available."""
    content = getattr(block, "content", None)
    return len(content) if isinstance(content, list) else None


def stream_turn(client: anthropic.Anthropic, messages: list[dict]) -> str:
    """Stream one user turn and return the assistant's text.

    Iterates the raw event stream so tool activity can be surfaced live. While
    waiting on the model a spinner runs; as the agent calls `web_search`, each
    query is printed, then a spinner runs while the server fetches results.

    `web_search` is a server-side tool, so the streamed text already reflects
    the results. If the server-side loop pauses (`stop_reason == "pause_turn"`),
    the partial assistant turn is appended and re-sent to resume, up to
    MAX_TOOL_CONTINUATIONS times. `messages` is a working list the caller owns.
    """
    spinner = Spinner(AGENT_PREFIX)
    answer_parts: list[str] = []
    # Persists across pause_turn resumes so "[Agent]: " prints once per turn,
    # not once per stream and not once per (citation-split) text block.
    text_started = False
    try:
        for _ in range(MAX_TOOL_CONTINUATIONS + 1):
            spinner.start(THINKING_LABEL)
            with client.messages.stream(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            ) as stream:
                for event in stream:
                    if event.type == "content_block_start":
                        spinner.stop()  # something is about to print or stream
                        block = event.content_block
                        if (
                            block.type == "server_tool_use"
                            and getattr(block, "name", None) == "web_search"
                        ):
                            inp = getattr(block, "input", None)
                            query = inp.get("query", "") if isinstance(inp, dict) else ""
                            print(_tool_line(query), flush=True)
                            spinner.start(SEARCHING_LABEL)  # server runs the search
                        elif block.type == "web_search_tool_result":
                            n = _result_count(block)
                            if n is not None:
                                print(f"{RESULT_PREFIX}{n} results", flush=True)
                            if not text_started:
                                spinner.start(THINKING_LABEL)
                        elif block.type == "text":
                            if not text_started:
                                print(AGENT_PREFIX, end="", flush=True)
                                text_started = True
                        elif not text_started:
                            # Internal plumbing of dynamic web search
                            # (code_execution and its results). Don't surface it;
                            # just keep the spinner running.
                            spinner.start(THINKING_LABEL)
                    elif event.type == "content_block_delta":
                        delta = event.delta
                        if delta.type == "text_delta":
                            answer_parts.append(delta.text)
                            print(delta.text, end="", flush=True)
                        # input_json_delta ignored: web_search's query is complete
                        # at content_block_start; other tool inputs aren't shown.
                final = stream.get_final_message()

            spinner.stop()
            if final.stop_reason != "pause_turn":
                break
            # Resume: re-send with the paused assistant content appended.
            messages.append({"role": "assistant", "content": final.content})
        else:
            # Loop ran out while still paused — the answer is cut short.
            print(f"\n{TRUNCATION_NOTICE}", flush=True)
    finally:
        spinner.stop()

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

        # Work on a copy so a server-tool `pause_turn` (which appends partial
        # assistant blocks) doesn't leak into the durable text history.
        working = messages + [{"role": "user", "content": question}]

        try:
            answer = stream_turn(client, working)
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
            print("\n[no response]")
            continue

        print("\n")
        messages.append({"role": "user", "content": question})
        messages.append({"role": "assistant", "content": answer})


if __name__ == "__main__":
    main()
