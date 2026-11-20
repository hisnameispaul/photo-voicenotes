"""Transcribe a recording session and write a CSV with one row per photo."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from photo_voicenotes.align import Event, PhotoNotes, align
from photo_voicenotes.filenames import parse_scan_name
from photo_voicenotes.paths import sessions_dir
from photo_voicenotes.whisper import WhisperError, find_model, find_whisper, read_words, transcribe

TRANSCRIPT = "transcript.json"
CSV_COLUMNS = ["filename", "roll", "frame", "sequence", "description", "people", "place", "date",
               "shown_at", "seconds_on_screen"]


def latest_session() -> Path:
    root = sessions_dir()
    sessions = sorted(p for p in root.iterdir() if (p / "session.json").is_file()) if root.is_dir() else []
    if not sessions:
        raise SystemExit(f"No recording sessions found in {root}")
    return sessions[-1]


def load_session(folder: Path) -> tuple[dict, list[Event]]:
    metadata = json.loads((folder / "session.json").read_text(encoding="utf-8"))
    events = []
    with (folder / "events.jsonl").open(encoding="utf-8") as lines:
        for line in lines:
            if line.strip():
                record = json.loads(line)
                events.append(Event(record["t"], record["image"]))
    return metadata, events


def _timestamp(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}:{secs:02d}"


def write_csv(path: Path, notes: list[PhotoNotes]) -> None:
    # utf-8-sig so Excel recognises the encoding; LibreOffice and others ignore the marker.
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for note in notes:
            scan = parse_scan_name(Path(note.image))
            writer.writerow({
                "filename": note.image,
                "roll": scan.roll if scan else "",
                "frame": scan.frame if scan else "",
                "sequence": scan.sequence if scan else "",
                "description": note.description,
                "shown_at": _timestamp(note.first_shown),
                "seconds_on_screen": f"{note.seconds_on_screen:.1f}",
            })


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="photo-voicenotes-transcribe", description=__doc__)
    parser.add_argument("session", nargs="?", type=Path, help="session folder (default: most recent)")
    parser.add_argument("-o", "--output", type=Path, help="CSV path (default: photos.csv in the session folder)")
    parser.add_argument("--model", type=Path, help="Whisper ggml model file")
    parser.add_argument("--whisper", type=Path, help="path to whisper.cpp's whisper-cli")
    parser.add_argument("--language", default="auto", help="spoken language code, e.g. en (default: detect)")
    parser.add_argument("--retranscribe", action="store_true", help="run Whisper again even if a transcript exists")
    args = parser.parse_args(argv)

    folder = args.session.expanduser().resolve() if args.session else latest_session()
    metadata, events = load_session(folder)
    if not events:
        raise SystemExit(f"{folder} has no photo events")
    transcript = folder / TRANSCRIPT

    if args.retranscribe or not transcript.is_file():
        try:
            whisper, model = find_whisper(args.whisper), find_model(args.model)
            print(f"Transcribing {folder / metadata['audio']} with {model.name}…", file=sys.stderr)
            transcribe(folder / metadata["audio"], transcript, model, whisper, args.language)
        except WhisperError as exc:
            raise SystemExit(str(exc)) from exc

    duration = metadata.get("duration") or max(e.t for e in events)
    notes = align(events, read_words(transcript), duration)
    output = args.output or folder / "photos.csv"
    write_csv(output, notes)
    described = sum(1 for n in notes if n.description)
    print(f"Wrote {output}: {len(notes)} photos, {described} with descriptions", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
