import copy
import json
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.generation import GenerationError, PlotOptions
from app.profile import ROOT
from app.stroke_fonts import FontPackage, FontStore, LATIN_STYLES, NaturalOptions, WritingOptions, natural_glyph, package_data, validate_package, write_package
from app.text_layout import TextOptions, character_paths, generate_text_task
from app.text_writing import layout_stroke_text, missing_characters, resolve_glyph, stroke_glyph


def sample_package():
    return validate_package({"schema": 1, "name": "个人测试", "glyphs": {"永": {"advance": 1, "variants": [{"strokes": [[[.2, .2], [.5, .5], [.8, .2]], [[.5, .5], [.5, .8]]]}, {"strokes": [[[.1, .2], [.5, .55], [.9, .2]], [[.5, .55], [.5, .9]]]}]}}})


class StrokeFontTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        (ROOT / ".runtime/tmp").mkdir(parents=True, exist_ok=True)

    def test_json_round_trip_import_preserves_strokes_versions_and_name(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".runtime/tmp") as folder:
            store = FontStore(Path(folder) / "fonts")
            package = sample_package()
            identity = store.save(package)
            loaded = store.load(identity)
            self.assertEqual(package_data(loaded), package_data(package))
            target = write_package(package, Path(folder) / "export.json")
            imported = store.import_file(target)
            self.assertNotEqual(identity, imported.id)
            self.assertEqual(len(store.catalog()), 2)

    def test_record_plan_round_trip_preserves_repeated_version_slots(self):
        package = sample_package()
        package.record_plan = tuple("永永天地永")
        with tempfile.TemporaryDirectory(dir=ROOT / ".runtime/tmp") as folder:
            store = FontStore(folder)
            self.assertEqual(store.load(store.save(package)).record_plan, tuple("永永天地永"))

    def test_invalid_packages_do_not_replace_saved_font(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".runtime/tmp") as folder:
            target = Path(folder) / "font.json"
            package = sample_package()
            write_package(package, target)
            old = target.read_bytes()
            package.glyphs["永"]["variants"][0]["strokes"] = [[(float("nan"), 0), (1, 1)]]
            with self.assertRaises(GenerationError):
                write_package(package, target)
            self.assertEqual(target.read_bytes(), old)

    def test_schema_finite_bounds_versions_and_zero_length_rejected(self):
        base = json.loads(json.dumps(package_data(sample_package())))
        cases = []
        for key, value in (("schema", 2), ("name", ""), ("glyphs", {})):
            data = copy.deepcopy(base); data[key] = value; cases.append(data)
        for advance in (0, 3, float("nan"), True):
            data = copy.deepcopy(base); data["glyphs"]["永"]["advance"] = advance; cases.append(data)
        for paths in ([], [[[0, 0]]], [[[0, 0], [0, 0]]], [[[0, 0], [1.1, 1]]], [[[0, 0], [1, float("inf")]]]):
            data = copy.deepcopy(base); data["glyphs"]["永"]["variants"][0]["strokes"] = paths; cases.append(data)
        data = copy.deepcopy(base); data["glyphs"]["永"]["variants"] *= 9; cases.append(data)
        for data in cases:
            with self.subTest(data=data), self.assertRaises(GenerationError):
                validate_package(data)

    def test_catalog_ignores_invalid_json_and_id(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".runtime/tmp") as folder:
            store = FontStore(folder)
            (Path(folder) / ("user_" + "a" * 32 + ".json")).write_text("{", encoding="utf-8")
            write_package(sample_package(), Path(folder) / "user_invalid.json")
            self.assertEqual(store.catalog(), [])
            for identity in ("../secret", "user_invalid", ""):
                with self.assertRaises(GenerationError): store.load(identity)

    def test_delete_removes_only_a_valid_saved_user_font(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".runtime/tmp") as folder:
            store = FontStore(folder)
            package = sample_package()
            identity = store.save(package)
            marker = Path(folder) / "keep.json"
            marker.write_text("keep", encoding="utf-8")
            self.assertEqual(store.delete(identity), identity)
            self.assertFalse((Path(folder) / (identity + ".json")).exists())
            self.assertTrue(marker.exists())
            with self.assertRaises(GenerationError):
                store.delete("../keep")
            with self.assertRaises(GenerationError):
                store.delete(identity)

    def test_custom_priority_and_explicit_fallback(self):
        package = sample_package()
        options = WritingOptions(package)
        self.assertEqual(len(resolve_glyph("永", options).strokes), 2)
        self.assertEqual(len(resolve_glyph("中", options).strokes), 4)
        options.fallback = False
        self.assertEqual(missing_characters("永中中A，🙂", options), ["中", "🙂"])
        with self.assertRaisesRegex(GenerationError, "缺少1种字符"):
            layout_stroke_text("永中", writing=options)
        doc = layout_stroke_text("永中", writing=WritingOptions(package))
        self.assertIn("中", "".join(doc.pages[0].writing_notes))

    def test_real_latin_styles_have_different_shapes_and_valid_bounds(self):
        shapes = []
        for style, title in LATIN_STYLES:
            shapes.append(tuple(stroke_glyph(char, style).strokes for char in "Hello123"))
            for char in "".join(chr(i) for i in range(33, 127)) + "ＡＷ０９！？（）":
                glyph = stroke_glyph(char, style)
                self.assertTrue(glyph.strokes, (style, char))
                self.assertTrue(all(0 <= x <= glyph.advance and 0 <= y <= 1 for path in glyph.strokes for x, y in path), (style, char))
        self.assertEqual(len(set(shapes)), 4)

    def test_seed_is_repeatable_but_occurrences_and_seed_change_shape(self):
        writing = WritingOptions(natural=NaturalOptions(True, .7, 12345))
        a = layout_stroke_text("永永永永永", writing=writing)
        b = layout_stroke_text("永永永永永", writing=writing)
        paths = character_paths(a.pages[0], PlotOptions())
        self.assertEqual(paths, character_paths(b.pages[0], PlotOptions()))
        chars = a.pages[0].lines[0].characters
        self.assertEqual(len({char.strokes for char in chars}), 5)
        self.assertGreater(len({char.position.y() for char in chars}), 1)
        c = layout_stroke_text("永永永永永", writing=WritingOptions(natural=NaturalOptions(True, .7, 12346)))
        self.assertNotEqual(paths, character_paths(c.pages[0], PlotOptions()))

    def test_disabled_and_zero_strength_match_old_geometry(self):
        original = character_paths(layout_stroke_text("口中 永\nEnglish 123").pages[0], PlotOptions())
        disabled = layout_stroke_text("口中 永\nEnglish 123", writing=WritingOptions(natural=NaturalOptions(False, 1, 999)))
        zero = layout_stroke_text("口中 永\nEnglish 123", writing=WritingOptions(natural=NaturalOptions(True, 0, 999)))
        self.assertEqual(original, character_paths(disabled.pages[0], PlotOptions()))
        self.assertEqual(original, character_paths(zero.pages[0], PlotOptions()))

    def test_shared_field_preserves_shared_junction_and_stroke_order(self):
        source = sample_package().glyphs["永"]["variants"][0]["strokes"]
        for seed in range(1, 20):
            paths, advance = natural_glyph(source, 1, NaturalOptions(True, 1, seed), 2)
            self.assertEqual(len(paths), len(source))
            self.assertIn(paths[1][0], paths[0])
            self.assertTrue(all(0 <= x <= advance and 0 <= y <= 1 for path in paths for x, y in path))

    def test_variants_selected_with_stable_seed_and_retain_recorded_order(self):
        writing = WritingOptions(sample_package(), natural=NaturalOptions(True, .5, 42))
        versions = [resolve_glyph("永", writing, i).strokes for i in range(20)]
        self.assertEqual(len(set(versions)), 2)
        self.assertEqual(versions, [resolve_glyph("永", writing, i).strokes for i in range(20)])
        writing.natural = NaturalOptions(False)
        self.assertEqual(len({resolve_glyph("永", writing, i).strokes for i in range(20)}), 1)

    def test_natural_wrap_pagination_alignment_margins_all_styles(self):
        for style, title in LATIN_STYLES:
            for align in ("left", "center", "right"):
                options = TextOptions(paper_width=60, paper_height=40, margin_left=4, margin_right=4, margin_top=4, margin_bottom=4, alignment=align, indent=1, font_size=18)
                writing = WritingOptions(latin_style=style, natural=NaturalOptions(True, 1, 512))
                doc = layout_stroke_text("中文测试。English 123 Wgj()！" * 12, options, writing=writing)
                self.assertGreater(len(doc.pages), 1)
                for page in doc.pages:
                    self.assertTrue(options.content_rect.adjusted(-.001, -.001, .001, .001).contains(page.bounds))

    def test_gcode_uses_prepared_natural_geometry_without_resampling(self):
        writing = WritingOptions(natural=NaturalOptions(True, .7, 99))
        doc = layout_stroke_text("口口中", writing=writing)
        a = generate_text_task(doc.pages[0], doc.options, PlotOptions())
        b = generate_text_task(doc.pages[0], doc.options, PlotOptions())
        self.assertEqual(a.program.source_text, b.program.source_text)
        self.assertEqual(a.path_count, 10)
        self.assertTrue(any("99" in note for note in a.notes))
        self.assertNotIn("G10", a.program.source_text)

    def test_invalid_natural_options(self):
        for natural in (NaturalOptions(True, float("nan")), NaturalOptions(True, 2), NaturalOptions(True, .5, 0), NaturalOptions(True, .5, True)):
            with self.assertRaises(GenerationError): layout_stroke_text("永", writing=WritingOptions(natural=natural))


if __name__ == "__main__":
    unittest.main()
