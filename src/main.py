"""A minimal agent loop: the user asks a question, the agent answers.

Keeps a running conversation history so follow-up questions have context.
Type 'exit' or 'quit' (or press Ctrl-C / Ctrl-D) to leave.
"""

import os
import sys

import anthropic
from dotenv import load_dotenv

from settings import MAX_TOKENS, MAX_TOOL_CONTINUATIONS, MODEL, SYSTEM_PROMPT, TOOLS


def stream_turn(client: anthropic.Anthropic, messages: list[dict]) -> str:
    """Stream one user turn and return the assistant's text.

    `web_search` is a server-side tool: Anthropic runs the search inline, so
    the streamed text already reflects the results. If the server-side loop
    pauses (`stop_reason == "pause_turn"`), append the partial assistant turn
    and re-send to resume, up to MAX_TOOL_CONTINUATIONS times. `messages` is a
    working list the caller owns; this appends paused turns to it as it resumes.
    """
    answer_parts: list[str] = []
    for _ in range(MAX_TOOL_CONTINUATIONS + 1):
        with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        ) as stream:
            for text in stream.text_stream:
                answer_parts.append(text)
                print(text, end="", flush=True)
            final = stream.get_final_message()

        if final.stop_reason != "pause_turn":
            break
        # Resume: re-send with the paused assistant content appended.
        messages.append({"role": "assistant", "content": final.content})
    else:
        # Loop ran out while still paused — the answer is cut short.
        print("\n[truncated — search budget exhausted]", end="", flush=True)

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
            question = input("you> ").strip()
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

        print("bot> ", end="", flush=True)
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
