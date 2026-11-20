"""Transcribing sessions in the background from the viewer, and the first-run model download."""

from __future__ import annotations

import threading
from collections import deque
from pathlib import Path

from PySide6.QtCore import QEventLoop, QObject, QProcess, Qt, Signal
from PySide6.QtWidgets import QMessageBox, QProgressDialog, QWidget

from photo_voicenotes.models import DownloadCancelled, DownloadError, ModelInfo, download
from photo_voicenotes.transcribe import TRANSCRIPT, CsvResult, load_session, session_csv
from photo_voicenotes.whisper import parse_progress, whisper_command


class SessionTranscriber(QObject):
    """Runs whisper-cli on one session at a time, then writes its CSV."""

    progress = Signal(int)  # percent of the current session
    finished = Signal(object)  # CsvResult
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._queue: deque[tuple[Path, Path, Path]] = deque()
        self._process: QProcess | None = None
        self._folder: Path | None = None
        self._log: list[str] = []

    @property
    def busy(self) -> bool:
        return self._process is not None

    @property
    def pending(self) -> int:
        return len(self._queue) + (1 if self.busy else 0)

    def enqueue(self, folder: Path, whisper: Path, model: Path) -> None:
        self._queue.append((folder, whisper, model))
        if not self.busy:
            self._start_next()

    def stop(self) -> None:
        self._queue.clear()
        if self._process is not None:
            self._process.kill()
            self._process.waitForFinished(3000)

    def _start_next(self) -> None:
        if not self._queue:
            return
        folder, whisper, model = self._queue.popleft()
        try:
            metadata, _ = load_session(folder)
        except (OSError, ValueError) as exc:
            self.failed.emit(f"Could not read the recording in {folder}: {exc}")
            self._start_next()
            return
        command = whisper_command(folder / metadata["audio"], folder / TRANSCRIPT, model, whisper)
        self._folder, self._log = folder, []
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self._process.setStandardOutputFile(QProcess.nullDevice())
        self._process.readyReadStandardError.connect(self._on_stderr)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error)
        self.progress.emit(0)
        self._process.start(command[0], command[1:])

    def _on_stderr(self) -> None:
        assert self._process is not None
        for line in bytes(self._process.readAllStandardError()).decode(errors="replace").splitlines():
            self._log.append(line)
            percent = parse_progress(line)
            if percent is not None:
                self.progress.emit(percent)

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._done(f"Could not start whisper-cli: {self._process.errorString() if self._process else error}")

    def _on_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        if self._process is None:
            return
        assert self._folder is not None
        if exit_status != QProcess.ExitStatus.NormalExit or exit_code != 0:
            self._done("Transcription failed:\n" + "\n".join(self._log[-10:]))
            return
        try:
            result = session_csv(self._folder)
        except (OSError, ValueError, KeyError) as exc:
            self._done(f"Could not write the CSV: {exc}")
            return
        self._done(result)

    def _done(self, outcome: CsvResult | str) -> None:
        if self._process is not None:
            self._process.deleteLater()
        self._process, self._folder = None, None
        if isinstance(outcome, str):
            self.failed.emit(outcome)
        else:
            self.finished.emit(outcome)
        self._start_next()


class _DownloadBridge(QObject):
    """Carries progress from the download thread to the dialog on the GUI thread."""

    progress = Signal(int, int)
    done = Signal(object)  # None on success, else an exception


def ensure_model(parent: QWidget, model: ModelInfo) -> bool:
    """Make sure the model is downloaded, asking first. Returns False if it isn't."""
    if model.path.is_file():
        return True
    answer = QMessageBox.question(
        parent, "Download transcription model",
        f"Transcription needs a one-off download of the {model.key} model "
        f"({model.size_mb} MB) from Hugging Face.\n\n"
        "After that, everything runs on this computer without the internet.\n\nDownload now?",
    )
    if answer != QMessageBox.StandardButton.Yes:
        return False

    dialog = QProgressDialog(f"Downloading the {model.key} model…", "Cancel", 0, model.size_mb, parent)
    dialog.setWindowTitle("Download transcription model")
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    dialog.setMinimumDuration(0)
    dialog.setAutoReset(False)
    dialog.setAutoClose(False)

    cancel = threading.Event()
    bridge = _DownloadBridge()
    loop = QEventLoop()
    outcome: list[object] = []

    def on_progress(done: int, total: int) -> None:
        dialog.setMaximum(max(1, round(total / 1e6)))
        dialog.setValue(round(done / 1e6))
        dialog.setLabelText(f"Downloading the {model.key} model… {done / 1e6:.0f} of {total / 1e6:.0f} MB")

    def on_done(error: object) -> None:
        outcome.append(error)
        loop.quit()

    def worker() -> None:
        try:
            download(model, bridge.progress.emit, cancel)
            bridge.done.emit(None)
        except DownloadError as exc:
            bridge.done.emit(exc)

    bridge.progress.connect(on_progress)
    bridge.done.connect(on_done)
    dialog.canceled.connect(cancel.set)
    threading.Thread(target=worker, daemon=True).start()
    loop.exec()
    dialog.close()

    error = outcome[0] if outcome else None
    if isinstance(error, DownloadCancelled):
        return False
    if isinstance(error, Exception):
        QMessageBox.warning(parent, "Download failed", str(error))
        return False
    return True
