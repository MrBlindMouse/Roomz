"""Launcher. Use: uv run python run.py"""

import uvicorn

from app.main import app

if __name__ == "__main__":
    # app.main already called configure_logging(); keep that instead of uvicorn's defaults.
    uvicorn.run(app, host="0.0.0.0", port=8000, log_config=None)
