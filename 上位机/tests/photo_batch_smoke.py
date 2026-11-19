"""整批照片分页、筛选、后台切换、取消、应用和已有字库保护。"""

import hashlib
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import cv2
import numpy as np
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from app.appearance import configure_appearance
from app.generation import checkpoint
from app.photo_samples import gray_image, trace_sample
from app.photo_quality import calibrate_photo_samples
from app.ui.photo_batch_dialog import PhotoBatchCalibrationDialog
from app.ui.photo_font_dialog import PhotoFontDialog
from app.stroke_fonts import FontStore


def snapshot():
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / "config").rglob("*") if p.is_file()}


def wait_until(condition, timeout=20):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        application.processEvents()
        if condition():
            return
        time.sleep(.005)
    raise AssertionError("整批照片窗口未完成预期操作")


application = QApplication([])
configure_appearance(application)
output = ROOT / ".runtime/photo-batch-smoke"
output.mkdir(parents=True, exist_ok=True)
before = snapshot()
pixels = np.full((256, 256), 255, np.uint8)
cv2.rectangle(pixels, (50, 45), (190, 205), 0, 5)
samples = [trace_sample(chr(0x5000 + i), gray_image(pixels)) for i in range(67)]
samples[5].note = "边界穿过笔画，需要核对。"
originals = [s.strokes for s in samples]
dialog = PhotoBatchCalibrationDialog(samples)
dialog.show()
assert dialog.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint
assert dialog.windowFlags() & Qt.WindowType.WindowCloseButtonHint
wait_until(lambda: dialog.worker is None and dialog.apply_button.isEnabled())
seen = []
while True:
    seen.extend(dialog.grid.indices)
    if not dialog.next.isEnabled():
        break
    dialog.next.click()
