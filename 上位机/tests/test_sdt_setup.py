import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from app.profile import ROOT
from app.ui import sdt_dialog
from app.ui.sdt_dialog import SdtDialog
from tools import prepare_sdt as setup


class SdtSetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        (ROOT / ".runtime/tmp").mkdir(parents=True, exist_ok=True)

    def test_download_validates_range_before_accepting_bytes(self):
        class Response(io.BytesIO):
            status = 206
            headers = {"Content-Range": "bytes 100-103/1000"}
        with patch.object(setup.urllib.request, "urlopen", return_value=Response(b"abcd")):
            self.assertEqual(setup.fetch("https://example.invalid/data", 100, 103), b"abcd")
        with patch.object(setup.urllib.request, "urlopen", side_effect=lambda *a, **k: Response(b"abcd")), patch.object(setup.time, "sleep"):
            with self.assertRaisesRegex(ValueError, "未正确返回"):
                setup.fetch("https://example.invalid/data", 101, 104)

    def test_verified_download_reuses_file_and_hash_failure_preserves_original(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".runtime/tmp") as folder:
            folder = Path(folder)
            target = folder / "model.bin"
            raw = b"valid checkpoint bytes"
            expected = hashlib.sha256(raw).hexdigest()
            target.write_bytes(raw)
            with patch.object(setup, "BASE", folder), patch.object(setup, "fetch") as fetch:
                setup.download_parts("https://example.invalid/data", target, len(raw), expected)
                fetch.assert_not_called()
            with patch.object(setup, "BASE", folder), patch.object(setup, "fetch", return_value=b"x" * len(raw)):
                with self.assertRaisesRegex(ValueError, "校验不符"):
                    setup.download_parts("https://example.invalid/data", target, len(raw), "0" * 64)
            self.assertEqual(target.read_bytes(), raw)
            self.assertFalse(list((folder / "downloads/model.bin.parts").glob("*.bin")))

    def test_changed_content_zip_record_is_rejected(self):
        with self.assertRaises((ValueError, __import__("struct").error)):
            setup.extract_content(b"bad zip header")

    def test_dialog_remembers_runtime_paths_and_accepts_recorded_sample_shortcut(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".runtime/tmp") as folder:
            prefs = Path(folder) / "prefs.json"
            with patch.object(sdt_dialog, "PREFERENCES", prefs):
                dialog = SdtDialog(samples=Path(folder) / "references")
                dialog.fields["repo"].setText(str(Path(folder) / "official-source"))
                dialog.variants.setValue(3)
                dialog.seed.setValue(2026)
                self.assertEqual(dialog.method.currentData(), "personal")
                self.assertEqual(dialog.run_button.text(), "学习并扩展个人字库")
                self.assertFalse(dialog.fields["checkpoint"].isEnabled())
                dialog.method.setCurrentIndex(dialog.method.findData("sdt"))
                self.assertTrue(dialog.fields["checkpoint"].isEnabled())
                dialog.font_name.setText("个人测试扩展")
                dialog.save_preferences()
                dialog.reject()
                loaded = SdtDialog()
                self.assertEqual(loaded.fields["samples"].text(), str(Path(folder) / "references"))
                self.assertEqual(loaded.fields["repo"].text(), str(Path(folder) / "official-source"))
                self.assertEqual((loaded.variants.value(), loaded.seed.value()), (3, 2026))
                self.assertEqual(loaded.font_name.text(), "个人测试扩展")
                self.assertEqual(loaded.method.currentData(), "sdt")
                self.assertEqual(json.loads(prefs.read_text(encoding="utf-8"))["name"], "个人测试扩展")
                loaded.reject()

    def test_trajectory_choice_remembers_model_folder_and_ignores_sdt_fields(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".runtime/tmp") as folder:
            with patch.object(sdt_dialog, "PREFERENCES", Path(folder) / "prefs.json"):
                dialog = SdtDialog()
                dialog.method.setCurrentIndex(dialog.method.findData("trajectory"))
                dialog.fields["model_root"].setText(str(Path(folder) / "model"))
                self.assertTrue(dialog.fields["model_root"].isEnabled())
                self.assertFalse(dialog.fields["checkpoint"].isEnabled())
                self.assertFalse(dialog.fields["repo"].isEnabled())
                dialog.save_preferences()
                dialog.reject()
                loaded = SdtDialog()
                self.assertEqual(loaded.method.currentData(), "trajectory")
                self.assertEqual(loaded.fields["model_root"].text(), str(Path(folder) / "model"))
                loaded.reject()


if __name__ == "__main__":
    unittest.main()
