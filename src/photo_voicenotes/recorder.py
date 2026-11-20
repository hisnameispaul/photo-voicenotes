"""Microphone recording to a 16-bit PCM WAV file using Qt Multimedia.

The recording position is measured in audio frames actually written, so image-change
timestamps share a clock with the audio rather than with the wall clock. Audio arrives in
chunks, so the time since the last chunk is added on top.
"""

from __future__ import annotations

import time
import wave
from pathlib import Path

from PySide6.QtCore import QIODevice, QObject
from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices

# Whisper works at 16 kHz mono; recording at that rate avoids a conversion step.
PREFERRED_RATE = 16000
# Upper bound on interpolation between chunks, in case audio stalls.
MAX_CHUNK_GAP = 1.0


class RecorderError(RuntimeError):
    pass


class WavRecorder(QObject):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._source: QAudioSource | None = None
        self._io: QIODevice | None = None
        self._wav: wave.Wave_write | None = None
        self._bytes_per_frame = 2
        self._sample_rate = PREFERRED_RATE
        self._frames_written = 0
        self._last_chunk_at = 0.0
        self._partial = b""

    @property
    def is_recording(self) -> bool:
        return self._source is not None

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def start(self, path: Path) -> None:
        if self.is_recording:
            raise RecorderError("Already recording")
        device = QMediaDevices.defaultAudioInput()
        if device.isNull():
            raise RecorderError("No microphone found")

        fmt = QAudioFormat()
        fmt.setSampleRate(PREFERRED_RATE)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if not device.isFormatSupported(fmt):
            # Fall back to the device's own rate/channels, still as 16-bit integers for WAV.
            preferred = device.preferredFormat()
            fmt.setSampleRate(preferred.sampleRate())
            fmt.setChannelCount(preferred.channelCount())
            if not device.isFormatSupported(fmt):
                raise RecorderError(f"Microphone '{device.description()}' does not support 16-bit audio")

        self._sample_rate = fmt.sampleRate()
        self._bytes_per_frame = fmt.bytesPerFrame()
        self._frames_written = 0
        self._last_chunk_at = time.monotonic()
        self._partial = b""

        self._wav = wave.open(str(path), "wb")
        self._wav.setnchannels(fmt.channelCount())
        self._wav.setsampwidth(2)
        self._wav.setframerate(fmt.sampleRate())

        self._source = QAudioSource(device, fmt, self)
        self._io = self._source.start()
        if self._io is None:
            self._cleanup()
            raise RecorderError("Could not open the microphone")
        self._io.readyRead.connect(self._drain)

    def position(self) -> float:
        """Seconds of audio recorded so far, after pulling in anything already captured."""
        self._drain()
        if not self.is_recording or self._frames_written == 0:
            return 0.0
        since_chunk = min(time.monotonic() - self._last_chunk_at, MAX_CHUNK_GAP)
        return self._frames_written / self._sample_rate + since_chunk

    def stop(self) -> float:
        """Stop recording, close the file and return its duration in seconds."""
        if not self.is_recording:
            return 0.0
        self._drain()
        duration = self._frames_written / self._sample_rate
        self._cleanup()
        return duration

    def _drain(self) -> None:
        if self._io is None or self._wav is None:
            return
        data = self._partial + bytes(self._io.readAll())
        # Only write whole frames; keep any partial frame for the next read.
        usable = len(data) - len(data) % self._bytes_per_frame
        self._partial = data[usable:]
        if usable:
            self._wav.writeframes(data[:usable])
            self._frames_written += usable // self._bytes_per_frame
            self._last_chunk_at = time.monotonic()

    def _cleanup(self) -> None:
        if self._source is not None:
            self._source.stop()
            self._source.deleteLater()
        if self._wav is not None:
            self._wav.close()
        self._source = None
        self._io = None
        self._wav = None
