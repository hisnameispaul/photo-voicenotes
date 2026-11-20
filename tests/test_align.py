import json
from pathlib import Path

from photo_voicenotes.align import Event, align
from photo_voicenotes.filenames import ScanName, parse_scan_name
from photo_voicenotes.whisper import Word, dtw_preset, read_words


def words(*pairs):
    return [Word(text, t) for text, t in pairs]


def descriptions(notes):
    return {n.image: n.description for n in notes}


def test_word_finished_after_keypress_stays_with_previous_photo():
    events = [Event(0.0, "a.tif"), Event(3.0, "b.tif")]
    # Real case: "...candles for his [press] birthday. [pause] That's me, Dad..."
    spoken = words(("blowing", 1.6), ("candles", 2.1), ("for", 2.9), ("his", 3.0),
                   ("birthday", 3.4), ("that's", 4.7), ("dad", 5.1))
    assert descriptions(align(events, spoken, 7.0)) == {
        "a.tif": "blowing candles for his birthday", "b.tif": "that's dad"}


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


def test_prefers_pause_at_keypress_over_bigger_pause_belonging_to_next_photo():
    # Real case: "...me and Gran. [press] Same again. [press] That's nursery"
    events = [Event(0.0, "a.tif"), Event(17.68, "b.tif"), Event(19.55, "c.tif")]
    spoken = words(("me", 16.86), ("and", 17.06), ("Gran.", 17.40), ("Same", 18.82),
                   ("again.", 19.10), ("That's", 21.48), ("nursery.", 22.0))
    assert descriptions(align(events, spoken, 24.0)) == {
        "a.tif": "me and Gran.", "b.tif": "Same again.", "c.tif": "That's nursery."}


def test_read_words_drops_text_invented_in_silence(tmp_path):
    def token(text, centiseconds):
        return {"text": text, "t_dtw": centiseconds, "offsets": {"from": centiseconds * 10}}

    transcript = {"transcription": [
        {"tokens": [token("[_BEG_]", 7200), token(" Same", 7256), token(" again", 7288), token(".", 7494)]},
        {"tokens": [token(" Thank", 7916), token(" you", 7916), token(".", 7916)]},
        # Same, but with the full stop placed a moment later.
        {"tokens": [token(" Thank", 10200), token(" you", 10200), token(".", 10228)]},
    ]}
    path = tmp_path / "transcript.json"
    path.write_text(json.dumps(transcript))
    assert [(w.text, w.t) for w in read_words(path)] == [("Same", 72.56), ("again.", 72.88)]


def test_read_words_drops_lone_punctuation(tmp_path):
    transcript = {"transcription": [
        {"tokens": [{"text": " .", "t_dtw": 94, "offsets": {"from": 940}}]},
        {"tokens": [{"text": " Uncle", "t_dtw": 300, "offsets": {"from": 3000}},
                    {"text": " Peter", "t_dtw": 340, "offsets": {"from": 3400}},
                    {"text": " .", "t_dtw": 380, "offsets": {"from": 3800}}]},
    ]}
    path = tmp_path / "transcript.json"
    path.write_text(json.dumps(transcript))
    assert [(w.text, w.t) for w in read_words(path)] == [("Uncle", 3.0), ("Peter.", 3.4)]


def test_sentence_unfinished_at_keypress_stays_with_photo_it_started_on():
    # Real case: "...at a playground, [press] me on a [press] slide, Peter. [press]"
    # The pause before "slide," is the biggest, but the sentence runs to the comma.
    events = [Event(0.0, "027.tif"), Event(47.90, "028.tif"), Event(50.19, "029.tif"),
              Event(51.87, "030.tif")]
    spoken = words(("at", 47.10), ("a", 47.20), ("playground,", 47.80), ("me", 49.09),
                   ("on", 49.29), ("a", 49.39), ("slide,", 50.49), ("Peter.", 51.69),
                   ("Bunch", 53.0), ("of", 53.2), ("kids.", 53.5))
    assert descriptions(align(events, spoken, 56.0)) == {
        "027.tif": "at a playground,", "028.tif": "me on a slide,", "029.tif": "Peter.",
        "030.tif": "Bunch of kids."}
