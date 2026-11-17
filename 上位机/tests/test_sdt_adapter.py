import importlib.util
import json
from pathlib import Path
import pickle
import tempfile
import unittest

import numpy as np
from app.profile import ROOT
from tools.sdt_adapter import existing_glyphs, read_inputs
from tools.sdt_adapter import run_personal, run_recorded


@unittest.skipUnless(importlib.util.find_spec("PIL"), "SDT样本检查需可选依赖Pillow")
class SdtInputTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image, ImageDraw
        (ROOT / ".runtime/tmp").mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=ROOT / ".runtime/tmp")
        self.addCleanup(self.directory.cleanup)
        folder = Path(self.directory.name)
        (folder / "models").mkdir()
        (folder / "models/model.py").write_text("# Input validation only, no fake inference", encoding="utf-8")
        (folder / "checkpoint.pth").touch()
        self.content = folder / "Chinese_content.pkl"
        self.content.write_bytes(pickle.dumps({"中": np.zeros((64, 64), dtype=np.uint8)}))
        self.samples = folder / "samples"
        self.samples.mkdir()
        image = Image.new("L", (256, 256), 255)
        ImageDraw.Draw(image).line((30, 30, 230, 230), fill=0, width=6)
        for index in range(15):
            image.save(self.samples / f"sample{index}.png")
        self.request = {"repo": str(folder), "checkpoint": str(folder / "checkpoint.pth"), "content": str(self.content), "samples": str(self.samples), "characters": "中中 \n"}

    def test_official_input_shapes_and_normalization(self):
        repo, checkpoint, content, images, chars = read_inputs(self.request)
        self.assertEqual(chars, ["中"])
        self.assertEqual(len(images), 15)
        self.assertEqual(images[0].shape, (64, 64))
        self.assertTrue(0 <= images[0].min() < images[0].max() <= 1)

    def test_missing_samples_and_content_characters_are_reported(self):
        self.request["characters"] = "国"
        with self.assertRaisesRegex(ValueError, "缺字：国"):
            read_inputs(self.request)
        self.request["characters"] = "中"
        (self.samples / "sample0.png").unlink()
        with self.assertRaisesRegex(ValueError, "至少需要15张"):
            read_inputs(self.request)

    def test_invalid_content_image_is_rejected(self):
        self.content.write_bytes(pickle.dumps({"中": np.zeros((32, 32), dtype=np.uint8)}))
        with self.assertRaisesRegex(ValueError, "64×64"):
            read_inputs(self.request)

    def test_manifest_ignores_stale_pngs_and_rejects_path_escape(self):
        from PIL import Image
        Image.new("L", (64, 64), 255).save(self.samples / "old-blank.png")
        records = [{"file": f"sample{i}.png"} for i in range(15)]
        manifest = self.samples / "samples.json"
        manifest.write_text(json.dumps({"samples": records}), encoding="utf-8")
        self.assertEqual(len(read_inputs(self.request)[3]), 15)
        records[0]["file"] = "../elsewhere.png"
        manifest.write_text(json.dumps({"samples": records}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "文件名无效"):
            read_inputs(self.request)

    def test_manifest_cannot_count_same_reference_file_fifteen_times(self):
        manifest = self.samples / "samples.json"
        manifest.write_text(json.dumps({"samples": [{"file": "sample0.png"}] * 15}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "重复列出"):
            read_inputs(self.request)

    def test_known_glyphs_are_preserved_and_can_extend_in_batches_with_original_references(self):
        glyph = {"advance": 1, "variants": [{"strokes": [[[.1, .1], [.9, .9]]]}]}
        (self.samples / "recorded-font.json").write_text(json.dumps({"schema": 1, "glyphs": {"龘": glyph}}), encoding="utf-8")
        base = self.samples.parent / "previous-batch.json"
        edited = {"advance": 1, "variants": [{"strokes": [[[.2, .1], [.8, .9]]]}]}
        base.write_text(json.dumps({"schema": 1, "glyphs": {"龘": edited, "国": glyph}}), encoding="utf-8")
        self.request.update(base_font=str(base), characters="龘国中")
        recorded = existing_glyphs(self.request)
        self.assertEqual(recorded, {"龘": edited, "国": glyph})
        self.assertEqual(read_inputs(self.request)[-1], ["龘", "国", "中"])


class RecordedExtensionTests(unittest.TestCase):
    def setUp(self):
        from app.personal_strokes import apply_affine, reference_strokes
        from unittest.mock import patch
        (ROOT / ".runtime/tmp").mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=ROOT / ".runtime/tmp")
        self.addCleanup(self.directory.cleanup)
        folder = Path(self.directory.name)
        self.source = folder / "recorded-font.json"
        matrix = np.array([[.65, .02], [-.01, .7], [.15, .13]])
        self.glyphs = {char: {"advance": 1., "variants": [{"strokes": [apply_affine(path, matrix).tolist() for path in reference_strokes(char)]}]} for char in "天地人大上下左右中日月山木金土"}
        self.data = {"schema": 1, "source": "用户录制", "glyphs": self.glyphs}
        self.source.write_text(json.dumps(self.data), encoding="utf-8")
        self.output = folder / "output/font.json"
        self.request = {"samples": str(folder), "method": "recorded", "characters": "离线扩天", "output": str(self.output)}
        self.report = patch("tools.sdt_adapter.report").start()
        self.addCleanup(patch.stopall)

    def test_new_font_preserves_recordings_and_independent_pen_lifts(self):
        run_recorded(self.request)
        result = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual({char: result["glyphs"][char] for char in self.glyphs}, self.glyphs)
        self.assertEqual([len(result["glyphs"][char]["variants"][0]["strokes"]) for char in "离线扩"], [10, 8, 6])
        self.assertEqual(json.loads(self.source.read_text(encoding="utf-8")), self.data)

    def test_original_vector_recordings_are_required(self):
        self.source.unlink()
        with self.assertRaisesRegex(ValueError, "需要原始录字"):
            run_recorded(self.request)
        self.assertFalse(self.output.exists())

    def test_generated_reference_fonts_are_rejected(self):
        for source in ("SDT本地模型生成", "个人轨迹校准", "个人笔迹学习", "OLHWG离线轨迹模型生成"):
            self.data["source"] = source
            self.source.write_text(json.dumps(self.data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "包含生成字"):
                run_recorded(self.request)
            self.assertFalse(self.output.exists())

    def test_personal_learning_saves_model_and_preserves_existing_glyphs(self):
        self.request.update(method="personal", characters="林明离线天")
        run_personal(self.request)
        result = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual({char: result["glyphs"][char] for char in self.glyphs}, self.glyphs)
        self.assertTrue(self.output.with_name("personal-model.json").is_file())
        summary = json.loads(self.output.with_name("summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["method"], "personal")
        self.assertEqual(summary["details"]["林"]["reused_strokes"], 8)
        self.assertIn("天", summary["preserved_requested"])

    def test_personal_probe_does_not_create_font_and_cannot_use_synthetic_samples(self):
        run_personal(self.request, probe=True)
        self.assertFalse(self.output.exists())
        self.data["source"] = "个人笔迹学习"
        self.source.write_text(json.dumps(self.data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "样本含生成字"):
            run_personal(self.request)

    def test_personal_relearns_requested_synthetic_glyph_but_keeps_original_and_other_glyphs(self):
        fake = {"advance": 1., "variants": [{"strokes": [[[.1, .1], [.9, .9]]]}]}
        base = self.source.parent / "old-extension.json"
        base.write_text(json.dumps({"schema": 1, "glyphs": {"林": fake, "明": fake}}), encoding="utf-8")
        self.request.update(base_font=str(base), characters="林天")
        run_personal(self.request)
        result = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertNotEqual(result["glyphs"]["林"], fake)
        self.assertEqual(result["glyphs"]["明"], fake)
        self.assertEqual(result["glyphs"]["天"], self.glyphs["天"])
        self.assertEqual(json.loads(self.output.with_name("summary.json").read_text(encoding="utf-8"))["regenerated_existing"], ["林"])


if __name__ == "__main__":
    unittest.main()
