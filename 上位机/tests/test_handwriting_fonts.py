"""手写字体的真实覆盖、轨迹完整性及逐字生成验证；不访问串口或网络。"""

from collections import Counter
import hashlib
import json
import math
import os
import unittest
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.builtin_stroke_fonts import BUILTIN_FONTS, CATALOG, DATA, builtin_paths, font_info
from app.generation import GenerationError, PlotOptions
from app.stroke_fonts import NaturalOptions, WritingOptions
from app.text_layout import TextOptions, character_paths, generate_text_task
from app.text_writing import layout_stroke_text, missing_characters, resolve_glyph
from tools.build_handwriting_fonts import gb2312_hanzi, join_paths


class HandwritingFontsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.catalog = json.loads(CATALOG.read_text(encoding="utf-8"))["fonts"]

    def test_sources_are_actual_bundled_fonts_with_audited_coverage(self):
        self.assertGreaterEqual(len(self.catalog), 16)
        self.assertEqual(len({entry["id"] for entry in self.catalog}), len(self.catalog))
        complete = 0
        for entry in self.catalog:
            identity = "builtin:" + entry["id"]
            info = font_info(identity)
            self.assertEqual(BUILTIN_FONTS[identity], entry["name"])
            archive_path = DATA / (entry["id"] + ".zip")
            self.assertEqual(hashlib.sha256(archive_path.read_bytes()).hexdigest(), info["archive_sha256"])
            self.assertTrue((DATA / info["license_file"]).is_file())
            self.assertFalse(info["ordered"])
            self.assertTrue(info["handwriting"])
            with ZipFile(archive_path) as archive:
                available = {chr(int(name[:-5], 16)) for name in archive.namelist()}
            self.assertEqual(len(available), info["glyphs"])
            missing = gb2312_hanzi() - available
            self.assertEqual(set(info["gb2312_missing"]), missing)
            self.assertEqual(info["gb2312_hanzi"], 6763 - len(missing))
            if not missing:
                complete += 1
        self.assertGreaterEqual(complete, 6)

    def test_all_archived_paths_are_finite_nonzero_and_inside_the_cell(self):
        for entry in self.catalog:
            info = font_info("builtin:" + entry["id"])
            paths_count, points_count = 0, 0
            with ZipFile(DATA / (entry["id"] + ".zip")) as archive:
                for name in archive.namelist():
                    paths = json.loads(archive.read(name))
                    self.assertTrue(paths, (entry["id"], name))
                    for path in paths:
                        self.assertGreaterEqual(len(path), 2)
                        self.assertTrue(all(a != b for a, b in zip(path, path[1:])), (entry["id"], name))
                        self.assertTrue(all(math.isfinite(v) and 0 <= v <= 1 for p in path for v in p), (entry["id"], name))
                    paths_count += len(paths)
                    points_count += sum(map(len, paths))
            self.assertEqual((paths_count, points_count), (info["paths"], info["points"]))

    def test_fonts_have_different_glyphs_and_missing_chars_cannot_silently_fall_back(self):
        signatures = set()
        for entry in self.catalog:
            identity = "builtin:" + entry["id"]
            writing = WritingOptions(builtin_font=identity)
            shapes = tuple(resolve_glyph(char, writing).strokes for char in "你好世界永田")
            signatures.add(shapes)
            self.assertEqual(missing_characters("你好🙂🙂", writing), ["🙂"])
            missing = font_info(identity)["gb2312_missing"]
            if missing:
                char = missing[0]
                self.assertIsNone(resolve_glyph(char, writing))
                with self.assertRaisesRegex(GenerationError, "缺少1种字符"):
                    layout_stroke_text("你好" + char, writing=writing)
        self.assertEqual(len(signatures), len(self.catalog))

    def test_each_character_is_finished_before_next_and_natural_layout_stays_inside_paper(self):
        options = TextOptions(paper_width=70, paper_height=35, margin_left=4, margin_right=4, margin_top=4, margin_bottom=4, font_size=18)
        for entry in self.catalog:
            writing = WritingOptions(builtin_font="builtin:" + entry["id"], natural=NaturalOptions(True, .7, 2026, .4, .2))
            document = layout_stroke_text("你好世界永田，Hello123。" * 3, options, writing=writing)
            for page in document.pages:
                self.assertTrue(options.content_rect.contains(page.bounds), entry["id"])
            page = document.pages[0]
            paths = character_paths(page, PlotOptions())
            first = page.lines[0].characters[0]
            expected = [[(x + first.position.x(), y + first.position.y()) for x, y in stroke] for stroke in first.strokes]
            self.assertEqual(paths[:len(expected)], expected)
            task = generate_text_task(page, document.options, PlotOptions())
            self.assertEqual(task.path_count, sum(len(char.strokes) for line in page.lines for char in line.characters))
            self.assertNotIn("G10", task.program.source_text)
            self.assertTrue(any("不承诺标准笔顺" in note for note in task.notes))

    def test_original_english_selection_is_retained(self):
        for entry in self.catalog:
            for char in "Hello123，。":
                self.assertEqual(resolve_glyph(char, WritingOptions(builtin_font="builtin:" + entry["id"])), resolve_glyph(char, WritingOptions()))

    def test_junction_joining_does_not_add_or_repeat_any_segment(self):
        def edges(paths):
            return Counter(tuple(sorted((a, b))) for path in paths for a, b in zip(path, path[1:]))

        source = [[(0, 0), (1, 0)], [(1, 0), (2, 0)], [(1, 0), (1, 1)], [(10, 0), (11, 0)], [(5, 5), (6, 5), (6, 6), (5, 5)]]
        actual = join_paths(source)
        self.assertEqual(edges(actual), edges(source))
        self.assertLess(len(actual), len(source))
        self.assertEqual(join_paths([]), [])


if __name__ == "__main__":
    unittest.main()
