"""A recording session: the audio file plus a log of which image was on screen when.

Each session lives in its own folder:
    audio.wav       the narration
    events.jsonl    one line per image shown: {"t": seconds_into_audio, "image": filename}
    session.json    metadata (image folder, start time, duration)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


class Session:
    def __init__(self, sessions_root: Path, image_folder: Path) -> None:
        self.started = datetime.now().astimezone()
        self.folder = sessions_root / self.started.strftime("%Y-%m-%d_%H%M%S")
        self.folder.mkdir(parents=True, exist_ok=False)
        self.image_folder = image_folder
        self.audio_path = self.folder / "audio.wav"
        self._events = (self.folder / "events.jsonl").open("a", encoding="utf-8")
        self._write_metadata(duration=None)

    def log_image(self, seconds: float, image: Path) -> None:
        # Flushed per line so a crash mid-session loses nothing already narrated.
        self._events.write(json.dumps({"t": round(seconds, 3), "image": image.name}) + "\n")
        self._events.flush()

    def close(self, duration: float) -> None:
        self._events.close()
        self._write_metadata(duration=round(duration, 3))

    def _write_metadata(self, duration: float | None) -> None:
        metadata = {
            "version": 1,
            "image_folder": str(self.image_folder),
            "started": self.started.isoformat(timespec="seconds"),
            "audio": self.audio_path.name,
            "duration": duration,
        }
        (self.folder / "session.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
