import unittest
from dataclasses import replace
from unittest.mock import patch

import numpy as np
from PySide6.QtGui import QImage

from app.gcode import parse_program
from app.generation import (
    GenerationCancelled, GenerationError, PlotOptions, centerline_paths, contour_paths,
    fill_paths, generate_task, image_gray, ink_connector, join_touching_paths, order_paths, save_gcode,
    threshold_image
)
from app.profile import ROOT


def qimage(mask):
    gray = (255 - mask).astype(np.uint8)
    return QImage(gray.data, gray.shape[1], gray.shape[0], gray.strides[0], QImage.Format.Format_Grayscale8).copy()


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.mask = np.zeros((81, 101), np.uint8)
        self.mask[5:76, 5:96] = 255
        self.mask[25:56, 35:66] = 0
        self.options = PlotOptions(width=10, height=8, crop=False, spacing=0.2, min_length=0.01)

    def test_transparent_background_is_white(self):
        image = QImage(8, 8, QImage.Format.Format_RGBA8888)
        image.fill(0)
        self.assertTrue(np.all(image_gray(image) == 255))

    def test_threshold_invert_and_crop(self):
        image = qimage(self.mask)
        plain = threshold_image(image, self.options)
        self.assertTrue(np.array_equal(plain, self.mask))
        inverted = threshold_image(image, replace(self.options, invert=True))
        self.assertTrue(np.array_equal(inverted, 255 - self.mask))
        cropped = threshold_image(image, replace(self.options, crop=True))
        self.assertEqual(cropped.shape, (71, 91))

    def test_blank_rejected(self):
        with self.assertRaisesRegex(GenerationError, "没有黑色"):
            generate_task(qimage(np.zeros((20, 20), np.uint8)), self.options)

    def test_contours_keep_hole(self):
        paths = contour_paths(self.mask, 0.1, 0.1, self.options, None)
        self.assertEqual(len(paths), 2)
        self.assertTrue(all(path[0] == path[-1] for path in paths))

    def test_fill_never_crosses_hole(self):
        paths = fill_paths(self.mask, 0.1, 0.1, self.options, None)
        self.assertGreater(len(paths), 1)
        for path in paths:
            for start, end in zip(path, path[1:]):
                self.assertTrue(ink_connector(self.mask, start, end, 0.1, 0.1))

    def test_fill_continuous_rectangle_has_one_path(self):
        mask = np.full((81, 101), 255, np.uint8)
        paths = fill_paths(mask, 0.1, 0.1, self.options, None)
        self.assertEqual(len(paths), 1)
        ys = sorted(set(point[1] for point in paths[0]))
        self.assertTrue(np.allclose(np.diff(ys), 0.2))

    def test_vertical_fill_keeps_hole(self):
        paths = fill_paths(self.mask, 0.1, 0.1, replace(self.options, vertical=True), None)
        for path in paths:
            for start, end in zip(path, path[1:]):
                self.assertTrue(ink_connector(self.mask, start, end, 0.1, 0.1))

    def test_centerline_single_stroke_is_open(self):
        mask = np.zeros((40, 80), np.uint8)
        mask[18:23, 10:70] = 255
        paths = centerline_paths(mask, 0.1, 0.1, self.options, None)
        self.assertEqual(len(paths), 1)
        self.assertNotEqual(paths[0][0], paths[0][-1])
        self.assertGreater(abs(paths[0][0][0] - paths[0][-1][0]), 5)

    def test_centerline_closed_loop(self):
        mask = np.zeros((41, 41), np.uint8)
        mask[8:33, 8:33] = 255
        mask[12:29, 12:29] = 0
        paths = centerline_paths(mask, 0.1, 0.1, self.options, None)
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0][0], paths[0][-1])

    def test_path_order_keeps_geometry(self):
        paths = [[(20, 10), (22, 10)], [(3, 5), (0, 0)], [(8, 6), (8, 7)]]
        ordered = order_paths(paths, None)
        self.assertEqual(ordered[0][0], (0, 0))
        self.assertEqual({frozenset(path) for path in paths}, {frozenset(path) for path in ordered})

    def test_join_only_shared_endpoints(self):
        paths = [[(0, 0), (1, 0)], [(2, 0), (1, 0)], [(3, 0), (4, 0)]]
        joined = join_touching_paths(paths, None)
        self.assertEqual(len(joined), 2)
        self.assertIn([(0, 0), (1, 0), (2, 0)], joined)

    def test_curved_fill_connectors_stay_inside_ink(self):
        import cv2

        mask = np.zeros((150, 150), np.uint8)
        cv2.circle(mask, (75, 75), 65, 255, -1)
        cv2.circle(mask, (75, 75), 40, 0, -1)
        paths = fill_paths(mask, 0.04, 0.04, self.options, None)
        for path in paths:
            for start, end in zip(path, path[1:]):
                self.assertTrue(ink_connector(mask, start, end, 0.04, 0.04))

    def test_generation_has_explicit_modes_and_no_eeprom(self):
        task = generate_task(qimage(self.mask), self.options)
        source = task.program.source_text
        self.assertIn("G21 G90 G94 G17 G54", source)
        self.assertNotIn("G10", source)
        self.assertNotIn("M3", source)
        self.assertEqual(task.program.z_bounds, (-0.4, 2))
        self.assertTrue(source.endswith("G53 G1 Z2.0000 F240\n"))
        self.assertTrue(all(len(command.text) < 80 for command in task.program.commands))

    def test_z_remains_machine_coordinate_under_g54_offset(self):
        task = generate_task(qimage(self.mask), self.options)
        parsed = parse_program(task.program.source_text, initial_work=(5, 5, -98), g54_offset=(-190, -280, 100), pen_threshold=0.8)
        self.assertAlmostEqual(parsed.z_bounds[0], -0.4)
        self.assertEqual(parsed.z_bounds[1], 2)
        self.assertAlmostEqual(parsed.drawing_length, task.program.drawing_length)

    def test_each_motion_transition_sets_feed(self):
        task = generate_task(qimage(self.mask), replace(self.options, draw_feed=333, travel_feed=888, up_feed=222, down_feed=111))
        lines = [command.text for command in task.program.commands]
        for index, line in enumerate(lines):
            if line.startswith("G53 G1 Z-0.4000"):
                self.assertIn("F888", lines[index - 1])
                self.assertIn("F111", line)
                self.assertIn("F333", lines[index + 1])
            elif line.startswith("G53 G1 Z2.0000"):
                self.assertIn("F222", line)

    def test_all_modes_generate_parseable_paths(self):
        for mode in ("outline", "outline_fill", "fill", "centerline"):
            task = generate_task(qimage(self.mask), replace(self.options, mode=mode))
            self.assertGreater(task.program.drawing_length, 0)
            self.assertGreater(task.path_count, 0)
            x0, y0, x1, y1 = task.program.bounds
            self.assertTrue(10 <= x0 <= x1 <= 20.001)
            self.assertTrue(10 <= y0 <= y1 <= 18.001)

    def test_parameters_and_out_of_paper_rejected(self):
        for changes in ({"x": -1}, {"width": 300}, {"pen_down": 3}, {"spacing": 0}, {"draw_feed": float("nan")}, {"height": 0}):
            with self.assertRaises(GenerationError):
                generate_task(qimage(self.mask), replace(self.options, **changes))

    def test_cancellation(self):
        with self.assertRaises(GenerationCancelled):
            generate_task(qimage(self.mask), self.options, cancel=lambda: True)

    def test_export_preserves_text(self):
        task = generate_task(qimage(self.mask), self.options)
        path = ROOT / ".runtime" / "generation_test.ngc"
        save_gcode(task.program, path)
        self.assertEqual(path.read_text(encoding="utf-8"), task.program.source_text)

    def test_export_outside_host_is_allowed(self):
        task = generate_task(qimage(self.mask), self.options)
        path = (ROOT.parent / "用户选择的目录" / "图片任务.ngc").resolve()
        # 模拟用户选择外部目录，不向工程允许范围之外实际写入测试文件。
        with patch("pathlib.Path.mkdir"), patch("pathlib.Path.write_text") as write, patch("pathlib.Path.replace") as replace_file:
            self.assertEqual(save_gcode(task.program, path), path)
        write.assert_called_once_with(task.program.source_text, encoding="utf-8", newline="\n")
        replace_file.assert_called_once_with(path)

    def test_export_cannot_overwrite_config_or_code(self):
        task = generate_task(qimage(self.mask), self.options)
        for path in (ROOT / "config" / "machine.json", ROOT / "main.py"):
            with self.assertRaisesRegex(GenerationError, "扩展名"):
                save_gcode(task.program, path)


if __name__ == "__main__":
    unittest.main()
