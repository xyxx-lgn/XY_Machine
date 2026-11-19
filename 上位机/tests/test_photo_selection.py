import unittest
from dataclasses import replace

import numpy as np

from app.photo_samples import PhotoSample, build_photo_package, gray_image
from app.photo_selection import choose_photo_variants


class PhotoSelectionTests(unittest.TestCase):
    def sample(self, char="动", x=.2, score=1.0, blocking=False):
        return PhotoSample(char, gray_image(np.full((256, 256), 255, np.uint8)), [[(x, .2), (x, .8)]],
                           quality_score=score, quality_blocking=blocking)

    def test_frequent_character_no_longer_prevents_whole_batch_import(self):
        samples = [self.sample(x=.10 + i * .03) for i in range(25)] + [self.sample("好")]
        selection = choose_photo_variants(samples)
        kept = [samples[i] for i in selection.indices]
        self.assertEqual(len(kept), 6)
        self.assertEqual(selection.overflow, 20)
        package = build_photo_package(kept)
        self.assertEqual(len(package.glyphs["动"]["variants"]), 5)
        self.assertIn("好", package.glyphs)

    def test_identical_shapes_are_skipped_and_better_quality_wins(self):
        bad = self.sample(score=.4, blocking=True)
        good = self.sample(score=.98)
        selection = choose_photo_variants([bad, good, replace(good), self.sample(x=.5)])
        self.assertEqual(selection.indices, (1, 3))
        self.assertEqual(selection.duplicates, 2)
        self.assertIsNone(good.calibration_source)

    def test_existing_library_and_collected_batches_are_never_replaced(self):
        base_sample = self.sample(x=.1)
        base = build_photo_package([base_sample])
        existing = [self.sample(x=.2)]
        samples = [self.sample(x=.1), self.sample(x=.2), self.sample(x=.3), self.sample(x=.4)]
        selection = choose_photo_variants(samples, existing, base, max_variants=3)
        self.assertEqual(selection.duplicates, 2)
        self.assertEqual(len(selection.indices), 1)
        self.assertEqual(selection.overflow, 1)
        result = build_photo_package(existing + [samples[i] for i in selection.indices], base_package=base)
        self.assertEqual(result.glyphs["动"]["variants"][0], base.glyphs["动"]["variants"][0])
        self.assertEqual(len(base.glyphs["动"]["variants"]), 1)

    def test_full_existing_quota_and_bad_samples_return_a_report(self):
        samples = [self.sample(), self.sample("坏"), self.sample("空")]
        samples[1].error, samples[2].strokes = "无效", []
        selection = choose_photo_variants(samples, existing=[self.sample(x=.5)], max_variants=1)
        self.assertEqual(selection.indices, ())
        self.assertEqual((selection.invalid, selection.overflow), (2, 1))
        self.assertIn("超过每字配额", selection.summary())


if __name__ == "__main__":
    unittest.main()
