from pathlib import Path

from photo_voicenotes import positions
from photo_voicenotes.filenames import find_photos

# Sequence numbers restart within a roll in real batches, so "1" matches twice here.
IMAGES = [Path(n) for n in ["#35 4  020.tif", "#35 4  021.tif", "#35 5 001.tif", "#69 2 743.tif",
                            "#70 1 001.tif", "holiday.jpg"]]


def test_find_by_sequence_number():
    assert find_photos(IMAGES, "743") == [3]
    assert find_photos(IMAGES, " 21 ") == [1]
    assert find_photos(IMAGES, "001") == [2, 4]
    assert find_photos(IMAGES, "999") == []


def test_find_by_full_scan_name():
    assert find_photos(IMAGES, "69 2 743") == [3]
    assert find_photos(IMAGES, "#35 5 1") == [2]


def test_find_by_part_of_filename():
    assert find_photos(IMAGES, "Holiday") == [5]
    assert find_photos(IMAGES, "") == []


def test_positions_are_remembered_per_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    first, second = Path("/photos/batch 1"), Path("/photos/batch 2")
    assert positions.last_viewed(first) is None
    positions.remember(first, first / "#69 2 743.tif")
    positions.remember(second, second / "#70 1 001.tif")
    positions.remember(first, first / "#69 2 744.tif")
    assert positions.last_viewed(first) == "#69 2 744.tif"
    assert positions.last_viewed(second) == "#70 1 001.tif"
    assert (tmp_path / "photo-voicenotes" / "last-viewed.json").is_file()


def test_unreadable_positions_file_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    (tmp_path / "photo-voicenotes").mkdir()
    (tmp_path / "photo-voicenotes" / "last-viewed.json").write_text("not json")
    assert positions.last_viewed(Path("/photos")) is None
    positions.remember(Path("/photos"), Path("/photos/a.tif"))
    assert positions.last_viewed(Path("/photos")) == "a.tif"
