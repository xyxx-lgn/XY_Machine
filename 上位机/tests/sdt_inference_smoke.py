"""真实SDT权重、Qt子进程、离线复现与模拟设备验收；需要已准备好的AI环境。"""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["TEMP"] = str(ROOT / ".runtime/tmp")
os.environ["TMP"] = os.environ["TEMP"]

from PySide6.QtWidgets import QApplication
from app.appearance import configure_appearance
from app.gcode import load_program
from app.profile import MachineProfile
from app import stroke_fonts
from app.stroke_fonts import FontPackage, FontStore
from app.text_writing import stroke_glyph
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
        time.sleep(.01)
    raise AssertionError("等待真实SDT模型或模拟设备超时")


def configuration_hashes():
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / "config").rglob("*") if p.is_file()}


application = QApplication.instance() or QApplication([])
configure_appearance(application)
before = configuration_hashes()
folder = ROOT / ".runtime/sdt/validation/integration"
folder.mkdir(parents=True, exist_ok=True)
stroke_fonts.FONT_ROOT = folder / "fonts"
sdt_dialog.PREFERENCES = folder / "sdt-preferences.json"
generation_dialog.PREFERENCES = folder / "generation-preferences.json"
for prefs in (sdt_dialog.PREFERENCES, generation_dialog.PREFERENCES):
    prefs.unlink(missing_ok=True)
samples = ROOT / ".runtime/sdt/validation/official-samples"
assert len(list(samples.glob("*.png"))) >= 15, "请先准备官方测试笔迹；本测试不伪造模型推理"

# 内置永字只用于验证已有轨迹保留，不作为模型生成字或个人风格验收。
known = json.loads(json.dumps({"advance": 1, "variants": [{"strokes": stroke_glyph("永").strokes}]}))
base = FontPackage("已有轨迹保留验证", {"永": known})
ai = SdtDialog(samples=samples, base_package=base)
ai.method.setCurrentIndex(ai.method.findData("sdt"))
ai.font_name.setText("官方参考模型验证样本_非个人笔迹")
ai.characters.setPlainText("离线扩展需调呢永")
ai.seed.setValue(2026)
ai.show()
ai.start(True)
wait_until(lambda: ai.process is None)
assert "检查通过" in ai.log.toPlainText(), ai.log.toPlainText()
assert not ai.import_button.isEnabled()
ai.start(False)
wait_until(lambda: ai.process is None)
assert ai.import_button.isEnabled(), ai.log.toPlainText()
model_font = ai.output_path
package = FontStore().read(model_font)
assert set(package.glyphs) == set("离线扩展需调呢永")
raw = json.loads(model_font.read_text(encoding="utf-8"))
assert raw["glyphs"]["永"] == known
summary = json.loads(model_font.with_name("summary.json").read_text(encoding="utf-8"))
assert len(summary["generated"]) == 7 and summary["preserved_requested"] == ["永"]
ai.grab().save(str(folder / "ai-complete.png"))
(folder / "ui-log.txt").write_text(ai.log.toPlainText(), encoding="utf-8")
ai.import_button.click()
assert ai.import_path == model_font

# 禁止Python网络连接后重跑同一组真实权重，验证离线及固定样式编号的轨迹一致。
request = model_font.with_name("request.json")
offline_request = json.loads(request.read_text(encoding="utf-8"))
offline_request["output"] = str(folder / "offline/font.json")
offline_file = folder / "offline-request.json"
offline_file.write_text(json.dumps(offline_request, ensure_ascii=False), encoding="utf-8")
code = """
import runpy, socket, sys, urllib.request
def blocked(*args, **kwargs):
    raise RuntimeError('Network forbidden during offline inference verification')
socket.socket.connect = blocked
socket.create_connection = blocked
urllib.request.urlopen = blocked
sys.argv = [sys.argv[1], '--request', sys.argv[2]]
runpy.run_path(sys.argv[0], run_name='__main__')
"""
result = subprocess.run([ai.fields["python"].text(), "-B", "-X", "utf8", "-c", code, str(ROOT / "tools/sdt_adapter.py"), str(offline_file)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=180)
(folder / "offline-log.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
assert result.returncode == 0, result.stdout + result.stderr
assert json.loads(Path(offline_request["output"]).read_text(encoding="utf-8"))["glyphs"] == raw["glyphs"]

manager = StrokeFontDialog(store=FontStore(folder / "fonts"))
manager.import_font(path=model_font)
identity = manager.selected_id
assert identity and len(manager.package.glyphs) == 8
manager.character.setText("离")
manager.select_character()
manager.canvas.replay()
assert manager.canvas.timer.isActive()
manager.canvas.timer.stop()
manager.canvas.replay_count = None
manager.accept()
assert FontStore(folder / "fonts").load(identity).glyphs == package.glyphs

dialog = GenerationDialog(MachineProfile(), "text")
dialog.stroke_font.setCurrentIndex(dialog.stroke_font.findData(identity))
dialog.stroke_fallback.setChecked(False)
dialog.natural_enabled.setChecked(False)
dialog.text.setPlainText("离线扩展\n需调呢永")
dialog.font_size.setValue(24)
dialog.show()
dialog.generate()
wait_until(lambda: dialog.worker is None)
task = dialog.task
assert task is not None, dialog.status.text()
assert task.path_count == sum(len(g["variants"][0]["strokes"]) for g in package.glyphs.values())
target = folder / "model-sample.ngc"
dialog.export(path=target)
assert len(load_program(target).commands) == len(task.program.commands)
application.processEvents()
dialog.preview.fit_job()
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
    wait_until(lambda: window.job.result == "complete")
    assert window.controller.state == "Idle"
finally:
    window.controller.disconnect_device()
    window.close()
assert configuration_hashes() == before, "不得修改常用配置或个人字库"
summary.update(offline_reproduction=True, qt_subprocess=True, font_import=True, paths=task.path_count, commands=len(task.program.commands), simulator="Idle", user_configuration_unchanged=True)
(folder / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print("Real SDT smoke passed: model inference, offline reproduction, preserved glyphs, editor import/replay, G-code and simulator Idle.")
print(json.dumps(summary, ensure_ascii=False))
