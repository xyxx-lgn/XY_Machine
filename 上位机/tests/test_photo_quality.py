"""照片字库整批质量评分、等比例校准和原始候选保护。"""

import os
import json
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import cv2
from PySide6.QtGui import QImage

from app.photo_quality import analyze_photo_samples, calibrate_photo_samples, sample_metrics
from app.photo_samples import PhotoSample, cell_frame, export_photo_samples, gray_image, load_photo_samples, normalize_cell, rgb_image, trace_sample
from app.profile import ROOT
from app.stroke_fonts import validate_package, package_data


class PhotoQualityTests(unittest.TestCase):
    def sample(self, character, top, bottom, left=78, right=176, note=""):
        pixels = np.full((256, 256), 255, np.uint8)
        cv2.rectangle(pixels, (left, top), (right, bottom), 0, 5)
        sample = trace_sample(character, gray_image(pixels))
        sample.note = note
        return sample

    def test_batch_report_flags_size_baseline_and_empty_candidates(self):
        samples = [self.sample("甲", 58, 184), self.sample("乙", 95, 125, 110, 140), self.sample("丙", 22, 226),
                   PhotoSample("丁", gray_image(np.full((256, 256), 255, np.uint8)), [])]
        report = analyze_photo_samples(samples)
        self.assertEqual(report.valid_count, 3)
        self.assertTrue(any("尺寸偏离" in flag for flag in report.items[1].flags))
        self.assertTrue(any("尺寸偏离" in flag for flag in report.items[2].flags))
        self.assertTrue(report.items[3].blocking)
        self.assertIn("重点核对", report.summary())

    def test_uniform_calibration_uses_common_baseline_and_preserves_aspect(self):
        samples = [self.sample("甲", 40, 180, 80, 160), self.sample("乙", 65, 155, 70, 190), self.sample("丙", 20, 220, 100, 145)]
        before = samples[1].image.copy()
        report = analyze_photo_samples(samples)
        calibrated, after = calibrate_photo_samples(samples, "uniform", report=report)
        heights = [item.ink_height for item in after.items]
        baselines = [item.bottom for item in after.items]
        before_heights = [item.ink_height for item in report.items]
        self.assertLess(max(heights) - min(heights), max(before_heights) - min(before_heights))
        self.assertLess(max(baselines) - min(baselines), .05)
        self.assertEqual(samples[1].image, before)
        self.assertTrue(all(item.coverage >= .94 for item in after.items))
        # 窄字不会被独立拉成方块，校准仍保持每个字的宽高比例。
        first, third = sample_metrics(calibrated[0]), sample_metrics(calibrated[2])
        self.assertGreater(first["width"] / first["height"], third["width"] / third["height"] * 1.5)

    def test_natural_calibration_keeps_some_variation_and_raw_is_copy(self):
        samples = [self.sample("甲", 45, 175), self.sample("乙", 66, 154), self.sample("丙", 25, 215)]
        natural, natural_report = calibrate_photo_samples(samples, "natural", .55)
        heights = [item.ink_height for item in natural_report.items]
        self.assertGreater(max(heights) - min(heights), .01)
        raw, _ = calibrate_photo_samples(samples, "raw")
        self.assertIsNot(raw[0], samples[0])
        self.assertEqual(raw[0].image, samples[0].image)

    def source_sample(self, char, width, line="line"):
        pixels = np.full((100, width, 3), 255, np.uint8)
        cv2.rectangle(pixels, (width // 2 - 18, 25), (width // 2 + 18, 80), (0, 0, 0), 3)
        sample = trace_sample(char, gray_image(normalize_cell(pixels)))
        sample.original, sample.line_id, sample.source_frame = rgb_image(pixels), line, cell_frame(pixels)
        return sample

    def test_different_crop_widths_use_same_source_line_scale(self):
        samples = [self.source_sample(c, w) for c, w in zip("甲乙丙", (60, 100, 145))]
        raw = analyze_photo_samples(samples)
        self.assertGreater(max(q.ink_height for q in raw.items) - min(q.ink_height for q in raw.items), .10)
        aligned, report = calibrate_photo_samples(samples)
        self.assertLess(max(q.ink_height for q in report.items) - min(q.ink_height for q in report.items), .02)
        self.assertLess(max(q.bottom for q in report.items) - min(q.bottom for q in report.items), .02)
        self.assertTrue(all(q.coverage > .94 for q in report.items))

    def test_horizontal_character_and_punctuation_are_not_enlarged_to_body_height(self):
        samples = [self.source_sample(c, 100) for c in "甲乙丙"]
        for char, y in (("一", 54), ("。", 82)):
            pixels = np.full((100, 100, 3), 255, np.uint8)
            if char == "一":
                cv2.line(pixels, (20, y), (80, y), (0, 0, 0), 3)
            else:
                cv2.circle(pixels, (40, y), 3, (0, 0, 0), 1)
            sample = trace_sample(char, gray_image(normalize_cell(pixels)))
            sample.original, sample.line_id, sample.source_frame = rgb_image(pixels), "line", cell_frame(pixels)
            samples.append(sample)
        aligned, report = calibrate_photo_samples(samples, "uniform")
        self.assertLess(report.items[-2].ink_height, .12)
        self.assertLess(report.items[-1].ink_height, .2)
        self.assertLess(report.items[-2].bottom, report.items[0].bottom - .12)
        self.assertFalse(any("尺寸" in flag for flag in report.items[-2].flags))

    def test_missing_side_is_detected_even_with_high_ocr_confidence(self):
        sample = self.sample("口", 40, 200, 40, 200)
        sample.confidence = .999
        sample.strokes = [[(40 / 255, 40 / 255), (200 / 255, 40 / 255), (200 / 255, 200 / 255), (40 / 255, 200 / 255)]]
        report = analyze_photo_samples([sample])
        self.assertTrue(report.items[0].blocking)
        self.assertLess(report.items[0].coverage, .85)
        self.assertTrue(any("可能漏笔" in f for f in report.items[0].flags))

    def test_small_missing_dot_is_detected_even_with_high_total_coverage(self):
        sample = self.sample("宁", 60, 200, 40, 210)
        pixels = sample.image.convertToFormat(QImage.Format.Format_Grayscale8)
        array = np.frombuffer(pixels.constBits(), np.uint8).reshape(256, pixels.bytesPerLine())[:, :256].copy()
        cv2.circle(array, (125, 30), 3, 0, -1)
        sample.image = gray_image(array)
        report = analyze_photo_samples([sample])
        self.assertGreater(report.items[0].coverage, .94)
        self.assertEqual(report.items[0].missing_parts, 1)
        self.assertTrue(report.items[0].blocking)

    def test_successful_line_isolation_is_a_reminder_but_bad_cut_is_blocking(self):
        sample = self.sample("口", 60, 200, 40, 210)
        sample.source_flags = ("行框已隔离疑似上下行墨迹，请对照原图核对",)
        report = analyze_photo_samples([sample])
        self.assertFalse(report.items[0].blocking)
        self.assertTrue(report.items[0].flags)
        sample.note = "边界穿过笔画"
        self.assertTrue(analyze_photo_samples([sample]).items[0].blocking)

    def test_recalibration_is_repeatable_and_restores_raw_without_losing_corrected_label(self):
        samples = [self.source_sample(c, w) for c, w in zip("甲乙丙", (60, 100, 145))]
        once, _ = calibrate_photo_samples(samples)
        twice, _ = calibrate_photo_samples(once)
        self.assertEqual(once[0].strokes, twice[0].strokes)
        once[0].character = "天"
        restored, _ = calibrate_photo_samples(once, "raw")
        self.assertEqual(restored[0].character, "天")
        self.assertEqual(restored[0].strokes, samples[0].strokes)
        self.assertEqual(restored[0].image, samples[0].image)

    def test_hundreds_of_samples_all_remain_inside_font_frame_and_keep_coverage(self):
        samples = [self.source_sample(chr(0x5000 + i), (65, 90, 120, 150)[i % 4], f"row-{i // 10}") for i in range(300)]
        aligned, report = calibrate_photo_samples(samples)
        self.assertEqual(len(report.items), 300)
        self.assertTrue(all(item.coverage >= .94 for item in report.items))
        self.assertTrue(all(0 <= x <= 1 and 0 <= y <= 1 for sample in aligned for path in sample.strokes for x, y in path))
        self.assertTrue(all(sample.original == original.original for sample, original in zip(aligned, samples)))

    def test_export_import_keeps_calibrated_and_raw_samples_for_future_rework(self):
        samples = [self.source_sample(c, w) for c, w in zip("甲乙丙", (60, 100, 145))]
        aligned, _ = calibrate_photo_samples(samples)
        aligned[0].label_suggestion, aligned[0].label_score = "天", .97
        (ROOT / ".runtime/tmp").mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / ".runtime/tmp") as directory:
            folder = Path(directory) / "batch"
            export_photo_samples(aligned, folder)
            loaded = load_photo_samples(folder)
            self.assertEqual(len(loaded), 3)
            self.assertEqual(loaded[0].image, aligned[0].image)
            self.assertEqual(loaded[0].original.convertToFormat(QImage.Format.Format_RGB888), aligned[0].original)
            restored, _ = calibrate_photo_samples(loaded, "raw")
            self.assertEqual(restored[0].image, samples[0].image)
            self.assertEqual(restored[0].strokes, samples[0].strokes)
            self.assertEqual(restored[0].source_flags, samples[0].source_flags)
            self.assertEqual(loaded[0].source_frame, aligned[0].source_frame)
            self.assertEqual(loaded[0].strokes, aligned[0].strokes)
            self.assertEqual((loaded[0].label_suggestion, loaded[0].label_score), ("天", .97))
            self.assertEqual((restored[0].label_suggestion, restored[0].label_score), ("天", .97))

    def test_corrupt_raw_coordinates_are_rejected_without_retracing(self):
        samples, _ = calibrate_photo_samples([self.source_sample("甲", 100)])
        with tempfile.TemporaryDirectory(dir=ROOT / ".runtime/tmp") as directory:
            folder = Path(directory) / "batch"
            export_photo_samples(samples, folder)
            manifest = json.loads((folder / "samples.json").read_text(encoding="utf8"))
            manifest["samples"][0]["raw"]["strokes"] = [[[0, 0], [5, 5]]]
            (folder / "samples.json").write_text(json.dumps(manifest), encoding="utf8")
            from app.generation import GenerationError
            with self.assertRaises(GenerationError):
                load_photo_samples(folder)


if __name__ == "__main__":
    unittest.main()
