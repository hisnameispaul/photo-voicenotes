"""Correcting the spelling of names and places in transcribed text.

Whisper writes what it hears, so "Bramleys" can come out as "Branley's". Any word or run of
words that closely resembles an entry in the names list is replaced by that entry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

# How alike a heard phrase and a listed name must be (0-1) to replace one with the other.
SIMILARITY = 0.8
# Shorter entries match too many ordinary words ("Rose" and "prose"), so they are not used.
MIN_LETTERS = 5

_EDGE = re.compile(r"^(?P<lead>[\"'(\[]*)(?P<core>.*?)(?P<trail>[.,!?;:\"')\]]*)$")
_SENTENCE_BREAK = (".", ",", "!", "?", ";", ":")


def _key(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", text.lower())


def usable(name: str) -> bool:
    return len(_key(name)) >= MIN_LETTERS


@dataclass
class Correction:
    heard: str
    corrected: str
    images: list[str] = field(default_factory=list)


def _match(phrase: str, names: list[str]) -> str | None:
    """The spelling a phrase should have, or None if it isn't close to any name."""
    parts = _EDGE.match(phrase)
    assert parts is not None
    lead, core, trail = parts["lead"], parts["core"], parts["trail"]
    if len(_key(core)) < MIN_LETTERS:
        return None
    best, best_score = None, SIMILARITY
    for name in names:
        if len(name.split()) != len(core.split()):
            continue
        # "Grandpa's", "the Andersons" and "the Andersons'" are the listed name itself plus a
        # possessive or plural: leave them exactly as heard.
        if _key(core) == _key(name) + "s":
            return phrase
        score = SequenceMatcher(None, _key(core), _key(name)).ratio()
        if score >= best_score and (best is None or score > best_score):
            best, best_score = name, score
    return f"{lead}{best}{trail}" if best is not None else None


def correct(text: str, names: list[str]) -> tuple[str, list[tuple[str, str]]]:
    """Text with near-miss names respelled, and the (heard, corrected) pairs changed."""
    names = [n for n in names if usable(n)]
    longest = max((len(n.split()) for n in names), default=0)
    words = text.split(" ")
    out: list[str] = []
    changes: list[tuple[str, str]] = []
    i = 0
    while i < len(words):
        for n in range(min(longest, len(words) - i), 0, -1):
            chunk = words[i:i + n]
            # A phrase can't run across the end of a sentence or clause, or the " / " that
            # joins separate descriptions of the same photo.
            if any(w.endswith(_SENTENCE_BREAK) or w == "/" for w in chunk[:-1]) or chunk[-1] == "/":
                continue
            phrase = " ".join(chunk)
            replacement = _match(phrase, names)
            if replacement is not None:
                out.append(replacement)
                if replacement != phrase:
                    changes.append((phrase.strip("\"'()[].,!?;:"), replacement.strip("\"'()[].,!?;:")))
                i += n
                break
        else:
            out.append(words[i])
            i += 1
    return " ".join(out), changes


def short_name(image: str) -> str:
    """"#36 7  034.tif" -> "034"; other filenames are shown whole."""
    parts = image.rsplit(".", 1)[0].split()
    return parts[-1] if len(parts) > 1 else image


def summary(corrections: list[Correction]) -> str:
    return "; ".join(f"{c.heard} → {c.corrected} ({', '.join(short_name(i) for i in c.images)})"
                     for c in corrections)
