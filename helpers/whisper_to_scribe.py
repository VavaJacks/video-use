"""Transcribe locally with HyperFrames (Whisper) and save it in Scribe's shape.

Fallback for when there is no ElevenLabs key. `hyperframes transcribe` writes a
flat word array `[{text, start, end}, ...]`; the rest of video-use reads
Scribe's `{"words": [{type, text, start, end, speaker_id}, ...]}`. This script
runs the transcription (or takes an existing word array) and writes
`<edit_dir>/transcripts/<video_stem>.json` in the Scribe shape, with `spacing`
entries between words so pack_transcripts.py sees the silences.

Caveat: Whisper tends to drop fillers ("ahn", "uh") and smooth over false
starts, so Scribe stays the better source for cutting repetitions. Use a
multilingual model (not `*.en`) for Portuguese.

Usage:
    python helpers/whisper_to_scribe.py <video> [--edit-dir DIR] [--language pt] [--model small]
    python helpers/whisper_to_scribe.py <video> --from-json transcript.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def to_scribe(words: list[dict], language: str | None = None) -> dict:
    """Convert a HyperFrames/Whisper word array to a Scribe-shaped payload."""
    out: list[dict] = []
    prev_end: float | None = None
    for w in words:
        text = (w.get("text") or "").strip()
        start = w.get("start")
        end = w.get("end")
        if not text or start is None or end is None:
            continue
        start = float(start)
        end = max(float(end), start)
        if prev_end is not None and start > prev_end:
            out.append({"type": "spacing", "text": " ", "start": prev_end, "end": start, "speaker_id": "speaker_0"})
        out.append({"type": "word", "text": text, "start": start, "end": end, "speaker_id": "speaker_0"})
        prev_end = end
    payload: dict = {"text": " ".join(w["text"] for w in out if w["type"] == "word"), "words": out}
    if language:
        payload["language_code"] = language
    payload["source"] = "hyperframes-whisper"
    return payload


def load_word_array(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    if isinstance(data, dict):
        # Tolerate {"words": [...]} or {"segments": [{"words": [...]}]}
        if isinstance(data.get("words"), list):
            return data["words"]
        words: list[dict] = []
        for seg in data.get("segments", []):
            words.extend(seg.get("words", []))
        return words
    if isinstance(data, list):
        return data
    sys.exit(f"unrecognized transcript shape: {path}")


def run_hyperframes_transcribe(video: Path, workdir: Path, model: str, language: str | None) -> Path:
    cmd = ["npx", "--yes", "hyperframes", "transcribe", str(video), "-d", str(workdir), "--json", "--model", model]
    if language:
        cmd += ["--language", language]
    print("running:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    out = workdir / "transcript.json"
    if not out.exists():
        sys.exit(f"hyperframes transcribe did not write {out}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Local Whisper (HyperFrames) → Scribe-shaped transcript")
    ap.add_argument("video", type=Path, help="Source video (its stem names the transcript)")
    ap.add_argument("--edit-dir", type=Path, default=None, help="Default: <video_dir>/edit")
    ap.add_argument("--from-json", type=Path, default=None, help="Convert an existing word array instead of transcribing")
    ap.add_argument("--model", default="small", help="Whisper model (multilingual for non-English: small, medium, large-v3)")
    ap.add_argument("--language", default=None, help="Language code, e.g. pt")
    ap.add_argument("--force", action="store_true", help="Overwrite a cached transcript")
    args = ap.parse_args()

    video = args.video.resolve()
    edit_dir = (args.edit_dir or video.parent / "edit").resolve()
    out_path = edit_dir / "transcripts" / f"{video.stem}.json"
    if out_path.exists() and not args.force:
        print(f"cached: {out_path}")
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.from_json:
        words = load_word_array(args.from_json)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            words = load_word_array(run_hyperframes_transcribe(video, Path(tmp), args.model, args.language))

    payload = to_scribe(words, args.language)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    n = sum(1 for w in payload["words"] if w["type"] == "word")
    print(f"saved: {out_path} ({n} words)")


if __name__ == "__main__":
    main()
