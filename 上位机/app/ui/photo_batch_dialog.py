"""照片整批校准：后台计算、分页三方对照、问题筛选和回到原图定位。"""

import math
import unicodedata

from PySide6.QtCore import QPointF, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import QAbstractItemView, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QScrollArea, QVBoxLayout, QWidget

from app.generation import GenerationCancelled
from app.photo_quality import calibrate_photo_samples
from app.ui.dialog_window import FunctionDialog


class CalibrationGrid(QWidget):
    selected = Signal(int)
    delete_requested = Signal(int)

    def __init__(self):
        super().__init__()
        self.samples, self.indices = [], []
        self.current = -1
        self.setMinimumWidth(600)

    def set_samples(self, samples, indices):
        self.samples, self.indices = samples, indices
        self.reflow()

    def reflow(self):
        columns = max(1, self.width() // 290)
        self.setMinimumHeight(max(160, math.ceil(len(self.indices) / columns) * 152))
        self.update()

    def resizeEvent(self, event):
        self.reflow()

    def mousePressEvent(self, event):
        columns = max(1, self.width() // 290)
        index = int(event.position().y()) // 152 * columns + int(event.position().x() / (self.width() / columns))
        if 0 <= index < len(self.indices):
            self.current = self.indices[index]
            self.selected.emit(self.current)
            row, column = divmod(index, columns)
            if event.button() == Qt.MouseButton.LeftButton and QRectF((column + 1) * self.width() / columns - 49, row * 152 + 5, 40, 22).contains(event.position()):
                self.delete_requested.emit(self.current)
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#f4f7fb"))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        columns = max(1, self.width() // 290)
        cell_width = self.width() / columns
        for position, index in enumerate(self.indices):
            row, column = divmod(position, columns)
            x, y = column * cell_width, row * 152
            if not QRectF(x, y, cell_width, 152).intersects(QRectF(event.rect())):
                continue
            sample = self.samples[index]
            color = QColor("#b82929" if sample.quality_blocking else "#b27400" if sample.quality_flags else "#477654")
            painter.setPen(QPen(color, 2.5 if self.current == index else 1))
            painter.drawRect(QRectF(x + 4, y + 3, cell_width - 8, 145))
            painter.drawText(QPointF(x + 10, y + 20), f"{index + 1:04d} {sample.character}  " + ("重点核对" if sample.quality_blocking else "提醒" if sample.quality_flags else "未见自动检查项"))
            painter.fillRect(QRectF(x + cell_width - 49, y + 5, 40, 22), QColor("#f4f7fb"))
            painter.setPen(QColor("#b82929"))
            painter.drawText(QRectF(x + cell_width - 49, y + 5, 40, 22), Qt.AlignmentFlag.AlignCenter, "删除")
            side = min(82, (cell_width - 36) / 3)
            for k, title in enumerate(("原单字", "校准黑白", "输出轨迹")):
                box = QRectF(x + 10 + k * (side + 8), y + 28, side, side)
                painter.fillRect(box, QColor("white"))
                if k < 2:
                    image = sample.original if k == 0 and sample.original is not None else (sample.calibration_source or sample).image if k == 0 else sample.image
                    scale = min(side / image.width(), side / image.height())
                    rect = QRectF(box.center().x() - image.width() * scale / 2, box.center().y() - image.height() * scale / 2, image.width() * scale, image.height() * scale)
                    painter.drawImage(rect, image)
                else:
                    painter.setPen(QPen(QColor("#222222"), 1.1))
                    for path in sample.strokes:
                        for a, b in zip(path, path[1:]):
                            painter.drawLine(QPointF(box.x() + a[0] * side, box.y() + a[1] * side), QPointF(box.x() + b[0] * side, box.y() + b[1] * side))
                painter.setPen(QColor("#455468"))
                painter.drawText(QPointF(box.x(), box.bottom() + 18), title)


class CalibrationWorker(QThread):
    ready = Signal(object, object, int)
    failed = Signal(str)

    def __init__(self, samples, mode, strength, revision, parent):
        super().__init__(parent)
        self.samples, self.mode, self.strength, self.revision = samples, mode, strength, revision

    def run(self):
        try:
            samples, report = calibrate_photo_samples(self.samples, self.mode, self.strength, cancel=self.isInterruptionRequested)
            if not self.isInterruptionRequested():
                self.ready.emit(samples, report, self.revision)
        except GenerationCancelled:
            pass
        except Exception as error:
            self.failed.emit(str(error) or type(error).__name__)


class PhotoBatchCalibrationDialog(FunctionDialog):
    def __init__(self, samples, parent=None, allow_location=True):
        super().__init__(parent)
        self.original_samples, self.preview_samples = list(samples), []
        self.result_samples, self.locate_index = None, -1
        self.result_indices = None
        self.deleted, self.delete_history = set(), []
        self.worker, self.pending, self.closing = None, False, False
        self.ready_revision = -1
        self.revision, self.page = 0, 0
        self.setWindowTitle("照片字库 · 整批质量检查与校准")
        self.resize(1060, 780)
        layout = QVBoxLayout(self)
        tip = QLabel("每页30字，对照原单字、黑白和输出轨迹。字格右上角可直接删除；下方列表支持Ctrl/Shift多选后按Delete，误删可撤销。删除在应用后生效，取消保留原候选。红色表示需要重点核对。")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        form = QFormLayout()
        self.mode = QComboBox()
        self.mode.addItem("按行统一尺度，保留自然比例（推荐）", "natural")
        self.mode.addItem("统一整齐（等比调整主体字）", "uniform")
        self.mode.addItem("保留原始比例，仅检查", "raw")
        form.addRow("批量处理", self.mode)
        self.strength = QDoubleSpinBox()
        self.strength.setRange(0, 1)
        self.strength.setDecimals(2)
        self.strength.setSingleStep(.05)
        self.strength.setValue(.70)
        form.addRow("主体字校正强度", self.strength)
        layout.addLayout(form)
        self.summary = QLabel("正在检查整批样本…")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        bar = QHBoxLayout()
        self.filter = QComboBox()
        self.filter.addItems(["全部样本", "重点核对", "所有提醒"])
        bar.addWidget(self.filter)
        self.character_filter = QComboBox()
        self.character_filter.addItems(["全部字符", "中文汉字", "数字与字母", "标点与其他"])
        bar.addWidget(self.character_filter)
        self.previous, self.next = QPushButton("上一页"), QPushButton("下一页")
        bar.addWidget(self.previous)
        self.page_label = QLabel()
        bar.addWidget(self.page_label)
        bar.addWidget(self.next)
        self.select_page_button = QPushButton("选中本页")
        bar.addWidget(self.select_page_button)
        bar.addStretch()
        layout.addLayout(bar)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.grid = CalibrationGrid()
        self.scroll.setWidget(self.grid)
        layout.addWidget(self.scroll, 1)
        self.details = QListWidget()
        self.details.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.details.setMaximumHeight(108)
        layout.addWidget(self.details)
        row = QHBoxLayout()
        self.locate = QPushButton("返回并定位所选字")
        self.locate.setEnabled(False)
        self.locate.setVisible(allow_location)
        row.addWidget(self.locate)
        self.delete_button = QPushButton("删除所选（Del）")
        self.undo_delete_button = QPushButton("撤销删除（Ctrl+Z）")
        self.delete_button.setEnabled(False)
        self.undo_delete_button.setEnabled(False)
        row.addWidget(self.delete_button)
        row.addWidget(self.undo_delete_button)
        row.addStretch()
        cancel, self.apply_button = QPushButton("取消"), QPushButton("应用校准并返回")
        row.addWidget(cancel)
        row.addWidget(self.apply_button)
        layout.addLayout(row)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(180)
        self.timer.timeout.connect(self.calculate)
        self.mode.currentIndexChanged.connect(self.refresh)
        self.strength.valueChanged.connect(self.refresh)
        self.filter.currentIndexChanged.connect(self.filter_changed)
        self.character_filter.currentIndexChanged.connect(self.filter_changed)
        self.select_page_button.clicked.connect(self.details.selectAll)
        self.previous.clicked.connect(lambda: self.change_page(-1))
        self.next.clicked.connect(lambda: self.change_page(1))
        self.grid.selected.connect(self.select_sample)
        self.grid.delete_requested.connect(lambda index: self.delete_samples([index]))
        self.details.currentItemChanged.connect(self.detail_selected)
        self.details.itemSelectionChanged.connect(self.update_delete_buttons)
        self.locate.clicked.connect(self.return_to_sample)
        self.delete_button.clicked.connect(self.delete_selected)
        self.undo_delete_button.clicked.connect(self.undo_delete)
        self.delete_shortcut = QShortcut(QKeySequence("Delete"), self)
        self.delete_shortcut.activated.connect(self.delete_selected)
        self.undo_shortcut = QShortcut(QKeySequence("Ctrl+Z"), self)
        self.undo_shortcut.activated.connect(self.undo_delete)
        cancel.clicked.connect(self.reject)
        self.apply_button.clicked.connect(self.apply)
        self.refresh()

    def refresh(self):
        self.revision += 1
        self.apply_button.setEnabled(False)
        self.update_delete_buttons()
        self.strength.setEnabled(self.mode.currentData() == "natural")
        self.timer.start()
        if self.worker is not None:
            self.worker.requestInterruption()

    def calculate(self):
        if self.worker is not None:
            self.pending = True
            return
        self.summary.setText("正在后台检查整批样本和校准；可以取消。")
        self.worker = CalibrationWorker(self.original_samples, self.mode.currentData(), self.strength.value(), self.revision, self)
        self.worker.ready.connect(self.calculated)
        self.worker.failed.connect(self.summary.setText)
        self.worker.finished.connect(self.finished_calculating)
        self.worker.start()

    def calculated(self, samples, report, revision):
        if self.closing or revision != self.revision:
            return
        self.preview_samples = samples
        self.update_summary()
        self.page = 0
        self.filter_changed()
        self.ready_revision = revision

    def finished_calculating(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        if self.closing:
            super().reject()
        elif self.pending:
            self.pending = False
            self.calculate()
        else:
            self.apply_button.setEnabled(self.ready_revision == self.revision and bool(self.preview_samples))
        self.update_delete_buttons()

    def filtered_indices(self):
        mode = self.filter.currentIndex()
        kind = self.character_filter.currentIndex()
        def matches_character(character):
            chinese = len(character) == 1 and "\u3400" <= character <= "\u9fff"
            letter_or_number = not chinese and unicodedata.category(character)[0] in "LN"
            return kind == 0 or (chinese if kind == 1 else letter_or_number if kind == 2 else not chinese and not letter_or_number)
        return [i for i, sample in enumerate(self.preview_samples) if i not in self.deleted and
                matches_character(sample.character) and (mode == 0 or (sample.quality_blocking if mode == 1 else sample.quality_flags))]

    def filter_changed(self):
        self.page = 0
        self.show_page()

    def change_page(self, amount):
        self.page += amount
        self.show_page()

    def show_page(self):
        indices = self.filtered_indices()
        count = max(1, math.ceil(len(indices) / 30))
        self.page = max(0, min(count - 1, self.page))
        current = indices[self.page * 30:(self.page + 1) * 30]
        self.grid.set_samples(self.preview_samples, current)
        self.page_label.setText(f"第{self.page + 1}/{count}页 · {len(indices)}项")
        self.previous.setEnabled(self.page > 0)
        self.next.setEnabled(self.page + 1 < count)
        self.scroll.verticalScrollBar().setValue(0)
        self.locate_index, self.grid.current = -1, -1
        self.locate.setEnabled(False)
        self.details.clear()
        for index in current:
            sample = self.preview_samples[index]
            detail = "、".join(sample.quality_flags) or "未发现自动检查项，仍需核对字形"
            item = QListWidgetItem(f"{index + 1:04d}　{sample.character}　{detail}")
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.details.addItem(item)
        self.update_delete_buttons()

    def select_sample(self, index):
        self.locate_index = index
        self.locate.setEnabled(True)
        for row in range(self.details.count()):
            if self.details.item(row).data(Qt.ItemDataRole.UserRole) == index:
                self.details.setCurrentRow(row)
                break

    def detail_selected(self, current, previous=None):
        if current is not None:
            self.locate_index = current.data(Qt.ItemDataRole.UserRole)
            self.grid.current = self.locate_index
            self.grid.update()
            self.locate.setEnabled(True)

    def return_to_sample(self):
        if self.deleted and self.worker is None:
            self.result_indices = [i for i in range(len(self.original_samples)) if i not in self.deleted]
            self.result_samples = [self.original_samples[i] for i in self.result_indices]
            self.accept()
            return
        self.reject()

    def update_summary(self):
        active = [s for i, s in enumerate(self.preview_samples) if i not in self.deleted]
        blocking = sum(s.quality_blocking for s in active)
        warnings = sum(bool(s.quality_flags) and not s.quality_blocking for s in active)
        self.summary.setText(f"当前{len(active)}个样本；重点核对{blocking}项，一般提醒{warnings}项。已移除{len(self.deleted)}项，应用后生效。自动检查不能确认错字或真实笔顺。")

    def update_delete_buttons(self):
        ready = self.worker is None and self.ready_revision == self.revision
        self.delete_button.setEnabled(ready and bool(self.details.selectedItems()))
        self.undo_delete_button.setEnabled(ready and bool(self.delete_history))

    def delete_selected(self):
        self.delete_samples([item.data(Qt.ItemDataRole.UserRole) for item in self.details.selectedItems()])

    def delete_samples(self, indices):
        if self.worker is not None or self.ready_revision != self.revision:
            return
        removed = set(indices) - self.deleted
        if not removed:
            return
        self.delete_history.append((removed, self.page))
        self.deleted.update(removed)
        self.show_page()
        self.update_summary()

    def undo_delete(self):
        if self.worker is not None or self.ready_revision != self.revision or not self.delete_history:
            return
        removed, self.page = self.delete_history.pop()
        self.deleted.difference_update(removed)
        self.show_page()
        self.update_summary()

    def done(self, result):
        # Esc、系统关闭与取消使用同一收尾流程，线程结束前不销毁窗口。
        if self.worker is not None:
            self.reject()
            return
        self.timer.stop()
        super().done(result)

    def apply(self):
        if self.apply_button.isEnabled() and self.preview_samples and self.worker is None:
            self.result_indices = [i for i in range(len(self.preview_samples)) if i not in self.deleted]
            self.result_samples = [self.preview_samples[i] for i in self.result_indices]
            self.accept()

    def reject(self):
        self.timer.stop()
        if self.worker is not None:
            self.closing = True
            self.worker.requestInterruption()
            self.summary.setText("正在取消，原字形保留…")
            return
        super().reject()

    def closeEvent(self, event):
        self.reject()
        event.setAccepted(not self.isVisible())
