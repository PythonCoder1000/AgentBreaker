"""Configuration values for the AgentBreaker chat agent.

Per the project Code rules, concrete settings — model, limits, prompts, tool
specs — live here rather than being hard-coded inline elsewhere.
"""

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096

# How many times to resume after a server-tool `pause_turn` before giving up.
# Web search runs a server-side loop that can pause itself after several
# iterations; this caps how many continuations we'll stream back.
MAX_TOOL_CONTINUATIONS = 5

SYSTEM_PROMPT = (
    "You are a helpful assistant. Answer the user's questions clearly and "
    "concisely.\n"
    "You have a web_search tool. Use it when the answer depends on current "
    "information — recent events, prices, releases, or anything time-sensitive "
    "or past your training cutoff — rather than answering from memory. Cite the "
    "sources you rely on."
)

# Server-side web search tool. It runs on Anthropic's infrastructure: declare it
# here and the model issues queries and reads results automatically. The
# `_20260209` version adds dynamic result filtering and is supported on
# Sonnet 4.6. `max_uses` caps searches *within a single request* (cost/abuse
# bound — distinct from MAX_TOOL_CONTINUATIONS, which caps `pause_turn` resumes).
WEB_SEARCH_MAX_USES = 5

TOOLS = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": WEB_SEARCH_MAX_USES},
]

# --- Conversation UI ---
USER_PREFIX = "[User]: "
AGENT_PREFIX = "[Agent]: "

# Loading spinner shown while the agent is working (Claude-CLI style). Only
# animated on a TTY; piped/redirected output skips the animation.
THINKING_LABEL = "Thinking..."
SEARCHING_LABEL = "Searching..."
SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPINNER_INTERVAL = 0.08  # seconds between frames

# How tool activity is rendered under the spinner, e.g. ⏺ web_search("query").
TOOL_BULLET = "⏺"
RESULT_PREFIX = "  ⎿ "

# Shown when the server-side search loop is still paused after the resume cap.
TRUNCATION_NOTICE = "[truncated — search budget exhausted]"
