"""Offline word-level transcription with sherpa-onnx + NVIDIA Parakeet TDT v3 (25 languages, incl. PT).

For when ElevenLabs is unavailable (no key, or the network blocks it). Accuracy is close to
Scribe, but Parakeet's own word end times run long over pauses, so this helper:

  1. finds real speech chunks from the audio (ffmpeg silencedetect),
  2. transcribes each chunk on its own,
  3. spreads that chunk's words inside the chunk's real start/end.

Word boundaries therefore agree with the audio, which is what silence cutting needs. Doing it
per chunk also surfaces false starts that a whole-file pass merges away.

Setup (once):
    pip install sherpa-onnx soundfile numpy
    curl -L -o parakeet.tar.bz2 https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2
    tar xjf parakeet.tar.bz2

Usage:
    python helpers/transcribe_local.py --model <parakeet_dir> --edit-dir <videos_dir>/edit <video> [<video> ...]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from whisper_to_scribe import to_scribe  # noqa: E402

SR = 16000


def silences(path: Path, threshold: str, min_sil: float) -> list[tuple[float, float]]:
    err = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af", f"silencedetect=noise={threshold}:d={min_sil}", "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    return list(zip(starts, ends + [1e9] * (len(starts) - len(ends))))


def speech_chunks(sil: list[tuple[float, float]], total: float, min_len: float) -> list[tuple[float, float]]:
    chunks, t = [], 0.0
    for a, b in sil:
        if a - t >= min_len:
            chunks.append((t, min(a, total)))
        t = b
    if total - t >= min_len:
        chunks.append((t, total))
    return chunks


def spread(tokens: list[tuple[str, float]], a: float, b: float) -> list[dict]:
    """Group sub-word tokens into words and place them inside [a, b] by their relative times."""
    words: list[dict] = []
    for tok, t in tokens:
        if tok.startswith((" ", "▁")) or not words:
            words.append({"text": tok.replace("▁", "").strip(), "t": t})
        else:
            words[-1]["text"] += tok
    words = [w for w in words if w["text"]]
    if not words:
        return []
    n = len(words)
    t0 = words[0]["t"]
    span = max(words[-1]["t"] - t0, 1e-3)
    usable = (b - a) * (n - 1) / n if n > 1 else 0.0  # leave the last word its share
    for w in words:
        w["start"] = round(a + (w["t"] - t0) / span * usable, 3) if n > 1 else round(a, 3)
    out = []
    for i, w in enumerate(words):
        end = words[i + 1]["start"] if i + 1 < n else round(b, 3)
        out.append({"text": w["text"], "start": w["start"], "end": end})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Offline Parakeet transcription → Scribe-shaped transcripts")
    ap.add_argument("videos", type=Path, nargs="+")
    ap.add_argument("--model", type=Path, required=True, help="Extracted sherpa-onnx Parakeet model dir")
    ap.add_argument("--edit-dir", type=Path, required=True)
    ap.add_argument("--silence-db", default="-38dB", help="silencedetect noise floor (default -38dB)")
    ap.add_argument("--min-silence", type=float, default=0.12)
    ap.add_argument("--language", default="pt")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    import numpy as np
    import sherpa_onnx
    import soundfile as sf

    m = args.model
    rec = sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=str(m / "encoder.int8.onnx"), decoder=str(m / "decoder.int8.onnx"),
        joiner=str(m / "joiner.int8.onnx"), tokens=str(m / "tokens.txt"),
        model_type="nemo_transducer", num_threads=4,
    )
    out_dir = args.edit_dir / "transcripts"
    out_dir.mkdir(parents=True, exist_ok=True)

    for v in args.videos:
        out = out_dir / f"{v.stem}.json"
        if out.exists() and not args.force:
            print(f"cached: {out.name}")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "a.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(v), "-ac", "1", "-ar", str(SR), str(wav)], check=True)
            audio, _ = sf.read(str(wav), dtype="float32")
        total = len(audio) / SR
        words: list[dict] = []
        for a, b in speech_chunks(silences(v, args.silence_db, args.min_silence), total, 0.12):
            pa, pb = max(0.0, a - 0.08), min(total, b + 0.08)
            s = rec.create_stream()
            s.accept_waveform(SR, np.ascontiguousarray(audio[int(pa * SR):int(pb * SR)]))
            rec.decode_stream(s)
            chunk = spread(list(zip(s.result.tokens, s.result.timestamps)), a, b)
            if chunk:
                print(f"  {v.stem} [{a:6.2f}-{b:6.2f}] {s.result.text.strip()}")
            words.extend(chunk)
        payload = to_scribe(words, args.language)
        payload["source"] = "sherpa-onnx parakeet-tdt-0.6b-v3 (energy-chunked)"
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"saved: {out} ({len(words)} words)")


if __name__ == "__main__":
    main()
