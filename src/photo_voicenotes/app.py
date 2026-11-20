"""Full-screen photo viewer that records narration and logs which photo is on screen."""

from __future__ import annotations

import argparse
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, QSize, QStandardPaths, Qt, QTimer, Signal
from PySide6.QtGui import (QActionGroup, QColor, QFont, QGuiApplication, QImage, QImageReader, QKeyEvent,
                           QPainter, QPixmap)
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMenu, QMessageBox, QWidget

from photo_voicenotes import positions
from photo_voicenotes.filenames import IMAGE_SUFFIXES, find_photos, list_images
from photo_voicenotes.models import MODELS, select_model, selected_model
from photo_voicenotes.names_dialog import NamesDialog
from photo_voicenotes.paths import APP_NAME, sessions_dir
from photo_voicenotes.recorder import RecorderError, WavRecorder
from photo_voicenotes.session import Session
from photo_voicenotes.corrections import summary
from photo_voicenotes.transcribe import (CSV_NAME, TRANSCRIPT, CsvResult, latest_session, load_session,
                                         session_csv)
from photo_voicenotes.transcriber import SessionTranscriber, ensure_model
from photo_voicenotes.whisper import WhisperError, find_model, find_whisper

PREFETCH = 2  # images kept decoded either side of the current one
HINT = "←/→ navigate · G go to photo · R record · T transcribe · right-click for more"


