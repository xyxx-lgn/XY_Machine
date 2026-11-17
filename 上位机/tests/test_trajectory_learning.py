import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from app.trajectory_learning import ROOT, content_labels, decode_trajectory, encode_reference, read_recordings, recorded_extent, reference_batch


class TrajectoryLearningTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".runtime/tmp").mkdir(parents=True, exist_ok=True)

    def test_reference_keeps_y_down_and_marks_each_pen_lift(self):
        paths = [[[0.1, 0.2], [0.1, 0.8]], [[0.2, 0.3], [0.8, 0.3]]]
        encoded = encode_reference(paths, 8)
        self.assertGreater(encoded[1, 1], 0)
        self.assertEqual(int((encoded[:, 2] < 0).sum()), 2)

    def test_decode_requires_complete_duration_and_keeps_final_stroke(self):
        sequence = np.asarray([[.1, 0, 1], [.1, 0, -1], [.1, .1, 1], [.1, 0, 1]], dtype=float)
        paths = decode_trajectory(sequence, 4)
        self.assertEqual(len(paths), 2)
        with self.assertRaisesRegex(ValueError, "长度"):
            decode_trajectory(sequence, 3)

    def test_generated_recording_cannot_be_used_as_real_reference(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".runtime/tmp") as folder:
            path = Path(folder) / "recorded-font.json"
            glyphs = {char: {"advance": 1, "variants": [{"strokes": [[[.1, .1], [.9, .9]]]}]} for char in "一二三四五六七八九十甲乙丙丁戊"}
            path.write_text(json.dumps({"schema": 1, "source": "OLHWG离线轨迹模型生成", "glyphs": glyphs}, ensure_ascii=False), encoding="utf8")
            with self.assertRaisesRegex(ValueError, "生成字"):
                read_recordings(folder)

    def test_dots_and_unfinished_last_stroke_are_not_removed(self):
        values = np.array([[.1, .1, -1], [.2, .2, 1], [.1, .1, 1], [.1, .1, 1]])
        paths = decode_trajectory(values, 4)
        self.assertEqual(len(paths), 2)
        self.assertEqual(len(paths[0]), 2)
        self.assertEqual(len(paths[1]), 3)
        self.assertGreater(np.ptp(paths[1], axis=0)[1], 0)

    def test_reference_batch_never_truncates_a_character(self):
        recordings = {char: {"variants": [{"strokes": [[[.1, .1], [.8, .9]]]}]} for char in "一二三四五六七八九十甲乙丙丁戊己庚辛壬癸"}
        batch, chosen = reference_batch(recordings, {"lengths": {}}, 2026)
        self.assertEqual(len(chosen), 15)
        self.assertEqual(batch.shape, (1, 1000, 3))
        self.assertTrue(np.all(batch[0, 675:, :2] == 0))
        self.assertTrue(np.all(batch[0, 675:, 2] == -1))

    def test_content_missing_and_invalid_durations_are_explicit(self):
        content = {"dictionary": ["中"], "lengths": {"中": 10}}
        labels, durations = content_labels(["中"], content)
        self.assertEqual((len(labels[0]), durations[0]), (120, [10, 110]))
        self.assertEqual((labels[0][0], labels[0][9], labels[0][10]), (4053, 4054, 4052))
        with self.assertRaisesRegex(ValueError, "不支持.*国"):
            content_labels(["国"], content)

    def test_new_glyph_uses_personal_recorded_size_instead_of_fixed_large_box(self):
        recordings = {"中": {"variants": [{"strokes": [[[.3, .3], [.7, .7]]]}]}}
        extent = recorded_extent(recordings)
        self.assertAlmostEqual(extent, .4)
        paths = decode_trajectory([[.1, .1, 1], [.1, .1, -1], [.1, .1, 1], [.1, .1, 1]], 4, extent)
        self.assertAlmostEqual(float(np.ptp(np.concatenate(paths), axis=0).max()), extent)


if __name__ == "__main__":
    unittest.main()
