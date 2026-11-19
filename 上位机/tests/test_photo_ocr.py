import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from app.generation import GenerationCancelled, GenerationError
from app.photo_ocr import character_boxes, recognize_photo, source_transform
from app.photo_samples import gray_image, normalize_cell, prepare_photo_samples, rgb_image
from app.photo_separation import isolate_line_band, separate_line_cells
from app.photo_recognition import align_character_positions, faint_line_indices, position_warnings, refine_character_cuts, verify_photo_labels
from app.photo_samples import PhotoSample


class PhotoOcrTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_auto_ink_keeps_pen_and_removes_tinted_shadow_paper(self):
        # 原纸面在120～190灰度之间，旧的210阈值会把绝大部分变为黑色。
        h, w = 180, 180
        field = np.tile(np.linspace(125, 190, w), (h, 1))
        pixels = np.dstack([field + 10, field + 5, field - 12]).astype(np.uint8)
        cv2.line(pixels, (30, 35), (150, 35), (26, 25, 24), 4)
        cv2.line(pixels, (90, 35), (90, 150), (26, 25, 24), 4)
        binary = normalize_cell(pixels, processing="auto")
        ink = binary < 128
        self.assertLess(float(ink.mean()), .10)
        self.assertGreater(float(ink.mean()), .01)
        samples = prepare_photo_samples(rgb_image(pixels), "丁", 1, 1, processing="auto")
        self.assertFalse(samples[0].error)
        self.assertIsNotNone(samples[0].original)

    def test_auto_ink_preserves_faint_thin_strokes_without_joining_spaces(self):
        pixels = np.full((150, 150, 3), (185, 180, 160), dtype=np.uint8)
        cv2.line(pixels, (45, 30), (45, 120), (105, 100, 95), 2)
        cv2.line(pixels, (75, 30), (75, 120), (105, 100, 95), 2)
        mask = (normalize_cell(pixels, processing="auto") < 128).astype(np.uint8)
        count, _ = cv2.connectedComponents(mask)
        self.assertEqual(count - 1, 2)

    def test_recognition_uses_character_locations_and_keeps_original_ink(self):
        pixels = np.full((100, 300, 3), 190, dtype=np.uint8)
        cv2.line(pixels, (35, 20), (35, 80), (20, 20, 20), 4)
        cv2.line(pixels, (160, 20), (270, 80), (20, 20, 20), 4)

        def engine(image, **kwargs):
            self.assertTrue(kwargs["return_word_box"])
            return [[[[0, 0], [299, 0], [299, 99], [0, 99]], "天地", .9,
                     [[[0, 0], [80, 0], [80, 99], [0, 99]], [[100, 0], [299, 0], [299, 99], [100, 99]]], ["天", "地"], [.98, .61]]], None

        samples = recognize_photo(rgb_image(pixels), engine=engine)
        self.assertEqual([s.character for s in samples], ["天", "地"])
        self.assertEqual([s.confidence for s in samples], [.98, .61])
        self.assertTrue(all(len(s.box) == 4 and not s.error for s in samples))
        self.assertNotEqual(samples[0].image, samples[1].image)
        self.assertLess(samples[0].original.width(), samples[1].original.width())
        self.assertIn("分数较低", samples[1].note)

    def test_empty_or_misaligned_ocr_and_cancel_are_explicit(self):
        image = QImage(100, 100, QImage.Format.Format_RGB32)
        image.fill(0xFFFFFFFF)
        with self.assertRaisesRegex(GenerationError, "未识别到"):
            recognize_photo(image, engine=lambda *args, **kwargs: (None, None))
        with self.assertRaises(GenerationCancelled):
            recognize_photo(image, cancel=lambda: True, engine=lambda *args: self.fail("取消后不应识字"))
        invalid = [[[[0, 0], [99, 0], [99, 99], [0, 99]], "天", .9, [], ["天"], [.9]]]
        with self.assertRaisesRegex(GenerationError, "数量不符"):
            recognize_photo(image, engine=lambda *args, **kwargs: (invalid, None))

    def test_separation_warning_is_attached_only_to_its_own_character(self):
        pixels = np.full((100, 240, 3), 255, dtype=np.uint8)
        cv2.line(pixels, (40, 20), (40, 80), (0, 0, 0), 5)
        cv2.line(pixels, (170, 20), (170, 80), (0, 0, 0), 5)
        line = [[[0, 0], [239, 0], [239, 99], [0, 99]], "甲乙", .99,
                [[[0, 0], [100, 0], [100, 99], [0, 99]], [[120, 0], [239, 0], [239, 99], [120, 99]]], ["甲", "乙"], [.99, .99]]

        def separate(line_rgb, ink, locations, cuts, cancel, reviews):
            reviews.extend(["字间墨迹相连，边界穿过笔画。", ""])
            return [(0, 110, pixels[:, :110]), (110, 240, pixels[:, 110:])]

        with patch("app.photo_ocr.separate_line_cells", side_effect=separate):
            samples = recognize_photo(rgb_image(pixels), engine=lambda *args, **kwargs: ([line], None))
        self.assertIn("边界穿过", samples[0].note)
        self.assertNotIn("边界穿过", samples[1].note)

    def test_source_highlight_maps_rectangles_and_perspective_back_to_photo(self):
        local = np.float32([[[0, 0], [99, 0], [99, 49], [0, 49]]])
        rectangle = source_transform((20, 30, 120, 80), 100, 50)
        np.testing.assert_allclose(cv2.perspectiveTransform(local, rectangle)[0], [[20, 30], [119, 30], [119, 79], [20, 79]], atol=.01)
        corners = [[20, 30], [140, 35], [130, 90], [25, 85]]
        perspective = source_transform(corners, 100, 50)
        np.testing.assert_allclose(cv2.perspectiveTransform(local, perspective)[0], corners, atol=.01)

    def test_faint_filter_is_relative_and_keeps_a_whole_faint_page(self):
        gray = np.full((180, 400), 245, np.uint8)
        def line(top, label, value, score):
            cv2.rectangle(gray, (25, top + 8), (350, top + 24), value, -1)
            return [[[10, top], [380, top], [380, top + 35], [10, top + 35]], label, score, [], [], []]
        lines = [line(5, "透印", 220, .45), line(60, "正文", 35, .98), line(110, "正文", 40, .99)]
        self.assertEqual(faint_line_indices(gray, lines), {0})
        self.assertEqual(faint_line_indices(gray, lines[:1]), set())
        lines[0][2] = .95
        self.assertEqual(faint_line_indices(gray, lines), set())

    def test_single_word_disagreement_marks_review_without_changing_original(self):
        pixels = np.full((64, 64, 3), 255, np.uint8)
        cv2.rectangle(pixels, (15, 15), (45, 50), (0, 0, 0), 3)
        samples = [PhotoSample(c, gray_image(pixels[:, :, 0]), [[(.2, .2), (.8, .8)]], original=rgb_image(pixels)) for c in "系条动"]
        before = [s.strokes for s in samples]
        verify_photo_labels(samples, lambda images: ([("条", .97), ("条", .99), ("动力", .93)], 0))
        self.assertEqual([s.character for s in samples], list("系条动"))
        self.assertEqual(samples[0].label_suggestion, "条")
        self.assertFalse(samples[1].source_flags)
        self.assertIn("多个字", samples[2].source_flags[0])
        self.assertEqual([s.strokes for s in samples], before)
        with self.assertRaises(GenerationCancelled):
            verify_photo_labels(samples, lambda x: self.fail("取消后不应识字"), cancel=lambda: True)

    def test_very_close_chinese_positions_are_not_silently_accepted(self):
        locations = [(x, c, .99) for x, c in zip((10, 50, 90, 130, 145, 185, 225), "机器人末立执行")]
        self.assertEqual(position_warnings(locations), {3, 4})

    def test_line_alignment_requires_clear_independent_improvement(self):
        locations = [(30 + 50 * i, c, .99) for i, c in enumerate("机器人传感系统")]
        pixels = np.full((80, 400, 3), 255, np.uint8)
        uncertain = lambda images: ([("错", .2)] * len(images), 0)
        self.assertIs(align_character_positions(pixels, locations, uncertain), locations)
        def improving(images):
            chars = [item[1] for item in locations]
            return ([("错", .2)] * 14 + [(c, .99) for c in chars], 0)
        shifted = align_character_positions(pixels, locations, improving)
        self.assertEqual([x[1:] for x in shifted], [x[1:] for x in locations])
        self.assertTrue(all(abs(a[0] - b[0] - 15) < .01 for a, b in zip(locations, shifted)))

    def test_mixed_numbers_and_chinese_keep_decoded_positions_before_display_box_clipping(self):
        pixels = np.full((60, 320, 3), 255, np.uint8)
        box = np.float32([[130, 80], [450, 80], [450, 140], [130, 140]])
        original_box = box.copy()
        result = ["中123文", .99, [80, [["中"], list("123"), ["文"]], [[10], [25, 35, 45], [60]],
                                    ["cn", "en&num", "cn"], [.91, .92, .93, .94, .95]]]
        output = character_boxes([pixels], [box], [result])[0]
        self.assertEqual(output[3], list("中123文"))
        self.assertEqual(output[4], result[2][4])
        centers = [np.asarray(b)[:, 0].mean() for b in output[2]]
        np.testing.assert_allclose(centers, [172, 232, 272, 312, 372], atol=1)
        np.testing.assert_array_equal(box, original_box)
        empty = character_boxes([pixels], [box], [["", 0, [80, [], [], [], [0]]]])
        self.assertEqual(empty[0][2:], [[], [], []])

    def test_short_name_line_can_be_aligned_without_changing_its_labels(self):
        locations = [(40 + 50 * i, char, .99) for i, char in enumerate("李广宁")]
        pixels = np.full((80, 190, 3), 255, np.uint8)
        def improving(images):
            return [("错", .2)] * 6 + [(char, .99) for char in "李广宁"], 0
        shifted = align_character_positions(pixels, locations, improving)
        self.assertEqual([x[1:] for x in shifted], [x[1:] for x in locations])
        self.assertTrue(all(abs(a[0] - b[0] - 15) < .01 for a, b in zip(locations, shifted)))

    def test_clear_ink_gap_cannot_be_pushed_inside_a_neighbor_by_ctc_positions(self):
        pixels = np.full((100, 240, 3), 255, np.uint8)
        cv2.rectangle(pixels, (25, 20), (65, 80), (0, 0, 0), 4)
        cv2.rectangle(pixels, (125, 20), (175, 80), (0, 0, 0), 4)
        ink = pixels[:, :, 0] < 128
        locations = [(110, "口", .99), (190, "口", .99)]
        cells = list(separate_line_cells(pixels, ink, locations, [0, 100, 240]))
        self.assertEqual(sum(np.count_nonzero(c[:, :, 0] < 128) for _, _, c in cells), np.count_nonzero(ink))
        self.assertEqual(np.count_nonzero(cells[0][2][:, :, 0] < 128), np.count_nonzero(ink[:, :100]))

    def test_short_line_curved_split_keeps_a_long_neighbor_stroke_whole(self):
        pixels = np.full((100, 240, 3), 255, np.uint8)
        cv2.rectangle(pixels, (25, 20), (85, 55), (0, 0, 0), 3)
        first = pixels[:, :, 0] < 128
        cv2.line(pixels, (140, 20), (78, 90), (0, 0, 0), 3)
        ink = pixels[:, :, 0] < 128
        # 两字符的横向外框重叠，但中间仍有弯曲空白；CTC峰靠得过近。
        cells = list(separate_line_cells(pixels, ink, [(100, "甲", .99), (125, "乙", .99)], [0, 110, 240]))
        self.assertEqual(np.count_nonzero(cells[0][2][:, :, 0] < 128), np.count_nonzero(first))
        self.assertEqual(np.count_nonzero(cells[1][2][:, :, 0] < 128), np.count_nonzero(ink & ~first))

    def test_moderate_disagreement_is_not_treated_as_a_clean_sample(self):
        pixels = np.full((64, 64, 3), 255, np.uint8)
        samples = [PhotoSample(char, gray_image(pixels[:, :, 0]), [], original=rgb_image(pixels)) for char in "社给"]
        verify_photo_labels(samples, lambda images: ([("土", .70), ("合", .3)], 0))
        self.assertTrue(all(s.source_flags for s in samples))
        self.assertTrue(all(not s.label_suggestion for s in samples))

    def test_boundary_repair_cannot_improve_one_character_by_destroying_its_neighbor(self):
        locations = [(25 + i * 50, char, .99) for i, char in enumerate("机器人传感系统")]
        cuts = list(range(0, 351, 50))
        pixels = np.full((80, 350, 3), 255, np.uint8)
        calls = []
        def recognizer(images):
            calls.append(len(images))
            if len(calls) == 1:
                return [(c if i else "错", .99) for i, (_, c, _) in enumerate(locations)], 0
            # 每次只支持一侧，不能因此移动两字之间的边界。
            return [("机" if i % 2 == 0 else "错", .99) for i in range(len(images))], 0
        self.assertEqual(refine_character_cuts(pixels, locations, cuts, recognizer), cuts)
        self.assertEqual(len(calls), 2)

    def test_short_numeric_line_is_verified_instead_of_skipping_all_non_chinese(self):
        pixels = np.full((80, 200, 3), 255, np.uint8)
        cuts, calls = [0, 100, 200], []
        def recognizer(images):
            calls.append(len(images))
            if len(calls) == 1:
                return [("12", .99), ("", 0)], 0
            return [("1" if i % 2 == 0 else "2", .99) for i in range(len(images))], 0
        adjusted = refine_character_cuts(pixels, [(50, "1", .99), (150, "2", .99)], cuts, recognizer)
        self.assertNotEqual(adjusted, cuts)
        self.assertEqual(adjusted[::2], [0, 200])
        self.assertEqual(cuts, [0, 100, 200])

    def test_repair_keeps_a_confident_neighbor_even_if_the_pair_total_improves(self):
        pixels = np.full((80, 200, 3), 255, np.uint8)
        cuts, calls = [0, 100, 200], []
        def recognizer(images):
            calls.append(len(images))
            if len(calls) == 1:
                return [("错", .1), ("乙", .99)], 0
            return [("甲", .99) if i % 2 == 0 else ("乙", .65) for i in range(len(images))], 0
        self.assertEqual(refine_character_cuts(pixels, [(50, "甲", .99), (150, "乙", .99)], cuts, recognizer), cuts)

    def test_thin_join_needs_geometry_and_recognition_before_becoming_a_reminder(self):
        pixels = np.full((64, 64, 3), 255, np.uint8)
        cv2.rectangle(pixels, (15, 15), (45, 50), (0, 0, 0), 3)
        note = "字间细连笔相连，边界穿过笔画；请复核分离后的字形。"
        sample = PhotoSample("口", gray_image(pixels[:, :, 0]), [], original=rgb_image(pixels), note=note)
        sample.strokes = [[(.25, .25), (.7, .7)]]
        verify_photo_labels([sample], lambda images: ([("口", .7)], 0))
        self.assertIn("边界穿过", sample.note)
        verify_photo_labels([sample], lambda images: ([("口", .99)], 0))
        self.assertNotIn("边界穿过", sample.note)
        self.assertIn("分离已复核", sample.note)

    def test_line_band_removes_separated_neighbor_without_cutting_main_ink(self):
        pixels = np.full((100, 600, 3), 255, np.uint8)
        for x in range(30, 570, 60):
            cv2.rectangle(pixels, (x, 20), (x + 25, 62), (0, 0, 0), 3)
            cv2.line(pixels, (x, 90), (x + 15, 99), (0, 0, 0), 2)
        ink = pixels[:, :, 0] < 128
        result, mask, changed = isolate_line_band(pixels, ink)
        self.assertTrue(changed)
        np.testing.assert_array_equal(result[:70], pixels[:70])
        self.assertFalse(mask[85:].any())
        np.testing.assert_array_equal(ink, pixels[:, :, 0] < 128)

    def test_line_band_keeps_horizontal_character_and_connected_descender(self):
        pixels = np.full((100, 100, 3), 255, np.uint8)
        for y in (20, 50, 80):
            cv2.line(pixels, (15, y), (85, y), (0, 0, 0), 3)
        result, _, changed = isolate_line_band(pixels, pixels[:, :, 0] < 128)
        self.assertFalse(changed)
        np.testing.assert_array_equal(result, pixels)
        wide = np.full((100, 600, 3), 255, np.uint8)
        cv2.rectangle(wide, (100, 20), (350, 65), (0, 0, 0), 3)
        cv2.line(wide, (350, 55), (350, 99), (0, 0, 0), 1)
        result, _, _ = isolate_line_band(wide, wide[:, :, 0] < 128)
        self.assertEqual(result[99, 350, 0], 0)


if __name__ == "__main__":
    unittest.main()
