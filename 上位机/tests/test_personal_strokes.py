import copy
import random
import unittest

import numpy as np

from app.personal_strokes import (
    apply_affine, calibrated_glyph, check_generated_strokes, contact_positions,
    fit_recorded_style, reference_strokes, resample
)


class PersonalStrokesTests(unittest.TestCase):
    def setUp(self):
        self.matrix = np.array([[.65, .025], [-.015, .7], [.15, .13]])
        self.glyphs = {char: {"variants": [{"strokes": [apply_affine(path, self.matrix).tolist() for path in reference_strokes(char)]}]} for char in "天地人大上下左右中日月山木金土田口国目心手足言女文书"}

    def test_learns_recorded_scale_slant_without_changing_sources(self):
        before = copy.deepcopy(self.glyphs)
        style = fit_recorded_style(self.glyphs)
        actual = calibrated_glyph("线", style)
        expected = [apply_affine(path, self.matrix) for path in reference_strokes("线")]
        self.assertLess(np.mean([np.linalg.norm(resample(a) - resample(b), axis=1).mean() for a, b in zip(actual, expected)]), .015)
        self.assertEqual(self.glyphs, before)

    def test_new_characters_keep_all_strokes_bounds_and_nonzero_paths(self):
        style = fit_recorded_style(self.glyphs)
        for char, count in (("离", 10), ("线", 8), ("扩", 6)):
            paths = calibrated_glyph(char, style)
            self.assertEqual(len(paths), count)
            self.assertTrue(all(0 <= coordinate <= 1 for path in paths for point in path for coordinate in point))
            self.assertTrue(all(np.linalg.norm(np.diff(path, axis=0), axis=1).sum() > 0 for path in paths))

    def test_reordered_reference_strokes_still_match(self):
        for glyph in self.glyphs.values():
            glyph["variants"][0]["strokes"].reverse()
        style = fit_recorded_style(self.glyphs)
        self.assertGreater(style["paired_strokes"], 60)
        self.assertEqual(len(calibrated_glyph("扩", style)), 6)

    def test_contact_in_middle_of_a_stroke_is_preserved(self):
        paths = [np.array([[.1, .5], [.9, .5]]), np.array([[.5, .1], [.5, .5]])]
        self.assertEqual(contact_positions(paths, 0), [0., .5, 1.])
        import app.personal_strokes as module
        from unittest.mock import patch
        style = fit_recorded_style(self.glyphs)
        with patch.object(module, "reference_strokes", return_value=paths):
            calibrated = calibrated_glyph("接", style)
        endpoint = np.array(calibrated[1][-1])
        self.assertLess(min(np.linalg.norm(point - endpoint) for point in calibrated[0]), 1e-10)

    def test_known_combined_strokes_do_not_force_a_false_pairing(self):
        self.glyphs["永"] = {"variants": [{"strokes": [[[.1, .1], [.4, .4]]]}]}
        style = fit_recorded_style(self.glyphs)
        self.assertIn("永", style["skipped"])
        self.assertNotIn("永", style["characters"])

    def test_small_reference_set_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "可靠配对的录字不足"):
            fit_recorded_style({"中": self.glyphs["中"]})

    def test_sdt_single_stroke_regression_is_blocked(self):
        for char in "离线扩":
            with self.assertRaisesRegex(ValueError, "被SDT预测为1笔"):
                check_generated_strokes(char, [[[.1, .1], [.8, .8]]])
        check_generated_strokes("一", [[[.1, .1], [.8, .8]]])
        check_generated_strokes("线", reference_strokes("线"))

    def test_seeded_template_selection_is_reproducible(self):
        style = fit_recorded_style(self.glyphs)
        self.assertEqual(calibrated_glyph("线", style, random.Random(20)), calibrated_glyph("线", style, random.Random(20)))


if __name__ == "__main__":
    unittest.main()
