"""用指定真实录字请求验证清晰分笔、原字保留、UI导入和模拟发送，不连接硬件。"""

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

from PySide6.QtWidgets import QApplication
from app.appearance import configure_appearance
from app.gcode import load_program
from app.profile import MachineProfile
from app import stroke_fonts
from app.stroke_fonts import FontStore
from app.personal_strokes import reference_strokes
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
    raise AssertionError("等待扩字或模拟设备超时")


def config_hashes():
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / "config").rglob("*") if p.is_file()}


parser = argparse.ArgumentParser()
parser.add_argument("--request", required=True)
args = parser.parse_args()
request = json.loads(Path(args.request).read_text(encoding="utf-8"))
application = QApplication.instance() or QApplication([])
configure_appearance(application)
before = config_hashes()
folder = ROOT / ".runtime/sdt/validation/recorded-extension"
folder.mkdir(parents=True, exist_ok=True)
stroke_fonts.FONT_ROOT = folder / "fonts"
sdt_dialog.PREFERENCES = folder / "sdt.json"
generation_dialog.PREFERENCES = folder / "generation.json"
sdt_dialog.PREFERENCES.unlink(missing_ok=True)
generation_dialog.PREFERENCES.unlink(missing_ok=True)
recorded_file = Path(request["samples"]) / "recorded-font.json"
original = FontStore().read(recorded_file)
original_json = json.loads(recorded_file.read_text(encoding="utf-8"))
ai = SdtDialog(samples=Path(request["samples"]), base_package=original)
ai.method.setCurrentIndex(ai.method.findData("recorded"))
ai.fields["python"].setText(sys.executable)
ai.font_name.setText("个人笔迹清晰分笔试样")
ai.characters.setPlainText(request["characters"])
assert ai.method.currentData() == "recorded"
ai.show()
ai.start(True)
wait_until(lambda: ai.process is None)
assert "检查通过" in ai.log.toPlainText(), ai.log.toPlainText()
ai.start(False)
wait_until(lambda: ai.process is None)
assert ai.import_button.isEnabled(), ai.log.toPlainText()
model_file = ai.output_path
result_json = json.loads(model_file.read_text(encoding="utf-8"))
for char, glyph in original_json["glyphs"].items():
    assert result_json["glyphs"][char] == glyph, "原始录字必须逐点保留"
new_chars = [char for char in dict.fromkeys(request["characters"]) if not char.isspace() and char not in original.glyphs]
for char in new_chars:
    glyph = result_json["glyphs"][char]
    assert len(glyph["variants"][0]["strokes"]) == len(reference_strokes(char))
ai.grab().save(str(folder / "extension-complete.png"))
(folder / "ui-log.txt").write_text(ai.log.toPlainText(), encoding="utf-8")
ai.import_button.click()
assert ai.import_path == model_file

# 在禁止网络连接的子进程重复生成，不需要PyTorch或CUDA。
offline = json.loads(model_file.with_name("request.json").read_text(encoding="utf-8"))
offline["output"] = str(folder / "offline/font.json")
offline_file = folder / "offline-request.json"
offline_file.write_text(json.dumps(offline, ensure_ascii=False), encoding="utf-8")
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
check = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", code, str(ROOT / "tools/sdt_adapter.py"), str(offline_file)], capture_output=True, text=True, encoding="utf-8", timeout=120)
assert check.returncode == 0, check.stdout + check.stderr
assert json.loads(Path(offline["output"]).read_text(encoding="utf-8"))["glyphs"] == result_json["glyphs"]

manager = StrokeFontDialog(store=FontStore(folder / "fonts"))
manager.import_font(path=model_file)
identity = manager.selected_id
manager.character.setText(new_chars[0])
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
dialog.text.setPlainText("".join(new_chars))
dialog.font_size.setValue(24)
dialog.show()
dialog.generate()
wait_until(lambda: dialog.worker is None)
task = dialog.task
assert task is not None, dialog.status.text()
assert task.path_count == sum(len(result_json["glyphs"][c]["variants"][0]["strokes"]) for c in new_chars)
target = folder / "sample.ngc"
dialog.export(path=target)
assert len(load_program(target).commands) == len(task.program.commands)
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
    # 模拟器每行固定等待60ms，较多分笔不能沿用短小样的180秒上限。
    wait_until(lambda: window.job.result == "complete", timeout=max(180, len(task.program.commands) * .1 + 60))
    assert window.controller.state == "Idle"
finally:
    window.controller.disconnect_device()
    window.close()
assert before == config_hashes(), "不得覆盖用户配置或字库"
summary = json.loads(model_file.with_name("summary.json").read_text(encoding="utf-8"))
summary.update(font_file=str(model_file), original_preserved=len(original.glyphs), paths=task.path_count, commands=len(task.program.commands), offline=True, simulator="Idle", configuration_unchanged=True)
(folder / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print("Recorded extension smoke passed: preserved original, canonical pen lifts, offline, font editor, G-code and simulator Idle.")
print(json.dumps(summary, ensure_ascii=False))
