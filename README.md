# Photo Voicenotes

Catalogue old scanned photos by talking about them.

Open a folder of scans in a full-screen viewer, press **R**, and describe each photo
(who, where, roughly when) as you flip through with the arrow keys. The app records the
narration and logs which photo was on screen at each moment. When you stop recording it
transcribes the audio locally with [whisper.cpp](https://github.com/ggml-org/whisper.cpp)
and turns it into a CSV with one row per photo, so nobody has to read filenames aloud.

Everything runs on your computer. The only time the app uses the internet is a one-off
download of the speech-recognition model, which it asks about first.

> **Status:** early development. Recording, transcription and CSV export work; packaged
> builds are still to come, so for now whisper.cpp has to be built separately (see below).

## Filenames

Scans named like `#69 2 721.tif` are read as roll 69, frame 2, sequence 721, and shown in
that order. Other image files (TIFF, JPEG, PNG) are shown after them in natural order.

## Running from source

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv run photo-voicenotes "/path/to/scans"                        # full screen
uv run photo-voicenotes --windowed "/path/to/scans"
uv run photo-voicenotes "/path/to/scans/#69 2 721.tif"          # start at this photo
```

A folder reopens at the photo you last viewed there. That position is kept in the app's
data folder, not the photo folder.

| Key | Action |
| --- | --- |
| → / ↓ / Space / Page Down | next photo |
| ← / ↑ / Backspace / Page Up | previous photo |
| Home / End | first / last photo |
| G | go to a photo by number (e.g. `743`) or name; if several share a number, G and Enter again moves to the next |
| R | start / stop recording (stopping starts transcription) |
| T | transcribe the last recording again |
| Ctrl+E | export the last recording's CSV to a location you choose |
| Ctrl+O | open a folder |
| F11 or F | toggle full screen (Esc leaves it) |
| Q | quit (stops and saves any recording) |

Right-click anywhere for a menu with the same actions, plus the choice of transcription
model:

- **Accurate** (large-v3-turbo, 574 MB, the default): best results; fast with a graphics card.
- **Fast** (small, 190 MB): for slower computers; less reliable with names.

Each recording is saved as a session folder in the app's data directory
(`~/.local/share/photo-voicenotes/sessions/` on Linux):

- `audio.wav`: the narration (16 kHz mono where the microphone allows)
- `events.jsonl`: one line per photo shown, e.g. `{"t": 12.48, "image": "#69 2 721.tif"}`,
  where `t` is seconds into the audio
- `session.json`: the photo folder, start time and duration

Your photos are never modified or copied.

## Transcription

When a recording stops, the app transcribes it in the background (progress shows in the
top-left corner) and writes `photos.csv` into the session folder. The first time, it offers
to download the selected model into the app's `models/` data folder
(`~/.local/share/photo-voicenotes/models/` on Linux); the download is checked against a
known checksum.

Transcription uses whisper.cpp's `whisper-cli`. Until packaged builds exist, build it
yourself (Vulkan works on AMD, Intel and NVIDIA GPUs; drop `-DGGML_VULKAN=ON` for CPU only):

```sh
git clone --depth 1 --branch v1.9.5 https://github.com/ggml-org/whisper.cpp tools/whisper.cpp
cmake -S tools/whisper.cpp -B tools/whisper.cpp/build -DGGML_VULKAN=ON -DCMAKE_BUILD_TYPE=Release
cmake --build tools/whisper.cpp/build -j --config Release
```

On Fedora the Vulkan build needs `glslc` and `spirv-headers-devel`. A build in
`tools/whisper.cpp` is found automatically; otherwise put `whisper-cli` on your `PATH` or
set `PHOTO_VOICENOTES_WHISPER`.

Sessions can also be transcribed from the command line, which downloads the model too if
needed (or pass `--model` to use a different ggml model file):

```sh
uv run photo-voicenotes-transcribe                 # most recent session
uv run photo-voicenotes-transcribe path/to/session --language en
```

This writes into the session folder:

- `photos.csv`: two columns, `filename` and `description`, with one row per photo shown in
  viewing order; photos shown but not described get an empty description
- `photos-as-heard.csv`: the same, with Whisper's text before any name corrections
- `corrections.txt`: which words were respelled, and in which photos
- `transcript.json`: Whisper's full output, with word timings

When each photo was shown is kept in the session's `events.jsonl`.

### Names and places

Whisper writes what it hears, so "Bramleys" can come out as "Branley's". Under right-click →
**Names and places…** you can list family names, places and other words, one per line, spelled
as you want them. After each transcription, any word or phrase that closely resembles an entry
is respelled to match, and the app shows what it changed, e.g. `Branley's → Bramleys (034, 035)`.

- The list is shared by all folders and kept in the app's data folder as `names.txt`.
- Entries shorter than 5 letters are ignored, as they would match too many ordinary words
  ("Rose" and "prose").
- A possessive is kept: with "Grandpa" in the list, "Grandpa's" is left as it is.
- Ordinary words close to a listed name can be respelled too (with "Bramleys" listed,
  "bramlets" would be), so check `corrections.txt`; Whisper's own text stays in
  `photos-as-heard.csv`.
- After editing the list, right-click → **Apply names list to last recording** updates that
  recording's CSV without transcribing it again. From the command line,
  `photo-voicenotes-transcribe <session>` does the same for any session.

Words are matched to photos using Whisper's word timings. People often press "next"
before finishing a sentence, so when a photo change lands mid-sentence, the rest of that
sentence (up to the first full stop or comma, within 1.5 seconds) stays with the photo it
started on. Without punctuation to go by, the change moves to the clearest pause near the
keypress instead. Photos shown for under 0.8 seconds count as flipped past and get no
description. A photo visited
more than once gets its descriptions joined with ` / `.

Re-running reuses the saved transcript; pass `--retranscribe` to run Whisper again.

## Development

```sh
uv run pytest
```

## License

[MIT](LICENSE)
