from pathlib import Path

from photo_voicenotes.align import Event, align
from photo_voicenotes.filenames import ScanName, parse_scan_name
from photo_voicenotes.whisper import Word, dtw_preset


def words(*pairs):
    return [Word(text, t) for text, t in pairs]


def descriptions(notes):
    return {n.image: n.description for n in notes}


def test_word_finished_after_keypress_stays_with_previous_photo():
    events = [Event(0.0, "a.tif"), Event(3.0, "b.tif")]
    spoken = words(("blowing", 1.5), ("his", 2.0), ("candles", 2.4), ("birthday", 3.4),
                   ("that's", 4.7), ("dad", 5.1))
    assert descriptions(align(events, spoken, 7.0)) == {
        "a.tif": "blowing his candles birthday", "b.tif": "that's dad"}


def test_cuts_at_keypress_when_there_is_no_pause():
    events = [Event(0.0, "a.tif"), Event(2.0, "b.tif")]
    spoken = words(("one", 1.0), ("two", 1.3), ("three", 1.6), ("four", 1.9), ("five", 2.2),
                   ("six", 2.5), ("seven", 2.8), ("eight", 3.1), ("nine", 3.4), ("ten", 3.7))
    assert descriptions(align(events, spoken, 5.0)) == {
        "a.tif": "one two three four", "b.tif": "five six seven eight nine ten"}


def test_photos_flipped_past_get_no_description():
    events = [Event(0.0, "a.tif"), Event(2.0, "b.tif"), Event(2.2, "c.tif"), Event(2.4, "d.tif")]
    spoken = words(("church", 1.0), ("garden", 3.5), ("party", 4.0))
    notes = align(events, spoken, 6.0)
    assert descriptions(notes) == {"a.tif": "church", "b.tif": "", "c.tif": "", "d.tif": "garden party"}
    assert [n.image for n in notes] == ["a.tif", "b.tif", "c.tif", "d.tif"]


def test_revisited_photo_combines_descriptions_and_time():
    events = [Event(0.0, "a.tif"), Event(3.0, "b.tif"), Event(6.0, "a.tif")]
    spoken = words(("gran", 1.0), ("dad", 4.0), ("and", 7.0), ("mum", 7.3))
    notes = {n.image: n for n in align(events, spoken, 9.0)}
    assert notes["a.tif"].description == "gran / and mum"
    assert notes["a.tif"].seconds_on_screen == 6.0


def test_fillers_are_dropped():
    spoken = words(("uh", 0.5), ("that's", 1.0), ("um", 1.4), ("nana", 1.8))
    assert descriptions(align([Event(0.0, "a.tif")], spoken, 3.0)) == {"a.tif": "that's nana"}


def test_parse_scan_names():
    assert parse_scan_name(Path("#69 2 721.tif")) == ScanName(69, 2, 721)
    assert parse_scan_name(Path("#34 7  002.tif")) == ScanName(34, 7, 2)
    assert parse_scan_name(Path("#33 07 002.tif")) == ScanName(33, 7, 2)
    assert parse_scan_name(Path("holiday.jpg")) is None


def test_dtw_preset_from_model_filename():
    assert dtw_preset(Path("ggml-large-v3-turbo-q5_0.bin")) == "large.v3.turbo"
    assert dtw_preset(Path("ggml-base.en.bin")) == "base.en"
    assert dtw_preset(Path("my-custom-model.bin")) is None
