"""SQLAlchemy models: LibraryRoot, Track, PlaylistOrder, PlaybackState."""

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utc_now() -> datetime:
    """Return current UTC time as naive datetime for DateTime(timezone=False) columns."""
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    """Declarative base for all models."""

    pass


class LibraryRoot(Base):
    """A user-chosen library folder (scanned recursively)."""

    __tablename__ = "library_roots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    path: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=_utc_now)

    tracks: Mapped[list["Track"]] = relationship("Track", back_populates="library_root")


class Track(Base):
    """A single audio file; may live in any library root (full filepath stored)."""

    __tablename__ = "tracks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    library_root_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("library_roots.id", ondelete="SET NULL"), nullable=True
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    filepath: Mapped[str] = mapped_column(String(1024), nullable=False, index=True)
    title: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    artist: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    album: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=_utc_now)

    library_root: Mapped[Optional["LibraryRoot"]] = relationship(
        "LibraryRoot", back_populates="tracks"
    )
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
        DateTime(timezone=False), default=_utc_now, onupdate=_utc_now
    )