class ImageLoader(QObject):
    """Decodes images on worker threads, downscaled to screen size to keep memory small."""

    loaded = Signal(object, QImage, str)  # path, image (null on failure), error message

    def __init__(self, target: QSize, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._target = target
        self._pool = ThreadPoolExecutor(max_workers=2)
        self._pending: set[Path] = set()

    def request(self, path: Path) -> None:
        if path not in self._pending:
            self._pending.add(path)
            self._pool.submit(self._load, path)

    def done(self, path: Path) -> None:
        self._pending.discard(path)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _load(self, path: Path) -> None:
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            self.loaded.emit(path, QImage(), reader.errorString())
            return
        if image.width() > self._target.width() or image.height() > self._target.height():
            image = image.scaled(self._target, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        self.loaded.emit(path, image.convertToFormat(QImage.Format.Format_RGB32), "")


class ViewerWindow(QWidget):
    def __init__(self, sessions_root: Path) -> None:
        super().__init__()
        self.setWindowTitle("Photo Voicenotes")
        self.setMinimumSize(640, 480)

        self._sessions_root = sessions_root
        self._folder: Path | None = None
        self._images: list[Path] = []
        self._index = 0
        self._cache: dict[Path, QImage] = {}
        self._errors: dict[Path, str] = {}
        self._scaled: tuple[Path, QSize, QPixmap] | None = None

        screen = QGuiApplication.primaryScreen()
        target = screen.size() * screen.devicePixelRatio() if screen else QSize(3840, 2160)
        self._loader = ImageLoader(target, self)
        self._loader.loaded.connect(self._on_loaded)

        self._recorder = WavRecorder(self)
        self._session: Session | None = None
        self._rec_timer = QTimer(self, interval=500)
        self._rec_timer.timeout.connect(self.update)
        self._last_session: Path | None = None
        self._last_query = ""

        # Saving the position is debounced so holding an arrow key doesn't rewrite the file.
        self._position_timer = QTimer(self, singleShot=True, interval=1000)
        self._position_timer.timeout.connect(self._save_position)

        self._transcriber = SessionTranscriber(self)
        self._transcribe_percent = 0
        self._transcriber.progress.connect(self._on_transcribe_progress)
        self._transcriber.finished.connect(self._on_transcribed)
        self._transcriber.failed.connect(self._on_transcribe_failed)

        self._message = ""
        self._message_timer = QTimer(self, singleShot=True)
        self._message_timer.timeout.connect(self._clear_message)

    # --- folder & navigation -------------------------------------------------

    def open_folder(self, folder: Path, start: Path | None = None) -> None:
        """Show a folder at the given photo, else where it was last left, else the first."""
        if self._recorder.is_recording:
            self._flash("Stop recording before opening another folder")
            return
        images = list_images(folder)
        if not images:
            self._flash(f"No images found in {folder}")
            return
        self._save_position()
        names = [p.name for p in images]
        wanted = start.name if start is not None else positions.last_viewed(folder)
        self._folder = folder
        self._images = images
        self._index = 0
        self._cache.clear()
        self._errors.clear()
        self._scaled = None
        self._go_to(names.index(wanted) if wanted in names else 0)

    def _save_position(self) -> None:
        self._position_timer.stop()
        if self._folder is not None and self._images:
            try:
                positions.remember(self._folder, self._current())
            except OSError as exc:
                print(f"Could not save position: {exc}", file=sys.stderr)

    def _go_to_photo(self) -> None:
        if not self._images:
            self._flash("Open a folder of photos first")
            return
        query, ok = QInputDialog.getText(self, "Go to photo", "Photo number (e.g. 743) or name:",
                                         text=self._last_query)
        if not ok or not query.strip():
            return
        self._last_query = query.strip()
        matches = find_photos(self._images, query)
        if not matches:
            self._flash(f"No photo matches \"{query.strip()}\"")
            return
        # Several photos can share a number; each G jumps to the next one after this photo.
        after = [i for i in matches if i > self._index]
        target = after[0] if after else matches[0]
        self._go_to(target)
        if len(matches) > 1:
            self._flash(f"{len(matches)} photos match \"{query.strip()}\": showing "
                        f"{matches.index(target) + 1} of {len(matches)}. Press G, Enter for the next.")

    def _choose_folder(self) -> None:
        start = str(self._folder) if self._folder else str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Open folder of photos", start)
        if chosen:
            self.open_folder(Path(chosen))

    def _go_to(self, index: int) -> None:
        if not self._images:
            return
        index = max(0, min(index, len(self._images) - 1))
        changed = index != self._index
        self._index = index
        if changed and self._session is not None:
            self._session.log_image(self._recorder.position(), self._current())
        self._position_timer.start()
        self._prefetch()
        self.update()

    def _current(self) -> Path:
        return self._images[self._index]

    def _prefetch(self) -> None:
        lo, hi = self._index - PREFETCH, self._index + PREFETCH
        wanted = {self._images[i] for i in range(max(0, lo), min(len(self._images), hi + 1))}
        for path in list(self._cache):
            if path not in wanted:
                del self._cache[path]
                self._loader.done(path)
        # Current image first so it is decoded before its neighbours.
        for path in sorted(wanted, key=lambda p: abs(self._images.index(p) - self._index)):
            if path not in self._cache and path not in self._errors:
                self._loader.request(path)

    def _on_loaded(self, path: Path, image: QImage, error: str) -> None:
        if self._images and path in self._images:
            if image.isNull():
                self._errors[path] = error or "Could not read image"
            else:
                self._cache[path] = image
        self._loader.done(path)
        if self._images and path == self._current():
            self.update()

    # --- recording -----------------------------------------------------------

    def _toggle_recording(self) -> None:
        if self._recorder.is_recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self) -> None:
        if not self._images or self._folder is None:
            self._flash("Open a folder of photos first")
            return
        session = Session(self._sessions_root, self._folder)
        try:
            self._recorder.start(session.audio_path)
        except RecorderError as exc:
            session.close(0.0)
            self._flash(f"Could not start recording: {exc}")
            return
        self._session = session
        session.log_image(0.0, self._current())
        self._rec_timer.start()
        self.update()

    def _stop_recording(self) -> None:
        duration = self._recorder.stop()
        self._rec_timer.stop()
        session, self._session = self._session, None
        self.update()
        if session is not None:
            session.close(duration)
            print(f"Saved session: {session.folder}", flush=True)
            self._last_session = session.folder
            self._transcribe(session.folder)

    # --- transcription & export ----------------------------------------------

    def _transcribe(self, folder: Path) -> None:
        try:
            whisper, model = find_whisper(), find_model()
        except WhisperError as exc:
            QMessageBox.warning(self, "Cannot transcribe",
                                f"{exc}\n\nThe recording is saved; press T to transcribe it later.")
            return
        if not model.is_file() and not ensure_model(self, selected_model()):
            self._flash("Recording saved but not transcribed. Press T to try again.", ms=8000)
            return
        self._transcriber.enqueue(folder, whisper, model)
        self.update()

    def _transcribe_last(self) -> None:
        folder = self._last_session or latest_session()
        if self._recorder.is_recording:
            self._flash("Stop recording first")
        elif folder is None:
            self._flash("No recordings yet")
        else:
            self._transcribe(folder)

    def _on_transcribe_progress(self, percent: int) -> None:
        self._transcribe_percent = percent
        self.update()

    def _on_transcribed(self, result: CsvResult) -> None:
        print(f"Wrote {result.path}", flush=True)
        corrected = f" Corrected: {summary(result.corrections)}." if result.corrections else ""
        self._flash(f"Transcribed: {result.described} of {result.photos} photos described.{corrected} "
                    "Right-click → Export CSV… to save a copy.", ms=12000)

    def _on_transcribe_failed(self, message: str) -> None:
        self.update()
        QMessageBox.warning(self, "Transcription failed",
                            f"{message}\n\nThe recording is saved; press T to try again.")

    def _edit_names(self) -> None:
        NamesDialog(self).exec()

    def _apply_names(self) -> None:
        """Re-correct the last recording's CSV with the current names list, without Whisper."""
        folder = self._last_session or latest_session()
        if folder is None or not (folder / TRANSCRIPT).is_file():
            self._flash("No transcribed recording yet")
            return
        if self._transcriber.busy:
            self._flash("Wait for the transcription to finish first")
            return
        try:
            result = session_csv(folder)
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.warning(self, "Could not apply names list", str(exc))
            return
        corrected = f"Corrected: {summary(result.corrections)}" if result.corrections else "No corrections needed."
        self._flash(f"Names list applied to the last recording. {corrected}", ms=12000)

    def _export_csv(self) -> None:
        folder = self._last_session or latest_session()
        if folder is None or not (folder / CSV_NAME).is_file():
            self._flash("No transcribed recording to export yet")
            return
        metadata, _ = load_session(folder)
        documents = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation))
        suggested = documents / f"{Path(metadata['image_folder']).name} {folder.name}.csv"
        chosen, _ = QFileDialog.getSaveFileName(self, "Export CSV", str(suggested), "CSV files (*.csv)")
        if not chosen:
            return
        try:
            shutil.copyfile(folder / CSV_NAME, chosen)
        except OSError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            return
        self._flash(f"Exported to {chosen}", ms=6000)

    # --- messages ------------------------------------------------------------

    def _flash(self, text: str, ms: int = 4000) -> None:
        self._message = text
        self._message_timer.start(ms)
        self.update()

    def _clear_message(self) -> None:
        self._message = ""
        self.update()

    # --- Qt events -----------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        ctrl = event.modifiers() & Qt.KeyboardModifier.ControlModifier
        if key in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_PageDown, Qt.Key.Key_Space):
            self._go_to(self._index + 1)
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_PageUp, Qt.Key.Key_Backspace):
            self._go_to(self._index - 1)
        elif key == Qt.Key.Key_Home:
            self._go_to(0)
        elif key == Qt.Key.Key_End:
            self._go_to(len(self._images) - 1)
        elif key == Qt.Key.Key_R and not ctrl:
            self._toggle_recording()
        elif key == Qt.Key.Key_G and not ctrl:
            self._go_to_photo()
        elif key == Qt.Key.Key_T and not ctrl:
            self._transcribe_last()
        elif key == Qt.Key.Key_E and ctrl:
            self._export_csv()
        elif key == Qt.Key.Key_O and ctrl:
            self._choose_folder()
        elif key in (Qt.Key.Key_F11, Qt.Key.Key_F):
            self._toggle_full_screen()
        elif key == Qt.Key.Key_Escape and self.isFullScreen():
            self.showNormal()
        elif key == Qt.Key.Key_Q or (key == Qt.Key.Key_W and ctrl):
            self.close()
        else:
            super().keyPressEvent(event)

    def _toggle_full_screen(self) -> None:
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        menu.addAction("Open folder…\tCtrl+O", self._choose_folder)
        menu.addAction("Go to photo…\tG", self._go_to_photo)
        recording = self._recorder.is_recording
        menu.addAction(("Stop recording" if recording else "Start recording") + "\tR", self._toggle_recording)
        menu.addAction("Transcribe last recording\tT", self._transcribe_last)
        menu.addAction("Export CSV…\tCtrl+E", self._export_csv)
        menu.addAction("Names and places…", self._edit_names)
        menu.addAction("Apply names list to last recording", self._apply_names)
        models = menu.addMenu("Transcription model")
        group = QActionGroup(models)
        for model in MODELS.values():
            action = models.addAction(f"{model.label} ({model.size_mb} MB)")
            action.setCheckable(True)
            action.setChecked(model.key == selected_model().key)
            action.triggered.connect(lambda _checked=False, key=model.key: select_model(key))
            group.addAction(action)
        menu.addSeparator()
        menu.addAction(("Leave full screen" if self.isFullScreen() else "Full screen") + "\tF11",
                       self._toggle_full_screen)
        menu.addAction("Quit\tQ", self.close)
        menu.exec(event.globalPos())

    def closeEvent(self, event) -> None:
        if self._recorder.is_recording:
            self._stop_recording()
        if self._transcriber.busy:
            answer = QMessageBox.question(
                self, "Transcription in progress",
                "A recording is still being transcribed. Quit anyway?\n\n"
                "The recording is kept; you can transcribe it later with T.")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._transcriber.stop()
        self._save_position()
        self._loader.shutdown()
        super().closeEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        if not self._images:
            self._draw_centre(painter, "Press Ctrl+O to open a folder of photos")
        else:
            path = self._current()
            if path in self._cache:
                pixmap = self._scaled_pixmap(path)
                x = (self.width() - pixmap.width() / pixmap.devicePixelRatio()) / 2
                y = (self.height() - pixmap.height() / pixmap.devicePixelRatio()) / 2
                painter.drawPixmap(int(x), int(y), pixmap)
            elif path in self._errors:
                self._draw_centre(painter, f"{path.name}\n{self._errors[path]}")
            else:
                self._draw_centre(painter, "Loading…")
            self._draw_status(painter, f"{self._index + 1} / {len(self._images)}    {path.name}")

        if self._recorder.is_recording:
            self._draw_recording(painter)
        if self._transcriber.busy:
            queued = self._transcriber.pending - 1
            extra = f" (+{queued} waiting)" if queued else ""
            self._draw_badge(painter, f"Transcribing… {self._transcribe_percent}%{extra}")
        if self._message:
            self._draw_status(painter, self._message, message=True)
        painter.end()

    def _scaled_pixmap(self, path: Path) -> QPixmap:
        dpr = self.devicePixelRatioF()
        size = self.size() * dpr
        if self._scaled is None or self._scaled[0] != path or self._scaled[1] != size:
            image = self._cache[path].scaled(size, Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation)
            pixmap = QPixmap.fromImage(image)
            pixmap.setDevicePixelRatio(dpr)
            self._scaled = (path, size, pixmap)
        return self._scaled[2]

    def _draw_centre(self, painter: QPainter, text: str) -> None:
        painter.setPen(QColor(200, 200, 200))
        painter.setFont(QFont(self.font().family(), 16))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)

    def _draw_status(self, painter: QPainter, text: str, message: bool = False) -> None:
        """The status bar along the bottom, or a message bar just above it."""
        painter.setFont(QFont(self.font().family(), 11))
        metrics = painter.fontMetrics()
        height = metrics.height() + 12
        y = self.height() - height * (2 if message else 1)
        painter.fillRect(0, y, self.width(), height, QColor(0, 0, 0, 160))
        painter.setPen(QColor(220, 220, 220))
        painter.drawText(12, y + 6 + metrics.ascent(), text)
        if not message:
            painter.setPen(QColor(140, 140, 140))
            hint_width = metrics.horizontalAdvance(HINT)
            painter.drawText(self.width() - hint_width - 12, y + 6 + metrics.ascent(), HINT)

    def _draw_badge(self, painter: QPainter, text: str) -> None:
        painter.setFont(QFont(self.font().family(), 12))
        metrics = painter.fontMetrics()
        width, height = metrics.horizontalAdvance(text) + 24, metrics.height() + 14
        painter.fillRect(16, 16, width, height, QColor(0, 0, 0, 180))
        painter.setPen(QColor(230, 230, 230))
        painter.drawText(28, 23 + metrics.ascent(), text)

    def _draw_recording(self, painter: QPainter) -> None:
        seconds = int(self._recorder.position())
        text = f"REC  {seconds // 60:02d}:{seconds % 60:02d}"
        painter.setFont(QFont(self.font().family(), 14, QFont.Weight.Bold))
        metrics = painter.fontMetrics()
        width, height = metrics.horizontalAdvance(text) + 44, metrics.height() + 14
        x, y = self.width() - width - 16, 16
        painter.fillRect(x, y, width, height, QColor(0, 0, 0, 180))
        painter.setBrush(QColor(220, 30, 30))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(x + 12, y + height // 2 - 6, 12, 12)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(x + 32, y + 7 + metrics.ascent(), text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=APP_NAME, description=__doc__)
    parser.add_argument("path", nargs="?", type=Path,
                        help="folder of photos to open, or one photo to open its folder at")
    parser.add_argument("--windowed", action="store_true", help="start in a window instead of full screen")
    args = parser.parse_args(argv)

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName("Photo Voicenotes")
    # Qt refuses images over 256 MB by default; high-resolution 16-bit scans can exceed that.
    QImageReader.setAllocationLimit(0)

    window = ViewerWindow(sessions_dir())
    if args.path:
        path = args.path.expanduser().resolve()
        if path.is_dir():
            window.open_folder(path)
        elif path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
            window.open_folder(path.parent, start=path)
        else:
            parser.error(f"not a folder or a supported image: {args.path}")
    window.show() if args.windowed else window.showFullScreen()
    return app.exec()
