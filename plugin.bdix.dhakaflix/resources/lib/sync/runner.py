"""Coordinate catalog crawling and atomic library synchronization."""

import hashlib
import os
import time

from resources.lib.library_index import LibraryIndex

from .catalog import crawl_catalog
from .models import SyncCancelled, SyncError, SyncResult
from .paths import (
    _limit_output_path,
    _managed_path,
    _media_output_path,
    _native_relative_path,
    _path_key,
)
from .transactions import (
    DELETION_CONFIRMATION_INTERVAL,
    DELETION_CONFIRMATION_MIN_MEDIA,
    DELETION_CONFIRMATION_RATIO,
    _commit_transaction,
    _load_json,
    _recover_transaction,
    _save_json,
)

FULL_CRAWL_INTERVAL = 7 * 24 * 60 * 60


def run_sync(
    client,
    library_dir,
    cancelled=None,
    progress=None,
    media_types=("movie", "series"),
    full_crawl=None,
):
    media_types = frozenset(media_types)
    if not media_types or not media_types <= {"movie", "series"}:
        raise SyncError("Select at least one valid library media type to sync")
    cancelled = cancelled or (lambda: False)
    progress = progress or (lambda page_count, media_count, category: None)
    os.makedirs(library_dir, exist_ok=True)
    state_path = os.path.join(library_dir, "state.json")
    manifest_path = os.path.join(library_dir, "manifest.json")
    index_path = os.path.join(library_dir, "library.sqlite")
    state = _load_json(state_path, {})
    state["last_attempt"] = time.time()
    _save_json(state_path, state)
    index = None
    try:
        index = LibraryIndex(index_path)
        _recover_transaction(library_dir, index)
        migration_requires_full = index.needs_migration
        if index.needs_migration:
            previous_manifest = _load_json(manifest_path, {"entries": {}})
            if not isinstance(previous_manifest, dict):
                raise SyncError("The previous library manifest is invalid")
            legacy_entries = previous_manifest.get("entries", {})
            previous_directories = previous_manifest.get("directories", {})
            if not isinstance(legacy_entries, dict) or not isinstance(
                previous_directories, dict
            ):
                raise SyncError("The previous library manifest is invalid")
            if legacy_entries or previous_directories:
                index.import_manifest(legacy_entries, previous_directories)
            del previous_manifest
            del legacy_entries
        else:
            previous_directories = index.directory_entries()
        incremental_state_valid = (
            not index.needs_migration
            and bool(previous_directories)
            and all(
                isinstance(directory, dict)
                and (
                    directory.get("modified") is None
                    or isinstance(directory.get("modified"), str)
                )
                and (
                    directory.get("parent_url") is None
                    or isinstance(directory.get("parent_url"), str)
                )
                for directory in previous_directories.values()
            )
        )
        last_full_crawl = state.get("last_full_crawl")
        full_crawl_required = not incremental_state_valid or migration_requires_full
        if full_crawl is None:
            full_crawl = (
                full_crawl_required
                or not isinstance(last_full_crawl, (int, float))
                or time.time() - last_full_crawl >= FULL_CRAWL_INTERVAL
            )
        else:
            full_crawl = full_crawl_required or bool(full_crawl)
        if not incremental_state_valid:
            previous_directories = {}

        (
            categories,
            media_items,
            page_count,
            errors,
            directory_states,
            visited_directories,
        ) = crawl_catalog(
            client,
            client.origin_url,
            cancelled=cancelled,
            progress=progress,
            previous_directories=previous_directories,
            full_crawl=full_crawl,
            media_types=media_types,
        )
        if errors:
            state["last_error"] = "; ".join(errors[:20])
            state["last_result"] = {
                "complete": False,
                "page_count": page_count,
                "media_count": len(media_items),
            }
            _save_json(state_path, state)
            return SyncResult(False, 0, 0, page_count, errors, 0)

        removed_directories = previous_directories.keys() - directory_states.keys()
        affected_directories = set(visited_directories) | removed_directories
        previous_media_count = sum(index.counts())
        if full_crawl:
            old_entries = {}
            old_paths = {_native_relative_path(path) for path in index.media_paths()}
            affected_media_count = previous_media_count
        else:
            old_entries = index.media_entries_for_directories(affected_directories)
            old_entries.update(
                index.media_entries_for_urls(media.url for media in media_items)
            )
            old_paths = {
                _native_relative_path(entry["path"])
                for entry in old_entries.values()
                if isinstance(entry, dict) and isinstance(entry.get("path"), str)
            }
            affected_media_count = len(old_entries)
        if previous_media_count - affected_media_count + len(media_items) <= 0:
            message = (
                "The crawl found no supported videos; "
                "existing library entries were kept"
            )
            state["last_error"] = message
            state["last_result"] = {
                "complete": False,
                "page_count": page_count,
                "media_count": 0,
            }
            _save_json(state_path, state)
            return SyncResult(False, 0, 0, page_count, (message,), 0)

        if not full_crawl:
            index.exclude_media_urls(old_entries)
        used_paths = set()
        desired = {}
        replacements = []
        for media in media_items:
            if cancelled():
                raise SyncCancelled("Sync cancelled because Kodi is shutting down")
            relative_path, media_type = _media_output_path(media)
            relative_path = _limit_output_path(library_dir, relative_path)
            path_key = _path_key(relative_path)
            existing_url = index.media_url_for_path_key(path_key)
            if path_key in used_paths or (
                existing_url is not None
                and existing_url != media.url
                and index.path_key_in_use(path_key)
            ):
                relative_path, media_type = _media_output_path(
                    media,
                    disambiguate=True,
                )
                relative_path = _limit_output_path(library_dir, relative_path)
            path_key = _path_key(relative_path)
            existing_url = index.media_url_for_path_key(path_key)
            if path_key in used_paths or (
                existing_url is not None
                and existing_url != media.url
                and index.path_key_in_use(path_key)
            ):
                raise SyncError(
                    "A generated library path collides with an existing media entry"
                )
            used_paths.add(path_key)
            desired[media.url] = {
                "path": relative_path,
                "media_type": media_type,
                "source_directory": media.source_directory,
            }
            output_path = _managed_path(library_dir, relative_path)
            expected_content = (media.url + "\n").encode("utf-8")
            try:
                with open(output_path, "rb") as existing:
                    unchanged = existing.read() == expected_content
            except FileNotFoundError:
                unchanged = False
            if not unchanged:
                replacements.append(("replace", output_path, media.url + "\n"))
        if cancelled():
            raise SyncCancelled("Sync cancelled because Kodi is shutting down")

        stale_paths = []
        for stale_path in sorted(old_paths):
            if _path_key(stale_path) in used_paths:
                continue
            stale_paths.append(_managed_path(library_dir, stale_path))

        pruned_count = sum(os.path.isfile(path) for path in stale_paths)
        if (
            previous_media_count >= DELETION_CONFIRMATION_MIN_MEDIA
            and len(stale_paths) / previous_media_count >= DELETION_CONFIRMATION_RATIO
        ):
            signature = hashlib.sha256(
                "\n".join(
                    sorted(os.path.relpath(path, library_dir) for path in stale_paths)
                ).encode("utf-8")
            ).hexdigest()
            candidate = state.get("prune_candidate")
            now = time.time()
            first_seen = (
                candidate.get("first_seen") if isinstance(candidate, dict) else None
            )
            valid_first_seen = (
                isinstance(first_seen, (int, float)) and 0 <= first_seen <= now
            )
            confirmed = (
                isinstance(candidate, dict)
                and candidate.get("signature") == signature
                and valid_first_seen
                and now - first_seen >= DELETION_CONFIRMATION_INTERVAL
            )
            if not confirmed:
                if (
                    not isinstance(candidate, dict)
                    or candidate.get("signature") != signature
                    or not valid_first_seen
                ):
                    state["prune_candidate"] = {
                        "signature": signature,
                        "first_seen": now,
                        "count": len(stale_paths),
                        "total": previous_media_count,
                    }
                message = (
                    f"The crawl proposes removing {len(stale_paths)} of "
                    f"{previous_media_count} indexed videos. The same complete "
                    "result must be confirmed after 30 minutes before removal."
                )
                state["last_error"] = message
                state["last_result"] = {
                    "complete": False,
                    "page_count": page_count,
                    "media_count": len(media_items),
                    "candidate_pruned_count": len(stale_paths),
                }
                _save_json(state_path, state)
                return SyncResult(False, 0, 0, page_count, (message,), 0)
        else:
            state.pop("prune_candidate", None)

        state.pop("prune_candidate", None)
        _commit_transaction(
            library_dir,
            index,
            replacements,
            stale_paths,
            {
                "entries": desired,
                "directories": directory_states,
                "previous_directories": previous_directories,
                "full_crawl": full_crawl,
                "visited": visited_directories,
            },
        )
        managed_roots = {
            os.path.realpath(os.path.join(library_dir, "Movies")),
            os.path.realpath(os.path.join(library_dir, "TV Shows")),
        }
        empty_directories = set()
        for stale_path in stale_paths:
            directory = os.path.dirname(stale_path)
            for root in managed_roots:
                try:
                    if os.path.commonpath((root, directory)) != root:
                        continue
                except ValueError:
                    continue
                while directory != root:
                    empty_directories.add(directory)
                    parent = os.path.dirname(directory)
                    if parent == directory:
                        break
                    directory = parent
        for directory in sorted(empty_directories, key=len, reverse=True):
            try:
                os.rmdir(directory)
            except FileNotFoundError:
                continue
            except OSError:
                continue

        if os.path.exists(manifest_path):
            try:
                os.remove(manifest_path)
            except OSError:
                pass
        movie_count, episode_count = index.counts()
        completed_at = time.time()
        state.pop("last_error", None)
        state["last_success"] = completed_at
        if full_crawl:
            state["last_full_crawl"] = completed_at
        state["last_result"] = {
            "complete": True,
            "full_crawl": full_crawl,
            "category_count": len(categories),
            "page_count": page_count,
            "movie_count": movie_count,
            "episode_count": episode_count,
            "pruned_count": pruned_count,
        }
        _save_json(state_path, state)
        return SyncResult(
            True,
            movie_count,
            episode_count,
            page_count,
            errors,
            pruned_count,
        )
    except Exception as error:
        state["last_error"] = str(error)
        _save_json(state_path, state)
        raise
    finally:
        if index is not None:
            index.close()
