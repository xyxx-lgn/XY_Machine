import json
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QApplication

from app.generation import GenerationCancelled, GenerationError
from app.photo_samples import build_photo_package, crop_photo, extract_photo_samples, grid_cells, photo_font_package, prepare_photo_samples, trace_sample
from app.profile import ROOT
from app.stroke_fonts import FontStore, WritingOptions, package_data, validate_package
from app.text_writing import layout_stroke_text


class PhotoSampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        (ROOT / ".runtime/tmp").mkdir(parents=True, exist_ok=True)

    def test_every_photo_mode_can_roundtrip_in_the_existing_font_format(self):
        image = self.letter_image()
        for mode in ("continuous", "pen_shape", "ink", "legacy"):
            with self.subTest(mode=mode):
                sample = prepare_photo_samples(image, "甲", 1, 1, trajectory=mode, ink_width=.03)[0]
                self.assertFalse(sample.error, sample.error)
                package = build_photo_package([sample])
                restored = validate_package(json.loads(json.dumps(package_data(package))))
                self.assertEqual(restored.glyphs, package.glyphs)
                self.assertFalse(restored.ordered)
                with tempfile.TemporaryDirectory(dir=ROOT / ".runtime/tmp") as folder:
                    store = FontStore(folder)
                    saved = store.save(package)
                    self.assertEqual(store.load(saved).glyphs, package.glyphs)

    def test_oversized_photo_paths_fail_before_preview_or_collection(self):
        sample = prepare_photo_samples(self.letter_image(), "甲", 1, 1)[0]
        too_many = [[(40, 40), (50, 50)]] * 129
        with patch("app.photo_samples.ink_reproduction_paths", return_value=too_many):
            with self.assertRaisesRegex(GenerationError, "最多128条"):
                trace_sample("甲", sample.image, trajectory="ink")
        long_path = [(40 + i / 100, 40 + (i % 2)) for i in range(4001)]
        with patch("app.photo_samples.continuous_photo_paths", return_value=[long_path]):
            with self.assertRaisesRegex(GenerationError, "超过4000点"):
                trace_sample("甲", sample.image)

    def test_grid_photo_is_split_and_converted_to_candidate_font(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".runtime/tmp") as folder:
            folder = Path(folder)
            image = QImage(10 * 64, 2 * 64, QImage.Format.Format_RGB32)
            image.fill(0xFFFFFFFF)
            painter = QPainter(image)
            painter.setPen(QPen(QColor("#202020"), 5))
            for index in range(15):
                row, column = divmod(index, 10)
                x, y = column * 64, row * 64
                painter.drawLine(x + 15, y + 15, x + 49, y + 49)
            painter.end()
            source = folder / "photo.png"
            self.assertTrue(image.save(str(source)))
            output, chars = extract_photo_samples(source, "一二三四五六七八九十一二三四五", 2, 10, folder / "samples")
            self.assertEqual(len(chars), 15)
            package = photo_font_package(output)
            self.assertEqual(len(package.glyphs), 10)
            self.assertEqual(len(package.glyphs["一"]["variants"]), 2)
            self.assertEqual(len(package.record_plan), 15)
            self.assertFalse(package.ordered)
            self.assertFalse(validate_package(json.loads(json.dumps(package_data(package)))).ordered)
            self.assertTrue(package.source.startswith("照片中线导入"))
            self.assertEqual(len(json.loads((output / "samples.json").read_text(encoding="utf-8"))["samples"]), 15)

    def test_blank_photo_cell_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".runtime/tmp") as folder:
            folder = Path(folder)
            image = QImage(10 * 64, 2 * 64, QImage.Format.Format_RGB32)
            image.fill(0xFFFFFFFF)
            source = folder / "blank.png"
            image.save(str(source))
            with self.assertRaisesRegex(Exception, "几乎为空"):
                extract_photo_samples(source, "一二三四五六七八九十一二三四五", 2, 10, folder / "samples")

    def letter_image(self):
        image = QImage(200, 200, QImage.Format.Format_RGB32)
        image.fill(0xFFFFFFFF)
        painter = QPainter(image)
        painter.setPen(QPen(QColor("black"), 7))
        painter.drawLine(30, 40, 170, 40)
        painter.drawLine(100, 40, 100, 160)
        painter.end()
        return image

    def test_one_character_photo_keeps_aspect_and_placement(self):
        image = self.letter_image()
        painter = QPainter(image)
        painter.fillRect(0, 0, 200, 200, QColor("white"))
        painter.setPen(QPen(QColor("black"), 7))
        painter.drawLine(35, 80, 165, 80)
        painter.end()
        samples = prepare_photo_samples(image, "一", 1, 1, margin=0)
        package = build_photo_package(samples)
        points = [p for path in package.glyphs["一"]["variants"][0]["strokes"] for p in path]
        self.assertGreater(max(x for x, y in points) - min(x for x, y in points), .5)
        self.assertLess(max(y for x, y in points) - min(y for x, y in points), .05)
        self.assertTrue(all(.3 < y < .5 for x, y in points))
        page = layout_stroke_text("一一", writing=WritingOptions(package=package)).pages[0]
        self.assertFalse(page.stroke_ordered)

    def test_bad_grid_cell_retains_character_and_does_not_shift_following(self):
        image = QImage(600, 200, QImage.Format.Format_RGB32)
        image.fill(0xFFFFFFFF)
        painter = QPainter(image)
        painter.drawImage(0, 0, self.letter_image())
        painter.drawImage(400, 0, self.letter_image())
        painter.end()
        samples = prepare_photo_samples(image, "天地人", 1, 3)
        self.assertEqual([s.character for s in samples], list("天地人"))
        self.assertFalse(samples[0].error)
        self.assertIn("几乎为空", samples[1].error)
        self.assertFalse(samples[2].error)

    def test_template_gutters_crop_and_colored_lines(self):
        image = QImage(520, 240, QImage.Format.Format_RGB32)
        image.fill(0xFFFFFFFF)
        painter = QPainter(image)
        painter.drawImage(20, 20, self.letter_image())
        painter.drawImage(300, 20, self.letter_image())
        painter.setPen(QPen(QColor("red"), 3))
        painter.drawLine(20, 120, 500, 120)
        painter.end()
        samples = prepare_photo_samples(image, "甲乙", 1, 2, margin=0, gap_x=40, region=(20, 20, 500, 220))
        self.assertTrue(all(not s.error for s in samples))
        self.assertEqual(samples[0].strokes, samples[1].strokes)
        boxes = grid_cells(1880, 2420, 10, 10, 2 / 17 * 100, 8 / 17 * 100)
        self.assertEqual(boxes[0], (0, 0, 170, 170))
        self.assertEqual(boxes[-1], (1710, 2250, 1880, 2420))

    def test_perspective_correction_and_invalid_corners(self):
        pixels = np.ones((200, 300, 3), dtype=np.uint8) * 255
        result = crop_photo(pixels, [(20, 20), (280, 10), (260, 180), (40, 190)])
        self.assertGreater(result.shape[1], result.shape[0])
        for points in ([(0, 0), (200, 200), (0, 200), (200, 0)], [(0, 0), (1, 0), (1, 1), (0, 1)]):
            with self.assertRaises(GenerationError):
                crop_photo(pixels, points)

    def test_cancel_and_input_mismatch_never_write_output(self):
        with self.assertRaises(GenerationCancelled):
            prepare_photo_samples(self.letter_image(), "甲", 1, 1, cancel=lambda: True)
        with self.assertRaisesRegex(GenerationError, "字符数量"):
            prepare_photo_samples(self.letter_image(), "甲乙", 1, 1)
        sample = prepare_photo_samples(self.letter_image(), "甲", 1, 1)[0]
        with self.assertRaisesRegex(GenerationError, "16个版本"):
            build_photo_package([sample] * 17)

    def test_continue_recording_keeps_base_coordinates_and_versions(self):
        samples = prepare_photo_samples(self.letter_image(), "甲", 1, 1)
        base = build_photo_package(samples)
        before = json.dumps(package_data(base))
        extra = prepare_photo_samples(self.letter_image(), "乙", 1, 1)
        merged = build_photo_package(samples + extra, base_package=base)
        self.assertEqual(set(merged.glyphs), set("甲乙"))
        self.assertEqual(len(merged.glyphs["甲"]["variants"]), 2)
        self.assertEqual(merged.glyphs["甲"]["variants"][0], base.glyphs["甲"]["variants"][0])
        self.assertEqual(json.dumps(package_data(base)), before)


if __name__ == "__main__":
    unittest.main()
