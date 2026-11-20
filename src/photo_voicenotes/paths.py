"""Per-user data locations, following each platform's conventions."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QStandardPaths

APP_NAME = "photo-voicenotes"


def data_dir() -> Path:
    """e.g. ~/.local/share/photo-voicenotes on Linux, %APPDATA%/photo-voicenotes on Windows."""
    QCoreApplication.setApplicationName(APP_NAME)
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))


def sessions_dir() -> Path:
    return data_dir() / "sessions"


def models_dir() -> Path:
    return data_dir() / "models"
