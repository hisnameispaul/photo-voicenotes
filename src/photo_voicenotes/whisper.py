"""Running whisper.cpp's command-line tool and reading its word timings."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from photo_voicenotes.models import selected_model

WHISPER_ENV = "PHOTO_VOICENOTES_WHISPER"
MODEL_ENV = "PHOTO_VOICENOTES_MODEL"
# Whisper copies the style of the text before it. Casual speech can start it off in lower
# case with no punctuation, which then sticks; a punctuated example keeps it in sentences.
STYLE_PROMPT = "OK, here's the next one. This is Mum, Dad and Gran on holiday in the summer of 1975."

# A source checkout keeps its own whisper.cpp build here (see README).
_CHECKOUT_BUILD = Path(__file__).resolve().parents[2] / "tools" / "whisper.cpp" / "build" / "bin"


class WhisperError(RuntimeError):
    pass


@dataclass(frozen=True)
class Word:
    text: str
    t: float  # seconds into the audio


def find_whisper(explicit: Path | None = None) -> Path:
    candidates = [explicit, os.environ.get(WHISPER_ENV), shutil.which("whisper-cli")]
    candidates += [_CHECKOUT_BUILD / name for name in ("whisper-cli", "whisper-cli.exe")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise WhisperError(
        f"Could not find whisper-cli. Put it on PATH, set {WHISPER_ENV}, or pass --whisper."
    )


def find_model(explicit: Path | None = None) -> Path:
    """An explicitly chosen model file, or else the selected model's (possibly not yet
    downloaded) location."""
    override = explicit or os.environ.get(MODEL_ENV)
    if override:
        if not Path(override).is_file():
            raise WhisperError(f"Model file not found: {override}")
        return Path(override)
    return selected_model().path


def dtw_preset(model: Path) -> str | None:
    """Word-alignment preset for a model file, e.g. ggml-large-v3-turbo-q5_0.bin -> large.v3.turbo."""
    name = re.sub(r"^ggml-", "", model.stem)
    name = re.sub(r"-q\d+_\d+$", "", name)
    preset = name.replace("-", ".")
    known = {"tiny", "tiny.en", "base", "base.en", "small", "small.en", "medium", "medium.en",
             "large.v1", "large.v2", "large.v3", "large.v3.turbo"}
    return preset if preset in known else None


def whisper_command(audio: Path, output_json: Path, model: Path, whisper: Path,
                    language: str = "auto", prompt: str = STYLE_PROMPT) -> list[str]:
    """whisper-cli arguments that write whisper.cpp's full JSON format to output_json."""
    command = [
        str(whisper), "-m", str(model), "-f", str(audio), "-l", language,
        "-ojf", "-of", str(output_json.with_suffix("")), "-pp",
    ]
    if prompt:
        command += ["--prompt", prompt]
    preset = dtw_preset(model)
    if preset:
        # DTW gives much more accurate word timings; it requires flash attention off.
        command += ["-dtw", preset, "-nfa"]
    return command


def parse_progress(line: str) -> int | None:
    """Percentage from a whisper-cli progress line such as "...: progress =  45%"."""
    match = re.search(r"progress\s*=\s*(\d+)%", line)
    return int(match.group(1)) if match else None


def transcribe(audio: Path, output_json: Path, model: Path, whisper: Path, language: str = "auto") -> None:
    """Transcribe audio, printing progress to stderr."""
    command = whisper_command(audio, output_json, model, whisper, language)
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               text=True, errors="replace")
    log = []
    assert process.stderr is not None
    for line in process.stderr:
        log.append(line)
        percent = parse_progress(line)
        if percent is not None:
            print(f"  {percent}%", file=sys.stderr, flush=True)
    if process.wait() != 0 or not output_json.is_file():
        raise WhisperError("whisper-cli failed:\n" + "".join(log[-20:]))


def _is_phantom(tokens: list[dict]) -> bool:
    """True for text Whisper invented in silence, such as a closing "Thank you."

    Word alignment can't place invented words against any actual speech, so every word
    lands on the same instant; real speech, even two words, is always spread over time.
    Punctuation is ignored, as it can be placed a moment apart even in invented text.
    """
    spoken = [t for t in tokens if any(c.isalnum() for c in t["text"])]
    times = {t.get("t_dtw", -1) for t in spoken}
    return len(spoken) > 1 and len(times) == 1 and -1 not in times


def read_words(transcript_json: Path) -> list[Word]:
    """Join whisper's sub-word tokens into words, each timed by its first token."""
    data = json.loads(transcript_json.read_text(encoding="utf-8", errors="replace"))
    words: list[list] = []
    for segment in data["transcription"]:
        # Skip special tokens such as [_BEG_] and timestamps.
        tokens = [t for t in segment["tokens"] if not t["text"].startswith("[_") and t["text"].strip()]
        if _is_phantom(tokens):
            continue
        for token in tokens:
            text = token["text"]
            dtw = token.get("t_dtw", -1)
            t = dtw / 100 if dtw >= 0 else token["offsets"]["from"] / 1000
            if not any(c.isalnum() for c in text):
                # Punctuation belongs to the word before it; on its own it is noise.
                if words:
                    words[-1][0] += text.strip()
            elif text.startswith(" ") or not words:
                words.append([text.strip(), t])
            else:
                words[-1][0] += text
    return [Word(text, t) for text, t in words]
