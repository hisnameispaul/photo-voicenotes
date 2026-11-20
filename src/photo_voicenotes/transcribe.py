"""Transcribe a recording session and write a CSV with one row per photo."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from photo_voicenotes.align import Event, PhotoNotes, align
from photo_voicenotes.corrections import Correction, correct, summary
from photo_voicenotes.models import DownloadError, download, selected_model
from photo_voicenotes.paths import sessions_dir
from photo_voicenotes.vocabulary import load_names
from photo_voicenotes.whisper import WhisperError, find_model, find_whisper, read_words, transcribe

TRANSCRIPT = "transcript.json"
CSV_NAME = "photos.csv"
HEARD_CSV_NAME = "photos-as-heard.csv"  # Whisper's text before name corrections
CORRECTIONS_NAME = "corrections.txt"
CSV_COLUMNS = ["filename", "description"]


@dataclass(frozen=True)
class CsvResult:
    path: Path
    photos: int
    described: int
    corrections: list[Correction]


def latest_session() -> Path | None:
    root = sessions_dir()
    if not root.is_dir():
        return None
    sessions = sorted(p for p in root.iterdir() if (p / "session.json").is_file())
    return sessions[-1] if sessions else None


def load_session(folder: Path) -> tuple[dict, list[Event]]:
    metadata = json.loads((folder / "session.json").read_text(encoding="utf-8"))
    events = []
    with (folder / "events.jsonl").open(encoding="utf-8") as lines:
        for line in lines:
            if line.strip():
                record = json.loads(line)
                events.append(Event(record["t"], record["image"]))
    return metadata, events


def write_csv(path: Path, rows: list[tuple[str, str]]) -> None:
    # utf-8-sig so Excel recognises the encoding; LibreOffice and others ignore the marker.
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for image, description in rows:
            writer.writerow({"filename": image, "description": description})


def write_corrections(path: Path, corrections: list[Correction]) -> None:
    lines = [f"{c.heard} → {c.corrected}: {', '.join(c.images)}" for c in corrections]
    path.write_text("\n".join(lines or ["No corrections were needed."]) + "\n", encoding="utf-8")


def session_csv(folder: Path, output: Path | None = None, names: list[str] | None = None) -> CsvResult:
    """Match a session's existing transcript to its photos, correct the spelling of names
    from the names list, and write the CSVs. Always starts again from Whisper's transcript,
    so it can be re-run after the names list changes."""
    metadata, events = load_session(folder)
    if not events:
        raise ValueError(f"{folder} has no photo events")
    duration = metadata.get("duration") or max(e.t for e in events)
    notes = align(events, read_words(folder / TRANSCRIPT), duration)
    names = load_names() if names is None else names

    heard, corrected = [], []
    found: dict[tuple[str, str], Correction] = {}
    for note in notes:
        text, changes = correct(note.description, names)
        heard.append((note.image, note.description))
        corrected.append((note.image, text))
        for before, after in changes:
            entry = found.setdefault((before, after), Correction(before, after))
            if note.image not in entry.images:
                entry.images.append(note.image)

    output = output or folder / CSV_NAME
    write_csv(output, corrected)
    write_csv(folder / HEARD_CSV_NAME, heard)
    corrections = list(found.values())
    write_corrections(folder / CORRECTIONS_NAME, corrections)
    return CsvResult(output, len(notes), sum(1 for n in notes if n.description), corrections)


def _print_download_progress(done: int, total: int) -> None:
    print(f"\r  {done / 1e6:.0f} / {total / 1e6:.0f} MB", end="", file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="photo-voicenotes-transcribe", description=__doc__)
    parser.add_argument("session", nargs="?", type=Path, help="session folder (default: most recent)")
    parser.add_argument("-o", "--output", type=Path, help=f"CSV path (default: {CSV_NAME} in the session folder)")
    parser.add_argument("--model", type=Path, help="Whisper ggml model file (default: the app's selected model)")
    parser.add_argument("--whisper", type=Path, help="path to whisper.cpp's whisper-cli")
    parser.add_argument("--language", default="auto", help="spoken language code, e.g. en (default: detect)")
    parser.add_argument("--retranscribe", action="store_true",
                        help="run Whisper again even if a transcript exists (without it, an existing "
                             "transcript is reused, which re-applies the current names list)")
    args = parser.parse_args(argv)

    folder = args.session.expanduser().resolve() if args.session else latest_session()
    if folder is None:
        raise SystemExit(f"No recording sessions found in {sessions_dir()}")
    metadata, _ = load_session(folder)
    transcript = folder / TRANSCRIPT

    if args.retranscribe or not transcript.is_file():
        try:
            whisper, model = find_whisper(args.whisper), find_model(args.model)
            if not model.is_file():
                info = selected_model()
                print(f"Downloading the {info.key} model ({info.size_mb} MB)…", file=sys.stderr)
                download(info, _print_download_progress)
                print(file=sys.stderr)
            print(f"Transcribing {folder / metadata['audio']} with {model.name}…", file=sys.stderr)
            transcribe(folder / metadata["audio"], transcript, model, whisper, args.language)
        except (WhisperError, DownloadError) as exc:
            raise SystemExit(str(exc)) from exc

    try:
        result = session_csv(folder, args.output)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Wrote {result.path}: {result.photos} photos, {result.described} with descriptions", file=sys.stderr)
    if result.corrections:
        print(f"Corrected: {summary(result.corrections)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
