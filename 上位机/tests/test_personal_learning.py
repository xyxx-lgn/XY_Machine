import copy
import json
import unittest
from unittest.mock import patch

import numpy as np

from app.personal_learning import (
    adapt_recorded_stroke, component_groups, component_index, component_strokes,
    constrain_junctions, decomposition_tree, fit_personal_model,
    generate_personal_glyph, model_fingerprint, paired_indices, predicted_stroke,
    stroke_positions
)
from app.personal_strokes import apply_affine, reference_strokes, resample


class PersonalLearningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.matrix = np.array([[.65, .02], [-.01, .7], [.15, .13]])
        cls.glyphs = {char: {"advance": 1., "variants": [{"strokes": [apply_affine(path, cls.matrix).tolist() for path in reference_strokes(char)]}]} for char in "天地人大上下左右中日月山木金土田口国目心手足言女文书"}
        cls.model = fit_personal_model(cls.glyphs)

    def test_component_asset_and_nested_structure(self):
        self.assertEqual(len(component_index()), 9574)
        self.assertEqual(decomposition_tree("⿰女子")[0], "⿰")
        self.assertIn(("木", [0, 1, 2, 3]), component_groups("林"))
        self.assertTrue({"木", "目", "心"} <= {symbol for symbol, indices in component_groups("想")})

    def test_real_components_generate_unrecorded_characters(self):
        before = copy.deepcopy(self.glyphs)
        paths, details = generate_personal_glyph("林", self.model)
        self.assertEqual(len(paths), 8)
        self.assertEqual(details["reused_strokes"], 8)
        self.assertEqual([item["recorded_character"] for item in details["components"]], ["木", "木"])
        self.assertEqual(self.glyphs, before)
        loaded = json.loads(json.dumps(self.model))
        self.assertEqual(generate_personal_glyph("林", loaded), (paths, details))

    def test_custom_combined_strokes_are_kept_in_new_character(self):
        glyphs = copy.deepcopy(self.glyphs)
        child = [apply_affine(path, self.matrix).tolist() for path in reference_strokes("子")]
        glyphs["子"] = {"advance": 1., "variants": [{"strokes": [child[0] + child[1], child[2]]}]}
        model = fit_personal_model(glyphs)
        paths, details = generate_personal_glyph("好", model)
        self.assertEqual(len(paths), 5)
        child_trace = next(item for item in details["components"] if item["component"] == "子")
        self.assertEqual(child_trace["personal_strokes"], 2)
        self.assertEqual(child_trace["recorded_character"], "子")

    def test_actual_recorded_stroke_change_affects_unrecorded_component(self):
        glyphs = copy.deepcopy(self.glyphs)
        path = glyphs["木"]["variants"][0]["strokes"][0]
        points = resample(path).copy()
        points[:, 1] += .025 * np.sin(np.linspace(0, np.pi, len(points)))
        glyphs["木"]["variants"][0]["strokes"][0] = points.tolist()
        model = fit_personal_model(glyphs)
        self.assertNotEqual(generate_personal_glyph("林", model)[0], generate_personal_glyph("林", self.model)[0])
        self.assertNotEqual(model_fingerprint(glyphs), model_fingerprint(self.glyphs))

    def test_learns_internal_shape_beyond_global_affine(self):
        glyphs = copy.deepcopy(self.glyphs)
        for glyph in glyphs.values():
            for version in glyph["variants"]:
                for index, path in enumerate(version["strokes"]):
                    points = resample(path).copy()
                    points[:, 1] += .025 * np.sin(np.linspace(0, np.pi, len(points)))
                    version["strokes"][index] = points.tolist()
        model = fit_personal_model(glyphs)
        path = reference_strokes("线")[0]
        learned, confidence = predicted_stroke(path, model)
        base = apply_affine(resample(path), np.asarray(model["matrix"]))
        self.assertGreater(float(np.linalg.norm(resample(learned) - base)), .005)

    def test_learned_junction_and_closed_ring_stay_connected(self):
        import app.personal_learning as module
        paths = [np.array([[.1, .5], [.9, .5]]), np.array([[.5, .1], [.5, .5]])]
        with patch.object(module, "reference_strokes", return_value=paths), patch.object(module, "component_groups", return_value=[]):
            output, details = generate_personal_glyph("接", self.model)
        endpoint = np.asarray(output[1][-1])
        self.assertLess(min(np.linalg.norm(np.asarray(point) - endpoint) for point in output[0]), 1e-10)
        loop = [np.array([[.2, .2], [.8, .2], [.8, .8], [.2, .8], [.2, .2]])]
        with patch.object(module, "reference_strokes", return_value=loop), patch.object(module, "component_groups", return_value=[]):
            output, details = generate_personal_glyph("环", self.model)
        self.assertEqual(output[0][0], output[0][-1])

    def test_unseen_characters_have_finite_independent_bounded_paths(self):
        for char in "离线扩展需要调用林明好休想":
            paths, details = generate_personal_glyph(char, self.model)
            self.assertGreater(len(paths), 1)
            for path in paths:
                points = np.asarray(path)
                self.assertTrue(np.isfinite(points).all())
                self.assertTrue((points >= 0).all() and (points <= 1).all())
                self.assertGreater(float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum()), 0)

    def test_invalid_samples_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "至少需要15"):
            fit_personal_model({"木": self.glyphs["木"]})
        glyphs = copy.deepcopy(self.glyphs)
        glyphs["木"]["variants"][0]["strokes"][0][0][0] = float("nan")
        with self.assertRaisesRegex(ValueError, "无效坐标"):
            fit_personal_model(glyphs)

    def test_context_turns_long_wood_stroke_into_short_side_component(self):
        identity = np.array([[1., 0.], [0., 1.], [0., 0.]])
        source = reference_strokes("木")[3]
        target = reference_strokes("林")[3]
        raw = resample(source)
        raw[:, 1] += .05 * np.linspace(0, 1, len(raw))
        output = adapt_recorded_stroke(source, raw, target, identity)
        base = resample(target, stroke_positions(target))
        self.assertLessEqual(float(np.linalg.norm(output - base, axis=1).max()), .026 + 1e-10)
        self.assertLess(np.linalg.norm(np.diff(output, axis=0), axis=1).sum(), np.linalg.norm(np.diff(source, axis=0), axis=1).sum() * .6)

    def test_component_feet_do_not_copy_wood_shared_start_into_qi(self):
        identity = np.array([[1., 0.], [0., 1.], [0., 0.]])
        wood, qi = reference_strokes("木"), reference_strokes("其")
        raw = [p.tolist() for p in wood[2:]]
        raw[1][0] = raw[0][0]
        candidates = [{"character": "木", "version": 0, "symbol": "八", "canonical": [p.tolist() for p in wood[2:]], "strokes": raw}]
        paths, character = component_strokes(candidates, qi[6:], identity)
        self.assertEqual(character, "木")
        self.assertGreater(float(np.linalg.norm(paths[0][0] - paths[1][0])), .1)

    def test_learned_displacement_cannot_scatter_small_structures(self):
        model = copy.deepcopy(self.model)
        model["weights"] = (np.asarray(model["weights"]) + 100).tolist()
        target = reference_strokes("展")[4]
        positions = stroke_positions(target)
        paths, score = predicted_stroke(target, model)
        base = apply_affine(resample(target, positions), np.asarray(model["matrix"]))
        self.assertLessEqual(float(np.linalg.norm(paths - base, axis=1).max()), .026 + 1e-10)

    def test_junction_constraint_includes_component_and_other_stroke(self):
        source = [np.array([[.1, .5], [.9, .5]]), np.array([[.5, .1], [.5, .5]])]
        output = [resample(path, stroke_positions(path, [0, .5, 1] if i == 0 else [0, 1])) for i, path in enumerate(source)]
        output[0] += [.015, .01]
        output[1] += [-.01, -.02]
        count = constrain_junctions(source, output, {0: 0, 1: 1})
        self.assertGreater(count, 0)
        self.assertLess(min(np.linalg.norm(p - output[1][-1]) for p in output[0]), 1e-10)
        self.assertLess(float(np.linalg.norm(np.diff(output[0], axis=0), axis=1).max()), .05)


if __name__ == "__main__":
    unittest.main()
