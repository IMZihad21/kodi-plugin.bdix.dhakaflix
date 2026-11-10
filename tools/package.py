#!/usr/bin/env python3
"""Validate, reproducibly package, and checksum the Kodi add-on ZIP."""

import argparse
import ast
import hashlib
import os
import tempfile
import xml.etree.ElementTree as ET
from contextlib import suppress
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
ADDON_ID = "plugin.bdix.dhakaflix"
ADDON_DIR = ROOT / ADDON_ID
PYTHON_API_VERSION = "3.0.0"
PYTHON_FEATURE_VERSION = (3, 8)


def validate():
    addon_xml = ADDON_DIR / "addon.xml"
    if not addon_xml.is_file():
        raise ValueError(f"Missing {addon_xml}")
    addon = ET.parse(str(addon_xml)).getroot()
    if addon.tag != "addon" or addon.attrib.get("id") != ADDON_ID:
        raise ValueError("addon.xml has an invalid add-on ID")
    version = addon.attrib.get("version", "")
    version_parts = version.split(".")
    if len(version_parts) != 3 or any(not part.isdigit() for part in version_parts):
        raise ValueError("addon.xml version must be numeric major.minor.patch")

    dependencies = {
        dependency.attrib.get("addon"): dependency.attrib.get("version")
        for dependency in addon.findall("./requires/import")
    }
    if dependencies.get("xbmc.python") != PYTHON_API_VERSION:
        raise ValueError(f"addon.xml must require xbmc.python {PYTHON_API_VERSION}")

    plugin_extensions = addon.findall("./extension[@point='xbmc.python.pluginsource']")
    if not any(
        extension.attrib.get("library") == "default.py"
        for extension in plugin_extensions
    ):
        raise ValueError("addon.xml must register default.py as a Kodi plugin source")
    service_extensions = addon.findall("./extension[@point='xbmc.service']")
    if not any(
        extension.attrib.get("library") == "service.py"
        for extension in service_extensions
    ):
        raise ValueError("addon.xml must register service.py as a Kodi service")
    if not (ADDON_DIR / "default.py").is_file():
        raise ValueError("Missing add-on entry point")
    if not (ADDON_DIR / "service.py").is_file():
        raise ValueError("Missing add-on service")
    if not (ADDON_DIR / "LICENSE").is_file():
        raise ValueError("Missing add-on license")
    icon = addon.find("./extension[@point='xbmc.addon.metadata']/assets/icon")
    if icon is None or not icon.text or not (ADDON_DIR / icon.text).is_file():
        raise ValueError("addon.xml must reference an existing icon asset")

    settings_path = ADDON_DIR / "resources" / "settings.xml"
    settings = ET.parse(str(settings_path)).getroot()
    if settings.tag != "settings" or settings.attrib.get("version") != "1":
        raise ValueError("Kodi settings must use settings version 1")
    section = settings.find("./section")
    if section is None or section.attrib.get("id") != ADDON_ID:
        raise ValueError("Kodi settings section must use the add-on ID")

    setting_ids = set()
    for setting in settings.findall(".//setting"):
        setting_id = setting.attrib.get("id")
        if not setting_id or setting_id in setting_ids:
            raise ValueError("Kodi settings contain a missing or duplicate setting ID")
        setting_ids.add(setting_id)
        if not setting.attrib.get("label") or setting.find("control") is None:
            raise ValueError(f"Kodi setting {setting_id} is missing a label or control")

    for source in ADDON_DIR.rglob("*.py"):
        if "__pycache__" in source.parts:
            continue
        code = source.read_text(encoding="utf-8")
        try:
            ast.parse(
                code,
                filename=str(source),
                feature_version=PYTHON_FEATURE_VERSION,
            )
        except SyntaxError as error:
            raise ValueError(
                f"{source.relative_to(ADDON_DIR)} is not compatible with Python "
                f"{PYTHON_FEATURE_VERSION[0]}.{PYTHON_FEATURE_VERSION[1]}"
            ) from error
    return version


def _write_checksum(output_path):
    digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
    checksum_path = output_path.with_suffix(output_path.suffix + ".sha256")
    checksum_path.write_text(
        f"{digest}  {output_path.name}\n",
        encoding="ascii",
    )
    return checksum_path


def build(output_path):
    version = validate()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=output_path.name + ".",
        suffix=".tmp",
        dir=str(output_path.parent),
    )
    os.close(descriptor)
    try:
        with ZipFile(temporary_path, "w", ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(ADDON_DIR.rglob("*")):
                if (
                    not path.is_file()
                    or "__pycache__" in path.parts
                    or path.suffix == ".pyc"
                ):
                    continue
                relative_path = Path(ADDON_ID) / path.relative_to(ADDON_DIR)
                info = ZipInfo(str(relative_path).replace(os.sep, "/"))
                info.date_time = (2020, 1, 1, 0, 0, 0)
                info.compress_type = ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(
                    info,
                    path.read_bytes(),
                    compress_type=ZIP_DEFLATED,
                    compresslevel=9,
                )
        with ZipFile(temporary_path, "r") as archive:
            corrupt_entry = archive.testzip()
            if corrupt_entry:
                raise ValueError(f"Invalid ZIP member: {corrupt_entry}")
            names = archive.namelist()
            if not names or any(not name.startswith(ADDON_ID + "/") for name in names):
                raise ValueError("ZIP must contain only the add-on ID directory")
            if ADDON_ID + "/addon.xml" not in names:
                raise ValueError("ZIP is missing addon.xml")
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, str(output_path))
    except Exception:
        with suppress(FileNotFoundError):
            os.remove(temporary_path)
        raise
    checksum_path = _write_checksum(output_path)
    return version, output_path, checksum_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="ZIP output path")
    arguments = parser.parse_args()
    version = validate()
    output_path = arguments.output or ROOT / "dist" / f"{ADDON_ID}-{version}.zip"
    version, output_path, checksum_path = build(output_path)
    print(f"Packaged {ADDON_ID} version {version} at {output_path}")
    print(f"SHA-256 checksum: {checksum_path}")


if __name__ == "__main__":
    main()
