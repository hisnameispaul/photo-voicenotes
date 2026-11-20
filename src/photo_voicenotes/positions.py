"""Remembering the last photo viewed in each folder.

Stored in the app's data folder, never in the photo folder.
"""

from __future__ import annotations

import json
from pathlib import Path

from photo_voicenotes.paths import data_dir


def _file() -> Path:
    return data_dir() / "last-viewed.json"


def _load() -> dict[str, str]:
    try:
        positions = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return positions if isinstance(positions, dict) else {}


def last_viewed(folder: Path) -> str | None:
    """Filename of the photo last shown in this folder, if any."""
    name = _load().get(str(folder))
    return name if isinstance(name, str) else None


def remember(folder: Path, image: Path) -> None:
    positions = _load()
    positions[str(folder)] = image.name
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write then rename, so a crash mid-write can't leave a corrupt file.
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(positions, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)
