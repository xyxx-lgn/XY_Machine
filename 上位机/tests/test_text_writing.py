import json
import math
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PySide6.QtCore import QPointF
from PySide6.QtGui import QPainterPath
from PySide6.QtWidgets import QApplication

from app.generation import GenerationCancelled, GenerationError, PlotOptions, save_gcode
from app.text_layout import TextOptions, character_paths, generate_artwork_text_task, generate_text_task, render_page
from app.text_writing import DATA, contact_strokes, hanzi_archive, layout_stroke_artwork, layout_stroke_text, nearest_point, stroke_glyph, validate_text

application = QApplication.instance() or QApplication([])


class StrokeWritingTests(unittest.TestCase):
    def test_real_hanzi_counts_and_fold(self):
        for char, count in [('十', 2), ('口', 3), ('中', 4), ('永', 5), ('田', 5), ('国', 8)]:
            self.assertEqual(len(stroke_glyph(char).strokes), count)
        fold = stroke_glyph('口').strokes[1]
        self.assertGreater(max(p[0] for p in fold) - fold[0][0], .3)
        self.assertGreater(fold[-1][1] - fold[0][1], .2)

    def test_original_points_keep_order_and_direction(self):
        # 补接点可以增加首尾点，原始中线所有点仍按原顺序出现。
        from zipfile import ZipFile
        with ZipFile(DATA / 'hanzi-writer-data-2.0.1.zip') as archive:
            for char in '十口中永田国你好写字':
                source = json.loads(archive.read(char + '.json'))['medians']
                final = stroke_glyph(char).strokes
                for raw, actual in zip(source, final):
                    expected = [(x / 1024, (900 - y) / 1024) for x, y in raw]
                    offset = 0 if actual[0] == expected[0] else 1
                    self.assertEqual(list(actual[offset:offset + len(raw)]), expected)

    def test_box_contacts_are_connected_without_merging_strokes(self):
        paths = stroke_glyph('口').strokes
        for index, path in enumerate(paths):
            distance = min(math.dist(point, nearest_point(point, a, b)) for other, target in enumerate(paths) if other != index for source, destination in ((path, target), (target, path)) for point in (source[0], source[-1]) for a, b in zip(destination, destination[1:]))
            self.assertLess(distance, 1e-8)
        self.assertEqual(len(paths), 3)

    def test_contact_correction_never_bridges_white_space(self):
        outlines = [QPainterPath(), QPainterPath()]
        outlines[0].addRect(0, 0, 20, 10)
        outlines[1].addRect(30, 0, 20, 10)
        medians = [[[1, 5], [18, 5]], [[32, 5], [48, 5]]]
        self.assertEqual(contact_strokes(medians, outlines), [list(map(tuple, path)) for path in medians])

    def test_dot_and_intentionally_separate_strokes_remain(self):
        self.assertEqual(len(stroke_glyph('永').strokes), 5)
        dot = stroke_glyph('永').strokes[0]
        others = stroke_glyph('永').strokes[1:]
        distance = min(math.dist(p, nearest_point(p, a, b)) for p in dot for target in others for a, b in zip(target, target[1:]))
        self.assertGreater(distance, .03)
        for char in '：；…':
            self.assertTrue(all(len(path) >= 2 for path in stroke_glyph(char).strokes))

    def test_latin_digits_ascii_and_fullwidth_stay_inside_cell(self):
        for char in ''.join(chr(i) for i in range(33, 127)) + 'ＡＢＷａｚ０９！？（）％':
            glyph = stroke_glyph(char)
            self.assertTrue(glyph.strokes, char)
            for x, y in [point for path in glyph.strokes for point in path]:
                self.assertTrue(0 <= x <= glyph.advance and 0 <= y <= 1, (char, x, y, glyph.advance))

    def test_missing_characters_are_explicit_and_deduplicated(self):
        with self.assertRaisesRegex(GenerationError, '缺少2种字符') as error:
            layout_stroke_text('你好🙂🙂𰻞')
        self.assertIn('U+1F642', str(error.exception))
        self.assertIn('U+30EDE', str(error.exception))

    def test_common_chinese_english_and_punctuation_are_offline(self):
        validate_text('你好，世界！“写字机”（版本：123）；中文、English。学习—测试…￥10×2÷1±3。')
        self.assertEqual(len(hanzi_archive().namelist()), 9574)

    def test_fixed_size_wraps_without_shrinking(self):
        wide = layout_stroke_text('永' * 30, TextOptions(font_size=18))
        narrow = layout_stroke_text('永' * 30, TextOptions(font_size=18, margin_right=145))
        self.assertGreater(len(narrow.pages[0].lines), len(wide.pages[0].lines))
        self.assertEqual(wide.pages[0].lines[0].characters[0].strokes, narrow.pages[0].lines[0].characters[0].strokes)

    def test_wrap_preserves_words_and_punctuation(self):
        options = TextOptions(paper_width=40, margin_left=5, margin_right=5, margin_top=5, margin_bottom=5)
        doc = layout_stroke_text('中文测试：你好，世界！ English words together.', options)
        for line in doc.pages[0].lines:
            self.assertFalse(line.text.startswith(('，', '。', '！', '：')))
        self.assertIn('English', ''.join(line.text for line in doc.pages[0].lines))
        self.assertFalse(any(line.text.endswith('Eng') for line in doc.pages[0].lines))

    def test_indentation_spacing_and_page_breaks(self):
        options = TextOptions(paper_width=60, paper_height=32, margin_left=5, margin_right=5, margin_top=5, margin_bottom=5, indent=2, letter_spacing=.3)
        doc = layout_stroke_text('你好，世界！' * 25, options)
        self.assertGreater(len(doc.pages), 1)
        first = doc.pages[0].lines[0].characters[0]
        self.assertAlmostEqual(first.position.x(), 5 + .09 + 2 * options.em_mm)
        for page in doc.pages:
            self.assertTrue(options.content_rect.adjusted(-.001, -.001, .001, .001).contains(page.bounds))

    def test_alignment_and_blank_paragraphs(self):
        left = layout_stroke_text('中文\n\n中文')
        centered = layout_stroke_text('中文', TextOptions(alignment='center'))
        right = layout_stroke_text('中文', TextOptions(alignment='right'))
        self.assertEqual(len(left.pages[0].lines), 3)
        self.assertTrue(left.pages[0].lines[1].path.isEmpty())
        self.assertLess(left.pages[0].bounds.left(), centered.pages[0].bounds.left())
        self.assertLess(centered.pages[0].bounds.left(), right.pages[0].bounds.left())

    def test_art_filters_and_optimizer_cannot_modify_strokes(self):
        doc = layout_stroke_text('口口中')
        page = doc.pages[0]
        normal = character_paths(page, PlotOptions())
        with patch('app.text_layout.order_paths', side_effect=AssertionError('reordered')):
            special = character_paths(page, PlotOptions(mode='fill', optimize=True, min_length=10, tolerance=1))
        self.assertEqual(normal, special)
        self.assertEqual(len(normal), 10)
        for a, b in zip(normal[:3], normal[3:6]):
            self.assertAlmostEqual(b[0][0] - a[0][0], doc.options.em_mm)

    def test_one_stroke_one_pen_down_and_source_sequence(self):
        doc = layout_stroke_text('口中')
        paths = character_paths(doc.pages[0], PlotOptions())
        task = generate_text_task(doc.pages[0], doc.options, PlotOptions(mode='outline'))
        source = task.program.source_text
        self.assertEqual(source.count('G53 G1 Z-0.4000'), 7)
        self.assertEqual(task.path_count, 7)
        # 从发给机器的文本恢复每一条实际落笔轨迹，包括落笔前的起点。
        actual, current, drawing = [], None, False
        for line in source.splitlines():
            if line.startswith('G1 X'):
                parts = line.split()
                point = (float(parts[1][1:]), float(parts[2][1:]))
                if drawing:
                    actual[-1].append(point)
                current = point
            elif line.startswith('G53 G1 Z-0.4000'):
                actual.append([current]); drawing = True
            elif line.startswith('G53 G1 Z2.0000'):
                drawing = False
        self.assertEqual(actual, [[(round(x, 4), round(y, 4)) for x, y in path] for path in paths])
        self.assertNotIn('G10', source)
        self.assertEqual(task.program.preview_pen_width, .18)

    def test_preview_width_does_not_add_paths_or_fill(self):
        a = layout_stroke_text('田国', pen_width=.1)
        b = layout_stroke_text('田国', pen_width=.3)
        ta = generate_text_task(a.pages[0], a.options, PlotOptions())
        tb = generate_text_task(b.pages[0], b.options, PlotOptions())
        self.assertEqual(ta.path_count, tb.path_count)
        self.assertEqual(ta.point_count, tb.point_count)
        self.assertFalse(render_page(a.pages[0], a.options).isNull())

    def test_cancellation_before_emission(self):
        doc = layout_stroke_text('测试')
        with self.assertRaises(GenerationCancelled):
            generate_text_task(doc.pages[0], doc.options, PlotOptions(), cancel=lambda: True)

    def test_artwork_uses_ordered_strokes(self):
        options = PlotOptions(x=10, y=20, width=40, height=15)
        page = layout_stroke_artwork('口中', 1.2, 'left', options)
        task = generate_artwork_text_task(page, options, render_page(page, TextOptions()))
        self.assertEqual(task.path_count, 7)
        self.assertTrue(9.99 <= task.program.bounds[0] < task.program.bounds[2] <= 50.01)
        self.assertTrue(19.99 <= task.program.bounds[1] < task.program.bounds[3] <= 35.01)

    def test_invalid_text_and_layout_parameters(self):
        for text in ['', '  ', '永' * 20001]:
            with self.assertRaises(GenerationError): layout_stroke_text(text)
        for width in [0, float('nan'), 2]:
            with self.assertRaises(GenerationError): layout_stroke_text('永', pen_width=width)


if __name__ == '__main__':
    unittest.main()
