"""录字画板、中文确认、照片核对流程；可另用真实照片验证本机离线识字。"""

import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["TEMP"] = str(ROOT / ".runtime/tmp")
os.environ["TMP"] = os.environ["TEMP"]

import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QMessageBox
from app.appearance import configure_appearance
from app.generation import GenerationError, read_image
from app.photo_samples import PhotoSample, gray_image
from app.photo_ocr import recognize_photo
from app.stroke_fonts import FontStore
from app.text_writing import stroke_glyph
from app.ui.font_messages import confirm_font_action
from app.ui.photo_font_dialog import PhotoFontDialog
from app.ui.stroke_font_dialog import StrokeFontDialog


def snapshot():
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / "config").rglob("*") if p.is_file()}


def wait_until(condition, timeout=45):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        application.processEvents()
        if condition():
            return
        time.sleep(.005)
    raise AssertionError("等待照片识字超时")


def pixels(widget):
    image = widget.grab().toImage().convertToFormat(QImage.Format.Format_RGB888)
    return np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.bytesPerLine())[:, :image.width() * 3].copy().reshape(image.height(), image.width(), 3)


application = QApplication([])
configure_appearance(application)
output = ROOT / ".runtime/photo-input-smoke"
output.mkdir(parents=True, exist_ok=True)
before = snapshot()
store = FontStore(output / "fonts")
manual = StrokeFontDialog(store=store)
manual.show()
application.processEvents()
manual.character.setText("永")
manual.canvas.set_strokes(stroke_glyph("永").strokes)
assert not manual.canvas.show_numbers and not manual.number_checkbox.isChecked()
plain = pixels(manual.canvas)
manual.grab().save(str(output / "manual-no-numbers.png"))
manual.number_checkbox.setChecked(True)
numbered = pixels(manual.canvas)
diff = np.any(plain != numbered, axis=2)
# 大字号回归会使新增彩色像素超过几万；小编号应只占极少画板面积。
assert 10 < int(diff.sum()) < 1600, int(diff.sum())
manual.grab().save(str(output / "manual-small-numbers.png"))

modal = {}
def dismiss():
    box = application.activeModalWidget()
    assert isinstance(box, QMessageBox)
    modal["labels"] = [b.text() for b in box.buttons()]
    assert set(modal["labels"]) == {"放弃修改", "取消"}
    assert box.defaultButton().text() == "取消" and box.escapeButton().text() == "取消"
    box.grab().save(str(output / "chinese-confirmation.png"))
    box.defaultButton().click()
QTimer.singleShot(50, dismiss)
assert not confirm_font_action(manual, "尚未保存", "当前修改还没有保存，确定放弃吗？")
manual.canvas.set_strokes([])
manual.accept()

photo = PhotoFontDialog(store=store, plan=list("你好天地人永中文学习"))
photo.show()
application.processEvents()
photo.mode.setCurrentIndex(photo.mode.findData("auto"))
image = QImage(700, 400, QImage.Format.Format_RGB32)
image.fill(0xFFFFFFFF)
photo.canvas.set_image(image)
samples = [PhotoSample(c, gray_image(np.full((256, 256), 255, np.uint8)), stroke_glyph(c).strokes,
                       box=((10 + i * 120, 10), (110 + i * 120, 10), (110 + i * 120, 110), (10 + i * 120, 110)), confidence=.9)
           for i, c in enumerate("你好")]
