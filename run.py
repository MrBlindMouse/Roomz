"""Launcher: create the single Player, attach to app, then run FastAPI. Use: uv run python run.py"""

import uvicorn

from app.main import app
from app.player import Player

if __name__ == "__main__":
    app.state.player = Player()
    uvicorn.run(app, host="0.0.0.0", port=8000)
