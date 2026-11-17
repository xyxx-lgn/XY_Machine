import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app import builtin_stroke_fonts as fonts
from app.generation import GenerationError, PlotOptions
from app.profile import ROOT
from app.stroke_fonts import NaturalOptions, WritingOptions, natural_glyph
from app.text_layout import TextOptions, character_paths, generate_text_task
from app.text_writing import layout_stroke_text, missing_characters, nearest_point, resolve_glyph


class BuiltinStrokeFontTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_bundled_archives_match_audited_counts_hash_and_valid_paths(self):
        for identity, count in (("heiti", 20976), ("mingti", 27632)):
            metadata = json.loads((fonts.DATA / (identity + ".json")).read_text(encoding="utf-8"))
            source = fonts.DATA / (identity + ".zip")
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), metadata["archive_sha256"])
            self.assertFalse(metadata["ordered"])
            with ZipFile(source) as archive:
                self.assertEqual(len(archive.namelist()), count)
                paths_count, points_count = 0, 0
                for name in archive.namelist():
                    paths = json.loads(archive.read(name))
                    self.assertTrue(paths, name)
                    for path in paths:
                        self.assertGreaterEqual(len(path), 2, name)
                        self.assertTrue(all(a != b for a, b in zip(path, path[1:])), name)
                        self.assertTrue(all(0 <= x <= 1 and 0 <= y <= 1 for x, y in path), name)
                    paths_count += len(paths)
                    points_count += sum(map(len, paths))
                self.assertEqual((paths_count, points_count), (metadata["paths"], metadata["points"]))

    def test_actual_fonts_are_distinct_and_missing_glyph_is_never_silently_replaced(self):
        shapes = []
        for identity in ("", "builtin:heiti", "builtin:mingti"):
            writing = WritingOptions(builtin_font=identity)
            shapes.append(tuple(resolve_glyph(char, writing).strokes for char in "永国中文测试"))
            self.assertEqual(missing_characters("中文🙂🙂", writing), ["🙂"])
            self.assertEqual(resolve_glyph("A", writing), resolve_glyph("A", WritingOptions()))
        self.assertEqual(len(set(shapes)), 3)

    def test_metadata_does_not_claim_normative_stroke_order_and_tasks_keep_character_order(self):
        for identity in fonts.BUILTIN_FONTS:
            document = layout_stroke_text("永中", writing=WritingOptions(builtin_font=identity, natural=NaturalOptions(True, .7, 100)))
            page = document.pages[0]
            self.assertFalse(page.stroke_ordered)
            task = generate_text_task(page, document.options, PlotOptions())
            self.assertTrue(any("不承诺标准笔顺" in note for note in task.notes))
            self.assertFalse(any(note.startswith("按字库笔顺") for note in task.notes))
            paths = character_paths(page, PlotOptions())
            first = page.lines[0].characters[0]
            self.assertEqual(paths[:len(first.strokes)], [[(x + first.position.x(), y + first.position.y()) for x, y in path] for path in first.strokes])
            self.assertEqual(task.path_count, sum(len(c.strokes) for c in page.lines[0].characters))

    def test_missing_and_corrupt_archive_raise_readable_error(self):
        fonts.builtin_paths.cache_clear()
        self.addCleanup(fonts.builtin_paths.cache_clear)
        self.addCleanup(fonts.font_archive.cache_clear)
        with tempfile.TemporaryDirectory(dir=ROOT / ".runtime/tmp") as directory:
            with patch.object(fonts, "DATA", Path(directory)):
                fonts.font_archive.cache_clear()
                with self.assertRaisesRegex(GenerationError, "字库读取失败"):
                    fonts.builtin_paths("builtin:heiti", "测")
                (Path(directory) / "heiti.zip").write_bytes(b"broken")
                with self.assertRaisesRegex(GenerationError, "字库读取失败"):
                    fonts.builtin_paths("builtin:heiti", "测")
            fonts.font_archive.cache_clear()

    def test_smooth_deformation_curves_long_line_and_preserves_t_junction(self):
        source = (((.1, .4), (.9, .4)), ((.37, .4), (.37, .85)))
        paths, advance = natural_glyph(source, 1, NaturalOptions(True, 1, 2026), 1)
        self.assertEqual(len(paths), 2)
        self.assertGreater(len(paths[0]), 2)
        self.assertIn(paths[1][0], paths[0])
        deviation = max(math.dist(p, nearest_point(p, paths[0][0], paths[0][-1])) for p in paths[0])
        self.assertGreater(deviation, .001)
        self.assertTrue(all(0 <= x <= advance and 0 <= y <= 1 for path in paths for x, y in path))

    def test_controls_are_independent_and_zero_matches_original(self):
        def chars(natural):
            return layout_stroke_text("永永永永永", writing=WritingOptions(natural=natural)).pages[0].lines[0].characters

        source = chars(NaturalOptions())
        same = chars(NaturalOptions(True, 0, 5, 0, 0))
        self.assertEqual([(c.strokes, c.position) for c in source], [(c.strokes, c.position) for c in same])
        spacing = chars(NaturalOptions(True, 0, 5, 1, 0))
        self.assertEqual([c.strokes for c in source], [c.strokes for c in spacing])
        self.assertEqual(len({c.position.y() for c in spacing}), 1)
        self.assertNotEqual([c.position.x() for c in source], [c.position.x() for c in spacing])
        for a, b in zip(spacing, spacing[1:]):
            self.assertLess(a.path.boundingRect().right() + a.position.x(), b.path.boundingRect().left() + b.position.x())
        shape = chars(NaturalOptions(True, 1, 5, 0, 0))
        self.assertEqual(len({c.position.y() for c in shape}), 1)
        self.assertNotEqual(source[0].strokes, shape[0].strokes)
        baseline = chars(NaturalOptions(True, 0, 5, 0, 1))
        self.assertEqual([c.strokes for c in source], [c.strokes for c in baseline])
        self.assertGreater(len({c.position.y() for c in baseline}), 1)

    def test_new_fonts_with_all_variation_controls_wrap_inside_page(self):
        for identity in fonts.BUILTIN_FONTS:
            options = TextOptions(paper_width=55, paper_height=35, margin_left=4, margin_right=4, margin_top=4, margin_bottom=4, font_size=18)
            writing = WritingOptions(builtin_font=identity, natural=NaturalOptions(True, 1, 2147483647, 1, 1))
            document = layout_stroke_text("中文永田，永永田田。Hello123 " * 8, options, writing=writing)
            self.assertGreater(len(document.pages), 1)
            self.assertTrue(all(options.content_rect.contains(page.bounds) for page in document.pages))

    def test_new_strength_values_are_validated(self):
        for natural in (NaturalOptions(True, .5, 1, float("nan")), NaturalOptions(True, .5, 1, 0, 2), NaturalOptions(True, .5, 1, True)):
            with self.assertRaises(GenerationError):
                natural.validate()


if __name__ == "__main__":
    unittest.main()
