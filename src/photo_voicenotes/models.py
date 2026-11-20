"""Whisper models the app can use, and downloading them on first use."""

from __future__ import annotations

import hashlib
import threading
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSettings

from photo_voicenotes.paths import APP_NAME, models_dir

# Pinned so the checksums below always match what is downloaded.
_HF_REVISION = "5359861c739e955e79d9a303bcbc70fb988958b1"
_HF_URL = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/{_HF_REVISION}/"
_SETTING = "transcription/model"
_CHUNK = 1 << 20


@dataclass(frozen=True)
class ModelInfo:
    key: str
    label: str
    filename: str
    size: int
    sha256: str

    @property
    def url(self) -> str:
        return _HF_URL + self.filename

    @property
    def path(self) -> Path:
        return models_dir() / self.filename

    @property
    def size_mb(self) -> int:
        return round(self.size / 1e6)


MODELS = {
    m.key: m for m in [
        ModelInfo("large-v3-turbo", "Accurate (large-v3-turbo): best with a graphics card",
                  "ggml-large-v3-turbo-q5_0.bin", 574_041_195,
                  "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2"),
        ModelInfo("small", "Fast (small): for slower computers, less accurate with names",
                  "ggml-small-q5_1.bin", 190_085_487,
                  "ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb"),
    ]
}
DEFAULT_MODEL = "large-v3-turbo"


class DownloadError(RuntimeError):
    pass


class DownloadCancelled(DownloadError):
    pass


def _settings() -> QSettings:
    return QSettings(APP_NAME, APP_NAME)


def selected_model() -> ModelInfo:
    key = _settings().value(_SETTING, DEFAULT_MODEL)
    return MODELS.get(str(key), MODELS[DEFAULT_MODEL])


def select_model(key: str) -> None:
    _settings().setValue(_SETTING, MODELS[key].key)


def download(model: ModelInfo, progress: Callable[[int, int], None] | None = None,
             cancel: threading.Event | None = None) -> Path:
    """Download a model, verifying its checksum. Returns the installed path.

    Data goes to a .part file that is renamed only once complete and verified, so an
    interrupted download never leaves a broken model in place.
    """
    target = model.path
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    done = 0
    try:
        with urllib.request.urlopen(model.url, timeout=30) as response, partial.open("wb") as out:
            total = int(response.headers.get("Content-Length") or model.size)
            while chunk := response.read(_CHUNK):
                if cancel is not None and cancel.is_set():
                    raise DownloadCancelled("Download cancelled")
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
    except DownloadError:
        partial.unlink(missing_ok=True)
        raise
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise DownloadError(f"Could not download {model.filename}: {exc}") from exc

    if digest.hexdigest() != model.sha256:
        partial.unlink(missing_ok=True)
        raise DownloadError(f"{model.filename} was corrupted during download; please try again")
    partial.replace(target)
    return target
