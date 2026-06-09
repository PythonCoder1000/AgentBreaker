"""Tool-call interceptor for the Breaker Agent.

When the Breaker Agent runs, every client-side tool call it makes (send_email,
run_bash) is routed through this interceptor instead of the real tool handler.
For now it blocks every call with a fixed message; later it can inspect the
call's contents and decide whether to allow, block, or rewrite it.

(web_search is a server-side tool executed by Anthropic's infrastructure, so it
never reaches the client-side dispatch and is not routed through here.)
"""

from rich.console import Console
from rich.text import Text

from settings import INTERCEPT_BLOCK_MESSAGE, RESULT_PREFIX, TOOL_BULLET


def intercept(console: Console, tool_name: str, tool_input: dict) -> str:
    """Block a Breaker Agent tool call; return the result string the agent sees.

    `tool_input` is accepted (and ignored for now) so a future version can base
    its decision on the actual command/recipient/contents.
    """
    console.print()
    console.print(Text(f"{TOOL_BULLET} {tool_name} (intercepted)", style="yellow"))
    console.print(Text(f"{RESULT_PREFIX}{INTERCEPT_BLOCK_MESSAGE}", style="red"))
    return INTERCEPT_BLOCK_MESSAGE
