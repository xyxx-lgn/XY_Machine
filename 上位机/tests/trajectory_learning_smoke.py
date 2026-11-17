"""真实录字、离线AI扩字、留出字、导入回放与模拟发送；不连接硬件。"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from app.appearance import configure_appearance
from app.gcode import load_program
from app.profile import MachineProfile
from app import stroke_fonts
from app.stroke_fonts import FontStore
from app.transport import SIMULATOR
from app.ui import generation_dialog, sdt_dialog
from app.ui.generation_dialog import GenerationDialog
from app.ui.main_window import MainWindow
from app.ui.sdt_dialog import SdtDialog
from app.ui.stroke_font_dialog import StrokeFontDialog


def wait_until(condition, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if condition():
            return
        time.sleep(.005)
    raise AssertionError("等待轨迹模型或模拟设备超时")


def config_hashes():
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / "config").rglob("*") if p.is_file()}


parser = argparse.ArgumentParser()
parser.add_argument("--samples", required=True, type=Path)
parser.add_argument("--base", type=Path)
args = parser.parse_args()
application = QApplication.instance() or QApplication([])
configure_appearance(application)
before = config_hashes()
folder = ROOT / ".runtime/trajectory-learning/smoke"
folder.mkdir(parents=True, exist_ok=True)
stroke_fonts.FONT_ROOT = folder / "fonts"
sdt_dialog.PREFERENCES = folder / "sdt.json"
generation_dialog.PREFERENCES = folder / "generation.json"
for path in (sdt_dialog.PREFERENCES, generation_dialog.PREFERENCES):
    path.unlink(missing_ok=True)
original = json.loads((args.samples / "recorded-font.json").read_text(encoding="utf8"))
base = FontStore().read(args.base or args.samples / "recorded-font.json")
ai = SdtDialog(samples=args.samples, base_package=base)
ai.method.setCurrentIndex(ai.method.findData("trajectory"))
ai.font_name.setText("我的笔迹_离线AI轨迹扩字试样")
ai.characters.setPlainText("调林展其离线扩还是需要调用他在的呢明好休想书")
ai.variants.setValue(1)
ai.seed.setValue(2026)
ai.show()
ai.start(True)
wait_until(lambda: ai.process is None)
assert "检查通过" in ai.log.toPlainText(), ai.log.toPlainText()
ai.start(False)
wait_until(lambda: ai.process is None, timeout=720)
assert ai.import_button.isEnabled(), ai.log.toPlainText()
font_file = ai.output_path
result = json.loads(font_file.read_text(encoding="utf8"))
summary = json.loads(font_file.with_name("summary.json").read_text(encoding="utf8"))
assert summary["method"] == "trajectory"
for char, glyph in original["glyphs"].items():
    assert result["glyphs"][char] == glyph, "不得改动真实录字"
for char in summary["generated"] + summary["regenerated_existing"]:
    assert len(result["glyphs"][char]["variants"]) == 1
assert summary["preserved_requested"] == ["书"]
for char, glyph in base.glyphs.items():
    if char not in ai.characters.toPlainText() and char not in original["glyphs"]:
        assert result["glyphs"][char] == json.loads(json.dumps(glyph))
assert all(len(trace["reference_characters"]) == 15 for trace in summary["diagnostics"].values())
ai.grab().save(str(folder / "ai-complete.png"))
(folder / "ui-log.txt").write_text(ai.log.toPlainText(), encoding="utf8")
ai.import_button.click()
assert ai.import_path == font_file

# 四个真实字完全从参考中排除；禁止网络后检查模型能否生成真正未录的字。
heldout = json.loads(json.dumps(original))
for char in "天地书女":
    del heldout["glyphs"][char]
heldout_folder = folder / "heldout"
heldout_folder.mkdir(exist_ok=True)
(heldout_folder / "recorded-font.json").write_text(json.dumps(heldout, ensure_ascii=False), encoding="utf8")
request = json.loads(font_file.with_name("request.json").read_text(encoding="utf8"))
request.pop("base_font", None)
request.update(samples=str(heldout_folder), characters="天地书女", output=str(heldout_folder / "output/font.json"))
heldout_request = folder / "heldout-request.json"
heldout_request.write_text(json.dumps(request, ensure_ascii=False), encoding="utf8")
code = """
import runpy, socket, sys, urllib.request
def blocked(*args, **kwargs):
    raise RuntimeError('Network forbidden')
socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.create_connection = blocked
urllib.request.urlopen = blocked
sys.argv = [sys.argv[1], '--request', sys.argv[2]]
runpy.run_path(sys.argv[0], run_name='__main__')
"""
offline = subprocess.run([ai.fields["python"].text(), "-B", "-X", "utf8", "-c", code, str(ROOT / "tools/sdt_adapter.py"), str(heldout_request)], cwd=ROOT, capture_output=True, text=True, encoding="utf8", timeout=300)
assert offline.returncode == 0, offline.stdout + offline.stderr
heldout_result = json.loads(Path(request["output"]).read_text(encoding="utf8"))
heldout_summary = json.loads(Path(request["output"]).with_name("summary.json").read_text(encoding="utf8"))
assert heldout_summary["generated"] == list("天地书女")
assert all(not set(trace["reference_characters"]) & set("天地书女") for trace in heldout_summary["diagnostics"].values())
assert all(heldout_result["glyphs"][c] != original["glyphs"][c] for c in "天地书女")
FontStore().read(request["output"])

manager = StrokeFontDialog(store=FontStore(folder / "fonts"))
manager.import_font(path=font_file)
identity = manager.selected_id
manager.character.setText("调")
manager.select_character()
manager.show()
manager.canvas.replay()
assert manager.canvas.timer.isActive()
manager.canvas.timer.stop()
manager.canvas.replay_count = None
manager.grab().save(str(folder / "font-editor.png"))
manager.accept()

dialog = GenerationDialog(MachineProfile(), "text")
dialog.stroke_font.setCurrentIndex(dialog.stroke_font.findData(identity))
dialog.stroke_fallback.setChecked(False)
dialog.natural_enabled.setChecked(False)
dialog.text.setPlainText("调林展其")
dialog.font_size.setValue(24)
dialog.show()
dialog.generate()
wait_until(lambda: dialog.worker is None)
task = dialog.task
assert task is not None, dialog.status.text()
assert task.path_count == sum(len(result["glyphs"][c]["variants"][0]["strokes"]) for c in "调林展其")
dialog.export(path=folder / "personal-sample.ngc")
assert len(load_program(folder / "personal-sample.ngc").commands) == len(task.program.commands)
dialog.preview.fit_job()
application.processEvents()
dialog.grab().save(str(folder / "text-preview.png"))
dialog.reject()

window = MainWindow()
window.save_profile = lambda: None
window.confirm = lambda title, message: True
try:
    window.load_generated(task)
    window.port_combo.setCurrentIndex(window.port_combo.findData(SIMULATOR))
    window.toggle_connection()
    wait_until(lambda: window.controller.available)
    window.baseline_check.setChecked(True)
    wait_until(lambda: window.pen_up_button.isEnabled())
    window.pen_up_button.click()
    wait_until(lambda: window.controller.available and window.controller.machine[2] == window.profile.pen_up)
    window.start_job()
    wait_until(lambda: window.job.result == "complete")
    assert window.controller.state == "Idle"
finally:
    window.controller.disconnect_device()
    window.close()
assert before == config_hashes(), "不得改动常用配置或原字库"
summary.update(font_file=str(font_file), heldout=heldout_summary, paths=task.path_count, commands=len(task.program.commands), offline=True, simulator="Idle", configuration_unchanged=True)
(folder / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf8")
for widget in application.topLevelWidgets():
    widget.close()
    widget.deleteLater()
QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
application.processEvents()
print("Trajectory smoke passed: real recordings, unseen glyphs, offline heldout, GUI replay, G-code and simulator Idle.")
print(json.dumps({key: summary[key] for key in ("font_file", "inference_seconds", "paths", "commands", "offline", "simulator", "configuration_unchanged")}, ensure_ascii=False))
