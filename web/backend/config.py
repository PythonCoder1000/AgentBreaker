"""Configuration for the AgentBreaker web demo (values only, no logic)."""

from pathlib import Path

# Repo / source layout.
REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"

# Server. (8000 is commonly taken by other local dev servers; 8010 avoids the clash.)
# In a deployment the platform injects $PORT and you bind 0.0.0.0 via the start
# command (see render.yaml); these defaults are for local `python app.py`.
HOST = "127.0.0.1"
PORT = 8010

# Optional HTTP Basic Auth gate. Enabled only when BASIC_AUTH_USER and
# BASIC_AUTH_PASS are both set in the environment (so local dev stays open).
# This realm string is what the browser shows in its login dialog.
BASIC_AUTH_REALM = "AgentBreaker demo"

# Unauthenticated liveness path, so a hosting platform can health-check the app
# without tripping the auth gate.
HEALTH_PATH = "/healthz"

# How long the engine waits for a human approve/deny on an escalation before it
# defaults to deny — so a worker thread can't hang forever if the browser leaves.
ESCALATION_TIMEOUT_SECONDS = 180

# Truncation for what we stream to the browser (keeps the live feed light).
EVENT_OUTPUT_MAX_CHARS = 4000      # bash output / email reply shown per event
EMAIL_BODY_PREVIEW_CHARS = 600     # email-body preview inside a tool_call card

# File viewer: cap how much of a single file the explorer will load into the
# browser when the user clicks a file to read its contents.
FILE_VIEW_MAX_BYTES = 256 * 1024

# Cache-Control for the served frontend assets. "no-cache" means the browser
# must revalidate before reuse (cheap ETag 304s) instead of heuristically
# caching the ES modules — so a frontend edit actually shows up on reload.
STATIC_CACHE_CONTROL = "no-cache"

# Scripted client reply used once a scenario's own replies are exhausted (the
# harness never sends real email; the "recipient reply" is always simulated).
DEFAULT_EMAIL_REPLY = "Acknowledged, thanks."
