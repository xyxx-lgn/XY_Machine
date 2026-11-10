import os
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["TEMP"] = str(ROOT / ".runtime" / "tmp")
os.environ["TMP"] = os.environ["TEMP"]

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from app.appearance import configure_appearance
from app.generation import PlotOptions, read_image
from app.image_processing import binary_thumbnail, process_image
from app.profile import MachineProfile
from app.transport import SIMULATOR
from app.ui import generation_dialog
from app.ui.generation_dialog import GenerationDialog
from app.ui.image_inspection import ImageInspectionDialog
from app.ui.main_window import MainWindow
from test_image_processing import cartoon_source


def wait_until(application, condition, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if condition():
            return
        time.sleep(0.005)
    raise AssertionError("等待图片处理状态超时")


def idle(dialog):
    return dialog.worker is None and dialog.image_worker is None and not dialog.pending_generate


app = QApplication([])
configure_appearance(app)
generation_dialog.PREFERENCES = ROOT / ".runtime" / "image_preferences_test.json"
generation_dialog.PREFERENCES.unlink(missing_ok=True)
folder = ROOT / ".runtime" / "screenshots"
folder.mkdir(parents=True, exist_ok=True)
source_path = ROOT / "OIP-C.webp"
if not source_path.exists():
    source_path = ROOT / ".runtime" / "synthetic_cartoon.png"
    cartoon_source().save(str(source_path))
window = MainWindow()
window.profile = MachineProfile(pen_up=3)
window.controller.profile = window.profile
window.save_profile = lambda: None
window.confirm = lambda title, message: True
window.show()

dialog = GenerationDialog(window.profile, "image", window)
dialog.show()
dialog.choose_image(path=source_path)
wait_until(app, lambda: idle(dialog) and dialog.processed is not None)
assert dialog.binary_preview.pixmap() is not None
assert not window.controller.connected

# 自动黑白预览只采用最新一组参数，较早线程的结果不能覆盖新选择。
dialog.processing.setCurrentIndex(dialog.processing.findData("cartoon"))
dialog.begin_image_preview()
dialog.detail.setValue(0)
dialog.sensitivity.setValue(80)
dialog.detail.setValue(50)
wait_until(app, lambda: idle(dialog) and dialog.processed_revision == dialog.image_revision and not dialog.preview_timer.isActive())
assert dialog.mode.currentData() == "centerline"
assert not dialog.threshold.isEnabled() and not dialog.invert.isEnabled()
assert dialog.source_preview.pixmap() is not None
assert dialog.binary_preview.pixmap() is not None
assert dialog.task is None and not dialog.load_button.isEnabled()
dialog.recommended_processing()
dialog.width.setValue(40)
dialog.generate()
wait_until(app, lambda: idle(dialog) and dialog.task is not None)
assert dialog.task.program.drawing_length > 0
assert dialog.task.program.z_bounds == (-0.4, 3)
assert dialog.task.program.bounds[3] > dialog.y.value() + 35
dialog.grab().save(str(folder / "image-cartoon-dialog.png"))


def inspect_image():
    inspection = app.activeModalWidget()
    assert isinstance(inspection, ImageInspectionDialog)
    inspection.show_zoom(1)
    assert inspection.scale_label.text() == "100%"
    inspection.fit()
    inspection.grab().save(str(folder / "image-cartoon-enlarged.png"))
    inspection.reject()


QTimer.singleShot(50, inspect_image)
dialog.enlarge_button.click()
assert dialog.task is not None
dialog.export(path=ROOT / ".runtime" / "cartoon_preview.ngc")
dialog.load_task()
window.load_generated(dialog.loaded_task)
assert not window.job.active and not window.controller.connected

# 保存和重新打开后，图片处理参数恢复；设备Z依旧使用当前配置。
dialog = GenerationDialog(MachineProfile(pen_up=4), "image", window)
assert dialog.processing.currentData() == "cartoon" and dialog.detail.value() == 50
assert dialog.sensitivity.value() == 80
assert dialog.pen_up.value() == 4
dialog.choose_image(path=source_path)
dialog.begin_image_preview()
dialog.reject()
wait_until(app, lambda: idle(dialog))
assert not dialog.isVisible()

# 每种新处理方式均可更新预览；小断口修补、反相、失败输入不会留下可载入的旧任务。
dialog = GenerationDialog(window.profile, "image", window)
dialog.choose_image(path=source_path)
for processing in ("adaptive", "edges", "threshold"):
    dialog.processing.setCurrentIndex(dialog.processing.findData(processing))
    dialog.generate()
    wait_until(app, lambda: idle(dialog))
    assert dialog.task is not None, dialog.status.text()
dialog.choose_image(path=ROOT / ".runtime" / "does_not_exist.png")
wait_until(app, lambda: idle(dialog))
assert dialog.source is None and dialog.task is None
assert not dialog.load_button.isEnabled() and not dialog.enlarge_button.isEnabled()
dialog.reject()

# 新处理方式生成的小型外轮廓，走完整模拟发送流程。
small_path = ROOT / ".runtime" / "small_cartoon.png"
cartoon_source().save(str(small_path))
dialog = GenerationDialog(window.profile, "image", window)
dialog.choose_image(path=small_path)
dialog.processing.setCurrentIndex(dialog.processing.findData("cartoon"))
dialog.detail.setValue(0)
dialog.width.setValue(10)
dialog.tolerance.setValue(0.3)
dialog.generate()
wait_until(app, lambda: idle(dialog) and dialog.task is not None)
dialog.load_task()
window.load_generated(dialog.loaded_task)
window.port_combo.setCurrentIndex(window.port_combo.findData(SIMULATOR))
window.toggle_connection()
wait_until(app, lambda: window.controller.available)
window.baseline_check.setChecked(True)
wait_until(app, lambda: window.pen_up_button.isEnabled())
window.pen_up_button.click()
wait_until(app, lambda: window.controller.available and window.controller.machine[2] == 3)
window.start_job()
wait_until(app, lambda: window.job.result == "complete")
assert window.controller.machine[2] == 3
window.controller.disconnect_device()
wait_until(app, lambda: window.loader is None)
window.close()

# 导出原图、原阈值和新线稿的对照图，仅用于查看效果。
source = read_image(source_path)
base = PlotOptions(crop=False, threshold=200)
old = process_image(source, base).image()
new = process_image(source, replace(base, processing="cartoon", edge_sensitivity=80)).image()
canvas = QImage(1260, 475, QImage.Format.Format_RGB32)
canvas.fill(Qt.GlobalColor.white)
painter = QPainter(canvas)
painter.setFont(QFont("Microsoft YaHei", 14))
painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
for index, (title, image) in enumerate((("原图", source), ("原方法：灰度阈值200", old), ("新方法：白底卡通，灵敏度80", new))):
    painter.drawText(QRectF(index * 420 + 10, 8, 400, 35), Qt.AlignmentFlag.AlignCenter, title)
    if index:
        image = binary_thumbnail(image, 400, 400)
        painter.drawImage(QPointF(index * 420 + 10 + (400 - image.width()) / 2, 50 + (400 - image.height()) / 2), image)
    else:
        image = image.scaled(400, 400, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        painter.drawImage(QPointF(10 + (400 - image.width()) / 2, 50 + (400 - image.height()) / 2), image)
painter.end()
canvas.save(str(folder / "image-processing-comparison.png"))
print("Image processing GUI: PASS (live/latest preview, queue, crop, controls, inspect, preferences, close, failure, export, simulator)")
