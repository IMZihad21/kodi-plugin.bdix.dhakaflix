"""Kodi video-source registration and generated library paths."""

import os
import stat
import tempfile
import xml.etree.ElementTree as ET
from contextlib import suppress

ADDON_ID = "plugin.bdix.dhakaflix"


class SourceRegistrationError(RuntimeError):
    """Raised when Kodi sources cannot be safely updated."""


class SyncRequestError(OSError):
    """Raised when a manual sync request cannot be queued."""


def library_paths(addon_profile):
    root = os.path.join(addon_profile, "library")
    return {
        "movies": os.path.join(root, "Movies"),
        "tv": os.path.join(root, "TV Shows"),
        "state": root,
    }


def ensure_library_paths(paths):
    for key in ("movies", "tv"):
        path = paths[key]
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as error:
            raise SourceRegistrationError(
                f"Unable to create the DhakaFlix {key} library path {path}: {error}"
            ) from error
        if not os.path.isdir(path):
            raise SourceRegistrationError(
                f"The DhakaFlix {key} library path is not a directory: {path}"
            )
        if not os.access(path, os.W_OK):
            raise SourceRegistrationError(
                f"The DhakaFlix {key} library path is not writable: {path}"
            )


def enqueue_manual_sync(library_dir):
    request_path = os.path.join(library_dir, "sync.request")
    try:
        os.makedirs(library_dir, exist_ok=True)
        descriptor = os.open(
            request_path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        )
        with os.fdopen(descriptor, "w", encoding="ascii") as request:
            request.write("requested\n")
            request.flush()
            os.fsync(request.fileno())
    except FileExistsError:
        return False
    except OSError as error:
        raise SyncRequestError(
            f"Unable to queue a manual sync request: {error}"
        ) from error
    return True


def _normalized_path(path):
    return os.path.normcase(os.path.normpath(path)).rstrip("\\/")


def _read_sources(path):
    try:
        if os.path.islink(path):
            raise SourceRegistrationError(
                "Kodi sources.xml is a symbolic link and was not modified"
            )
        with open(path, "rb") as source:
            original = source.read()
        metadata = os.stat(path)
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        tree = ET.ElementTree(ET.fromstring(original, parser=parser))
        root = tree.getroot()
        if root.tag != "sources":
            raise SourceRegistrationError(
                "Kodi sources.xml has an unexpected root element"
            )
        return tree, root, original, metadata
    except FileNotFoundError:
        return ET.ElementTree(ET.Element("sources")), None, None, None
    except ET.ParseError as error:
        raise SourceRegistrationError(
            f"Kodi sources.xml is malformed and was not changed: {error}"
        ) from error
    except OSError as error:
        raise SourceRegistrationError(
            f"Unable to read Kodi sources.xml: {error}"
        ) from error


def _atomic_write(path, content, mode=None, metadata=None):
    parent = os.path.dirname(path)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=".dhakaflix-sources-", suffix=".tmp", dir=parent
    )
    try:
        if mode is not None:
            os.chmod(temporary_path, stat.S_IMODE(mode))
        with os.fdopen(descriptor, "wb") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        if metadata is not None:
            with suppress(OSError):
                os.utime(
                    temporary_path,
                    ns=(metadata.st_atime_ns, metadata.st_mtime_ns),
                )
        os.replace(temporary_path, path)
        if os.name != "nt":
            with suppress(OSError):
                directory_descriptor = os.open(parent, os.O_RDONLY)
                try:
                    os.fsync(directory_descriptor)
                finally:
                    os.close(directory_descriptor)
    except Exception:
        with suppress(FileNotFoundError):
            os.remove(temporary_path)
        raise


def _same_file_snapshot(path, original, metadata):
    try:
        current_metadata = os.stat(path)
        with open(path, "rb") as source:
            current = source.read()
    except FileNotFoundError:
        return False
    return (
        current == original
        and current_metadata.st_dev == metadata.st_dev
        and current_metadata.st_ino == metadata.st_ino
        and current_metadata.st_size == metadata.st_size
        and current_metadata.st_mtime_ns == metadata.st_mtime_ns
    )


