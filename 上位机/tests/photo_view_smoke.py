"""功能窗口最大化、照片滚轮缩放/拖动/坐标映射、单字擦除与回归。"""

import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["TEMP"] = str(ROOT / ".runtime/tmp")
os.environ["TMP"] = os.environ["TEMP"]

import cv2
import numpy as np
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.appearance import configure_appearance
from app.photo_samples import gray_image, trace_sample
from app.profile import MachineProfile
from app.stroke_fonts import FontStore
from app.ui.generation_dialog import GenerationDialog
from app.ui.image_inspection import ImageInspectionDialog
from app.ui.photo_font_dialog import PhotoFontDialog
from app.ui.photo_sample_editor import PhotoSampleEditor
from app.ui.sdt_dialog import SdtDialog
from app.ui.settings_dialog import SettingsDialog
from app.ui.stroke_font_dialog import StrokeFontDialog


application = QApplication([])
configure_appearance(application)
output = ROOT / ".runtime/photo-view-smoke"
output.mkdir(parents=True, exist_ok=True)
before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / "config").rglob("*") if p.is_file()}
store = FontStore(output / "fonts")
image = QImage(3000, 4000, QImage.Format.Format_RGB32)
image.fill(0xFFFFFFFF)
photo = PhotoFontDialog(store=store)
photo.show()
application.processEvents()
photo.canvas.set_image(image)
photo.canvas.set_rectangle(QPointF(1000, 1400), QPointF(1500, 1900))
region = photo.canvas.region()
center = QPointF(photo.canvas.width() / 2, photo.canvas.height() / 2)
source_before = photo.canvas.source_point(center)
wheel = QWheelEvent(center, photo.canvas.mapToGlobal(center), QPoint(), QPoint(0, 360), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
application.sendEvent(photo.canvas, wheel)
assert photo.canvas.zoom > 1.7 and photo.canvas.region() == region
assert (photo.canvas.source_point(center) - source_before).manhattanLength() < .01
before_pan = QPointF(photo.canvas.pan)
QTest.mousePress(photo.canvas, Qt.MouseButton.RightButton, pos=center.toPoint())
QTest.mouseMove(photo.canvas, (center + QPointF(50, 25)).toPoint())
QTest.mouseRelease(photo.canvas, Qt.MouseButton.RightButton, pos=(center + QPointF(50, 25)).toPoint())
assert photo.canvas.pan != before_pan and photo.canvas.region() == region
start, end = QPointF((center - QPointF(40, 40)).toPoint()), QPointF((center + QPointF(40, 40)).toPoint())
a, b = photo.canvas.source_point(start), photo.canvas.source_point(end)
QTest.mousePress(photo.canvas, Qt.MouseButton.LeftButton, pos=start.toPoint())
QTest.mouseMove(photo.canvas, end.toPoint())
QTest.mouseRelease(photo.canvas, Qt.MouseButton.LeftButton, pos=end.toPoint())
selected = photo.canvas.region()
assert abs(selected[0][0] - a.x()) < 1 and abs(selected[2][1] - b.y()) < 1
photo.canvas.fit_image()
assert photo.canvas.zoom == 1 and photo.canvas.pan == QPointF()

binary = np.full((256, 256), 255, np.uint8)
cv2.rectangle(binary, (90, 45), (185, 170), 0, 10)
cv2.circle(binary, (30, 210), 5, 0, -1)
sample = trace_sample("口", gray_image(binary))
original = sample.image.copy()
editor = PhotoSampleEditor(sample, parent=photo)
editor.show()
application.processEvents()
box = editor.canvas.image_rect()
position = QPoint(round(box.x() + 30 / 256 * box.width()), round(box.y() + 210 / 256 * box.height()))
editor.size.setValue(20)
QTest.mouseClick(editor.canvas, Qt.MouseButton.LeftButton, pos=position)
editor.apply()
assert editor.result_sample is not None and editor.result_sample.image.pixelColor(30, 210).red() == 255
assert sample.image == original
assert len(editor.result_sample.strokes) < len(sample.strokes)
photo.show_candidates([sample])
photo.grab().save(str(output / "photo-tools.png"))

# 笔宽模拟只改显示；墨迹还原改变笔宽必须重新提取，避免沿用旧填线。
assert set(photo.trajectory.itemData(i) for i in range(photo.trajectory.count())) == {"pen_shape", "continuous", "ink", "legacy"}
photo.simulate_pen.setChecked(False)
assert photo.preview.sample is sample and not photo.preview.simulate_pen
photo.pen_width.setValue(.4)
assert photo.candidates == [sample] and abs(photo.preview.pen_width - .05) < 1e-6
photo.trajectory.setCurrentIndex(photo.trajectory.findData("ink"))
assert not photo.candidates and not photo.trajectory_hint.isHidden()
ink_sample = trace_sample("口", original, trajectory="ink", ink_width=.05)
photo.show_candidates([ink_sample])
assert not ink_sample.error
photo.pen_width.setValue(.35)
assert not photo.candidates
photo.trajectory.setCurrentIndex(photo.trajectory.findData("pen_shape"))
photo.show_candidates([sample])

# 标题栏关闭与右下角关闭共用未保存保护；取消时窗口必须继续可见。
photo.batches = [[sample]]
with patch("app.ui.photo_font_dialog.confirm_font_action", return_value=False):
    assert not photo.close() and photo.isVisible()
with patch("app.ui.photo_font_dialog.confirm_font_action", return_value=True):
    assert photo.close() and not photo.isVisible()
photo.batches = []

profile = MachineProfile()
windows = [photo, editor, StrokeFontDialog(store=store), SettingsDialog(profile), ImageInspectionDialog(image), SdtDialog(), GenerationDialog(profile, "text"), GenerationDialog(profile, "image")]
for window in windows:
    assert window.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint, type(window).__name__
    assert window.windowFlags() & Qt.WindowType.WindowCloseButtonHint, type(window).__name__
    window.showMaximized()
    application.processEvents()
    assert window.isMaximized(), type(window).__name__
    window.showNormal()
    application.processEvents()
    assert not window.isMaximized(), type(window).__name__
    assert window.close(), type(window).__name__
    application.processEvents()
    assert not window.isVisible(), type(window).__name__
assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before}
for window in windows:
    window.deleteLater()
application.processEvents()
(output / "report.json").write_text(json.dumps(dict(windows=len(windows), zoom_and_pan=True, source_coordinates=True, eraser_preserves_original=True, config_preserved=True), indent=2), encoding="utf-8")
print("Photo view smoke passed: 8 windows, zoom, pan, source coordinates, erasing and config preservation")
