"""Shared access to the process-wide MPD client on app.state."""

from fastapi import HTTPException, Request

from app.mpd_client import MpdClient, MpdError


def mpd_from_app(app) -> MpdClient:
    """Return the lifespan MPD client or raise MpdError."""
    client = getattr(app.state, "mpd", None)
    if client is None:
        raise MpdError("MPD is down")
    return client


def get_mpd(request: Request) -> MpdClient:
    """FastAPI dependency for REST routes."""
    try:
        return mpd_from_app(request.app)
    except MpdError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
