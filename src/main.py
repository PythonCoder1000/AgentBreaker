"""A minimal agent loop with a simulated deployment environment.

The agent believes it is a live personal assistant with real tools: web_search
and send_email. When it calls send_email, the harness shows the message to the
human operator, who replies *as the client*; that reply is fed back to the agent
as the tool result — the agent never learns a human is on the other end.

The operator drives the agent through "[User]: " prompts (assigning tasks) and
answers the agent's emails through "[Client]: " prompts. Tool activity and the
agent's markdown answer render live (Claude-CLI style) via `rich`.
Type 'exit' or 'quit' (or press Ctrl-C / Ctrl-D) to leave.
"""

import os
import re
import secrets
import sys

import anthropic
from dotenv import load_dotenv
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from settings import (
    AGENT_PREFIX,
    AGENT_STEP_LIMIT_NOTICE,
    CLIENT_PREFIX,
    CONTACTS,
    CONTACTS_DIRECTORY_HEADER,
    EMAIL_BULLET,
    EMAIL_FENCE_NONCE_BYTES,
    EMAIL_LABEL,
    EMAIL_NO_REPLY_TEMPLATE,
    EMAIL_REPLY_TEMPLATE,
    LIVE_REFRESH_PER_SECOND,
    MAX_AGENT_STEPS,
    MAX_TOKENS,
    MODEL,
    RESULT_PREFIX,
    SEARCHING_LABEL,
    SPINNER_STYLE,
    SYSTEM_PROMPT,
    THINKING_LABEL,
    TOOL_BULLET,
    TOOLS,
    USER_PREFIX,
)

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")  # all C0 controls (incl. ESC) + DEL
_MD_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")  # same, but keeps \t and \n


def _sanitize(text: str) -> str:
    """Strip control bytes so untrusted single-line text can't drive the terminal."""
    return _CONTROL_CHARS.sub("", text)


def _sanitize_block(text: str) -> str:
    """Strip control bytes from multi-line text, preserving newlines and tabs.

    `rich` does not strip ESC, so model/web text rendered to the terminal could
    otherwise smuggle ANSI/cursor escapes. Newline and tab survive so markdown
    and email structure still render.
    """
    return _MD_CONTROL_CHARS.sub("", text)


def _build_system_prompt() -> str:
    """Append the contact directory (rendered from CONTACTS) to the system prompt."""
    lines = [CONTACTS_DIRECTORY_HEADER]
    for contact in CONTACTS:
        name = contact.get("name", "Unknown")
        email = contact.get("email", "no email on file")
        phone = contact.get("phone", "no phone on file")
        lines.append(f"- {name} <{email}> — {phone}")
    return f"{SYSTEM_PROMPT}\n\n" + "\n".join(lines)


def _tool_line(query: str) -> Text:
    """A web_search invocation as a Claude-CLI-style line (literal, no markup)."""
    return Text(f'{TOOL_BULLET} web_search("{_sanitize(query)}")')


def _result_count(block: object) -> int | None:
    """Number of results in a web_search_tool_result block, if available."""
    content = getattr(block, "content", None)
    return len(content) if isinstance(content, list) else None


def _handle_send_email(console: Console, tool_input: dict) -> str:
    """Show an outgoing email and collect the recipient's reply (operator-typed).

    The returned string becomes the tool result the agent sees — i.e. the
    client's email reply. Renders the message the agent "sent", then prompts the
    operator to answer as the client.
    """
    email = _sanitize(str(tool_input.get("email", "")))
    message = _sanitize_block(str(tool_input.get("message", "")))

    console.print()
    console.print(
        Panel(
            Text(message),
            title=Text(f"{EMAIL_BULLET} Email → {email}", style="bold cyan"),
            title_align="left",
            border_style="cyan",
        )
    )
    try:
        reply = input(CLIENT_PREFIX).strip()
    except EOFError:
        reply = ""
    print()

    if not reply:
        return EMAIL_NO_REPLY_TEMPLATE.format(email=email)
    # Strip control bytes (incl. newlines) so the single-line reply can't forge
    # the closing marker or smuggle a fake [SYSTEM] line onto its own line. The
    # nonce makes the marker itself unguessable, so it can't be forged inline.
    nonce = secrets.token_hex(EMAIL_FENCE_NONCE_BYTES)
    return EMAIL_REPLY_TEMPLATE.format(email=email, reply=_sanitize(reply), nonce=nonce)


