"""Shared types and errors for library synchronization."""

from dataclasses import dataclass


class SyncError(RuntimeError):
    """Raised when a library sync cannot safely complete."""


class SyncCancelled(SyncError):
    """Raised when Kodi is shutting down or a sync was cancelled."""


@dataclass(frozen=True, slots=True)
class Category:
    label: str
    url: str
    media_type: str


@dataclass(frozen=True, slots=True)
class Media:
    name: str
    url: str
    media_type: str
    category: str
    path_parts: tuple
    source_directory: str = ""


@dataclass(frozen=True, slots=True)
class SyncResult:
    complete: bool
    movie_count: int
    episode_count: int
    page_count: int
    errors: tuple
    pruned_count: int
