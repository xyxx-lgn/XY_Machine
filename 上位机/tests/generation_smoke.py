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

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.appearance import configure_appearance
from app.gcode import load_program
from app.generation import PlotOptions, generate_task, render_text
from app.profile import MachineProfile
from app.transport import SIMULATOR
from app.ui import generation_dialog
from app.ui.generation_dialog import GenerationDialog
from app.ui.main_window import MainWindow


def wait_until(application, condition, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if condition():
            return
        time.sleep(0.005)
    raise AssertionError("等待生成/界面状态超时")


application = QApplication([])
configure_appearance(application)
folder = ROOT / ".runtime" / "screenshots"
folder.mkdir(parents=True, exist_ok=True)
generation_dialog.PREFERENCES = ROOT / ".runtime" / "generation_preferences_test.json"
generation_dialog.PREFERENCES.unlink(missing_ok=True)
window = MainWindow()
# 使用独立内存参数，既不保存用户机器配置，也不打开真实串口。
window.profile = MachineProfile(pen_up=3.0, pen_up_feed=300)
window.controller.profile = window.profile
window.save_profile = lambda: None
window.confirm = lambda title, message: True
window.show()
application.processEvents()
assert window.text_button.isEnabled() and window.image_button.isEnabled()
assert not window.export_button.isEnabled()

# 文本刚改变就指定总高，也应保留用户最后编辑的尺寸，不被字体比例刷新覆盖。
geometry_dialog = GenerationDialog(window.profile, "text", window)
geometry_dialog.text.setPlainText("H")
geometry_dialog.height.setValue(10)
geometry_dialog.generate()
wait_until(application, lambda: geometry_dialog.worker is None)
assert geometry_dialog.task is not None
assert geometry_dialog.height.value() == 10
geometry_dialog.reject()

dialog = GenerationDialog(window.profile, "text", window)
dialog.show()
assert dialog.pen_up.value() == 3 and dialog.up_feed.value() == 300
dialog.text.setPlainText("HOB 123\n写字机")
dialog.mode.setCurrentIndex(dialog.mode.findData("fill"))
dialog.generate()
wait_until(application, lambda: dialog.worker is None)
assert dialog.task is not None, dialog.status.text()
assert dialog.task.path_count > 0
assert dialog.task.program.z_bounds == (-0.4, 3.0)
assert dialog.load_button.isEnabled() and dialog.save_button.isEnabled()
dialog.preview.fit_job()
application.processEvents()
dialog.grab().save(str(folder / "stage3-text-fill.png"))
dialog.export(path=ROOT / ".runtime" / "text_fill_test.ngc")
parsed = load_program(ROOT / ".runtime" / "text_fill_test.ngc", pen_threshold=1.3)
assert len(parsed.commands) == len(dialog.task.program.commands)
dialog.x.setValue(11)
assert dialog.task is None and not dialog.load_button.isEnabled()
assert not dialog.save_button.isEnabled()
dialog.generate()
wait_until(application, lambda: dialog.worker is None)
dialog.load_task()
assert dialog.loaded_task is not None
window.load_generated(dialog.loaded_task)
assert window.job.program is dialog.loaded_task.program
assert not window.controller.connected and not window.job.active
assert window.export_button.isEnabled()
window.export_program(path=ROOT / ".runtime" / "main_export_test.ngc")
window.grab().save(str(folder / "stage3-main-loaded.png"))

# 记住常用生成参数，但下次打开仍取最新设备Z，不沿用旧任务覆盖值。
dialog = GenerationDialog(MachineProfile(pen_up=4, pen_up_feed=360), "text", window)
assert dialog.text.toPlainText() == "HOB 123\n写字机"
assert dialog.x.value() == 11
assert dialog.pen_up.value() == 4 and dialog.up_feed.value() == 360
dialog.reject()

dialog = GenerationDialog(window.profile, "image", window)
dialog.show()
dialog.choose_image(path=ROOT / "examples" / "阶段三线稿示例.png")
assert dialog.source is not None
for mode in ("outline", "outline_fill", "fill", "centerline"):
    dialog.mode.setCurrentIndex(dialog.mode.findData(mode))
    dialog.generate()
    wait_until(application, lambda: dialog.worker is None, 30)
    assert dialog.task is not None, (mode, dialog.status.text())
    assert dialog.task.program.drawing_length > 0
    application.processEvents()
    dialog.grab().save(str(folder / f"stage3-image-{mode}.png"))
dialog.invert.setChecked(True)
assert dialog.task is None
dialog.reject()

# 背景线程可以中止；关闭窗口时先结束工作线程，不留下运行中的QThread。
dialog = GenerationDialog(window.profile, "image", window)
dialog.choose_image(path=ROOT / "examples" / "阶段三线稿示例.png")
dialog.mode.setCurrentIndex(dialog.mode.findData("centerline"))
dialog.width.setValue(190)
dialog.generate()
dialog.cancel_generation()
wait_until(application, lambda: dialog.worker is None, 30)
assert dialog.task is None
dialog.generate()
dialog.reject()
wait_until(application, lambda: dialog.worker is None, 30)
assert not dialog.isVisible()

# 顶部入口确实能打开窗口；不需要连接设备即可进入生成流程。
def close_generator():
    active = application.activeModalWidget()
    assert isinstance(active, GenerationDialog)
    active.reject()

QTimer.singleShot(50, close_generator)
window.text_button.click()
assert not window.controller.connected

# 小型生成任务走完整模拟发送通道，并在结束时确认机器Z抬起。
image = render_text("I", "Arial")
task = generate_task(image, PlotOptions(width=2, height=5, mode="centerline", tolerance=0.05, pen_up=3))
window.load_generated(task)
window.port_combo.setCurrentIndex(window.port_combo.findData(SIMULATOR))
window.toggle_connection()
wait_until(application, lambda: window.controller.available)
window.baseline_check.setChecked(True)
wait_until(application, lambda: window.pen_up_button.isEnabled())
window.pen_up_button.click()
wait_until(application, lambda: window.controller.available and window.controller.machine[2] == 3)
window.start_job()
wait_until(application, lambda: window.job.active)
wait_until(application, lambda: window.job.result == "complete", 20)
assert window.controller.machine[2] == 3
assert window.job.program.preview_pen_threshold == task.program.preview_pen_threshold
assert window.controller.transport.offset == [-190, -280, 0]
window.grab().save(str(folder / "stage3-simulator-complete.png"))
window.controller.disconnect_device()
wait_until(application, lambda: window.loader is None)
window.close()
print("Generation GUI: PASS (text/CJK/fill/contour/centerline/preferences/dirty/export/cancel/load/simulator complete)")
