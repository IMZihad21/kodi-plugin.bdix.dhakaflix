"""Library synchronization package."""

import os

from .catalog import crawl_catalog, discover_categories
from .models import Category, Media, SyncCancelled, SyncError, SyncResult
from .paths import (
    MAX_COMPONENT_BYTES,
    MAX_LIBRARY_PATH_LENGTH,
    _limit_output_path,
    _media_output_path,
    _path_length,
    _safe_component,
)
from .runner import FULL_CRAWL_INTERVAL, run_sync
from .transactions import (
    DELETION_CONFIRMATION_INTERVAL,
    DELETION_CONFIRMATION_MIN_MEDIA,
    DELETION_CONFIRMATION_RATIO,
    _atomic_write,
)

__all__ = [
    "Category",
    "DELETION_CONFIRMATION_INTERVAL",
    "DELETION_CONFIRMATION_MIN_MEDIA",
    "DELETION_CONFIRMATION_RATIO",
    "FULL_CRAWL_INTERVAL",
    "MAX_COMPONENT_BYTES",
    "MAX_LIBRARY_PATH_LENGTH",
    "Media",
    "SyncCancelled",
    "SyncError",
    "SyncResult",
    "_atomic_write",
    "_limit_output_path",
    "_media_output_path",
    "_path_length",
    "_safe_component",
    "crawl_catalog",
    "discover_categories",
    "os",
    "run_sync",
]
