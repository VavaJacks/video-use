"""Export an output-timeline word transcript for HyperFrames.

After render.py has cut the sources into final.mp4, the Scribe transcripts in
<edit_dir>/transcripts/ still carry SOURCE timestamps. HyperFrames overlay
workflows (talking-head-recut, embedded-captions) need word timings on the
OUTPUT timeline of the cut video. This helper walks the EDL ranges in order and
writes a flat word array:

    [{"text": "Olá", "start": 0.05, "end": 0.31}, ...]

Output time = word.start - segment_start + segment_offset (Hard Rule 5), each
word clamped to its kept range, so a word that straddles a cut never leaks past it.

Usage:
    python helpers/export_transcript.py <edl.json> -o <edit_dir>/transcript_output.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def output_words(edl: dict, transcripts: dict[str, dict]) -> list[dict]:
    """Map the kept words of every EDL range onto the output timeline."""
    words_out: list[dict] = []
    seg_offset = 0.0
    for r in edl["ranges"]:
        seg_start = float(r["start"])
        seg_end = float(r["end"])
        transcript = transcripts.get(r["source"]) or {}
        for w in transcript.get("words", []):
            if w.get("type", "word") != "word":
                continue
            text = (w.get("text") or "").strip()
            ws, we = w.get("start"), w.get("end")
            if not text or ws is None or we is None:
                continue
            if we <= seg_start or ws >= seg_end:
                continue
            start = max(float(ws), seg_start) - seg_start + seg_offset
            end = min(float(we), seg_end) - seg_start + seg_offset
            words_out.append({"text": text, "start": round(start, 3), "end": round(end, 3)})
        seg_offset += seg_end - seg_start
    return words_out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("edl", type=Path, help="edl.json used to render the cut")
    ap.add_argument("-o", "--output", type=Path, help="output JSON (default: <edit_dir>/transcript_output.json)")
    ap.add_argument("--edit-dir", type=Path, help="edit dir holding transcripts/ (default: the EDL's directory)")
    args = ap.parse_args()

    edl = json.loads(args.edl.read_text())
    edit_dir = (args.edit_dir or args.edl.parent).resolve()
    out = args.output or edit_dir / "transcript_output.json"

    transcripts: dict[str, dict] = {}
    for name in {r["source"] for r in edl["ranges"]}:
        path = edit_dir / "transcripts" / f"{name}.json"
        if not path.exists():
            sys.exit(f"missing transcript for source {name}: {path}")
        transcripts[name] = json.loads(path.read_text())

    words = output_words(edl, transcripts)
    out.write_text(json.dumps(words, ensure_ascii=False, indent=1))
    total = sum(float(r["end"]) - float(r["start"]) for r in edl["ranges"])
    print(f"{len(words)} words → {out} (output duration {total:.2f}s)")


if __name__ == "__main__":
    main()
