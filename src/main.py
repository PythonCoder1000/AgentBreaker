"""A minimal agent loop: the user asks a question, the agent answers.

Keeps a running conversation history so follow-up questions have context.
Type 'exit' or 'quit' (or press Ctrl-C / Ctrl-D) to leave.
"""

import os
import sys

import anthropic
from dotenv import load_dotenv

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096
SYSTEM_PROMPT = "You are a helpful assistant. Answer the user's questions clearly and concisely."


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

        messages.append({"role": "user", "content": question})

        print("bot> ", end="", flush=True)
        answer_parts: list[str] = []
        try:
            with client.messages.stream(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=messages,
            ) as stream:
                for text in stream.text_stream:
                    answer_parts.append(text)
                    print(text, end="", flush=True)
        except anthropic.APIError as exc:
            print(f"\n[API error: {exc}]")
            messages.pop()  # drop the question so history stays consistent
            continue
        except KeyboardInterrupt:
            print("\n[interrupted]")
            messages.pop()  # abort this turn; keep history consistent
            continue

        answer = "".join(answer_parts)
        if not answer:
            # No text came back (empty refusal, hit max_tokens before any text,
            # etc.). Appending an empty assistant turn would make the API reject
            # every following request, so drop this turn instead.
            print("\n[no response]")
            messages.pop()
            continue

        print("\n")
        messages.append({"role": "assistant", "content": answer})


if __name__ == "__main__":
    main()
