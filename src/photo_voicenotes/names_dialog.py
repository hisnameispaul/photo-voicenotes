"""Editing the shared list of names and places that transcripts are corrected to."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from photo_voicenotes.corrections import MIN_LETTERS, usable
from photo_voicenotes.vocabulary import load_names, save_names


class NamesDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Names and places")
        self.resize(460, 520)

        intro = QLabel(
            "One name, place or other word per line, spelled the way you want it written. "
            "After each transcription, words that sound close to one of these are respelled to "
            "match. The list is shared by all folders.\n\n"
            "To update a recording that is already transcribed, use “Apply names list to last "
            "recording” in the right-click menu."
        )
        intro.setWordWrap(True)
        self._editor = QPlainTextEdit("\n".join(load_names()))
        self._editor.setPlaceholderText("Auntie Jean\nGrandpa\nRose Cottage\n…")
        self._status = QLabel()
        self._status.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self._editor)
        layout.addWidget(self._status)
        layout.addWidget(buttons)

        self._editor.textChanged.connect(self._update_status)
        self._update_status()

    def _names(self) -> list[str]:
        return [line.strip() for line in self._editor.toPlainText().splitlines() if line.strip()]

    def _update_status(self) -> None:
        too_short = [n for n in self._names() if not usable(n)]
        self._status.setText(
            f"Too short to use (under {MIN_LETTERS} letters, they would match too many ordinary words): "
            + ", ".join(too_short) if too_short else "")

    def _save(self) -> None:
        save_names(self._names())
        self.accept()
