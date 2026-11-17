import json
import os
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from app.handwriting_samples import SAMPLE_PLAN, SAMPLE_PLANS, export_recorded_samples, export_sample_sheet
from app.profile import ROOT
from app.sdt_paths import tokens_to_strokes
from app.stroke_fonts import FontPackage, FontStore
from app.text_writing import missing_characters


class SampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        (ROOT / ".runtime/tmp").mkdir(parents=True, exist_ok=True)

    def test_a4_sheet_has_100_numbered_cells_supported_characters(self):
        self.assertEqual(len(SAMPLE_PLAN), 100)
        self.assertLess(len(set(SAMPLE_PLAN)), 100)
        self.assertEqual(missing_characters("".join(SAMPLE_PLAN)), [])
        with tempfile.TemporaryDirectory(dir=ROOT / ".runtime/tmp") as folder:
            target = export_sample_sheet(Path(folder) / "sheet.svg")
            root = ET.parse(target).getroot()
            self.assertEqual(root.attrib["width"], "210mm")
            self.assertEqual(root.attrib["height"], "297mm")
            self.assertEqual(len(root.findall("{http://www.w3.org/2000/svg}g/{http://www.w3.org/2000/svg}rect")), 100)

    def test_reference_png_and_manifest_match_actual_recorded_variants(self):
        glyph = {"advance": 1, "variants": [{"strokes": [[(.1, .1), (.9, .9)]]}, {"strokes": [[(.1, .9), (.9, .1)]]}]}
        package = FontPackage("测试字体", {"永": glyph})
        with tempfile.TemporaryDirectory(dir=ROOT / ".runtime/tmp") as folder:
            self.assertEqual(export_recorded_samples(package, folder), 2)
            manifest = json.loads((Path(folder) / "samples.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["count"], 2)
            for item in manifest["samples"]:
                image = QImage(str(Path(folder) / item["file"]))
                self.assertEqual((image.width(), image.height()), (256, 256))
                self.assertLess(image.pixelColor(128, 128).red(), 20)
            loaded = FontStore().read(Path(folder) / "recorded-font.json")
            self.assertEqual(len(loaded.glyphs["永"]["variants"]), 2)

    def test_large_plans_export_real_a4_pages_and_keep_repeated_versions(self):
        self.assertEqual([len(p) for p in SAMPLE_PLANS.values()], [100, 300, 1000, 3755, 6763])
        with tempfile.TemporaryDirectory(dir=ROOT / ".runtime/tmp") as folder:
            output = export_sample_sheet(Path(folder) / "test.svg", tuple("永天地" * 34))
            self.assertTrue(output.is_dir())
            pages = sorted(output.glob("*.svg"))
            self.assertEqual(len(pages), 2)
            for page in pages:
                self.assertEqual(ET.parse(page).getroot().attrib["height"], "297mm")
            data = json.loads((output / "录字清单.json").read_text(encoding="utf-8"))
            self.assertEqual("".join(p["characters"] for p in data["pages"]), "永天地" * 34)

    def test_sdt_splits_after_penup_and_does_not_connect_travel(self):
        tokens = [[0, 0, 1, 0, 0], [1, 0, 0, 1, 0], [4, 3, 1, 0, 0], [1, 0, 0, 1, 0], [0, 0, 0, 0, 1]]
        paths = tokens_to_strokes(tokens)
        self.assertEqual([len(path) for path in paths], [2, 2])
        self.assertGreater(paths[1][0][0] - paths[0][-1][0], .4)
        self.assertTrue(all(0 <= x <= 1 and 0 <= y <= 1 for path in paths for x, y in path))

    def test_sdt_keeps_unterminated_last_stroke_before_eos(self):
        paths = tokens_to_strokes([[0, 0, 1, 0, 0], [1, 1, 0, 1, 0], [3, 0, 1, 0, 0], [1, 1, 1, 0, 0], [0, 0, 0, 0, 1]])
        self.assertEqual([len(path) for path in paths], [2, 2])

    def test_sdt_dot_has_short_nonzero_path(self):
        paths = tokens_to_strokes([[0, 0, 0, 1, 0], [2, 2, 1, 0, 0], [1, 0, 0, 1, 0], [0, 0, 0, 0, 1]])
        self.assertEqual(len(paths[0]), 2)
        self.assertNotEqual(paths[0][0], paths[0][1])

    def test_sdt_invalid_or_truncated_results_rejected(self):
        for tokens in ([], [[1, 1, 1, 0, 0]], [[0, 0, 0, 0, 1]], [[0, 0, 1, 1, 0]], [[float("nan"), 0, 1, 0, 0]], [[0, 0, 0, 0, .5]], [[0, 0, 1, 0]]):
            with self.subTest(tokens=tokens), self.assertRaises(ValueError): tokens_to_strokes(tokens)


if __name__ == "__main__":
    unittest.main()