photo.show_candidates(samples)
assert all(photo.results.item(i).checkState() == Qt.CheckState.Unchecked for i in range(2))
assert photo.canvas.sample_boxes == samples
# 删除中间问题字后保留其余勾选状态，并刷新编号、当前字、定位框和自动识字内容。
bad = PhotoSample("错", samples[0].image, [], error="提取异常")
photo.show_candidates([samples[0], bad, samples[1]])
photo.batches = [[samples[0]]]
source_before = photo.canvas.image.copy()
photo.results.item(2).setCheckState(Qt.CheckState.Checked)
photo.results.setCurrentRow(1)
photo.delete_button.click()
assert photo.candidates == samples and photo.results.count() == 2
assert photo.characters.toPlainText() == "你好" and photo.canvas.sample_boxes == photo.candidates
assert photo.results.item(1).text().startswith("0002")
assert photo.results.item(1).checkState() == Qt.CheckState.Checked
assert photo.preview.sample is samples[1] and photo.canvas.selected_sample == 1
photo.select_valid()
assert all(photo.results.item(i).checkState() == Qt.CheckState.Checked for i in range(2))
photo.results.setCurrentRow(0)
photo.delete_button.click()
assert photo.candidates == [samples[1]] and photo.preview.sample is samples[1]
assert photo.results.item(0).text().startswith("0001") and photo.canvas.selected_sample == 0
photo.delete_button.click()
assert not photo.candidates and photo.results.count() == 0 and photo.preview.sample is None
assert not photo.canvas.sample_boxes and photo.canvas.selected_sample == -1
assert not photo.correct_character.text() and not photo.characters.toPlainText()
assert not photo.delete_button.isEnabled() and not photo.add_batch_button.isEnabled()
photo.set_busy(True)
assert not photo.delete_button.isEnabled()
photo.set_busy(False)
assert not photo.delete_button.isEnabled()
assert photo.collected() == [samples[0]] and photo.canvas.image == source_before
photo.batches = []
photo.update_collection()
# 删除同字中的一个候选仍保留另一版，不按字符名称批量删除。
photo.show_candidates([samples[0], samples[0], samples[1]])
photo.delete_button.click()
assert photo.candidates == samples
photo.select_valid()
photo.collect()
assert photo.collected() == samples
photo.undo_batch()
photo.show_candidates(samples)
original_paths = samples[0].strokes
photo.correct_character.setText("你")
photo.correct_sample()
assert samples[0].strokes is original_paths
photo.correct_character.setText("天")
photo.correct_sample()
assert samples[0].character == "天" and photo.characters.toPlainText() == "天好"
photo.select_valid()
photo.results.setCurrentRow(1)
photo.recrop_sample()
assert photo.recrop_review is not None and not photo.candidates and photo.mode.currentData() == "single"
photo.return_review()
assert len(photo.candidates) == 2 and photo.candidates[0].strokes is original_paths
assert photo.results.item(0).checkState() == Qt.CheckState.Checked
photo.recrop_sample()
replacement = PhotoSample("好", samples[1].image, samples[1].strokes, box=samples[1].box)
photo.return_review(replacement=replacement)
assert photo.candidates[1] is replacement and photo.results.item(1).checkState() == Qt.CheckState.Unchecked
assert photo.results.item(0).checkState() == Qt.CheckState.Checked
photo.select_valid()
photo.collect()
assert len(photo.collected()) == 2
photo.undo_batch()
with patch("app.photo_ocr.recognize_photo", side_effect=GenerationError("识字失败验证")):
    photo.auto_generate()
    wait_until(lambda: photo.worker is None)
assert "识字失败验证" in photo.status.text() and not photo.candidates
assert photo.auto_button.isEnabled() and photo.progress.maximum() > 0

report = dict(small_number_pixels=int(diff.sum()), chinese_buttons=modal["labels"], recrop_preserved_candidates=True, candidate_deletion=True)
if len(sys.argv) > 1:
    # 阻断Python联网入口，实际加载本地ONNX模型并从用户照片提取；不替换识字结果。
    def offline(*args, **kwargs):
        raise AssertionError("离线验证期间不允许联网")
    start = time.monotonic()
    with patch.object(socket, "create_connection", offline), patch.object(socket.socket, "connect", offline), patch.object(socket, "getaddrinfo", offline):
        source = read_image(sys.argv[1])
        photo.canvas.set_image(source)
        from PySide6.QtCore import QPointF
        photo.canvas.set_rectangle(QPointF(300, 710), QPointF(2410, 1100))
        photo.auto_generate()
        wait_until(lambda: photo.worker is None)
    assert photo.candidates, photo.status.text()
    assert all(photo.results.item(i).checkState() == Qt.CheckState.Unchecked for i in range(len(photo.candidates)))
    report.update(offline_seconds=round(time.monotonic() - start, 3), candidates=len(photo.candidates),
                  extraction_errors=sum(bool(s.error) for s in photo.candidates), recognized=photo.characters.toPlainText())
    photo.results.setCurrentRow(next(i for i, s in enumerate(photo.candidates) if s.character == "是"))
    application.processEvents()
    sample = photo.preview.sample
    sample.original.save(str(output / "photo-original-char.png"))
    sample.image.save(str(output / "photo-binary-char.png"))
    photo.preview.grab().save(str(output / "photo-char-comparison.png"))
application.processEvents()
photo.grab().save(str(output / "photo-review.png"))
assert photo.width() <= 1200 and photo.height() <= 870, (photo.width(), photo.height())
for widget in (photo.preview, photo.results, photo.save_button):
    assert photo.rect().contains(widget.mapTo(photo, widget.rect().bottomRight()))
# 小窗口允许滚动核对区，不能把最下面的收录按钮永久挤出可操作范围。
photo.resize(1040, 680)
application.processEvents()
bar = photo.review_scroll.verticalScrollBar()
bar.setValue(bar.maximum())
application.processEvents()
button_point = photo.add_batch_button.mapTo(photo.review_scroll.viewport(), photo.add_batch_button.rect().center())
assert photo.review_scroll.viewport().rect().contains(button_point)
photo.grab().save(str(output / "photo-small-window.png"))
photo.reject()
assert snapshot() == before
for widget in application.topLevelWidgets():
    widget.deleteLater()
QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
application.processEvents()
(output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print("Photo input smoke passed:", report)
