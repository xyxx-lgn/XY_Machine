import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from app.generation import GenerationCancelled, GenerationError
from app.photo_ocr import recognize_photo, source_transform
from app.photo_samples import gray_image, normalize_cell, prepare_photo_samples, rgb_image


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


if __name__ == "__main__":
    unittest.main()