def _source_paths(video_element):
    found = set()
    for source in video_element.findall("source"):
        for path_element in source.findall("path"):
            if path_element.text and path_element.text.strip():
                found.add(_normalized_path(path_element.text.strip()))
    return found


def _create_source(video_element, name, path):
    source = ET.SubElement(video_element, "source")
    ET.SubElement(source, "name").text = name
    path_element = ET.SubElement(source, "path", {"pathversion": "1"})
    path_element.text = os.path.abspath(path)
    ET.SubElement(source, "allowsharing").text = "true"


def register_sources(sources_xml_path, movies_path, tv_path):
    sources_xml_path = os.path.abspath(sources_xml_path)
    tree, root, original, metadata = _read_sources(sources_xml_path)
    if root is None:
        root = tree.getroot()
    video = root.find("video")
    if video is None:
        video = ET.SubElement(root, "video")

    desired = (
        ("DhakaFlix Movies", movies_path),
        ("DhakaFlix TV Shows", tv_path),
    )
    existing_paths = _source_paths(video)
    changed = False
    for name, path in desired:
        absolute_path = os.path.abspath(path)
        normalized_path = _normalized_path(absolute_path)
        matching_named = [
            source
            for source in video.findall("source")
            if source.findtext("name", default="").strip().casefold() == name.casefold()
        ]
        if matching_named:
            named_paths = {
                _normalized_path(path_element.text.strip())
                for source in matching_named
                for path_element in source.findall("path")
                if path_element.text and path_element.text.strip()
            }
            if normalized_path not in named_paths:
                raise SourceRegistrationError(
                    f"Kodi already has a source named {name!r} with a different path"
                )
            existing_paths.add(normalized_path)
            continue
        if normalized_path in existing_paths:
            continue
        _create_source(video, name, absolute_path)
        existing_paths.add(normalized_path)
        changed = True

    if not changed:
        return False

    parent = os.path.dirname(sources_xml_path)
    try:
        os.makedirs(parent, exist_ok=True)
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=".sources-dhakaflix-", suffix=".xml", dir=parent
        )
        try:
            with os.fdopen(descriptor, "wb") as target:
                target.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
                tree.write(target, encoding="utf-8", xml_declaration=False)
                target.flush()
                os.fsync(target.fileno())
            ET.parse(temporary_path)
            if original is not None:
                if not _same_file_snapshot(sources_xml_path, original, metadata):
                    raise SourceRegistrationError(
                        "Kodi sources.xml changed during registration; no changes were applied"
                    )
                backup_path = sources_xml_path + ".dhakaflix.bak"
                _atomic_write(
                    backup_path,
                    original,
                    mode=metadata.st_mode,
                    metadata=metadata,
                )
                if not _same_file_snapshot(sources_xml_path, original, metadata):
                    raise SourceRegistrationError(
                        "Kodi sources.xml changed before registration could be committed"
                    )
                os.chmod(temporary_path, stat.S_IMODE(metadata.st_mode))
            elif os.path.exists(sources_xml_path):
                raise SourceRegistrationError(
                    "Kodi sources.xml was created during registration; no changes were applied"
                )
            os.replace(temporary_path, sources_xml_path)
            if os.name != "nt":
                with suppress(OSError):
                    directory_descriptor = os.open(parent, os.O_RDONLY)
                    try:
                        os.fsync(directory_descriptor)
                    finally:
                        os.close(directory_descriptor)
        except Exception:
            with suppress(FileNotFoundError):
                os.remove(temporary_path)
            raise
    except SourceRegistrationError:
        raise
    except (OSError, ET.ParseError) as error:
        raise SourceRegistrationError(
            f"Unable to safely register DhakaFlix library paths: {error}"
        ) from error
    return True
