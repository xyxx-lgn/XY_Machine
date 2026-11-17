import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['TEMP'] = str(ROOT / '.runtime/tmp')
os.environ['TMP'] = os.environ['TEMP']

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
from app.appearance import configure_appearance
from app.gcode import load_program
from app.profile import MachineProfile
from app.transport import SIMULATOR
from app.ui import generation_dialog
from app.ui.generation_dialog import GenerationDialog
from app.ui.main_window import MainWindow


def wait_until(condition, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if condition(): return
        time.sleep(.005)
    raise AssertionError('等待笔画生成/模拟发送超时')


application = QApplication([])
configure_appearance(application)
preferences = ROOT / '.runtime/stroke_preferences_test.json'
generation_dialog.PREFERENCES = preferences
preferences.unlink(missing_ok=True)
profile = MachineProfile()
folder = ROOT / '.runtime/screenshots'
folder.mkdir(parents=True, exist_ok=True)

# 新入口保留排版和速度，但锁定真实笔画样式与笔顺。
dialog = GenerationDialog(profile, 'text')
dialog.show()
assert dialog.mode.currentData() == 'strokes'
assert not dialog.font.isEnabled() and not dialog.bold.isEnabled()
assert not dialog.minimum.isEnabled() and not dialog.optimize.isEnabled()
assert not dialog.tolerance.isEnabled() and not dialog.spacing.isEnabled()
assert dialog.text_pen_width.isEnabled()
dialog.text.setPlainText('十口中永田国\n你好，世界！HOB 123\n独立笔画，连续折笔。')
dialog.font_size.setValue(18)
dialog.generate()
wait_until(lambda: dialog.worker is None)
assert dialog.task is not None, dialog.status.text()
assert dialog.task.program.preview_pen_width == .18
assert not dialog.preview.drawing_pen().isCosmetic()
assert dialog.task.program.z_bounds == (-.4, 2)
application.processEvents()
application.processEvents()
scale, left, top, _, _ = dialog.preview.view_geometry()
x0, y0, x1, y1 = dialog.task.program.bounds
assert 0 < left + (x0 + x1) * scale / 2 < dialog.preview.width()
assert 0 < top + (y0 + y1) * scale / 2 < dialog.preview.height()
dialog.grab().save(str(folder / 'stroke-writing-dialog.png'))
exported = ROOT / '.runtime/stroke-writing-sample.ngc'
dialog.export(path=exported)
parsed = load_program(exported)
assert len(parsed.commands) == len(dialog.task.program.commands)
count = dialog.task.path_count

dialog.text.setPlainText('你好🙂🙂')
dialog.generate()
assert dialog.worker is None and dialog.task is None
assert '缺少1种字符' in dialog.status.text()
assert not dialog.save_button.isEnabled() and not dialog.load_button.isEnabled()

# 旧实心入口迁移；原有骨架/轮廓/填充的显式偏好仍有效。
dialog.reject()
preferences.write_text(json.dumps({'text': {'mode': 'font', 'font': 'SimHei', 'up_feed': 800, 'text_pen_width': .2}}), encoding='utf-8')
dialog = GenerationDialog(profile, 'text')
assert dialog.mode.currentData() == 'strokes' and dialog.up_feed.value() == 800
assert dialog.text_pen_width.value() == .2
dialog.mode.setCurrentIndex(dialog.mode.findData('outline'))
assert dialog.font.isEnabled() and dialog.minimum.isEnabled()
dialog.text.setPlainText('H')
dialog.generate()
wait_until(lambda: dialog.worker is None)
assert dialog.task is not None, dialog.status.text()
assert not hasattr(dialog.task.program, 'preview_pen_width')
dialog.mode.setCurrentIndex(dialog.mode.findData('strokes'))
dialog.layout_mode.setCurrentIndex(dialog.layout_mode.findData('artwork'))
dialog.text.setPlainText('口中')
dialog.height.setValue(15)
dialog.generate()
wait_until(lambda: dialog.worker is None)
assert dialog.task is not None, dialog.status.text()
assert dialog.task.path_count == 7
assert not dialog.invert.isEnabled() and not dialog.crop.isEnabled()
dialog.export(path=ROOT / '.runtime/stroke-artwork.ngc')
dialog.reject()
dialog = GenerationDialog(profile, 'text')
assert dialog.mode.currentData() == 'strokes' and dialog.text_pen_width.value() == .2
dialog.layout_mode.setCurrentIndex(dialog.layout_mode.findData('page'))
dialog.text.setPlainText('口' * 1000)
dialog.generate()
dialog.cancel_generation()
wait_until(lambda: dialog.worker is None)
assert dialog.task is None and '取消' in dialog.status.text()
dialog.reject()

image_dialog = GenerationDialog(profile, 'image')
assert image_dialog.mode.count() == 4
assert not hasattr(image_dialog, 'text_pen_width')
image_dialog.reject()

# 小任务走完整模拟通道；从未打开真实串口或修改用户机器配置。
generation_dialog.PREFERENCES = ROOT / '.runtime/stroke_simulation_preferences.json'
generation_dialog.PREFERENCES.unlink(missing_ok=True)
dialog = GenerationDialog(profile, 'text')
dialog.text.setPlainText('口')
dialog.generate()
wait_until(lambda: dialog.worker is None)
task = dialog.task
assert task is not None and task.path_count == 3
dialog.reject()
window = MainWindow()
window.profile = profile
window.controller.profile = profile
window.save_profile = lambda: None
window.confirm = lambda title, message: True
window.show()
try:
    window.load_generated(task)
    assert not window.controller.connected
    window.port_combo.setCurrentIndex(window.port_combo.findData(SIMULATOR))
    window.toggle_connection()
    wait_until(lambda: window.controller.available)
    window.baseline_check.setChecked(True)
    wait_until(lambda: window.pen_up_button.isEnabled())
    window.pen_up_button.click()
    wait_until(lambda: window.controller.available and window.controller.machine[2] == profile.pen_up)
    window.start_job()
    wait_until(lambda: window.job.result == 'complete')
    assert window.controller.state == 'Idle'
finally:
    window.controller.disconnect_device()
    window.close()
print('Stroke writing smoke passed: order, missing glyphs, preferences, artwork, cancellation, legacy modes, export and simulator.')
