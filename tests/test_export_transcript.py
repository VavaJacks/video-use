import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "helpers" / "export_transcript.py"
SPEC = importlib.util.spec_from_file_location("video_use_export_transcript", MODULE_PATH)
assert SPEC and SPEC.loader
export = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(export)


def w(text, start, end, kind="word"):
    return {"type": kind, "text": text, "start": start, "end": end}


class OutputWordsTests(unittest.TestCase):
    def test_ranges_are_shifted_onto_output_timeline(self):
        transcripts = {
            "A": {"words": [
                w("Olá", 1.0, 1.4), w(" ", 1.4, 1.5, "spacing"), w("pessoal", 1.5, 2.0),
                w("é...", 2.5, 3.0),  # repetition / filler that the EDL drops
                w("hoje", 4.0, 4.3),
            ]},
            "B": {"words": [w("vamos", 10.0, 10.4)]},
        }
        edl = {"ranges": [
            {"source": "A", "start": 0.95, "end": 2.08},
            {"source": "A", "start": 3.95, "end": 4.38},
            {"source": "B", "start": 9.9, "end": 10.5},
        ]}
        got = export.output_words(edl, transcripts)
        self.assertEqual([x["text"] for x in got], ["Olá", "pessoal", "hoje", "vamos"])
        self.assertAlmostEqual(got[0]["start"], 0.05)
        self.assertAlmostEqual(got[2]["start"], 1.13 + 0.05)
        self.assertAlmostEqual(got[3]["start"], 1.13 + 0.43 + 0.1)

    def test_word_straddling_a_cut_is_clamped(self):
        transcripts = {"A": {"words": [w("cortado", 1.0, 2.0)]}}
        got = export.output_words({"ranges": [{"source": "A", "start": 1.5, "end": 3.0}]}, transcripts)
        self.assertEqual(got, [{"text": "cortado", "start": 0.0, "end": 0.5}])

    def test_word_split_across_adjacent_ranges_is_kept_once(self):
        transcripts = {"A": {"words": [w("são", 1.0, 1.5), w("contas", 1.6, 2.0)]}}
        edl = {"ranges": [{"source": "A", "start": 0.9, "end": 1.1}, {"source": "A", "start": 1.2, "end": 2.1}]}
        got = export.output_words(edl, transcripts)
        self.assertEqual([x["text"] for x in got], ["são", "contas"])
        self.assertAlmostEqual(got[0]["start"], 0.2)


if __name__ == "__main__":
    unittest.main()
