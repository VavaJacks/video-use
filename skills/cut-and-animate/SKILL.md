---
name: cut-and-animate
description: Two-stage pipeline for talking-head footage — video-use cuts silences, hesitations and repeated takes, then HyperFrames adds animations and visualizations synced to what is being said, on top of the cut. Use when the user wants "cut the silences and repetitions, then add animations / graphics about what I'm saying".
---

# Cut and animate (video-use → HyperFrames)

Stage 1 (video-use) produces a clean cut. Stage 2 (HyperFrames) plays that cut
untouched and layers timed graphic cards over it. The two stages share one
transcript, remapped onto the cut timeline, so stage 2 never re-transcribes and
the graphics land on the spoken words.

Everything from `../../SKILL.md` still applies (Hard Rules, ask → confirm → execute,
self-eval). For stage 2 read the HyperFrames skills: `/hyperframes` (entry),
`/talking-head-recut` (the overlay workflow this pipeline feeds), `/hyperframes-core`.

```
sources ──transcribe──> transcripts/*.json ──auto_edl.py──> edl.json + cut_report.md
                                                              │ agent reviews repetitions
                                                              ▼
                                       render.py --no-subtitles ──> edit/cut.mp4
                                                              │
                                    edl_to_hyperframes.py ────┘──> edit/hyperframes/
                                                              │   input-video.mp4, transcript.json (cut timeline),
                                                              │   metadata.json, BRIEF.md
                                                              ▼
                                  /talking-head-recut steps 5–11 ──> edit/hyperframes/output.mp4
```

Helpers live in `../../helpers/`. All outputs go in `<videos_dir>/edit/` (Hard Rule 12).

## Stage 1 — cut (video-use)

1. **Transcribe** (cached, Hard Rule 9):
   - With `ELEVENLABS_API_KEY`: `python helpers/transcribe_batch.py <videos_dir>` (best: keeps
     fillers and false starts verbatim, which is what repetition cutting needs).
   - Without a key, or when api.elevenlabs.io / huggingface.co are blocked:
     `python helpers/transcribe_local.py --model <parakeet_dir> --edit-dir <edit> <videos…>`
     (Parakeet TDT v3 via sherpa-onnx; setup in the file header, model from GitHub releases).
     It transcribes each real speech chunk separately, so word boundaries match the audio and
     false starts show up as their own phrase.
   - Or: `python helpers/whisper_to_scribe.py <video> --language pt --model small`
     runs `hyperframes transcribe` locally and saves the Scribe shape. Use a multilingual model
     (`small`, `medium`, `large-v3`), never `*.en`, for Portuguese. Whisper smooths over some
     hesitations — say so to the user and lean harder on the review in step 3.
2. **First-pass EDL:** `python helpers/auto_edl.py --edit-dir <videos_dir>/edit`
   - cuts every silence longer than `--max-gap` (default 0.35s) and hesitation sounds
     (`ahn`, `hum`, `ééé`, `uh`…). Cuts land on word boundaries, padded 60ms/100ms.
   - flags **repetition candidates** (a phrase restarted or said again within the next 3 phrases;
     the later take is assumed the keeper) and immediate stutters ("eu eu").
   - writes `edl.json` and `cut_report.md` (every phrase with an id like `C0103:p4`).
3. **Review repetitions** — the part that needs judgment. Read `cut_report.md` and
   `takes_packed.md` (`python helpers/pack_transcripts.py --edit-dir <edit>`). For each `FLAG`,
   decide which take is better (use `timeline_view.py` when unsure), and look for repetitions
   the detector missed (same idea re-said in different words). Then re-run:
   `auto_edl.py --edit-dir <edit> --drop C0103:p4,C0103:p9 --keep C0103:p12 [--drop-stutters]`
   or `--drop-retakes` to accept every flag. Summarize to the user what goes and why
   **before rendering** (Hard Rule 11).
4. **Render the cut** without captions (HyperFrames owns the graphics):
   `python helpers/render.py <edit>/edl.json -o <edit>/cut.mp4 --no-subtitles`
   Add `"grade"` to the EDL if the user wants color. Self-eval the cut boundaries
   (`timeline_view.py` on `cut.mp4`) as in the main SKILL.

## Stage 2 — animate (HyperFrames)

5. **Hand off:** `python helpers/edl_to_hyperframes.py <edit>/edl.json --video <edit>/cut.mp4`
   → `<edit>/hyperframes/` with `input-video.mp4`, `metadata.json`, `BRIEF.md` and
   `transcript.json` already on the cut timeline (segment offsets use the measured duration of
   each extracted segment, so there is no drift across many cuts).
6. **Run `/talking-head-recut` in `<edit>/hyperframes/`**, starting at its step 5 (correct the
   transcript): **skip its steps 3–4** (metadata and transcription are done). Plan cards from
   what is said — titles for topic changes, numbers as count-ups / charts, lists as staggered
   bullets, processes as simple diagrams, quotes as pull-quotes. Confirm aspect, layout, style and
   card count with the user (its step 7) unless they said to decide.
   Use `/hyperframes-registry` before hand-building a named effect.
7. **Validate and render:** `npx hyperframes lint`, `npx hyperframes check`, then
   `npx hyperframes render . -o output.mp4`. Pull a few frames with ffmpeg at card times and look
   at them before showing the user.

## Environment notes

- HyperFrames needs Node 22+. Use the project-local CLI: `npx --yes hyperframes …`.
- No Chrome Headless Shell? `npx hyperframes browser ensure`, or point at an existing one with
  `HYPERFRAMES_CHROME_PATH=/path/to/headless_shell`.
- Offline / CDN blocked: load GSAP from a local file (the talking-head-recut skill ships
  `assets/vendor/gsap.min.js`; copy it next to `index.html`). Same for fonts.
- Render time is roughly 3–5× realtime on CPU; draft with a short `data-duration` first.

## Memory

Append to `<edit>/project.md` as usual, plus: dropped phrase ids and why, the card plan, and the
HyperFrames style choices, so a later session can re-cut and re-animate consistently.