assert seen == list(range(67)) and len(dialog.grid.indices) == 7
dialog.filter.setCurrentIndex(1)
assert dialog.grid.indices == [5]
dialog.select_sample(5)
assert dialog.locate_index == 5 and dialog.locate.isEnabled()
dialog.filter.setCurrentIndex(0)
# 真实点击字格的删除入口，跨页删除、撤销和过滤不能改变原候选。
first_index = dialog.grid.indices[0]
columns = max(1, dialog.grid.width() // 290)
QTest.mouseClick(dialog.grid, Qt.MouseButton.LeftButton, pos=QPoint(round(dialog.grid.width() / columns - 28), 15))
assert first_index in dialog.deleted and first_index not in dialog.grid.indices
dialog.undo_delete_button.click()
assert not dialog.deleted and first_index in dialog.grid.indices
dialog.select_sample(3)
dialog.details.item(4).setSelected(True)
assert len(dialog.details.selectedItems()) == 2
dialog.details.setFocus()
QTest.keyClick(dialog.details, Qt.Key.Key_Delete)
assert dialog.deleted == {3, 4}
dialog.change_page(1)
dialog.select_sample(35)
dialog.delete_button.click()
assert dialog.deleted == {3, 4, 35}
dialog.filter.setCurrentIndex(1)
assert dialog.grid.indices == [5]
dialog.undo_delete_button.click()
dialog.undo_delete_button.click()
assert not dialog.deleted
dialog.filter.setCurrentIndex(0)
# 连续切换只接受最终设置，不允许旧线程结果覆盖新选择。
dialog.mode.setCurrentIndex(1)
dialog.calculate()
dialog.mode.setCurrentIndex(2)
dialog.calculate()
wait_until(lambda: dialog.worker is None and dialog.apply_button.isEnabled() and not dialog.timer.isActive())
assert dialog.preview_samples[0].strokes == originals[0]
dialog.grab().save(str(output / "batch-preview.png"))
dialog.apply_button.click()
assert dialog.result() == QDialog.DialogCode.Accepted and dialog.result_samples is not None
assert all(s.strokes is original for s, original in zip(samples, originals))

# 应用删除保留原索引映射；全部删除也允许应用，取消则不提交删除。
removed = PhotoBatchCalibrationDialog(samples[:4])
removed.show()
wait_until(lambda: removed.worker is None and removed.apply_button.isEnabled())
removed.delete_samples([1, 3])
removed.mode.setCurrentIndex(2)
wait_until(lambda: removed.worker is None and removed.ready_revision == removed.revision and not removed.timer.isActive())
assert removed.deleted == {1, 3}
removed.apply_button.click()
assert removed.result_indices == [0, 2] and len(removed.result_samples) == 2
empty = PhotoBatchCalibrationDialog(samples[:2])
empty.show()
wait_until(lambda: empty.worker is None and empty.apply_button.isEnabled())
empty.delete_samples([0, 1])
empty.apply_button.click()
assert empty.result_samples == [] and empty.result_indices == []
discarded = PhotoBatchCalibrationDialog(samples[:2])
discarded.show()
wait_until(lambda: discarded.worker is None and discarded.apply_button.isEnabled())
discarded.delete_samples([0])
discarded.reject()
assert discarded.result_samples is None and len(discarded.original_samples) == 2

# 按字符类型筛掉页眉数字，不影响中文和其他页面的选择。
kinds = [trace_sample(char, gray_image(pixels)) for char in "中1A，文"]
filtered = PhotoBatchCalibrationDialog(kinds)
filtered.show()
wait_until(lambda: filtered.worker is None and filtered.apply_button.isEnabled())
filtered.character_filter.setCurrentIndex(2)
assert filtered.grid.indices == [1, 2]
filtered.select_page_button.click()
filtered.delete_button.click()
assert filtered.deleted == {1, 2}
filtered.character_filter.setCurrentIndex(1)
assert filtered.grid.indices == [0, 4]
filtered.undo_delete()
filtered.character_filter.setCurrentIndex(0)
assert filtered.grid.indices == list(range(5))
filtered.reject()

# 取消正在计算的窗口，线程退出后才能关闭，原候选保持不变。
def slow_calibration(*args, **kwargs):
    for _ in range(100):
        checkpoint(kwargs.get("cancel"))
        time.sleep(.003)
    return calibrate_photo_samples(*args, **kwargs)

with patch("app.ui.photo_batch_dialog.calibrate_photo_samples", side_effect=slow_calibration):
    cancelled = PhotoBatchCalibrationDialog(samples)
    cancelled.show()
    wait_until(lambda: cancelled.worker is not None)
    cancelled.close()
    wait_until(lambda: cancelled.worker is None and not cancelled.isVisible())
    assert cancelled.result_samples is None

# “已收录整批”应用后保持原来的批次边界，撤销仍只撤销最后一批。
photo = PhotoFontDialog(store=FontStore(output / "fonts"))
photo.batches = [samples[:3], samples[3:7]]
class AppliedBatch:
    def __init__(self, values, parent, allow_location=True):
        assert not allow_location
        self.result_samples, _ = calibrate_photo_samples(values)
    def exec(self):
        return QDialog.DialogCode.Accepted
with patch("app.ui.photo_font_dialog.PhotoBatchCalibrationDialog", AppliedBatch):
    photo.calibrate_collected()
assert [len(batch) for batch in photo.batches] == [3, 4]
photo.undo_batch()
assert len(photo.collected()) == 3
# 删除后不能用旧批次长度直接切片，否则会把下一批的字归入上一批。
photo.batches = [samples[:3], samples[3:7]]
class DeletedBatch:
    def __init__(self, values, parent, allow_location=True):
        self.result_indices = [0, 2, 4, 6]
        self.result_samples = [values[i] for i in self.result_indices]
    def exec(self):
        return QDialog.DialogCode.Accepted
with patch("app.ui.photo_font_dialog.PhotoBatchCalibrationDialog", DeletedBatch):
    photo.calibrate_collected()
assert [len(batch) for batch in photo.batches] == [2, 2]
assert [s.character for s in photo.batches[1]] == [samples[4].character, samples[6].character]
photo.batches = []
photo.show_candidates(samples[:7])
for i in range(photo.results.count()):
    photo.results.item(i).setCheckState(Qt.CheckState.Checked if i in (1, 4, 6) else Qt.CheckState.Unchecked)
class DeletedCandidates:
    def __init__(self, values, parent):
        self.result_indices = [0, 2, 4, 5, 6]
        self.result_samples = [values[i] for i in self.result_indices]
        self.locate_index = -1
    def exec(self):
        return QDialog.DialogCode.Accepted
with patch("app.ui.photo_font_dialog.PhotoBatchCalibrationDialog", DeletedCandidates):
    photo.batch_calibrate()
assert [s.character for s in photo.candidates] == [samples[i].character for i in (0, 2, 4, 5, 6)]
assert [i for i in range(photo.results.count()) if photo.results.item(i).checkState() == Qt.CheckState.Checked] == [2, 4], [(s.character, s.quality_blocking, s.quality_flags, photo.results.item(i).checkState()) for i, s in enumerate(photo.candidates)]
photo.close()
assert snapshot() == before
for widget in application.topLevelWidgets():
    widget.deleteLater()
QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
application.processEvents()
print("Photo batch smoke passed: all 67 entries reachable, filters, rapid changes, apply, cancel, batch undo and config protection")
