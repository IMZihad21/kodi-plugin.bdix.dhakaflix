"""Durable state writes and recoverable library-file transactions."""

import json
import os
import shutil
import tempfile
import uuid

from .models import SyncError
from .paths import _managed_path, _path_length

DELETION_CONFIRMATION_INTERVAL = 30 * 60
DELETION_CONFIRMATION_RATIO = 0.30
DELETION_CONFIRMATION_MIN_MEDIA = 20
_TRANSACTION_NAME = "sync.transaction.json"
_TRANSACTION_DIRECTORY = ".sync-transaction"


def _fsync_directory(path):
    if os.name == "nt":
        return
    try:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        pass


def _fsync_file(path):
    with open(path, "r+b") as source:
        os.fsync(source.fileno())


def _fsync_directory_chain(path, root_dir):
    root = os.path.realpath(root_dir)
    current = os.path.realpath(path)
    try:
        if os.path.commonpath((root, current)) != root:
            return
    except ValueError:
        return
    while True:
        _fsync_directory(current)
        if current == root:
            break
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent


def _atomic_write(path, data, create_directory=True, durable=True):
    directory = os.path.dirname(path) or os.curdir
    if create_directory:
        os.makedirs(directory, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(prefix=".dhakaflix-", dir=directory)
    try:
        output = os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")
        descriptor = None
        with output:
            output.write(data)
            output.flush()
            if durable:
                os.fsync(output.fileno())
        try:
            os.replace(temporary_path, path)
            if durable:
                _fsync_directory(directory)
        except OSError as error:
            raise OSError(
                f"{error}; atomic replace diagnostics: "
                f"destination={path!r}, destination_length="
                f"{_path_length(os.path.abspath(path))}, "
                f"temporary={temporary_path!r}, temporary_length="
                f"{_path_length(os.path.abspath(temporary_path))}, "
                f"parent_exists={os.path.isdir(directory)}, "
                f"temporary_exists={os.path.exists(temporary_path)}, "
                f"destination_exists={os.path.exists(path)}"
            ) from error
    finally:
        try:
            if descriptor is not None:
                os.close(descriptor)
        finally:
            try:
                os.remove(temporary_path)
            except FileNotFoundError:
                pass


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as source:
            return json.load(source)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as error:
        raise SyncError(f"Unable to read sync state {path}: {error}") from error


def _save_json(path, payload):
    _atomic_write(
        path,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )


def _transaction_paths(library_dir):
    return (
        os.path.join(library_dir, _TRANSACTION_NAME),
        os.path.join(library_dir, _TRANSACTION_DIRECTORY),
    )


def _prepare_transaction(library_dir, replacements, deletions):
    library_root = os.path.realpath(library_dir)
    journal_path, transaction_dir = _transaction_paths(library_root)
    if os.path.exists(journal_path) or os.path.exists(transaction_dir):
        raise SyncError(
            "An unfinished synchronization transaction must be recovered first"
        )
    staged_root = os.path.join(transaction_dir, "staged")
    backup_root = os.path.join(transaction_dir, "backup")
    os.makedirs(staged_root, exist_ok=False)
    os.makedirs(backup_root, exist_ok=True)
    operations = []
    try:
        index = 0
        for action, target_path, content in replacements:
            relative_target = os.path.relpath(target_path, library_root)
            if relative_target == os.pardir or relative_target.startswith(
                os.pardir + os.sep
            ):
                raise SyncError("Refusing to modify a path outside the managed library")
            existed = os.path.isfile(target_path)
            staged_relative = os.path.join("staged", f"{index:08d}")
            backup_relative = os.path.join("backup", f"{index:08d}")
            staged_path = os.path.join(transaction_dir, staged_relative)
            backup_path = os.path.join(transaction_dir, backup_relative)
            if action == "replace":
                with open(staged_path, "wb") as target:
                    target.write(content.encode("utf-8"))
                    target.flush()
                    os.fsync(target.fileno())
            if existed:
                shutil.copy2(target_path, backup_path)
                _fsync_file(backup_path)
            operations.append(
                {
                    "action": action,
                    "target": relative_target,
                    "existed": existed,
                    "staged": staged_relative if action == "replace" else None,
                    "backup": backup_relative if existed else None,
                }
            )
            index += 1
        for target_path in deletions:
            relative_target = os.path.relpath(target_path, library_root)
            if relative_target == os.pardir or relative_target.startswith(
                os.pardir + os.sep
            ):
                raise SyncError("Refusing to delete a path outside the managed library")
            existed = os.path.isfile(target_path)
            backup_relative = os.path.join("backup", f"{index:08d}")
            backup_path = os.path.join(transaction_dir, backup_relative)
            if existed:
                shutil.copy2(target_path, backup_path)
                _fsync_file(backup_path)
            operations.append(
                {
                    "action": "delete",
                    "target": relative_target,
                    "existed": existed,
                    "staged": None,
                    "backup": backup_relative if existed else None,
                }
            )
            index += 1
        _fsync_directory(staged_root)
        _fsync_directory(backup_root)
        _fsync_directory(transaction_dir)
        transaction_id = uuid.uuid4().hex
        _save_json(
            journal_path,
            {"version": 1, "transaction_id": transaction_id, "operations": operations},
        )
        return transaction_id, operations, transaction_dir, journal_path
    except Exception:
        shutil.rmtree(transaction_dir, ignore_errors=True)
        raise


def _restore_transaction(library_dir, operations, transaction_dir):
    for operation in reversed(operations):
        target_path = _managed_path(library_dir, operation["target"])
        if operation.get("existed"):
            backup_relative = operation.get("backup")
            if not isinstance(backup_relative, str):
                raise SyncError("Synchronization backup metadata is invalid")
            backup_path = os.path.join(transaction_dir, backup_relative)
            if not os.path.isfile(backup_path):
                raise SyncError("A required synchronization backup is missing")
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            descriptor, temporary_path = tempfile.mkstemp(
                prefix=".dhakaflix-restore-", dir=os.path.dirname(target_path)
            )
            os.close(descriptor)
            try:
                shutil.copy2(backup_path, temporary_path)
                _fsync_file(temporary_path)
                os.replace(temporary_path, target_path)
                _fsync_directory(os.path.dirname(target_path))
            except Exception:
                try:
                    os.remove(temporary_path)
                except FileNotFoundError:
                    pass
                raise
        else:
            try:
                os.remove(target_path)
            except FileNotFoundError:
                pass


def _recover_transaction(library_dir, index):
    journal_path, transaction_dir = _transaction_paths(library_dir)
    if not os.path.exists(journal_path):
        if os.path.isdir(transaction_dir):
            shutil.rmtree(transaction_dir)
        return
    journal = _load_json(journal_path, None)
    if (
        not isinstance(journal, dict)
        or journal.get("version") != 1
        or not isinstance(journal.get("transaction_id"), str)
        or not isinstance(journal.get("operations"), list)
    ):
        raise SyncError(
            "The synchronization recovery journal is invalid; library was not modified"
        )
    for index_value, operation in enumerate(journal["operations"]):
        if (
            not isinstance(operation, dict)
            or operation.get("action") not in ("replace", "delete")
            or not isinstance(operation.get("target"), str)
            or not isinstance(operation.get("existed"), bool)
        ):
            raise SyncError(
                "The synchronization recovery journal contains an invalid operation"
            )
        target = operation["target"]
        normalized_target = os.path.normpath(target)
        if (
            os.path.isabs(target)
            or normalized_target in (os.curdir, os.pardir)
            or normalized_target.startswith(os.pardir + os.sep)
            or normalized_target != target
        ):
            raise SyncError(
                "The synchronization recovery journal contains an unsafe path"
            )
        staged = operation.get("staged")
        backup = operation.get("backup")
        expected_staged = os.path.join("staged", f"{index_value:08d}")
        expected_backup = os.path.join("backup", f"{index_value:08d}")
        if operation["action"] == "replace":
            if staged != expected_staged:
                raise SyncError(
                    "The synchronization recovery journal contains an invalid "
                    "staged path"
                )
        elif staged is not None:
            raise SyncError(
                "The synchronization recovery journal contains an unexpected "
                "staged path"
            )
        if operation["existed"]:
            if backup != expected_backup:
                raise SyncError(
                    "The synchronization recovery journal contains an invalid "
                    "backup path"
                )
        elif backup is not None:
            raise SyncError(
                "The synchronization recovery journal contains an unexpected "
                "backup path"
            )
    if index.is_transaction_committed(journal["transaction_id"]):
        shutil.rmtree(transaction_dir, ignore_errors=True)
        os.remove(journal_path)
        return
    _restore_transaction(library_dir, journal["operations"], transaction_dir)
    shutil.rmtree(transaction_dir, ignore_errors=True)
    os.remove(journal_path)


def _commit_transaction(library_dir, index, replacements, deletions, snapshot):
    transaction_id, operations, transaction_dir, journal_path = _prepare_transaction(
        library_dir, replacements, deletions
    )
    try:
        for operation in operations:
            target_path = _managed_path(library_dir, operation["target"])
            if operation["action"] == "replace":
                staged_path = os.path.join(transaction_dir, operation["staged"])
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                os.replace(staged_path, target_path)
            else:
                try:
                    os.remove(target_path)
                except FileNotFoundError:
                    pass
            _fsync_directory_chain(os.path.dirname(target_path), library_dir)
        index.save_snapshot(
            snapshot["entries"],
            snapshot["directories"],
            snapshot["previous_directories"],
            snapshot["full_crawl"],
            snapshot["visited"],
            transaction_id=transaction_id,
        )
    except Exception:
        if not index.is_transaction_committed(transaction_id):
            _restore_transaction(library_dir, operations, transaction_dir)
            shutil.rmtree(transaction_dir, ignore_errors=True)
            try:
                os.remove(journal_path)
            except FileNotFoundError:
                pass
        raise
    shutil.rmtree(transaction_dir, ignore_errors=True)
    try:
        os.remove(journal_path)
    except FileNotFoundError:
        pass