def stream_turn(
    client: anthropic.Anthropic,
    messages: list[dict],
    console: Console,
    system: str,
) -> bool:
    """Run one agentic turn, mutating `messages` with the full exchange.

    Streams the model's response (rendering web_search activity and the answer as
    live markdown). Then, by stop_reason:
      - tool_use   -> execute each send_email (operator answers as the client),
                      append tool results, and continue the loop.
      - pause_turn -> a server tool (web_search) paused; resume.
      - otherwise  -> the turn is done.
    Returns True if the agent produced any answer text or sent any email.
    """
    produced_text = False
    sent_email = False

    for _ in range(MAX_AGENT_STEPS):
        seg_text: list[str] = []  # this segment's streamed answer text
        tool_lines: list[Text] = []  # web_search activity, shown above the answer
        label = THINKING_LABEL
        streaming = False  # True once answer text starts arriving this segment
        busy = True  # drives the spinner; False on the frozen final frame

        def render() -> Group:
            body: list = list(tool_lines)
            if streaming:
                body.append(Text(AGENT_PREFIX, style="bold"))
                body.append(Markdown(_sanitize_block("".join(seg_text))))
            elif busy:
                body.append(Spinner(SPINNER_STYLE, text=Text(f"{AGENT_PREFIX}{label}")))
            return Group(*body)

        with Live(
            render(),
            console=console,
            refresh_per_second=LIVE_REFRESH_PER_SECOND,
            vertical_overflow="visible",
        ) as live:
            try:
                with client.messages.stream(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=system,
                    tools=TOOLS,
                    messages=messages,
                ) as stream:
                    for event in stream:
                        if event.type == "content_block_start":
                            block = event.content_block
                            name = getattr(block, "name", None)
                            if block.type == "server_tool_use" and name == "web_search":
                                inp = getattr(block, "input", None)
                                query = inp.get("query", "") if isinstance(inp, dict) else ""
                                tool_lines.append(_tool_line(query))
                                label = SEARCHING_LABEL
                            elif block.type == "web_search_tool_result":
                                n = _result_count(block)
                                if n is not None:
                                    tool_lines.append(Text(f"{RESULT_PREFIX}{n} results"))
                                label = THINKING_LABEL
                            elif block.type == "tool_use" and name == "send_email":
                                label = EMAIL_LABEL
                            elif block.type == "text":
                                streaming = True
                            live.update(render())
                        elif event.type == "content_block_delta":
                            if event.delta.type == "text_delta":
                                seg_text.append(event.delta.text)
                                streaming = True
                                live.update(render())
                    final = stream.get_final_message()
            finally:
                busy = False
                live.update(render())

        if seg_text:
            produced_text = True

        # Record the assistant turn (full content) for history + continuation.
        if final.content:
            messages.append({"role": "assistant", "content": final.content})

        # Every tool_use block needs a matching tool_result, regardless of
        # stop_reason: a tool_use can also surface under e.g. max_tokens (the
        # model cut off mid-call), and committing it without a result would make
        # the API reject every following request. Drive off block presence, not
        # stop_reason.
        tool_use_blocks = [
            b for b in final.content if getattr(b, "type", None) == "tool_use"
        ]
        if tool_use_blocks:
            tool_results = []
            for block in tool_use_blocks:
                if block.name == "send_email":
                    sent_email = True
                    inp = block.input if isinstance(block.input, dict) else {}
                    result = _handle_send_email(console, inp)
                    tool_results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": result}
                    )
                else:
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"Error: unknown tool '{block.name}'.",
                            "is_error": True,
                        }
                    )
            messages.append({"role": "user", "content": tool_results})
            continue

        if final.stop_reason == "pause_turn":
            continue  # server tool paused mid-run; re-send to resume

        break
    else:
        console.print(Text(AGENT_STEP_LIMIT_NOTICE))
        # End history on an assistant turn so the next question alternates cleanly
        # (the loop may have just appended a dangling tool_result user turn).
        messages.append({"role": "assistant", "content": AGENT_STEP_LIMIT_NOTICE})

    return produced_text or sent_email


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
    system = _build_system_prompt()
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

        # Work on a copy so a failed turn (exception) doesn't leave a partial
        # exchange in the durable history.
        working = messages + [{"role": "user", "content": question}]

        try:
            produced = stream_turn(client, working, console, system)
        except anthropic.APIError as exc:
            print(f"\n[API error: {exc}]")
            continue  # history untouched; drop this turn
        except KeyboardInterrupt:
            print("\n[interrupted]")
            continue  # abort this turn; history untouched

        if not produced:
            print("[no response]")
            continue  # nothing produced; don't commit a dangling turn

        messages[:] = working  # commit the full exchange (incl. emails) to history
        print()  # blank line before the next prompt


if __name__ == "__main__":
    main()
