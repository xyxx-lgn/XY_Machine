"""照片字迹闭环、骨架覆盖、短末梢接续与邻字非矩形分离。"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np

from app.generation import GenerationCancelled, thin_mask
from app.photo_paths import continuous_photo_paths, ink_reproduction_paths
from app.photo_separation import blank_seam, separate_line_cells


class PhotoPathTests(unittest.TestCase):
    def raster(self, mask, paths):
        result = np.zeros(mask.shape, np.uint8)
        for path in paths:
            cv2.polylines(result, [np.rint(path).astype(np.int32)], False, 255, 1)
        return result

    def test_closed_mouth_keeps_every_side_and_closing_segment(self):
        mask = np.zeros((180, 180), np.uint8)
        cv2.rectangle(mask, (35, 40), (140, 135), 255, 10)
        paths = continuous_photo_paths(mask)
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0][0], paths[0][-1])
        ink = self.raster(mask, paths)
        skeleton = thin_mask(mask, None)
        distance = cv2.distanceTransform(255 - ink, cv2.DIST_L2, 5)
        self.assertLess(float(distance[skeleton > 0].max()), 1.5)
        contours, _ = cv2.findContours(ink, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        self.assertEqual(len(contours), 2)

    def test_junction_loop_and_dot_keep_ink_without_crossing_white(self):
        mask = np.zeros((200, 230), np.uint8)
        cv2.rectangle(mask, (100, 35), (180, 120), 255, 9)
        cv2.line(mask, (30, 75), (100, 75), 255, 9)
        cv2.line(mask, (55, 35), (55, 165), 255, 9)
        cv2.circle(mask, (205, 175), 5, 255, -1)
        paths = continuous_photo_paths(mask)
        ink = self.raster(mask, paths)
        self.assertGreater(int(ink[170:181, 200:211].sum()), 0)
        distance = cv2.distanceTransform(255 - ink, cv2.DIST_L2, 5)
        skeleton = thin_mask(mask, None)
        self.assertLess(float(distance[skeleton > 0].max()), 2)
        self.assertFalse(np.any((ink > 0) & (cv2.dilate(mask, np.ones((3, 3), np.uint8)) == 0)))

    def test_cross_needs_two_paths_and_never_draws_virtual_links(self):
        mask = np.zeros((150, 150), np.uint8)
        cv2.line(mask, (20, 75), (130, 75), 255, 5)
        cv2.line(mask, (75, 20), (75, 130), 255, 5)
        paths = continuous_photo_paths(mask)
        self.assertEqual(len(paths), 2)
        ink = self.raster(mask, paths)
        self.assertFalse(np.any((ink > 0) & (mask == 0)))

    def test_short_terminal_is_preserved_but_can_return_without_extra_pen_lift(self):
        mask = np.zeros((150, 150), np.uint8)
        cv2.line(mask, (20, 75), (130, 75), 255, 9)
        cv2.line(mask, (75, 75), (75, 65), 255, 5)
        paths = continuous_photo_paths(mask)
        self.assertEqual(len(paths), 1)
        ink = self.raster(mask, paths)
        self.assertGreater(int(ink[64:68, 73:78].sum()), 0)

    def test_curved_gap_keeps_both_close_characters_without_neighbor_bits(self):
        ink = np.zeros((90, 140), np.uint8)
        for y in range(10, 80):
            center = 50 + round(y * .3)
            ink[y, center - 18:center - 8] = 1
            ink[y, center + 7:center + 17] = 1
        pixels = np.full((90, 140, 3), 255, np.uint8)
        pixels[ink > 0] = 0
        seam = blank_seam(ink, 64, 46, 83)
        self.assertGreater(int(seam[-15] - seam[15]), 0)
        self.assertFalse(np.any(ink[np.arange(90), seam]))
        cells = list(separate_line_cells(pixels, ink, [(35, "甲", .9), (95, "乙", .9)], [0, 64, 140]))
        for index, (x0, x1, cell) in enumerate(cells):
            rows, cols = np.nonzero(cell[:, :, 0] < 128)
            global_x = cols + x0
            boundary = 50 + np.rint(rows * .3)
            self.assertTrue(np.all(global_x < boundary) if index == 0 else np.all(global_x > boundary))
        self.assertEqual(sum(np.count_nonzero(cell[:, :, 0] < 128) for _, _, cell in cells), int(ink.sum()))

    def test_separation_and_paths_respect_cancellation(self):
        mask = np.ones((150, 150), np.uint8)
        with self.assertRaises(GenerationCancelled):
            continuous_photo_paths(mask, lambda: True)
        with self.assertRaises(GenerationCancelled):
            blank_seam(mask, 75, 50, 100, lambda: True)

    def test_pen_shape_keeps_tip_and_closed_loop_inside_ink(self):
        mask = np.zeros((200, 200), np.uint8)
        cv2.line(mask, (20, 45), (90, 60), 255, 13)
        cv2.rectangle(mask, (100, 90), (170, 170), 255, 9)
        old = continuous_photo_paths(mask)
        new = continuous_photo_paths(mask, pen_shape=True)
        self.assertEqual(len(new), len(old))
        self.assertLess(min(x for p in new for x, y in p), min(x for p in old for x, y in p))
        self.assertTrue(any(p[0] == p[-1] for p in new))
        ink = self.raster(mask, new)
        self.assertFalse(np.any((ink > 0) & (cv2.dilate(mask, np.ones((3, 3), np.uint8)) == 0)))

    def test_ink_reproduction_preserves_width_and_hole_with_same_pen(self):
        mask = np.zeros((180, 180), np.uint8)
        cv2.rectangle(mask, (40, 40), (140, 140), 255, 17)
        cv2.line(mask, (20, 10), (140, 25), 255, 5)
        pen_width = 4
        def paint(paths):
            result = np.zeros(mask.shape, np.uint8)
            for path in paths:
                cv2.polylines(result, [np.rint(path).astype(np.int32)], False, 255, pen_width)
            return result > 0
        single = paint(continuous_photo_paths(mask, pen_shape=True))
        reproduced = paint(ink_reproduction_paths(mask, pen_width))
        self.assertGreater(np.count_nonzero(reproduced & (mask > 0)), np.count_nonzero(single & (mask > 0)) * 1.8)
        self.assertGreater(np.count_nonzero(reproduced & (mask > 0)) / np.count_nonzero(mask), .9)
        self.assertFalse(np.any(reproduced[65:115, 65:115]))
        with self.assertRaises(GenerationCancelled):
            ink_reproduction_paths(mask, pen_width, lambda: True)

    def test_touching_neighbor_is_flagged_without_erasing_a_whole_component(self):
        ink = np.zeros((100, 180), np.uint8)
        cv2.line(ink, (35, 15), (35, 85), 1, 7)
        cv2.line(ink, (140, 15), (140, 85), 1, 7)
        cv2.line(ink, (35, 50), (140, 50), 1, 3)
        pixels = np.full((100, 180, 3), 255, np.uint8)
        pixels[ink > 0] = 0
        reviews = []
        cells = list(separate_line_cells(pixels, ink, [(35, "甲", .99), (140, "乙", .99)], [0, 90, 180], reviews=reviews))
        self.assertTrue(all("边界穿过" in note for note in reviews))
        self.assertEqual(sum(np.count_nonzero(cell[:, :, 0] < 128) for _, _, cell in cells), int(ink.sum()))

    def test_small_cross_boundary_tail_is_flagged_before_whole_component_assignment(self):
        ink = np.zeros((100, 180), np.uint8)
        cv2.rectangle(ink, (28, 20), (72, 80), 1, -1)
        cv2.line(ink, (72, 50), (95, 50), 1, 3)
        cv2.line(ink, (145, 20), (145, 80), 1, 7)
        pixels = np.full((100, 180, 3), 255, np.uint8)
        pixels[ink > 0] = 0
        reviews = []
        # 固定边界以覆盖“90%以上属左字、少量右字粘入”的归属分支。
        from unittest.mock import patch
        with patch("app.photo_separation.blank_seam", return_value=np.full(100, 78, np.int32)):
            cells = list(separate_line_cells(pixels, ink, [(45, "甲", .99), (145, "乙", .99)], [0, 78, 180], reviews=reviews))
        self.assertTrue(all("边界穿过" in note for note in reviews))
        self.assertEqual(sum(np.count_nonzero(cell[:, :, 0] < 128) for _, _, cell in cells), int(ink.sum()))
        # 小面积细连笔已深入右字，仍按边界切开，不能整体塞到左字里。
        left = cells[0]
        rows, columns = np.nonzero(left[2][:, :, 0] < 128)
        self.assertTrue(np.all(columns + left[0] < 78))

    def test_clearly_separated_characters_have_no_touching_warning(self):
        ink = np.zeros((100, 180), np.uint8)
        cv2.line(ink, (35, 20), (35, 80), 1, 7)
        cv2.line(ink, (145, 20), (145, 80), 1, 7)
        pixels = np.full((100, 180, 3), 255, np.uint8)
        pixels[ink > 0] = 0
        reviews = []
        list(separate_line_cells(pixels, ink, [(35, "甲", .99), (145, "乙", .99)], [0, 90, 180], reviews=reviews))
        self.assertEqual(reviews, ["", ""])


if __name__ == "__main__":
    unittest.main()
