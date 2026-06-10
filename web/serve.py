"""Launch the AgentBreaker web demo.

    python web/serve.py

Serves the React frontend and the FastAPI/SSE backend on one port. Requires the
web extras (fastapi, uvicorn, sse-starlette) and ANTHROPIC_API_KEY in .env.
"""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent / "backend"
sys.path.insert(0, str(BACKEND))

from config import HOST, PORT  # noqa: E402


def main() -> None:
    import uvicorn

    uvicorn.run("app:app", host=HOST, port=PORT, app_dir=str(BACKEND), reload=False)


if __name__ == "__main__":
    main()
