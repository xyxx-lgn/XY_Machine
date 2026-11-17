"""使用指定真实录字验证离线学习、重新扩字、GUI与模拟任务，不连接硬件。"""

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
os.environ["TEMP"] = str(ROOT / ".runtime/tmp")
os.environ["TMP"] = os.environ["TEMP"]

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


def wait_until(condition, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if condition():
            return
        time.sleep(.005)
    raise AssertionError("等待个人笔迹学习或模拟设备超时")


def config_hashes():
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / "config").rglob("*") if p.is_file()}


parser = argparse.ArgumentParser()
parser.add_argument("--samples", required=True, type=Path)
parser.add_argument("--base", type=Path)
args = parser.parse_args()
application = QApplication.instance() or QApplication([])
configure_appearance(application)
before = config_hashes()
folder = ROOT / ".runtime/personal-learning/smoke"
folder.mkdir(parents=True, exist_ok=True)
(folder / "config-before.json").write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")
stroke_fonts.FONT_ROOT = folder / "fonts"
sdt_dialog.PREFERENCES = folder / "sdt.json"
generation_dialog.PREFERENCES = folder / "generation.json"
for path in (sdt_dialog.PREFERENCES, generation_dialog.PREFERENCES):
    path.unlink(missing_ok=True)
original = json.loads((args.samples / "recorded-font.json").read_text(encoding="utf-8"))
base = FontStore().read(args.base or (args.samples / "recorded-font.json"))
ai = SdtDialog(samples=args.samples, base_package=base)
ai.fields["python"].setText(sys.executable)
ai.font_name.setText("我的笔迹_离线学习扩展试样")
ai.characters.setPlainText("离线扩展还是需要调用其他在线的呢林明好休想书")
assert ai.method.currentData() == "personal"
ai.show()
ai.start(True)
wait_until(lambda: ai.process is None)
assert "检查通过" in ai.log.toPlainText(), ai.log.toPlainText()
ai.start(False)
wait_until(lambda: ai.process is None)
assert ai.import_button.isEnabled(), ai.log.toPlainText()
font_file = ai.output_path
result = json.loads(font_file.read_text(encoding="utf-8"))
summary = json.loads(font_file.with_name("summary.json").read_text(encoding="utf-8"))
assert summary["algorithm"] == "personal-stroke-context-constraints-v2"
for char, glyph in original["glyphs"].items():
    assert result["glyphs"][char] == glyph, "不得改动真实录字"
assert summary["details"]["林"]["reused_strokes"] == 8
assert {item["recorded_character"] for item in summary["details"]["明"]["components"]} == {"日", "月"}
assert any(item["component"] == "子" and item["recorded_character"] == "子" for item in summary["details"]["好"]["components"])
if args.base:
    previous = json.loads(args.base.read_text(encoding="utf-8"))
    assert set(summary["regenerated_existing"]) == set(summary["generated"]) & set(previous["glyphs"])
    assert all(result["glyphs"][c] != previous["glyphs"][c] for c in summary["regenerated_existing"])
assert font_file.with_name("personal-model.json").is_file()
assert "复用" in ai.log.toPlainText()
ai.grab().save(str(folder / "learning-complete.png"))
(folder / "ui-log.txt").write_text(ai.log.toPlainText(), encoding="utf-8")
ai.import_button.click()
assert ai.import_path == font_file

# 完全禁止网络连接后重新学习；调用主环境，不需要torch或CUDA。
request = json.loads(font_file.with_name("request.json").read_text(encoding="utf-8"))
request["output"] = str(folder / "offline/font.json")
offline_request = folder / "offline-request.json"
offline_request.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
code = """
import runpy, socket, sys, urllib.request
def blocked(*args, **kwargs):
    raise RuntimeError('Network forbidden')
socket.socket.connect = blocked
socket.create_connection = blocked
urllib.request.urlopen = blocked
sys.argv = [sys.argv[1], '--request', sys.argv[2]]
runpy.run_path(sys.argv[0], run_name='__main__')
"""
offline = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", code, str(ROOT / "tools/sdt_adapter.py"), str(offline_request)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=90)
assert offline.returncode == 0, offline.stdout + offline.stderr
assert json.loads(Path(request["output"]).read_text(encoding="utf-8"))["glyphs"] == result["glyphs"]

manager = StrokeFontDialog(store=FontStore(folder / "fonts"))
manager.import_font(path=font_file)
identity = manager.selected_id
manager.character.setText("好")
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
dialog.text.setPlainText("林明好休想")
dialog.font_size.setValue(24)
dialog.show()
dialog.generate()
wait_until(lambda: dialog.worker is None)
task = dialog.task
assert task is not None, dialog.status.text()
assert task.path_count == sum(len(result["glyphs"][c]["variants"][0]["strokes"]) for c in "林明好休想")
dialog.export(path=folder / "personal-sample.ngc")
assert len(load_program(folder / "personal-sample.ngc").commands) == len(task.program.commands)
dialog.preview.fit_job()
application.processEvents()
dialog.grab().save(str(folder / "text-preview.png"))
dialog.preview.grab().save(str(folder / "trajectories.png"))
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
    wait_until(lambda: window.job.result == "complete", timeout=max(180, len(task.program.commands) * .1 + 60))
    assert window.controller.state == "Idle"
finally:
    window.controller.disconnect_device()
    window.close()
assert before == config_hashes(), "不得改动常用配置或原字库"
summary.update(font_file=str(font_file), paths=task.path_count, commands=len(task.program.commands), offline=True, simulator="Idle", configuration_unchanged=True)
(folder / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
for widget in application.topLevelWidgets():
    widget.close()
    widget.deleteLater()
QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
application.processEvents()
print("Personal learning smoke passed: real strokes, re-generation, offline, GUI import/replay, G-code and simulator Idle.")
print(json.dumps(summary, ensure_ascii=False))
