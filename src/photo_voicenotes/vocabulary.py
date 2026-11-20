"""The shared list of names, places and other words whose spelling the transcript is corrected to.

The list lives in the app's data folder as names.txt, one entry per line.
"""

from __future__ import annotations

from pathlib import Path

from photo_voicenotes.paths import data_dir


def names_file() -> Path:
    return data_dir() / "names.txt"


def load_names() -> list[str]:
    try:
        text = names_file().read_text(encoding="utf-8")
    except OSError:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def save_names(names: list[str]) -> None:
    path = names_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{name.strip()}\n" for name in names if name.strip()), encoding="utf-8")
