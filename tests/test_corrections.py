import csv
import json

from photo_voicenotes.corrections import correct, short_name
from photo_voicenotes.transcribe import CORRECTIONS_NAME, CSV_NAME, HEARD_CSV_NAME, TRANSCRIPT, session_csv

NAMES = ["Peter", "Grandpa", "Grandma", "Anderson", "Elmwood Terrace", "Bramleys", "Lakeside Parcs",
         "Market Square", "Rose", "Jean"]


def test_corrects_near_misses_from_the_session():
    assert correct("I've just been to Branley's Toy Shop.", NAMES) == (
        "I've just been to Bramleys Toy Shop.", [("Branley's", "Bramleys")])
    assert correct("Me on holiday with Mum. Lakeside Parks, I think.", NAMES) == (
        "Me on holiday with Mum. Lakeside Parcs, I think.", [("Lakeside Parks", "Lakeside Parcs")])
    assert correct("That's Mum in Elmswood Terrace.", NAMES) == (
        "That's Mum in Elmwood Terrace.", [("Elmswood Terrace", "Elmwood Terrace")])


def test_possessive_is_kept_when_the_rest_matches_exactly():
    assert correct("back garden of Grandpa's house", NAMES) == ("back garden of Grandpa's house", [])
    assert correct("that's Anderson's bike", NAMES) == ("that's Anderson's bike", [])
    assert correct("that's the Andersons' bike", NAMES) == ("that's the Andersons' bike", [])
    assert correct("the Andersons came too", NAMES) == ("the Andersons came too", [])


def test_entries_shorter_than_five_letters_are_not_used():
    assert correct("that's the prose", NAMES) == ("that's the prose", [])
    assert correct("Joan and Rosa", NAMES) == ("Joan and Rosa", [])


def test_dissimilar_words_are_left_alone():
    text = "We drove north to Northumberland, then back through the terrace to the garden."
    assert correct(text, NAMES) == (text, [])


def test_already_correct_names_are_not_reported():
    assert correct("Grandma and Grandpa at Market Square.", NAMES) == (
        "Grandma and Grandpa at Market Square.", [])


def test_phrases_do_not_span_sentences_or_separate_descriptions():
    assert correct("Elmswood. Terrace", ["Elmwood Terrace"]) == ("Elmswood. Terrace", [])
    assert correct("Elmswood / Terrace", ["Elmwood Terrace"]) == ("Elmswood / Terrace", [])


def test_short_names_for_summary():
    assert short_name("#36 7  034.tif") == "034"
    assert short_name("holiday.jpg") == "holiday.jpg"


def _session(tmp_path, words):
    (tmp_path / "session.json").write_text(json.dumps({"audio": "audio.wav", "duration": 10.0}))
    (tmp_path / "events.jsonl").write_text(
        json.dumps({"t": 0.0, "image": "#36 7  034.tif"}) + "\n" + json.dumps({"t": 5.0, "image": "#36 7  035.tif"}) + "\n")
    tokens = [{"text": f" {w}", "t_dtw": round(t * 100), "offsets": {"from": round(t * 1000)}} for w, t in words]
    (tmp_path / TRANSCRIPT).write_text(json.dumps({"transcription": [{"tokens": tokens}]}))


def _read(path):
    return {r["filename"]: r["description"] for r in csv.DictReader(open(path, encoding="utf-8-sig"))}


def test_session_keeps_heard_text_and_lists_corrections(tmp_path):
    _session(tmp_path, [("Branley's", 1.0), ("Toy", 1.4), ("Shop.", 1.8), ("Branley's", 6.0), ("again.", 6.4)])
    result = session_csv(tmp_path, names=NAMES)
    assert _read(tmp_path / CSV_NAME) == {"#36 7  034.tif": "Bramleys Toy Shop.", "#36 7  035.tif": "Bramleys again."}
    assert _read(tmp_path / HEARD_CSV_NAME) == {"#36 7  034.tif": "Branley's Toy Shop.",
                                                "#36 7  035.tif": "Branley's again."}
    assert (tmp_path / CORRECTIONS_NAME).read_text() == "Branley's → Bramleys: #36 7  034.tif, #36 7  035.tif\n"
    assert [(c.heard, c.corrected, c.images) for c in result.corrections] == [
        ("Branley's", "Bramleys", ["#36 7  034.tif", "#36 7  035.tif"])]


def test_reapplying_after_the_list_changes_starts_from_whisper_text(tmp_path):
    _session(tmp_path, [("Branley's", 1.0), ("Toy", 1.4), ("Shop.", 1.8)])
    session_csv(tmp_path, names=NAMES)
    session_csv(tmp_path, names=["Peter"])
    assert _read(tmp_path / CSV_NAME)["#36 7  034.tif"] == "Branley's Toy Shop."
    assert (tmp_path / CORRECTIONS_NAME).read_text() == "No corrections were needed.\n"
