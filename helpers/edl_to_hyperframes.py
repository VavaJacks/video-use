"""Hand the cut video to HyperFrames: build its work dir with an output-timeline transcript.

After `render.py` has produced the cut (silences/repetitions removed, no burned
subtitles), this prepares the directory the HyperFrames `talking-head-recut`
workflow expects, so it can skip its own transcription:

  <out_dir>/
    input-video.mp4    copy of the cut video
    metadata.json      ffprobe width / height / fps / duration
    transcript.json    flat word array [{text, start, end}] on the CUT timeline
    BRIEF.md           what this is + the transcript as plain text

Word times are remapped with Hard Rule 5:
    output_time = word.start - segment_start + segment_offset
Segment offsets use the real durations of the extracted segments
(`clips_graded/seg_NN_<src>.mp4`, or clips_preview / clips_draft) when they exist,
so frame rounding at every cut cannot accumulate into drift; otherwise the EDL
arithmetic is used.

Usage:
    python helpers/edl_to_hyperframes.py <edit>/edl.json --video <edit>/cut.mp4
    python helpers/edl_to_hyperframes.py <edit>/edl.json --video <edit>/cut.mp4 -o <edit>/hyperframes
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def ffprobe_json(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate",
         "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


def probe_duration(path: Path) -> float | None:
    try:
        return float(ffprobe_json(path)["format"]["duration"])
    except Exception:
        return None


def segment_durations(edl: dict, edit_dir: Path, clips_dir: Path | None) -> list[float]:
    """Real duration of each extracted segment, falling back to end - start."""
    ranges = edl["ranges"]
    planned = [float(r["end"]) - float(r["start"]) for r in ranges]
    candidates = [clips_dir] if clips_dir else [edit_dir / d for d in ("clips_graded", "clips_preview", "clips_draft")]
    for d in candidates:
        if not d or not d.is_dir():
            continue
        paths = [d / f"seg_{i:02d}_{r['source']}.mp4" for i, r in enumerate(ranges)]
        if all(p.exists() for p in paths):
            measured = [probe_duration(p) for p in paths]
            if all(m is not None for m in measured):
                return [float(m) for m in measured]  # type: ignore[arg-type]
    return planned


def remap_words(edl: dict, edit_dir: Path, durations: list[float]) -> list[dict]:
    out: list[dict] = []
    offset = 0.0
    cache: dict[str, list[dict]] = {}
    for r, seg_dur in zip(edl["ranges"], durations):
        name = r["source"]
        seg_start, seg_end = float(r["start"]), float(r["end"])
        if name not in cache:
            tr = edit_dir / "transcripts" / f"{name}.json"
            data = json.loads(tr.read_text()) if tr.exists() else {"words": []}
            cache[name] = data.get("words", []) if isinstance(data, dict) else data
        for w in cache[name]:
            if w.get("type", "word") != "word":
                continue
            ws, we = w.get("start"), w.get("end")
            text = (w.get("text") or "").strip()
            if ws is None or we is None or not text:
                continue
            # A word belongs to this segment only if it starts inside it.
            if not (seg_start <= float(ws) < seg_end):
                continue
            start = max(0.0, float(ws) - seg_start) + offset
            end = min(float(we), seg_end) - seg_start + offset
            out.append({"text": text, "start": round(start, 3), "end": round(max(end, start + 0.02), 3)})
        offset += seg_dur
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Prepare a HyperFrames work dir from a video-use cut")
    ap.add_argument("edl", type=Path)
    ap.add_argument("--video", type=Path, required=True, help="The rendered cut (render.py output)")
    ap.add_argument("-o", "--out-dir", type=Path, default=None, help="Default: <edit>/hyperframes")
    ap.add_argument("--clips-dir", type=Path, default=None, help="Segment dir used for the cut (auto-detected)")
    args = ap.parse_args()

    edl_path = args.edl.resolve()
    edit_dir = edl_path.parent
    edl = json.loads(edl_path.read_text())
    video = args.video.resolve()
    if not video.exists():
        sys.exit(f"video not found: {video}")
    out_dir = (args.out_dir or edit_dir / "hyperframes").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    meta = ffprobe_json(video)
    duration = float(meta["format"]["duration"])
    (out_dir / "metadata.json").write_text(json.dumps(meta, indent=2))

    durations = segment_durations(edl, edit_dir, args.clips_dir.resolve() if args.clips_dir else None)
    words = remap_words(edl, edit_dir, durations)
    for w in words:  # clamp to media duration (Whisper/Scribe can overshoot)
        w["end"] = min(w["end"], duration)
        w["start"] = min(w["start"], w["end"])
    (out_dir / "transcript.json").write_text(json.dumps(words, indent=2, ensure_ascii=False))

    dest = out_dir / "input-video.mp4"
    if dest.resolve() != video:
        shutil.copy2(video, dest)

    drift = duration - sum(durations)
    text = " ".join(w["text"] for w in words)
    (out_dir / "BRIEF.md").write_text(
        "# Brief\n\n"
        "Input: `input-video.mp4` — a talking-head clip already cut by video-use "
        "(silences, fillers and repeated takes removed). Play it untouched.\n\n"
        "Goal: add graphic overlays (kinetic titles, lower-thirds, data callouts, quotes, "
        "simple diagrams) that visualize what is being said, synced to `transcript.json`.\n\n"
        "`transcript.json` is already on the cut timeline — skip `hyperframes transcribe`.\n\n"
        f"Duration: {duration:.2f}s · {len(words)} words\n\n"
        f"## Transcript\n\n{text}\n"
    )
    print(f"hyperframes work dir → {out_dir}")
    print(f"  {len(words)} words remapped onto {duration:.2f}s "
          f"(segments sum {sum(durations):.2f}s, diff {drift:+.3f}s)")
    if abs(drift) > 0.25:
        print("  warning: cut duration and segment sum differ by >0.25s — check the render", file=sys.stderr)


if __name__ == "__main__":
    main()
