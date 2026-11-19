import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import cv2
import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF
from PySide6.QtGui import QFontDatabase, QPainterPathStroker
from PySide6.QtWidgets import QApplication

from app.appearance import configure_appearance
from app.generation import GenerationCancelled, GenerationError, PlotOptions, centerline_paths, thin_mask
from app.text_layout import TextOptions, generate_artwork_text_task, generate_text_task, layout_artwork, layout_text, line_mask, render_page


class TextLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        configure_appearance(cls.application)
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/simhei.ttf")

    def setUp(self):
        self.options = TextOptions()

    def layout(self, text, **changes):
        return layout_text(text, "Microsoft YaHei", options=replace(self.options, **changes))

    def test_long_text_wraps_without_enter(self):
        text = "中文测试，自动换行。" * 30
        document = self.layout(text)
        self.assertGreater(len(document.pages[0].lines), 1)
        self.assertEqual("".join(line.text for page in document.pages for line in page.lines), text)

    def test_appending_text_keeps_first_line_geometry(self):
        first = self.layout("HOB")
        longer = self.layout("HOB\n" + "中文正文" * 100)
        self.assertEqual(first.pages[0].lines[0].path.boundingRect(), longer.pages[0].lines[0].path.boundingRect())

    def test_font_size_doubles_physical_glyph(self):
        small, large = self.layout("HOB", font_size=12), self.layout("HOB", font_size=24)
        self.assertAlmostEqual(large.pages[0].bounds.height() / small.pages[0].bounds.height(), 2, places=5)
        self.assertAlmostEqual(large.pages[0].bounds.width() / small.pages[0].bounds.width(), 2, places=5)

    def test_narrow_paper_adds_lines_and_keeps_size(self):
        wide = self.layout("中文自动换行" * 30)
        narrow = self.layout("中文自动换行" * 30, paper_width=110)
        self.assertGreater(sum(len(p.lines) for p in narrow.pages), sum(len(p.lines) for p in wide.pages))
        self.assertAlmostEqual(narrow.pages[0].lines[0].height, wide.pages[0].lines[0].height)

    def test_english_prefers_word_boundaries(self):
        doc = self.layout("English words must stay whole whenever possible.", paper_width=65, margin_left=10, margin_right=10)
        for line in doc.pages[0].lines[:-1]:
            self.assertTrue(line.text.endswith(" "), repr(line.text))

    def test_cjk_punctuation_does_not_start_line(self):
        doc = self.layout("测试，标点。" * 20, paper_width=50, margin_left=2, margin_right=2)
        for page in doc.pages:
            for line in page.lines:
                self.assertNotIn(line.text[:1], ("，", "。"))

    def test_manual_blank_lines_and_indent(self):
        doc = self.layout("第一段\n\n第二段", indent=2)
        self.assertEqual([line.text for line in doc.pages[0].lines], ["第一段", "", "第二段"])
        self.assertTrue(doc.pages[0].lines[1].path.isEmpty())
        self.assertGreater(doc.pages[0].lines[0].path.boundingRect().left(), 29)

    def test_alignment_uses_writable_width(self):
        positions = [self.layout("HOB", alignment=a).pages[0].bounds.left() for a in ("left", "center", "right")]
        self.assertLess(positions[0], positions[1])
        self.assertLess(positions[1], positions[2])

    def test_tighter_system_font_spacing_reduces_width_without_resizing_glyphs(self):
        text = "中文 HOB 123"
        normal, tight = (self.layout(text, letter_spacing=spacing) for spacing in (0, -.2))
        a, b = normal.pages[0].lines[0], tight.pages[0].lines[0]
        self.assertLess(b.path.boundingRect().width(), a.path.boundingRect().width() - 1)
        self.assertEqual([char.path for char in a.characters], [char.path for char in b.characters])

    def test_minimum_spacing_does_not_reverse_narrow_glyphs(self):
        text = "i.l: Iii"
        doc = self.layout(text, font_size=6, letter_spacing=-5)
        origins = [char.position.x() for char in doc.pages[0].lines[0].characters]
        self.assertEqual(len(origins), len(text.replace(" ", "")))
        self.assertTrue(all(left < right for left, right in zip(origins, origins[1:])))

    def test_negative_spacing_leaves_room_for_ink_at_the_end_of_wrapped_lines(self):
        text = "正文内容" * 40
        for alignment in ("left", "center", "right"):
            doc = self.layout(text, font_size=18, letter_spacing=-5, margin_left=31.75, margin_right=31.75, alignment=alignment)
            lines = [line for page in doc.pages for line in page.lines]
            self.assertGreater(len(lines), 1)
            self.assertEqual("".join(line.text for line in lines), text)
            self.assertTrue(all(doc.options.content_rect.contains(line.path.boundingRect()) for line in lines))

    def test_pages_preserve_all_lines_inside_margins(self):
        doc = self.layout("写字机测试\n" * 100)
        self.assertGreater(len(doc.pages), 1)
        self.assertEqual(sum(len(p.lines) for p in doc.pages), 101)
        for page in doc.pages:
            self.assertTrue(doc.options.content_rect.adjusted(-0.001, -0.001, 0.001, 0.001).contains(page.bounds))

    def test_negative_bearing_letter_is_not_clipped(self):
        doc = self.layout("j" * 100)
        self.assertGreaterEqual(doc.pages[0].bounds.left(), self.options.margin_left - 0.001)

    def test_blank_and_invalid_parameters_rejected(self):
        for text, changes in (("", {}), ("abc", {"font_size": float("nan")}), ("abc", {"font_size": 2}), ("abc", {"margin_left": 205}), ("abc", {"alignment": "invalid"}), ("abc", {"letter_spacing": -5.01})):
            with self.assertRaises(GenerationError):
                self.layout(text, **changes)

    def test_page_generation_preserves_size_holes_and_coordinates(self):
        doc = self.layout("HOB")
        for mode in ("outline", "fill", "outline_fill", "centerline"):
            task = generate_text_task(doc.pages[0], doc.options, PlotOptions(mode=mode))
            x0, y0, x1, y1 = task.program.bounds
            self.assertGreater(task.program.drawing_length, 0)
            self.assertGreaterEqual(x0, 20)
            self.assertLessEqual(x1, 190)
            self.assertGreaterEqual(y0, 20)
            self.assertLessEqual(y1, 277)
            self.assertEqual(task.program.z_bounds, (-0.4, 2))
            self.assertNotIn("G10", task.program.source_text)
            stroker = QPainterPathStroker()
            stroker.setWidth(0.05)
            region = doc.pages[0].lines[0].path
            edge = stroker.createStroke(region)
            for segment in task.program.segments:
                if segment.drawing:
                    for start, end in zip(segment.points, segment.points[1:]):
                        middle = QPointF((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
                        self.assertTrue(region.contains(middle) or edge.contains(middle) or mode in ("outline", "outline_fill", "centerline"))

    def test_line_raster_keeps_same_resolution_for_long_text(self):
        short, long = self.layout("H"), self.layout("H" * 40)
        small = line_mask(short.pages[0].lines[0].path)
        large = line_mask(long.pages[0].lines[0].path)
        self.assertEqual(small[3], large[3])
        self.assertEqual(small[0].shape[0], large[0].shape[0])

    def test_cancelled_page_does_not_generate_task(self):
        doc = self.layout("HOB")
        with self.assertRaises(GenerationCancelled):
            generate_text_task(doc.pages[0], doc.options, PlotOptions(), cancel=lambda: True)

    def assert_character_order(self, page, task):
        areas = [char.path.translated(char.position).boundingRect().adjusted(-0.03, -0.03, 0.03, 0.03) for line in page.lines for char in line.characters]
        owners = []
        for segment in task.program.segments:
            if segment.drawing:
                matches = [index for index, area in enumerate(areas) if all(area.contains(QPointF(x, y)) for x, y in segment.points)]
                self.assertEqual(len(matches), 1, "每条落笔路径只能属于一个字")
                owners.append(matches[0])
        self.assertEqual(owners, sorted(owners), "不能跳到后面的字再回来补写前面的字")
        self.assertEqual(set(owners), set(range(len(areas))), "每个可绘制字都必须完成")

    def test_each_character_finishes_before_next_in_all_modes(self):
        doc = self.layout("iH 国B\n执失利", letter_spacing=1)
        for mode in ("outline", "fill", "outline_fill", "centerline"):
            for optimize in (False, True):
                task = generate_text_task(doc.pages[0], doc.options, PlotOptions(mode=mode, optimize=optimize))
                self.assert_character_order(doc.pages[0], task)

    def test_repeated_characters_reuse_geometry_but_new_tasks_recompute(self):
        doc = self.layout("中中 ABCABC 中")
        with patch("app.text_layout.centerline_paths", wraps=centerline_paths) as trace:
            generate_text_task(doc.pages[0], doc.options, PlotOptions(mode="centerline"))
            self.assertEqual(trace.call_count, 4)
            generate_text_task(doc.pages[0], doc.options, PlotOptions(mode="centerline", tolerance=0.01))
            self.assertEqual(trace.call_count, 8)

    def test_combining_accent_stays_with_letter(self):
        doc = self.layout("A e\u0301 中")
        self.assertEqual(len(doc.pages[0].lines[0].characters), 3)
        task = generate_text_task(doc.pages[0], doc.options, PlotOptions(mode="centerline"))
        self.assert_character_order(doc.pages[0], task)

    def test_character_groups_preserve_original_line_ink(self):
        doc = self.layout("写字机 HOB e\u0301 j", indent=2)
        line = doc.pages[0].lines[0]
        combined = type(line.path)()
        combined.setFillRule(line.path.fillRule())
        for char in line.characters:
            combined.addPath(char.path.translated(char.position))
        original = line_mask(line.path)[0]
        regrouped = line_mask(combined)[0]
        self.assertTrue(np.array_equal(original, regrouped))

    def raster_paths(self, paths, shape, step=0.02):
        image = np.zeros(shape, np.uint8)
        for path in paths:
            points = np.rint(np.asarray(path) / step).astype(np.int32)
            cv2.polylines(image, [points], False, 255, 1)
        return image

    def test_short_bridge_survives_text_filter(self):
        mask = np.zeros((25, 25), np.uint8)
        mask[3:22, 10] = mask[3:22, 12] = 255
        mask[12, 10:13] = 255
        paths = centerline_paths(mask, 0.02, 0.02, PlotOptions(min_length=0.08), None, preserve_connections=True)
        result = self.raster_paths(paths, mask.shape)
        self.assertEqual(cv2.connectedComponents(result, connectivity=8)[0], 2)
        self.assertTrue(result[12, 11])

    def test_loop_keeps_attachment_after_simplifying(self):
        mask = np.zeros((35, 35), np.uint8)
        cv2.rectangle(mask, (8, 8), (25, 25), 255, 1)
        mask[16, 2:9] = 255
        paths = centerline_paths(mask, 0.02, 0.02, PlotOptions(min_length=0.08, tolerance=0.08), None, preserve_connections=True)
        result = self.raster_paths(paths, mask.shape)
        self.assertEqual(cv2.connectedComponents(result, connectivity=8)[0], 2)
        self.assertTrue(result[16, 8])

    def test_intentional_blank_between_strokes_stays_blank(self):
        mask = np.zeros((25, 50), np.uint8)
        mask[12, 4:20] = mask[12, 23:44] = 255
        paths = centerline_paths(mask, 0.02, 0.02, PlotOptions(), None, preserve_connections=True)
        result = self.raster_paths(paths, mask.shape)
        self.assertEqual(cv2.connectedComponents(result, connectivity=8)[0], 3)
        self.assertFalse(np.any(result[:, 20:23]))

    def test_simhei_junction_characters_keep_component_connectivity(self):
        if "SimHei" not in QFontDatabase.families():
            self.skipTest("系统未提供黑体字体")
        for size in (14, 18):
            doc = layout_text("开失利执张因", "SimHei", options=TextOptions(font_size=size))
            for char in doc.pages[0].lines[0].characters:
                mask, _, _, step = line_mask(char.path)
                paths = centerline_paths(mask, step, step, PlotOptions(), None, preserve_connections=True)
                result = self.raster_paths(paths, mask.shape, step)
                original_count = cv2.connectedComponents(thin_mask(mask, None), connectivity=8)[0]
                self.assertEqual(cv2.connectedComponents(result, connectivity=8)[0], original_count)

    def test_single_pixel_skeleton_dot_still_draws(self):
        doc = self.layout(". 。 ! i", letter_spacing=1)
        task = generate_text_task(doc.pages[0], doc.options, PlotOptions(mode="centerline"))
        self.assert_character_order(doc.pages[0], task)

    def test_thinning_preserves_disconnected_parts_and_ring_hole(self):
        mask = np.zeros((50, 80), np.uint8)
        cv2.rectangle(mask, (3, 3), (30, 40), 255, -1)
        cv2.rectangle(mask, (10, 10), (23, 33), 0, -1)
        mask[15:25, 50:72] = 255
        skeleton = thin_mask(mask, None)
        self.assertEqual(cv2.connectedComponents(skeleton, connectivity=8)[0], 3)
        _, hierarchy = cv2.findContours(skeleton, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        self.assertTrue(any(parent >= 0 for _, _, _, parent in hierarchy[0]))

    def test_artwork_scaling_preserves_character_order_and_dimensions(self):
        source = self.layout("iH 国B\n执失利")
        for crop in (False, True):
            options = PlotOptions(mode="centerline", x=10, y=12, width=55, height=20, crop=crop)
            page = layout_artwork("iH 国B\n执失利", "Microsoft YaHei", False, 1.2, "center", options)
            task = generate_artwork_text_task(page, options, render_page(source.pages[0], source.options))
            self.assert_character_order(page, task)
            self.assertGreaterEqual(task.program.bounds[0], 10)
            self.assertLessEqual(task.program.bounds[2], 65)
            if crop:
                self.assertAlmostEqual(page.bounds.width(), 55)
                self.assertAlmostEqual(page.bounds.height(), 20)


if __name__ == "__main__":
    unittest.main()
