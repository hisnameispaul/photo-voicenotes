"""Matching transcribed words to the photo that was on screen when they were spoken.

People tend to press "next" just before finishing a sentence, and pause before describing
the new photo. So each photo change is moved to the longest pause between words near the
keypress, rather than cutting at the exact keypress time.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field

from photo_voicenotes.whisper import Word

# Window around each keypress in which to look for the pause, in seconds.
SNAP_BEFORE = 0.5
SNAP_AFTER = 1.5
# Gaps shorter than this are ordinary spacing between words, not a pause.
MIN_PAUSE = 0.5
# Photos on screen for less than this were flipped past, not described.
MIN_VIEW = 0.8

_FILLERS = re.compile(r"^(uh+|um+|er+m?|hmm+|mm+)[.,!?]*$", re.IGNORECASE)


@dataclass(frozen=True)
class Event:
    t: float
    image: str


@dataclass
class PhotoNotes:
    image: str
    first_shown: float
    seconds_on_screen: float = 0.0
    parts: list[str] = field(default_factory=list)

    @property
    def description(self) -> str:
        return " / ".join(self.parts)


def _split_index(times: list[float], t: float, lo: int, duration: float) -> int:
    """Index of the first word belonging to the photo shown at keypress time t."""
    best, best_gap = None, MIN_PAUSE
    for k in range(lo, len(times) + 1):
        # Silence at the start or end of the recording counts as a pause of its real length.
        prev = times[k - 1] if k > 0 else 0.0
        nxt = times[k] if k < len(times) else duration
        if prev > t + SNAP_AFTER:
            break
        if nxt < t - SNAP_BEFORE:
            continue
        if nxt - prev > best_gap:
            best, best_gap = k, nxt - prev
    if best is None:  # no clear pause: cut at the keypress
        best = max(lo, bisect.bisect_left(times, t))
    return best


def _clean(words: list[Word]) -> str:
    return " ".join(w.text for w in words if not _FILLERS.match(w.text))


def align(events: list[Event], words: list[Word], duration: float) -> list[PhotoNotes]:
    """One entry per photo shown, in order of first appearance."""
    ends = [e.t for e in events[1:]] + [duration]
    notes: dict[str, PhotoNotes] = {}
    for event, end in zip(events, ends):
        note = notes.setdefault(event.image, PhotoNotes(event.image, event.t))
        note.seconds_on_screen += end - event.t

    # Only photos looked at long enough to be described take part in the matching.
    viewed = [e for e, end in zip(events, ends) if end - e.t >= MIN_VIEW]
    times = [w.t for w in words]
    splits = [0]
    for event in viewed[1:]:
        splits.append(_split_index(times, event.t, splits[-1], duration))
    splits.append(len(words))

    for event, start, stop in zip(viewed, splits, splits[1:]):
        text = _clean(words[start:stop])
        if text:
            notes[event.image].parts.append(text)
    return list(notes.values())
