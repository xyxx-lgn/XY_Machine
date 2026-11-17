"""真实Qt控件、录字持久化及模拟设备验证；不打开硬件串口。"""

import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["TEMP"] = str(ROOT / ".runtime/tmp")
os.environ["TMP"] = os.environ["TEMP"]

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.appearance import configure_appearance
from app.profile import MachineProfile
from app import stroke_fonts
from app.stroke_fonts import FontStore
from app.text_writing import stroke_glyph
from app.transport import SIMULATOR
from app.ui import generation_dialog
from app.ui.generation_dialog import GenerationDialog
from app.ui.main_window import MainWindow
from app.ui.stroke_font_dialog import StrokeFontDialog
from app.ui.sdt_dialog import SdtDialog
from app.ui import sdt_dialog


def wait_until(condition, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if condition():
            return
        time.sleep(.005)
    raise AssertionError("等待自然书写界面或模拟设备超时")


application = QApplication([])
configure_appearance(application)
test_root = ROOT / ".runtime/natural-writing-smoke"
test_root.mkdir(parents=True, exist_ok=True)
stroke_fonts.FONT_ROOT = test_root / "fonts"
generation_dialog.PREFERENCES = test_root / "preferences.json"
sdt_dialog.PREFERENCES = test_root / "sdt-preferences.json"
generation_dialog.PREFERENCES.unlink(missing_ok=True)
screens = ROOT / ".runtime/screenshots"
screens.mkdir(parents=True, exist_ok=True)
protected = [ROOT / "config/machine.json", ROOT / "config/generation.json"]
before = {p: p.read_bytes() if p.exists() else None for p in protected}

# 实际鼠标输入画板，验证按下/抬起的分笔、撤销、回放和保存后重开。
manager = StrokeFontDialog(store=FontStore(test_root / "fonts"))
manager.show()
application.processEvents()
manager.name.setText("我的试录字库")
manager.character.setText("口")
canvas = manager.canvas
box = canvas.square()

def draw_path(points):
    def point(p):
        return QPoint(round(box.left() + p[0] * box.width()), round(box.top() + p[1] * box.height()))
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=point(points[0]))
    for p in points[1:]:
        QTest.mouseMove(canvas, point(p))
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=point(points[-1]))

for path in [[(.2, .2), (.2, .8)], [(.2, .2), (.8, .2), (.8, .8)], [(.2, .8), (.8, .8)]]:
    draw_path(path)
assert len(canvas.strokes) == 3
manager.add_variant()
manager.canvas.set_strokes([[(x + .01, y) for x, y in path] for path in canvas.strokes])
manager.add_variant()
assert len(manager.package.glyphs["口"]["variants"]) == 2
manager.canvas.replay()
application.processEvents()
manager.canvas.timer.stop()
manager.canvas.replay_count = None
manager.canvas.undo()
assert len(manager.canvas.strokes) == 2
manager.select_variant()
manager.save_font()
identity = manager.selected_id
assert identity and FontStore(test_root / "fonts").load(identity).name == "我的试录字库"

# 100格可重复编辑而不重复附加版本；采样状态从字库版本恢复。
manager.samples.setCurrentRow(0)
manager.begin_sample()
manager.canvas.set_strokes(stroke_glyph("永").strokes)
manager.save_sample()
manager.save_sample()
assert len(manager.package.glyphs["永"]["variants"]) == 1
manager.save_font()
manager.export_font(path=test_root / "font-export.json")
manager.export_sheet(path=test_root / "100-samples.svg")
manager.export_samples(path=test_root / "reference-pngs")
assert (test_root / "reference-pngs/samples.json").is_file()
manager.grab().save(str(screens / "natural-font-editor.png"))
manager.accept()
manager = StrokeFontDialog(identity, store=FontStore(test_root / "fonts"))
assert manager.sample_exists(0)
assert len(manager.package.glyphs["口"]["variants"]) == 2
manager.import_font(path=test_root / "font-export.json")
assert manager.selected_id != identity
manager.accept()

