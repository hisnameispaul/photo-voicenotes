"""Full-screen photo viewer that records narration and logs which photo is on screen."""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QImageReader, QKeyEvent, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QFileDialog, QWidget

from photo_voicenotes.filenames import list_images
from photo_voicenotes.paths import APP_NAME, sessions_dir
from photo_voicenotes.recorder import RecorderError, WavRecorder
from photo_voicenotes.session import Session

PREFETCH = 2  # images kept decoded either side of the current one
HINT = "←/→ navigate · R record · Ctrl+O open folder · F11 full screen · Q quit"


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

        self._message = ""
        self._message_timer = QTimer(self, singleShot=True)
        self._message_timer.timeout.connect(self._clear_message)

    # --- folder & navigation -------------------------------------------------

    def open_folder(self, folder: Path) -> None:
        if self._recorder.is_recording:
            self._flash("Stop recording before opening another folder")
            return
        images = list_images(folder)
        if not images:
            self._flash(f"No images found in {folder}")
            return
        self._folder = folder
        self._images = images
        self._cache.clear()
        self._errors.clear()
        self._scaled = None
        self._go_to(0)

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
        if self._session is not None:
            self._session.close(duration)
            print(f"Saved session: {self._session.folder}", flush=True)
            self._flash(f"Saved {duration:.0f}s recording to {self._session.folder}", ms=8000)
            self._session = None
        self.update()

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
        elif key == Qt.Key.Key_O and ctrl:
            self._choose_folder()
        elif key in (Qt.Key.Key_F11, Qt.Key.Key_F):
            self.showNormal() if self.isFullScreen() else self.showFullScreen()
        elif key == Qt.Key.Key_Escape and self.isFullScreen():
            self.showNormal()
        elif key == Qt.Key.Key_Q or (key == Qt.Key.Key_W and ctrl):
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        if self._recorder.is_recording:
            self._stop_recording()
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
        elif self._message:
            self._draw_status(painter, self._message, top=True)
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

    def _draw_status(self, painter: QPainter, text: str, top: bool = False) -> None:
        painter.setFont(QFont(self.font().family(), 11))
        metrics = painter.fontMetrics()
        height = metrics.height() + 12
        y = 0 if top else self.height() - height
        painter.fillRect(0, y, self.width(), height, QColor(0, 0, 0, 160))
        painter.setPen(QColor(220, 220, 220))
        painter.drawText(12, y + 6 + metrics.ascent(), text)
        if not top:
            painter.setPen(QColor(140, 140, 140))
            hint_width = metrics.horizontalAdvance(HINT)
            painter.drawText(self.width() - hint_width - 12, y + 6 + metrics.ascent(), HINT)

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
    parser.add_argument("folder", nargs="?", type=Path, help="folder of photos to open")
    parser.add_argument("--windowed", action="store_true", help="start in a window instead of full screen")
    args = parser.parse_args(argv)

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName("Photo Voicenotes")
    # Qt refuses images over 256 MB by default; high-resolution 16-bit scans can exceed that.
    QImageReader.setAllocationLimit(0)

    window = ViewerWindow(sessions_dir())
    if args.folder:
        window.open_folder(args.folder.expanduser().resolve())
    window.show() if args.windowed else window.showFullScreen()
    return app.exec()
