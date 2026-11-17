"""大清单、底稿、照片批量录入、持久化与文字生成；全程使用隔离数据。"""

import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["TEMP"] = str(ROOT / ".runtime/tmp")
os.environ["TMP"] = os.environ["TEMP"]

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app import stroke_fonts
from app.appearance import configure_appearance
from app.profile import MachineProfile
from app.stroke_fonts import FontStore
from app.text_writing import stroke_glyph
from app.ui import generation_dialog
from app.ui.generation_dialog import GenerationDialog
from app.ui.photo_font_dialog import PhotoFontDialog
from app.ui.stroke_font_dialog import StrokeFontDialog


def snapshot():
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / "config").rglob("*") if p.is_file()}


def wait_until(condition, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        application.processEvents()
        if condition():
            return
        time.sleep(.005)
    raise AssertionError("照片录字或轨迹生成超时")


application = QApplication([])
configure_appearance(application)
test_root = ROOT / ".runtime/font-recording-smoke"
test_root.mkdir(parents=True, exist_ok=True)
before = snapshot()
stroke_fonts.FONT_ROOT = test_root / "fonts"
store = FontStore(stroke_fonts.FONT_ROOT)
generation_dialog.PREFERENCES = test_root / "preferences.json"
generation_dialog.PREFERENCES.unlink(missing_ok=True)

manager = StrokeFontDialog(store=store)
manager.show()
application.processEvents()
manager.name.setText("批量录字测试")
manager.plan_preset.setCurrentText("GB2312 6763字")
start = time.monotonic()
manager.apply_record_plan()
plan_seconds = time.monotonic() - start
assert manager.samples.count() == 6763
manager.begin_unrecorded()
char = manager.character.text()
assert char == manager.sample_plan[0] and manager.canvas.guide_character == char
assert manager.canvas.guide_enabled and not manager.canvas.strokes
manager.canvas.set_strokes(stroke_glyph(char).strokes)
manager.save_and_next()
identity = manager.selected_id
assert identity and len(store.load(identity).record_plan) == 6763
assert manager.samples.currentRow() == 1 and manager.sample_exists(0)
assert not manager.canvas.strokes
manager.begin_unrecorded()
assert manager.character.text() == manager.sample_plan[1]
manager.guide_checkbox.setChecked(False)
assert not manager.canvas.guide_enabled and not manager.canvas.strokes
manager.guide_checkbox.setChecked(True)
manager.grab().save(str(test_root / "manual-recording.png"))
manager.accept()
manager = StrokeFontDialog(identity, store=store)
assert manager.samples.count() == 6763 and manager.sample_exists(0)
manager.plan_input.setPlainText("你好，世界\n你好，世界！")
manager.apply_record_plan()
assert manager.sample_plan == list("你好，世界！")
manager.plan_input.clear()
manager.plan_preset.setCurrentText("100格基础")
manager.apply_record_plan()
assert len(manager.sample_plan) == 100 and len(set(manager.sample_plan)) < 100
manager.save_font()
manager.accept()
original = (store.root / (identity + ".json")).read_bytes()

# 使用真实楷体分笔轨迹制作一个拍照夹具，模拟双行方格样本，不控制机械硬件。
chars = "你好天地人永中文学习"
image = QImage(680, 320, QImage.Format.Format_RGB32)
image.fill(0xFFFFFFFF)
painter = QPainter(image)
painter.setRenderHint(QPainter.RenderHint.Antialiasing)
for index, char in enumerate(chars):
    x, y = 20 + index % 5 * 128, 20 + index // 5 * 128
    painter.setPen(QPen(QColor("#ff9999"), 1))
    painter.drawRect(x, y, 128, 128)
    painter.setPen(QPen(QColor("#101010"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    for path in stroke_glyph(char).strokes:
        for a, b in zip(path, path[1:]):
            painter.drawLine(QPointF(x + 6 + a[0] * 116, y + 6 + a[1] * 116), QPointF(x + 6 + b[0] * 116, y + 6 + b[1] * 116))
painter.end()
source = test_root / "photo-fixture.png"
assert image.save(str(source))

photo = PhotoFontDialog(store=store)
photo.show()
application.processEvents()
photo.choose_photo(path=source)
photo.rows.setValue(2)
photo.columns.setValue(5)
photo.characters.setPlainText(chars)
box = photo.canvas.image_rect()
start = QPoint(round(box.x() + 20 / image.width() * box.width()), round(box.y() + 20 / image.height() * box.height()))
end = QPoint(round(box.x() + 660 / image.width() * box.width()), round(box.y() + 276 / image.height() * box.height()))
QTest.mousePress(photo.canvas, Qt.MouseButton.LeftButton, pos=start)
QTest.mouseMove(photo.canvas, end)
QTest.mouseRelease(photo.canvas, Qt.MouseButton.LeftButton, pos=end)
assert len(photo.canvas.points) == 4
photo.generate()
wait_until(lambda: photo.worker is None)
assert len(photo.candidates) == 10, photo.status.text()
assert all(not sample.error for sample in photo.candidates), [s.error for s in photo.candidates]
photo.results.setCurrentRow(0)
application.processEvents()
photo.grab().save(str(test_root / "photo-recording.png"))
photo.collect()
assert len(photo.collected()) == 10 and not photo.add_batch_button.isEnabled()
photo.mode.setCurrentIndex(photo.mode.findData("single"))
photo.characters.setPlainText("你")
photo.canvas.set_rectangle(QPointF(20, 20), QPointF(148, 148))
photo.generate()
wait_until(lambda: photo.worker is None)
assert len(photo.candidates) == 1 and not photo.candidates[0].error
photo.collect()
assert len(photo.collected()) == 11
photo.undo_batch()
assert len(photo.collected()) == 10
photo.generate()
photo.cancel()
wait_until(lambda: photo.worker is None)
assert not photo.candidates and len(photo.collected()) == 10
photo.export_samples(path=test_root / ("sample-export-" + str(time.time_ns())))
photo.font_name.setText("照片字库测试")
photo.save_font()
photo_id = photo.selected_id
assert photo_id and photo_id != identity
package = store.load(photo_id)
assert len(package.glyphs) == 10 and not package.ordered
assert (store.root / (identity + ".json")).read_bytes() == original

dialog = GenerationDialog(MachineProfile(), "text")
dialog.stroke_font.setCurrentIndex(dialog.stroke_font.findData(photo_id))
dialog.text.setPlainText(chars)
dialog.generate()
wait_until(lambda: dialog.worker is None)
assert dialog.task is not None, dialog.status.text()
assert any("不能保证标准笔顺" in note for note in dialog.task.notes)
dialog.export(path=test_root / "photo-task.ngc")
dialog.reject()
QTest.qWait(250)
application.processEvents()
assert snapshot() == before
for widget in application.topLevelWidgets():
    widget.close()
    widget.deleteLater()
QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
application.processEvents()
report = dict(plan_count=6763, plan_refresh_seconds=round(plan_seconds, 3), photo_characters=len(chars), preserved_config=True, source_font_preserved=True)
(test_root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print("Font recording smoke passed:", report)