profile = MachineProfile()
dialog = GenerationDialog(profile, "text")
dialog.show()
dialog.stroke_font.setCurrentIndex(dialog.stroke_font.findData(identity))
dialog.natural_enabled.setChecked(True)
dialog.natural_strength.setValue(60)
dialog.natural_seed.setValue(2026)
dialog.latin_style.setCurrentIndex(dialog.latin_style.findData("scripts"))
dialog.text.setPlainText("口口永永\n你好，世界！Hello 123\n同字不同，保留笔顺。")
dialog.font_size.setValue(18)
dialog.generate()
wait_until(lambda: dialog.worker is None)
assert dialog.task is not None, dialog.status.text()
assert any("基础楷体补缺" in note for note in dialog.task.notes)
first = dialog.task.program.source_text
dialog.generate()
wait_until(lambda: dialog.worker is None)
assert dialog.task.program.source_text == first
application.processEvents()
dialog.grab().save(str(screens / "natural-writing-dialog.png"))
dialog.export(path=test_root / "natural-sample.ngc")
dialog.reject()

dialog = GenerationDialog(profile, "text")
assert dialog.stroke_font.currentData() == identity
assert dialog.natural_enabled.isChecked() and dialog.natural_strength.value() == 60
assert dialog.natural_seed.value() == 2026 and dialog.latin_style.currentData() == "scripts"
dialog.shuffle_natural()
assert dialog.natural_seed.value() != 2026 and dialog.task is None
dialog.stroke_fallback.setChecked(False)
dialog.text.setPlainText("口中")
dialog.generate()
assert dialog.worker is None and dialog.task is None and "缺少1种字符" in dialog.status.text()
dialog.stroke_fallback.setChecked(True)
dialog.text.setPlainText("口口")
dialog.generate()
wait_until(lambda: dialog.worker is None)
task = dialog.task
assert task is not None and task.path_count == 6
dialog.mode.setCurrentIndex(dialog.mode.findData("outline"))
assert not dialog.stroke_controls.isVisibleTo(dialog) and dialog.font.isEnabled()
dialog.reject()

# 故意选择没有AI依赖的主环境，失败必须可见且不能产生可用字库。
ai = SdtDialog()
ai.method.setCurrentIndex(ai.method.findData("sdt"))
ai.fields["python"].setText(sys.executable)
ai.start(True)
wait_until(lambda: ai.process is None)
assert "未完成" in ai.log.toPlainText()
assert not ai.import_button.isEnabled() and ai.import_path is None
ai.reject()

# 用独立的长任务夹具验证取消/关窗，不模拟或声称完成任何模型推理。
fixture = test_root / "process-cancel"
(fixture / "tools").mkdir(parents=True, exist_ok=True)
(fixture / "tools/sdt_adapter.py").write_text("import time\nprint('cancellation fixture started', flush=True)\ntime.sleep(45)\n", encoding="utf-8")
real_root = sdt_dialog.ROOT
sdt_dialog.ROOT = fixture
try:
    ai = SdtDialog()
    ai.fields["python"].setText(sys.executable)
    ai.start(False)
    wait_until(lambda: "fixture started" in ai.log.toPlainText())
    ai.reject()
    wait_until(lambda: ai.process is None)
    assert "已取消" in ai.log.toPlainText() and not ai.import_button.isEnabled()
finally:
    sdt_dialog.ROOT = real_root

# 已选字库被移走时必须报错，不能静默换成另一种字体继续发送。
generation_dialog.PREFERENCES.write_text(json.dumps({"text": {"mode": "strokes", "stroke_font": "user_" + "f" * 32}}), encoding="utf-8")
missing = GenerationDialog(profile, "text")
missing.generate()
assert missing.task is None and "字库无法使用" in missing.status.text()
missing.reject()

window = MainWindow()
window.profile = profile
window.controller.profile = profile
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
    wait_until(lambda: window.controller.available and window.controller.machine[2] == profile.pen_up)
    window.start_job()
    wait_until(lambda: window.job.result == "complete")
    assert window.controller.state == "Idle"
finally:
    window.controller.disconnect_device()
    window.close()
for p, data in before.items():
    assert (p.read_bytes() if p.exists() else None) == data, str(p)
# 处理Qt延后删除，避免多个无父窗口留到Python退出时才交叉析构。
for widget in application.topLevelWidgets():
    widget.close()
    widget.deleteLater()
QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
application.processEvents()
print("Natural writing smoke passed: mouse strokes, variants, sampling, font import/export, saved settings, fixed seed, fallback, missing font, AI diagnostics/cancellation and simulator.")
