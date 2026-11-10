import unittest
from dataclasses import replace

import cv2
import numpy as np
from PySide6.QtGui import QImage

from app.generation import GenerationError, PlotOptions, generate_task
from app.image_processing import ProcessingCancelled, binary_thumbnail, process_image, remove_specks


def rgb_image(rgb):
    rgb = np.ascontiguousarray(rgb)
    return QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888).copy()


def cartoon_source():
    rgb = np.full((160, 160, 3), 255, np.uint8)
    cv2.ellipse(rgb, (80, 92), (40, 54), 0, 0, 360, (250, 235, 90), -1)
    cv2.circle(rgb, (80, 51), 31, (250, 235, 90), -1)
    cv2.ellipse(rgb, (80, 108), (21, 28), 0, 0, 360, (249, 242, 205), -1)
    cv2.circle(rgb, (67, 46), 5, (20, 50, 20), -1)
    cv2.circle(rgb, (93, 46), 5, (20, 50, 20), -1)
    cv2.ellipse(rgb, (80, 65), (12, 6), 0, 0, 180, (70, 20, 10), -1)
    return rgb_image(rgb)


class ImageProcessingTests(unittest.TestCase):
    def setUp(self):
        self.image = cartoon_source()
        self.options = PlotOptions(processing="cartoon", mode="centerline", crop=False, width=20, height=20)

    def test_cartoon_keeps_light_yellow_outer_shape(self):
        old = process_image(self.image, replace(self.options, processing="threshold", threshold=200))
        new = process_image(self.image, replace(self.options, detail_level=0))
        old_box = cv2.boundingRect(old.mask)
        new_box = cv2.boundingRect(new.mask)
        self.assertLess(old_box[3] / 160, 0.4)
        self.assertGreater(new_box[3] / 1000, 0.75)

    def test_cartoon_outer_boundary_is_closed(self):
        processed = process_image(self.image, replace(self.options, detail_level=0))
        contours, _ = cv2.findContours(processed.mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        self.assertEqual(len(contours), 1)
        self.assertGreater(cv2.contourArea(contours[0]), 200000)

    def test_internal_details_can_be_switched_off(self):
        outline = process_image(self.image, replace(self.options, detail_level=0))
        details = process_image(self.image, replace(self.options, detail_level=70))
        self.assertGreater(np.count_nonzero(details.mask), np.count_nonzero(outline.mask))
        self.assertEqual(details.mask[0, 0], 0)

    def test_color_edge_detects_similar_gray_colors(self):
        rgb = np.full((100, 100, 3), (255, 128, 0), np.uint8)
        rgb[:, 50:] = (0, 255, 0)
        processed = process_image(rgb_image(rgb), replace(self.options, processing="edges", despeckle=0))
        self.assertGreater(np.count_nonzero(processed.mask[:, 490:510]), 800)
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        self.assertEqual(np.count_nonzero(cv2.Canny(gray, 30, 80)), 0)

    def test_adaptive_handles_uneven_paper_background(self):
        gray = np.tile(np.linspace(80, 250, 160, dtype=np.uint8), (160, 1))
        cv2.line(gray, (15, 80), (145, 80), 15, 2)
        image = rgb_image(cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB))
        processed = process_image(image, replace(self.options, processing="adaptive", smoothing=0))
        self.assertLess(np.mean(processed.mask > 0), 0.06)
        self.assertGreater(np.count_nonzero(processed.mask[490:510]), 2000)

    def test_noise_filter_preserves_long_lines(self):
        mask = np.zeros((80, 80), np.uint8)
        mask[5, 5] = 255
        mask[15:18, 15:18] = 255
        mask[40, 10:70] = 255
        cleaned = remove_specks(mask, 12, None)
        self.assertEqual(cleaned[5, 5], 0)
        self.assertEqual(cleaned[16, 16], 0)
        self.assertEqual(np.count_nonzero(cleaned), 60)

    def test_adaptive_inverse_changes_input_not_whole_page(self):
        rgb = np.full((100, 100, 3), 255, np.uint8)
        cv2.line(rgb, (10, 50), (90, 50), (0, 0, 0), 2)
        options = replace(self.options, processing="adaptive", smoothing=0)
        normal = process_image(rgb_image(rgb), options)
        inverted = process_image(rgb_image(255 - rgb), replace(options, invert=True))
        self.assertTrue(np.array_equal(normal.mask, inverted.mask))
        self.assertLess(np.mean(inverted.mask > 0), 0.05)

    def test_crop_ratio_matches_processed_shape(self):
        processed = process_image(self.image, replace(self.options, crop=True))
        self.assertLess(processed.mask.shape[0], 1000)
        self.assertAlmostEqual(processed.ratio, processed.mask.shape[1] / processed.mask.shape[0])
        self.assertTrue(np.any(processed.mask[0]) and np.any(processed.mask[-1]))

    def test_thumbnail_does_not_drop_single_pixel_lines(self):
        mask = np.zeros((1000, 1000), np.uint8)
        cv2.circle(mask, (500, 500), 400, 255, 1)
        image = QImage((255 - mask).data, 1000, 1000, 1000, QImage.Format.Format_Grayscale8).copy()
        thumbnail = binary_thumbnail(image, 100, 100)
        data = np.frombuffer(thumbnail.constBits(), np.uint8).reshape(thumbnail.height(), thumbnail.bytesPerLine())[:, :thumbnail.width()]
        contours, _ = cv2.findContours((data < 128).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        self.assertEqual(len(contours), 1)
        self.assertGreater(cv2.contourArea(contours[0]), 4500)

    def test_all_new_modes_create_machine_z_program(self):
        for mode in ("adaptive", "edges", "cartoon"):
            task = generate_task(self.image, replace(self.options, processing=mode, crop=True))
            self.assertGreater(task.program.drawing_length, 0)
            self.assertEqual(task.program.z_bounds, (-0.4, 2))
            self.assertNotIn("G10", task.program.source_text)
            self.assertGreater(len(task.notes), 2)

    def test_preprocessed_cache_matches_direct_generation(self):
        processed = process_image(self.image, self.options)
        cached = generate_task(self.image, self.options, processed=processed)
        direct = generate_task(self.image, self.options)
        self.assertEqual(cached.program.source_text, direct.program.source_text)

    def test_processing_cancellation(self):
        calls = []

        def cancel():
            calls.append(1)
            return len(calls) >= 3

        with self.assertRaises(ProcessingCancelled):
            process_image(self.image, self.options, cancel)

    def test_preprocess_parameter_validation(self):
        for changes in ({"processing": "bad"}, {"smoothing": 6}, {"smoothing": 2.5}, {"edge_sensitivity": -1}, {"detail_level": 101}, {"despeckle": 101}):
            with self.assertRaises(GenerationError):
                generate_task(self.image, replace(self.options, **changes))


if __name__ == "__main__":
    unittest.main()
