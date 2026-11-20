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

from photo_voicenotes.paths import models_dir

DEFAULT_MODEL = "ggml-large-v3-turbo-q5_0.bin"
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
    candidates = [explicit, os.environ.get(MODEL_ENV), models_dir() / DEFAULT_MODEL]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise WhisperError(
        f"Could not find a Whisper model. Put {DEFAULT_MODEL} in {models_dir()}, "
        f"set {MODEL_ENV}, or pass --model."
    )


def dtw_preset(model: Path) -> str | None:
    """Word-alignment preset for a model file, e.g. ggml-large-v3-turbo-q5_0.bin -> large.v3.turbo."""
    name = re.sub(r"^ggml-", "", model.stem)
    name = re.sub(r"-q\d+_\d+$", "", name)
    preset = name.replace("-", ".")
    known = {"tiny", "tiny.en", "base", "base.en", "small", "small.en", "medium", "medium.en",
             "large.v1", "large.v2", "large.v3", "large.v3.turbo"}
    return preset if preset in known else None


def transcribe(audio: Path, output_json: Path, model: Path, whisper: Path, language: str = "auto",
               prompt: str = STYLE_PROMPT) -> None:
    """Transcribe audio to whisper.cpp's full JSON format, printing progress to stderr."""
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
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               text=True, errors="replace")
    log = []
    assert process.stderr is not None
    for line in process.stderr:
        log.append(line)
        if "progress =" in line:
            print(f"  {line.strip().split('progress =')[-1].strip()}", file=sys.stderr, flush=True)
    if process.wait() != 0 or not output_json.is_file():
        raise WhisperError("whisper-cli failed:\n" + "".join(log[-20:]))


def _is_phantom(tokens: list[dict]) -> bool:
    """True for text Whisper invented in silence, such as a closing "Thank you."

    Word alignment can't place invented words against any actual speech, so every token
    lands on the same instant; real speech, even two words, is always spread over time.
    """
    times = {t.get("t_dtw", -1) for t in tokens}
    return len(tokens) > 1 and len(times) == 1 and -1 not in times


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
            if text.startswith(" ") or not words:
                words.append([text.strip(), t])
            else:
                words[-1][0] += text
    return [Word(text, t) for text, t in words]
