"""Finding images in a folder and parsing scan filenames like "#69 2 721.tif"."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

IMAGE_SUFFIXES = {".tif", ".tiff", ".jpg", ".jpeg", ".png"}

# "#69 2 721" -> roll 69, frame 2, sequence 721. The "#" and extra spaces are optional.
_SCAN_NAME = re.compile(r"#?\s*(\d+)\s+(\d+)\s+(\d+)")


@dataclass(frozen=True)
class ScanName:
    roll: int
    frame: int
    sequence: int


def parse_scan_name(path: Path) -> ScanName | None:
    match = _SCAN_NAME.fullmatch(path.stem.strip())
    if match is None:
        return None
    return ScanName(*(int(g) for g in match.groups()))


def _natural_key(text: str) -> list[int | str]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


def _sort_key(path: Path) -> tuple:
    # Scan-named files first, in roll/frame/sequence order; anything else after, naturally sorted.
    scan = parse_scan_name(path)
    if scan is not None:
        return (0, scan.roll, scan.frame, scan.sequence)
    return (1, _natural_key(path.name))


def list_images(folder: Path) -> list[Path]:
    images = [
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES and not p.name.startswith(".")
    ]
    return sorted(images, key=_sort_key)


def find_photos(images: list[Path], query: str) -> list[int]:
    """Indices of photos matching what the user typed.

    A number matches the sequence number ("743"); a full scan name matches exactly
    ("69 2 721" or "#69 2 721"); anything else matches part of the filename.
    """
    query = query.strip()
    if query.isdigit():
        number = int(query)
        return [i for i, p in enumerate(images) if (scan := parse_scan_name(p)) and scan.sequence == number]
    wanted = parse_scan_name(Path(query))
    if wanted is not None:
        return [i for i, p in enumerate(images) if parse_scan_name(p) == wanted]
    lowered = query.lower()
    return [i for i, p in enumerate(images) if lowered and lowered in p.name.lower()]
