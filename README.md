# Photo Voicenotes

Catalogue old scanned photos by talking about them.

Open a folder of scans in a full-screen viewer, press **R**, and describe each photo
(who, where, roughly when) as you flip through with the arrow keys. The app records the
narration and logs which photo was on screen at each moment. Later it transcribes the audio
locally with [whisper.cpp](https://github.com/ggml-org/whisper.cpp) and turns it into a
CSV with one row per photo, so nobody has to read filenames aloud.

Everything runs locally and offline.

> **Status:** early development. Recording, transcription and CSV export work from the
> command line; automatic model download and packaged builds are still to come.

## Filenames

Scans named like `#69 2 721.tif` are read as roll 69, frame 2, sequence 721, and shown in
that order. Other image files (TIFF, JPEG, PNG) are shown after them in natural order.

## Running from source

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv run photo-voicenotes "/path/to/scans"            # full screen
uv run photo-voicenotes --windowed "/path/to/scans"
```

| Key | Action |
| --- | --- |
| → / ↓ / Space / Page Down | next photo |
| ← / ↑ / Backspace / Page Up | previous photo |
| Home / End | first / last photo |
| R | start / stop recording |
| Ctrl+O | open a folder |
| F11 or F | toggle full screen (Esc leaves it) |
| Q | quit (stops and saves any recording) |

Each recording is saved as a session folder in the app's data directory
(`~/.local/share/photo-voicenotes/sessions/` on Linux):

- `audio.wav`: the narration (16 kHz mono where the microphone allows)
- `events.jsonl`: one line per photo shown, e.g. `{"t": 12.48, "image": "#69 2 721.tif"}`,
  where `t` is seconds into the audio
- `session.json`: the photo folder, start time and duration

Your photos are never modified or copied.

## Transcribing a session

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

Download a model, e.g.
[ggml-large-v3-turbo-q5_0.bin](https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin),
into the app's `models/` data folder (`~/.local/share/photo-voicenotes/models/` on Linux), or
pass `--model`. Then:

```sh
uv run photo-voicenotes-transcribe                 # most recent session
uv run photo-voicenotes-transcribe path/to/session --language en
```

This writes `transcript.json` and `photos.csv` into the session folder. The CSV has one row
per photo shown, in viewing order:

| Column | |
| --- | --- |
| `filename`, `roll`, `frame`, `sequence` | from names like `#69 2 721.tif` |
| `description` | what was said while the photo was on screen |
| `people`, `place`, `date` | left blank, for you to fill in |
| `shown_at` | when the photo appeared in `audio.wav` (m:ss), for listening back |
| `seconds_on_screen` | total time the photo was shown |

Words are matched to photos using Whisper's word timings. Because people often press
"next" just before finishing a sentence and then pause, each photo change is moved to the
longest pause within about half a second before to 1.5 seconds after the keypress. Photos
shown for under 0.8 seconds count as flipped past and get no description. A photo visited
more than once gets its descriptions joined with ` / `.

Re-running reuses the saved transcript; pass `--retranscribe` to run Whisper again.

## Development

```sh
uv run pytest
```

## License

[MIT](LICENSE)
