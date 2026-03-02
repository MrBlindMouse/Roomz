"""SQLAlchemy models: Track, PlaylistOrder, PlaybackState."""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for all models."""

    pass


class Track(Base):
    """A single audio file in data/music/."""

    __tablename__ = "tracks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(512), unique=True, nullable=False, index=True)
    filepath: Mapped[str] = mapped_column(String(1024), nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    artist: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    album: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow)

    playlist_entries: Mapped[list["PlaylistOrder"]] = relationship(
        "PlaylistOrder",
        back_populates="track",
        order_by="PlaylistOrder.position",
    )


class PlaylistOrder(Base):
    """Ordered playlist: each row is one track at a position."""

    __tablename__ = "playlist_order"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    track_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    track: Mapped["Track"] = relationship("Track", back_populates="playlist_entries")


class PlaybackState(Base):
    """Singleton global playback state (exactly one row)."""

    __tablename__ = "playback_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    current_track_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("tracks.id", ondelete="SET NULL"), nullable=True
    )
    is_playing: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    position_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=datetime.utcnow, onupdate=datetime.utcnow
    )
