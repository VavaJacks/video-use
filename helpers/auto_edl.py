"""Build a first-pass EDL that removes silences, fillers and repeated takes.

Reads every `<edit_dir>/transcripts/*.json` (Scribe word-level shape) and
writes:

  - `<edit_dir>/edl.json`        ranges to keep, ready for render.py
  - `<edit_dir>/cut_report.md`   every phrase with an id, what was cut and why

This is a *first pass*, not the final edit. Silence and filler removal are
mechanical. Repetition detection only flags candidates (a phrase restarted or
repeated within the next few phrases); the agent reads `cut_report.md`, decides,
and re-runs with `--drop` / `--keep` to override. Cuts always land on word
boundaries (Hard Rule 6) with padding inside the 30–200ms window (Hard Rule 7).

Usage:
    python helpers/auto_edl.py --edit-dir <videos_dir>/edit
    python helpers/auto_edl.py --edit-dir edit --drop-retakes
    python helpers/auto_edl.py --edit-dir edit --drop C0103:p4,C0103:p9 --keep C0103:p12
    python helpers/auto_edl.py --edit-dir edit --max-gap 0.3 --pad-before 0.05 --pad-after 0.08
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mts", ".MP4", ".MOV", ".MTS")

# Hesitation sounds only (PT + EN). Words like "tipo", "né", "então" carry
# meaning often enough that they stay for the agent to judge. "um" is the
# Portuguese article ("um projeto"), so only stretched "umm" counts.
FILLERS = {
    "ah", "ahn", "ahm", "eh", "ehh", "hum", "humm", "hm", "hmm", "hã", "hãn", "han",
    "uh", "uhh", "uhm", "umm", "er", "erm", "mm", "mmm",
}

PAD_MIN, PAD_MAX = 0.03, 0.2


def norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^\w\s]", "", t).strip()


def is_filler(word: dict) -> bool:
    if word.get("type") == "audio_event":
        return False
    # Keep accents here: "hã" is a filler, "há" ("there is") is not.
    raw = re.sub(r"[^\w\s]", "", word.get("text", "").lower()).strip()
    if raw in FILLERS:
        return True
    # Stretched hesitations: "ééé", "eee", "hummm", "ahhh", "ãããh"
    return bool(re.fullmatch(r"([eé]{2,}|[aã]{2,}h*|[aã]h+|h+u+m+|u+m{2,}|m{2,}|[aã]n{2,})", raw))


def load_words(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    words = data.get("words", []) if isinstance(data, dict) else data
    out = []
    for w in words:
        if w.get("type", "word") not in ("word", "audio_event"):
            continue
        if w.get("start") is None or w.get("end") is None:
            continue
        if not (w.get("text") or "").strip():
            continue
        out.append({**w, "start": float(w["start"]), "end": float(w["end"])})
    out.sort(key=lambda w: w["start"])
    return out


def group_phrases(words: list[dict], max_gap: float) -> list[list[int]]:
    """Indices of words grouped into phrases, split on silences >= max_gap."""
    phrases: list[list[int]] = []
    for i, w in enumerate(words):
        if phrases and w["start"] - words[phrases[-1][-1]]["end"] < max_gap:
            phrases[-1].append(i)
        else:
            phrases.append([i])
    return phrases


def phrase_tokens(words: list[dict], idx: list[int]) -> list[str]:
    return [t for t in (norm(words[i]["text"]) for i in idx if not is_filler(words[i])) if t]


def find_retakes(words: list[dict], phrases: list[list[int]], lookahead: int = 3) -> dict[int, str]:
    """Return {phrase_index: reason} for phrases that look like an abandoned or
    repeated take of a later phrase. The later take is assumed to be the keeper."""
    flagged: dict[int, str] = {}
    toks = [phrase_tokens(words, p) for p in phrases]
    for i, a in enumerate(toks):
        if len(a) < 2:
            continue
        for j in range(i + 1, min(len(toks), i + 1 + lookahead)):
            b = toks[j]
            if len(b) < 2:
                continue
            k = min(3, len(a), len(b))
            if a[:len(a)] == b[:len(a)] and len(a) < len(b):
                flagged[i] = f"abandoned start, restarted in p{j + 1}"
                break
            if a[:k] == b[:k]:
                flagged[i] = f"same opening as p{j + 1} (likely retake)"
                break
            ratio = SequenceMatcher(None, a, b).ratio()
            if ratio >= 0.75:
                flagged[i] = f"{ratio:.0%} similar to p{j + 1} (likely retake)"
                break
    return flagged


def find_stutters(words: list[dict]) -> set[int]:
    """Immediate word repeats ("eu eu", "the the"): flag the first copy."""
    out = set()
    for i in range(len(words) - 1):
        a, b = norm(words[i]["text"]), norm(words[i + 1]["text"])
        if a and a == b and words[i + 1]["start"] - words[i]["end"] < 0.6:
            out.add(i)
    return out


def build_ranges(
    words: list[dict],
    keep: list[bool],
    duration: float | None,
    max_gap: float,
    pad_before: float,
    pad_after: float,
    min_cut: float,
) -> list[tuple[float, float, list[int]]]:
    """Turn kept words into padded [start, end] ranges on word boundaries."""
    runs: list[list[int]] = []
    for i, w in enumerate(words):
        if not keep[i]:
            if runs and runs[-1]:
                runs.append([])
            continue
        if runs and runs[-1] and w["start"] - words[runs[-1][-1]]["end"] < max_gap:
            runs[-1].append(i)
        else:
            runs.append([i])
    runs = [r for r in runs if r]

    ranges: list[tuple[float, float, list[int]]] = []
    for r in runs:
        first, last = r[0], r[-1]
        start = words[first]["start"] - pad_before
        end = words[last]["end"] + pad_after
        # Never bleed into the neighbouring word's audio (cut lands in the gap).
        if first > 0:
            start = max(start, (words[first - 1]["end"] + words[first]["start"]) / 2)
        if last + 1 < len(words):
            end = min(end, (words[last]["end"] + words[last + 1]["start"]) / 2)
        start = max(0.0, start)
        if duration is not None:
            end = min(duration, end)
        ranges.append((start, end, r))

    # Merge cuts too small to be worth a seam, unless a dropped word sits between.
    merged: list[tuple[float, float, list[int]]] = []
    for start, end, r in ranges:
        if merged:
            ps, pe, pr = merged[-1]
            between_dropped = any(not keep[k] for k in range(pr[-1] + 1, r[0]))
            if start - pe < min_cut and not between_dropped:
                merged[-1] = (ps, end, pr + r)
                continue
        merged.append((start, end, r))
    return merged


def find_source(stem: str, sources_dir: Path) -> Path | None:
    for ext in VIDEO_EXTS:
        p = sources_dir / f"{stem}{ext}"
        if p.exists():
            return p.resolve()
    return None


def probe_duration(path: Path) -> float | None:
    import subprocess

    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return float(out)
    except Exception:
        return None


def parse_ids(value: str | None) -> set[tuple[str, int]]:
    out: set[tuple[str, int]] = set()
    for item in (value or "").split(","):
        item = item.strip()
        if not item:
            continue
        m = re.fullmatch(r"(.+):p(\d+)", item)
        if not m:
            sys.exit(f"bad phrase id {item!r}; expected <source>:p<N>, e.g. C0103:p4")
        out.add((m.group(1), int(m.group(2))))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="First-pass EDL: cut silences, fillers, repeated takes")
    ap.add_argument("--edit-dir", type=Path, required=True)
    ap.add_argument("--sources-dir", type=Path, default=None, help="Default: parent of edit dir")
    ap.add_argument("--max-gap", type=float, default=0.35, help="Silence (s) longer than this is cut. Default 0.35")
    ap.add_argument("--pad-before", type=float, default=0.06)
    ap.add_argument("--pad-after", type=float, default=0.10)
    ap.add_argument("--min-cut", type=float, default=0.15, help="Don't cut gaps shorter than this. Default 0.15")
    ap.add_argument("--keep-fillers", action="store_true", help="Don't cut hesitation sounds")
    ap.add_argument("--drop-retakes", action="store_true", help="Cut every phrase flagged as a retake")
    ap.add_argument("--drop-stutters", action="store_true", help="Cut the first copy of immediate word repeats")
    ap.add_argument("--drop", default=None, help="Comma list of phrase ids to cut, e.g. C0103:p4,C0103:p9")
    ap.add_argument("--keep", default=None, help="Comma list of phrase ids to keep even if flagged")
    ap.add_argument("--grade", default="none")
    args = ap.parse_args()

    for name in ("pad_before", "pad_after"):
        v = getattr(args, name)
        if not PAD_MIN <= v <= PAD_MAX:
            sys.exit(f"--{name.replace('_', '-')} must be within {PAD_MIN}-{PAD_MAX}s (Hard Rule 7)")

    edit_dir = args.edit_dir.resolve()
    sources_dir = (args.sources_dir or edit_dir.parent).resolve()
    tr_files = sorted((edit_dir / "transcripts").glob("*.json"))
    if not tr_files:
        sys.exit(f"no transcripts in {edit_dir / 'transcripts'}")

    drop_ids, keep_ids = parse_ids(args.drop), parse_ids(args.keep)
    sources: dict[str, str] = {}
    all_ranges: list[dict] = []
    report: list[str] = ["# Cut report", ""]
    total_in = total_out = 0.0

    for tr in tr_files:
        name = tr.stem
        src = find_source(name, sources_dir)
        if src is None:
            print(f"  skip {name}: no matching video in {sources_dir}")
            continue
        sources[name] = str(src)
        duration = probe_duration(src)
        words = load_words(tr)
        if not words:
            continue
        phrases = group_phrases(words, args.max_gap)
        retakes = find_retakes(words, phrases)
        stutters = find_stutters(words)

        keep = [True] * len(words)
        why: dict[int, str] = {}
        if not args.keep_fillers:
            for i, w in enumerate(words):
                if is_filler(w):
                    keep[i] = False
        if args.drop_stutters:
            for i in stutters:
                keep[i] = False
        for pi, idx in enumerate(phrases):
            pid = (name, pi + 1)
            if pid in keep_ids:
                for i in idx:
                    keep[i] = not is_filler(words[i]) or args.keep_fillers
                why[pi] = "kept (--keep)"
                continue
            if pid in drop_ids:
                why[pi] = "CUT (--drop)"
            elif pi in retakes and args.drop_retakes:
                why[pi] = f"CUT retake: {retakes[pi]}"
            elif pi in retakes:
                why[pi] = f"FLAG retake? {retakes[pi]}"
                continue
            else:
                continue
            for i in idx:
                keep[i] = False

        ranges = build_ranges(words, keep, duration, args.max_gap, args.pad_before, args.pad_after, args.min_cut)
        src_len = duration or words[-1]["end"]
        kept_len = sum(e - s for s, e, _ in ranges)
        total_in += src_len
        total_out += kept_len

        report += [f"## {name}  ({src_len:.1f}s → {kept_len:.1f}s, {len(ranges)} ranges)", ""]
        for pi, idx in enumerate(phrases):
            text = " ".join(
                (f"~~{words[i]['text']}~~" if not keep[i] else words[i]["text"]) for i in idx
            )
            tag = f"  ← {why[pi]}" if pi in why else ""
            if pi not in why and any(i in stutters for i in idx):
                tag = "  ← stutter (word repeated)" + ("" if args.drop_stutters else "; --drop-stutters to cut")
            report.append(f"- `{name}:p{pi + 1}` [{words[idx[0]]['start']:.2f}-{words[idx[-1]]['end']:.2f}] {text}{tag}")
        report.append("")

        for start, end, r in ranges:
            quote = " ".join(words[i]["text"] for i in r)
            all_ranges.append({
                "source": name,
                "start": round(start, 3),
                "end": round(end, 3),
                "quote": quote[:200],
                "reason": "auto: speech kept, silences/fillers removed",
            })

    if not all_ranges:
        sys.exit("nothing kept — check transcripts and source files")

    edl = {
        "version": 1,
        "sources": sources,
        "ranges": all_ranges,
        "grade": args.grade,
        "overlays": [],
        "total_duration_s": round(total_out, 2),
    }
    (edit_dir / "edl.json").write_text(json.dumps(edl, indent=2, ensure_ascii=False))
    summary = f"total: {total_in:.1f}s → {total_out:.1f}s (−{total_in - total_out:.1f}s, {len(all_ranges)} ranges)"
    report.insert(2, summary + "\n\nStruck-through words are cut. `FLAG` lines are repetition candidates still kept: "
                  "decide, then re-run with `--drop <id>` / `--keep <id>` or `--drop-retakes`.\n")
    (edit_dir / "cut_report.md").write_text("\n".join(report))
    print(summary)
    print(f"edl → {edit_dir / 'edl.json'}\nreport → {edit_dir / 'cut_report.md'}")


if __name__ == "__main__":
    main()
