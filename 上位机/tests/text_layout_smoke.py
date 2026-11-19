import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["TEMP"] = str(ROOT / ".runtime" / "tmp")
os.environ["TMP"] = os.environ["TEMP"]

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from app.appearance import configure_appearance
from app.gcode import load_program
from app.profile import MachineProfile
from app.transport import SIMULATOR
from app.ui import generation_dialog
from app.ui.generation_dialog import GenerationDialog
from app.ui.main_window import MainWindow


def wait_until(condition, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if condition():
            return
        time.sleep(0.005)
    raise AssertionError("等待正文排版或模拟发送超时")


application = QApplication([])
configure_appearance(application)
preferences = ROOT / ".runtime" / "text_layout_preferences_test.json"
generation_dialog.PREFERENCES = preferences
preferences.unlink(missing_ok=True)
folder = ROOT / ".runtime" / "screenshots"
folder.mkdir(parents=True, exist_ok=True)
profile = MachineProfile(pen_up=3, pen_up_feed=300)
dialog = GenerationDialog(profile, "text")
dialog.show()
assert dialog.is_page_layout()
assert not dialog.width.isEnabled() or not dialog.artwork_settings.isVisible()
assert not dialog.crop.isEnabled() and not dialog.invert.isEnabled()
dialog.text.setPlainText("固定字号，自动换行。这段文字没有手动回车，中文和 English words 都按纸张宽度排版。" * 10)
dialog.update_text_preview()
assert dialog.text_document is not None, dialog.status.text()
assert len(dialog.text_document.pages[0].lines) > 1
assert dialog.font_size.value() == 14
first_height = dialog.text_document.pages[0].lines[0].height
dialog.resize(1100, 800)
application.processEvents()
assert dialog.text_document.pages[0].lines[0].height == first_height
dialog.grab().save(str(folder / "text-layout-wrapped.png"))

# 缩小可写高度强制分页；更换页必须使旧任务失效且导出名包含页码。
dialog.margin_bottom.setValue(245)
dialog.text.setPlainText("HOB 123\n中文排版测试\n第三行文字\n第四行文字\n第五行文字\n第六行文字\n第七行文字\n第八行文字")
dialog.mode.setCurrentIndex(dialog.mode.findData("fill"))
dialog.update_text_preview()
assert len(dialog.text_document.pages) > 1
dialog.generate()
wait_until(lambda: dialog.worker is None)
assert dialog.task is not None, dialog.status.text()
assert "001" in dialog.task.program.path.name
assert dialog.task.program.z_bounds == (-0.4, 3)
dialog.next_page.click()
assert dialog.task is None and not dialog.save_button.isEnabled() and not dialog.load_button.isEnabled()
assert dialog.preview.program is None and dialog.page_number.value() == 2
dialog.generate()
wait_until(lambda: dialog.worker is None)
assert dialog.task is not None, dialog.status.text()
assert "002" in dialog.task.program.path.name
output = ROOT / ".runtime" / "正文第二页.ngc"
dialog.up_feed.setValue(1000)
dialog.down_feed.setValue(900)
dialog.font_preset.setCurrentIndex(dialog.font_preset.findData(12))
dialog.letter_spacing.setValue(-0.2)
dialog.paragraph_spacing.setValue(1)
dialog.indent.setValue(2)
dialog.alignment.setCurrentIndex(dialog.alignment.findData("center"))
dialog.font.setCurrentFont(QFont("Microsoft YaHei"))
dialog.bold.setChecked(True)
dialog.generate()
wait_until(lambda: dialog.worker is None)
assert dialog.task is not None, dialog.status.text()
dialog.export(path=output)
parsed = load_program(output, pen_threshold=1.3)
assert len(parsed.commands) == len(dialog.task.program.commands)
assert dialog.preview.text_rect is not None
dialog.grab().save(str(folder / "text-layout-page-generated.png"))
dialog.reject()

dialog = GenerationDialog(MachineProfile(pen_up=4), "text")
assert dialog.font_size.value() == 12 and dialog.font_preset.currentData() == 12
assert dialog.letter_spacing.value() == -0.2 and dialog.indent.value() == 2
assert dialog.paragraph_spacing.value() == 1 and dialog.margin_bottom.value() == 245
assert dialog.bold.isChecked() and dialog.alignment.currentData() == "center"
assert dialog.up_feed.value() == 1000 and dialog.down_feed.value() == 900 and dialog.pen_up.value() == 4
dialog.generate()
dialog.cancel_generation()
wait_until(lambda: dialog.worker is None)
assert dialog.task is None
dialog.margin_left.setValue(205)
dialog.generate()
assert dialog.task is None and not dialog.load_button.isEnabled()
assert "区域" in dialog.status.text()
dialog.reject()

# 旧偏好没有排版字段时默认进入正文，但保留原来的字体、填线和Z速度。
preferences.write_text(json.dumps({"text": {"text": "HOB", "font": "Microsoft YaHei", "up_feed": 800}}), encoding="utf-8")
dialog = GenerationDialog(profile, "text")
assert dialog.is_page_layout() and dialog.font_size.value() == 14 and dialog.up_feed.value() == 800
dialog.layout_mode.setCurrentIndex(dialog.layout_mode.findData("artwork"))
dialog.text.setPlainText("H")
dialog.height.setValue(10)
dialog.generate()
wait_until(lambda: dialog.worker is None)
assert dialog.task is not None and dialog.height.value() == 10
assert dialog.preview.text_rect is None
dialog.reject()

# 新的当前页任务复用完整模拟发送和发送前行程检查。
dialog = GenerationDialog(profile, "text")
dialog.text.setPlainText("I")
dialog.mode.setCurrentIndex(dialog.mode.findData("outline"))
dialog.generate()
wait_until(lambda: dialog.worker is None)
assert dialog.task is not None
task = dialog.task
dialog.reject()
window = MainWindow()
window.profile = profile
window.controller.profile = profile
window.save_profile = lambda: None
window.confirm = lambda title, message: True
window.show()
window.load_generated(task)
assert not window.controller.connected and not window.job.active
window.port_combo.setCurrentIndex(window.port_combo.findData(SIMULATOR))
window.toggle_connection()
wait_until(lambda: window.controller.available)
window.baseline_check.setChecked(True)
wait_until(lambda: window.pen_up_button.isEnabled())
window.pen_up_button.click()
wait_until(lambda: window.controller.available and window.controller.machine[2] == 3)
window.start_job()
wait_until(lambda: window.job.result == "complete")
assert window.controller.machine[2] == 3
window.controller.disconnect_device()
window.close()
print("Text layout GUI: PASS (fixed size/wrap/page/margins/preferences/dirty/export/artwork/cancel/simulator)")
