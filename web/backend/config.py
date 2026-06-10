"""Configuration for the AgentBreaker web demo (values only, no logic)."""

from pathlib import Path

# Repo / source layout.
REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"

# Server. (8000 is commonly taken by other local dev servers; 8010 avoids the clash.)
HOST = "127.0.0.1"
PORT = 8010

# How long the engine waits for a human approve/deny on an escalation before it
# defaults to deny — so a worker thread can't hang forever if the browser leaves.
ESCALATION_TIMEOUT_SECONDS = 180

# Truncation for what we stream to the browser (keeps the live feed light).
EVENT_OUTPUT_MAX_CHARS = 4000      # bash output / email reply shown per event
EMAIL_BODY_PREVIEW_CHARS = 600     # email-body preview inside a tool_call card

# Scripted client reply used once a scenario's own replies are exhausted (the
# harness never sends real email; the "recipient reply" is always simulated).
DEFAULT_EMAIL_REPLY = "Acknowledged, thanks."
