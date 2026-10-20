import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

HELPERS = Path(__file__).parents[1] / "helpers"


def load(name):
    spec = importlib.util.spec_from_file_location(f"video_use_{name}", HELPERS / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


auto_edl = load("auto_edl")
w2s = load("whisper_to_scribe")
e2h = load("edl_to_hyperframes")


def w(text, start, end):
    return {"type": "word", "text": text, "start": start, "end": end}


# "Hoje eu vou... ahn... Hoje eu vou mostrar o projeto. [long pause] Ele corta silêncios."
TAKE = [
    w("Hoje", 0.50, 0.70), w("eu", 0.72, 0.80), w("vou", 0.82, 1.00),
    w("ahn", 1.60, 2.00),
    w("Hoje", 2.80, 3.00), w("eu", 3.02, 3.10), w("vou", 3.12, 3.30), w("mostrar", 3.32, 3.70),
    w("o", 3.72, 3.76), w("projeto.", 3.78, 4.20),
    w("Ele", 6.00, 6.20), w("corta", 6.22, 6.50), w("silêncios.", 6.52, 7.10),
]


class FillerTests(unittest.TestCase):
    def test_fillers(self):
        for t in ["ahn", "Hum", "ééé", "uh,", "hmm", "hã", "ummm"]:
            self.assertTrue(auto_edl.is_filler({"text": t}), t)

    def test_not_fillers(self):
        for t in ["e", "é", "a", "há", "eu", "um", "uma", "aham"]:
            self.assertFalse(auto_edl.is_filler({"text": t}), t)


class RetakeTests(unittest.TestCase):
    def test_abandoned_start_is_flagged(self):
        words = [x for x in TAKE if x["text"] != "ahn"]
        phrases = auto_edl.group_phrases(words, 0.35)
        flagged = auto_edl.find_retakes(words, phrases)
        self.assertIn(0, flagged)
        self.assertNotIn(1, flagged)

    def test_stutter(self):
        words = [w("eu", 0, 0.1), w("eu", 0.15, 0.3), w("acho", 0.32, 0.6)]
        self.assertEqual(auto_edl.find_stutters(words), {0})


class BuildRangesTests(unittest.TestCase):
    def test_silences_and_fillers_cut_on_word_boundaries(self):
        keep = [not auto_edl.is_filler(x) for x in TAKE]
        ranges = auto_edl.build_ranges(TAKE, keep, 8.0, 0.35, 0.06, 0.10, 0.15)
        self.assertEqual(len(ranges), 3)
        (s0, e0, _), (s1, e1, _), (s2, e2, _) = ranges
        self.assertAlmostEqual(s0, 0.44)
        self.assertAlmostEqual(e0, 1.10)
        # never bleeds into the cut filler (1.60) or the next word
        self.assertLessEqual(e0, 1.30)
        self.assertGreaterEqual(s1, 2.40)
        self.assertAlmostEqual(s2, 5.94)
        self.assertAlmostEqual(e2, 7.20)

    def test_drop_retake(self):
        keep = [not auto_edl.is_filler(x) for x in TAKE]
        for i in range(3):
            keep[i] = False
        ranges = auto_edl.build_ranges(TAKE, keep, 8.0, 0.35, 0.06, 0.10, 0.15)
        self.assertEqual([r[2][0] for r in ranges], [4, 10])


class WhisperToScribeTests(unittest.TestCase):
    def test_spacing_inserted(self):
        out = w2s.to_scribe([{"text": "Oi", "start": 0.1, "end": 0.3}, {"text": "tudo", "start": 0.9, "end": 1.1}])
        types = [x["type"] for x in out["words"]]
        self.assertEqual(types, ["word", "spacing", "word"])
        self.assertAlmostEqual(out["words"][1]["end"] - out["words"][1]["start"], 0.6)


class RemapTests(unittest.TestCase):
    def test_output_timeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            edit = Path(tmp)
            (edit / "transcripts").mkdir()
            (edit / "transcripts" / "A.json").write_text(json.dumps({"words": TAKE}))
            edl = {"sources": {"A": "A.mp4"}, "ranges": [
                {"source": "A", "start": 2.74, "end": 4.30},
                {"source": "A", "start": 5.94, "end": 7.20},
            ]}
            durs = e2h.segment_durations(edl, edit, None)
            words = e2h.remap_words(edl, edit, durs)
            self.assertEqual([x["text"] for x in words][:2], ["Hoje", "eu"])
            self.assertAlmostEqual(words[0]["start"], 0.06, places=3)
            ele = next(x for x in words if x["text"] == "Ele")
            self.assertAlmostEqual(ele["start"], 1.56 + 0.06, places=3)
            self.assertNotIn("ahn", [x["text"] for x in words])


if __name__ == "__main__":
    unittest.main()
