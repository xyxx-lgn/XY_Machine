"""本地照片录字：自动识字或手工框选、逐字核对、累计后另存字库。"""

from PySide6.QtCore import QPointF, QRectF, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF, QTransform
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from app.generation import GenerationCancelled, GenerationError, read_image
from app.photo_samples import build_photo_package, export_photo_samples, grid_cells, prepare_photo_samples
from app.profile import ROOT
from app.stroke_fonts import FontStore
from app.ui.font_messages import confirm_font_action
from app.ui.dialog_window import FunctionDialog
from app.ui.photo_sample_editor import PhotoSampleEditor


class PhotoCanvas(QWidget):
    changed = Signal()
    view_changed = Signal(float)

    def __init__(self):
        super().__init__()
        self.image = None
        self.points = []
        self.corner_mode = False
        self.start = None
        self.grid = (1, 1, 0, 0)
        self.sample_boxes = []
        self.selected_sample = -1
        self.zoom = 1.0
        self.pan = QPointF()
        self.pan_start = None
        self.setMinimumSize(360, 280)
        self.setToolTip("滚轮以鼠标位置为中心缩放；按住右键或中键拖动；左键框选。")

    def set_image(self, image):
        self.image = image.copy()
        self.fit_image()
        self.reset_region()

    def fit_image(self):
        self.zoom = 1.0
        self.pan = QPointF()
        self.update()
        self.view_changed.emit(self.zoom)

    def reset_region(self):
        self.points = []
        self.start = None
        self.update()
        self.changed.emit()

    def image_rect(self):
        if self.image is None:
            return QRectF()
        scale = min((self.width() - 20) / self.image.width(), (self.height() - 20) / self.image.height())
        w, h = self.image.width() * scale * self.zoom, self.image.height() * scale * self.zoom
        return QRectF((self.width() - w) / 2 + self.pan.x(), (self.height() - h) / 2 + self.pan.y(), w, h)

    def bound_pan(self):
        box = self.image_rect()
        dx, dy = max(0, (box.width() - self.width() + 20) / 2), max(0, (box.height() - self.height() + 20) / 2)
        self.pan = QPointF(max(-dx, min(dx, self.pan.x())), max(-dy, min(dy, self.pan.y())))

    def zoom_at(self, factor, position):
        if self.image is None:
            return
        source = self.source_point(position)
        self.zoom = max(1, min(30, self.zoom * factor))
        box = self.image_rect()
        mapped = QPointF(box.x() + source.x() / self.image.width() * box.width(), box.y() + source.y() / self.image.height() * box.height())
        self.pan += position - mapped
        self.bound_pan()
        self.update()
        self.view_changed.emit(self.zoom)

    def focus_sample(self):
        if self.image is None or not 0 <= self.selected_sample < len(self.sample_boxes):
            return
        points = self.sample_boxes[self.selected_sample].box
        if len(points) != 4:
            return
        left, right = min(p[0] for p in points), max(p[0] for p in points)
        top, bottom = min(p[1] for p in points), max(p[1] for p in points)
        scale = min((self.width() - 20) / self.image.width(), (self.height() - 20) / self.image.height())
        self.zoom = max(1, min(30, .6 * min(self.width() / max(1, right - left), self.height() / max(1, bottom - top)) / scale))
        self.pan = QPointF((self.image.width() / 2 - (left + right) / 2) * scale * self.zoom, (self.image.height() / 2 - (top + bottom) / 2) * scale * self.zoom)
        self.bound_pan()
        self.update()
        self.view_changed.emit(self.zoom)

    def wheelEvent(self, event):
        self.zoom_at(1.2 ** (event.angleDelta().y() / 120), event.position())
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.image is not None:
            self.bound_pan()

    def source_point(self, point):
        box = self.image_rect()
        return QPointF(max(0, min(self.image.width(), (point.x() - box.x()) * self.image.width() / box.width())),
                       max(0, min(self.image.height(), (point.y() - box.y()) * self.image.height() / box.height())))

    def region(self):
        if len(self.points) == 4:
            return [(point.x(), point.y()) for point in self.points]
        if self.corner_mode and self.points:
            raise GenerationError("四角还没选完，请依次点击左上、右上、右下、左下。")
        return None

    def mousePressEvent(self, event):
        if self.image is not None and event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            self.pan_start = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if self.image is None or event.button() != Qt.MouseButton.LeftButton or not self.image_rect().contains(event.position()):
            return
        point = self.source_point(event.position())
        if self.corner_mode:
            if len(self.points) == 4:
                self.points = []
            self.points.append(point)
            self.changed.emit()
            self.update()
        else:
            self.start = point

    def mouseMoveEvent(self, event):
        if self.pan_start is not None:
            self.pan += event.position() - self.pan_start
            self.pan_start = event.position()
            self.bound_pan()
            self.update()
            return
        if self.start is not None:
            self.set_rectangle(self.start, self.source_point(event.position()))

    def mouseReleaseEvent(self, event):
        if self.pan_start is not None and event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            self.pan_start = None
            self.unsetCursor()
            return
        if self.start is not None and event.button() == Qt.MouseButton.LeftButton:
            self.set_rectangle(self.start, self.source_point(event.position()))
            self.start = None
            self.changed.emit()

    def set_rectangle(self, a, b):
        rect = QRectF(a, b).normalized()
        self.points = [rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft()]
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#eaf0f8"))
        if self.image is None:
            painter.setPen(QColor("#405675"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "导入手机照片或扫描图片\n拖动框选字格；倾斜照片可选四角校正")
            return
        box = self.image_rect()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(box, self.image)

        def screen(point):
            return QPointF(box.left() + point.x() / self.image.width() * box.width(), box.top() + point.y() / self.image.height() * box.height())

        points = [screen(p) for p in self.points]
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#1976d2"), 1.4))
        if self.sample_boxes:
            for index, sample in enumerate(self.sample_boxes):
                if len(sample.box) != 4:
                    continue
                painter.setPen(QPen(QColor("#f08020") if index == self.selected_sample else QColor("#3e91b5"), 2.5 if index == self.selected_sample else .7))
                painter.drawPolygon(QPolygonF([screen(QPointF(*point)) for point in sample.box]))
            painter.setPen(QPen(QColor("#1976d2"), 1.4))
        if len(points) == 4:
            painter.drawPolygon(QPolygonF(points))
            transform = QTransform()
            valid_grid = QTransform.squareToQuad(QPolygonF(points), transform)

            def at(x, y):
                return transform.map(QPointF(x, y))

            rows, columns, gap_x, gap_y = self.grid
            for x0, y0, x1, y1 in grid_cells(10000, 10000, rows, columns, gap_x, gap_y) if valid_grid else []:
                painter.drawPolygon(QPolygonF([at(x0 / 10000, y0 / 10000), at(x1 / 10000, y0 / 10000), at(x1 / 10000, y1 / 10000), at(x0 / 10000, y1 / 10000)]))
        for index, point in enumerate(points):
            painter.setBrush(QColor("#1976d2"))
            painter.drawEllipse(point, 4, 4)
            painter.drawText(point + QPointF(6, -6), str(index + 1))


class SamplePreview(QWidget):
    def __init__(self):
        super().__init__()
        self.sample = None
        self.pen_width = .03
        self.simulate_pen = True
        self.setMinimumHeight(145)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("white"))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        side = min((self.width() - 32) / 3, self.height() - 35)
        original, left, right = [QRectF(6 + index * (side + 8), 28, side, side) for index in range(3)]
        painter.setPen(QColor("#3b4d65"))
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(12)
        painter.setFont(font)
        painter.drawText(8, 19, "原始单字")
        painter.drawText(round(left.x()), 19, "黑白样本")
        painter.drawText(round(right.x()), 19, "笔宽模拟" if self.simulate_pen else "分段轨迹")
        if self.sample is not None:
            if self.sample.original is not None:
                image = self.sample.original
                scale = min(original.width() / image.width(), original.height() / image.height())
                rect = QRectF(original.center().x() - image.width() * scale / 2, original.center().y() - image.height() * scale / 2, image.width() * scale, image.height() * scale)
                painter.drawImage(rect, image)
            painter.drawImage(left, self.sample.image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            for index, path in enumerate(self.sample.strokes):
                color = QColor("#202020") if self.simulate_pen else QColor.fromHsv(index * 43 % 360, 180, 140)
                painter.setPen(QPen(color, max(1, self.pen_width * side) if self.simulate_pen else 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                for a, b in zip(path, path[1:]):
                    painter.drawLine(QPointF(right.x() + a[0] * side, right.y() + a[1] * side), QPointF(right.x() + b[0] * side, right.y() + b[1] * side))
        painter.setPen(QPen(QColor("#b5c4d8"), 1))
        painter.drawRect(original)
        painter.drawRect(left)
        painter.drawRect(right)


class PhotoWorker(QThread):
    ready = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)

    def __init__(self, image, settings, parent=None):
        super().__init__(parent)
        self.image, self.settings = image.copy(), settings

    def run(self):
        try:
            settings = dict(self.settings)
            if settings.pop("automatic", False):
                from app.photo_ocr import recognize_photo
                samples = recognize_photo(self.image, cancel=self.isInterruptionRequested, progress=self.progress.emit, **settings)
            else:
                samples = prepare_photo_samples(self.image, cancel=self.isInterruptionRequested, progress=self.progress.emit, **settings)
            if self.isInterruptionRequested():
                raise GenerationCancelled()
            self.ready.emit(samples)
        except GenerationCancelled:
            self.failed.emit("已取消提取，已收录字形保留。")
        except Exception as error:
            self.failed.emit(str(error) or type(error).__name__)


class PhotoFontDialog(FunctionDialog):
    def __init__(self, parent=None, store=None, plan=None, base_package=None):
        super().__init__(parent)
        self.store = store or FontStore()
        self.worker = None
        self.close_pending = False
        self.selected_id = ""
        self.base_package = base_package
        self.candidates, self.batches = [], []
        self.recrop_review = None
        self.setWindowTitle("照片生成个人字库 · 本地录入与核对")
        self.resize(1160, 850)
        layout = QVBoxLayout(self)
        tip = QLabel("导入手机照片后可点“自动识字并分字”：本机识字，不上传照片；整张识别或框选一两行均可。识字可能有错漏，先核对字符和裁切再勾选收录。原来的方格清单和手工逐字框选继续保留；照片没有真实笔顺。")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        self.base_checkbox = QCheckBox("保留当前字库，加入照片补录并另存副本" + (f"：{base_package.name}（{len(base_package.glyphs)}字）" if base_package else ""))
        self.base_checkbox.setVisible(base_package is not None)
        self.base_checkbox.setChecked(base_package is not None and not base_package.ordered)
        layout.addWidget(self.base_checkbox)
        self.controls = QWidget()
        controls = QHBoxLayout(self.controls)
        controls.setContentsMargins(0, 0, 0, 0)
        self.add_button(controls, "导入照片…", self.choose_photo)
        self.add_button(controls, "顺时针旋转90°", self.rotate)
        self.corner = QCheckBox("四角校正")
        controls.addWidget(self.corner)
        self.add_button(controls, "重选区域", lambda: self.canvas.reset_region())
        self.mode = QComboBox()
        self.mode.addItem("方格批量录字", "grid")
        self.mode.addItem("普通段落：逐字框选", "single")
        self.mode.addItem("自动识字与分字（离线）", "auto")
        controls.addWidget(self.mode)
        controls.addStretch()
        layout.addWidget(self.controls)
        splitter = QSplitter()
        layout.addWidget(splitter, 1)
        left = QWidget()
        column = QVBoxLayout(left)
        column.setContentsMargins(0, 0, 0, 0)
        self.instructions = QLabel("拖动框选第一格左上角到最后一格右下角，不包含标题；四角模式依次点左上、右上、右下、左下。")
        self.instructions.setWordWrap(True)
        column.addWidget(self.instructions)
        self.canvas = PhotoCanvas()
        view_bar = QHBoxLayout()
        self.add_button(view_bar, "适配全图", self.canvas.fit_image)
        self.add_button(view_bar, "定位本字", self.canvas.focus_sample)
        self.zoom_label = QLabel("1.0倍 · 滚轮缩放，右键拖动，左键框选")
        view_bar.addWidget(self.zoom_label, 1)
        self.canvas.view_changed.connect(lambda zoom: self.zoom_label.setText(f"{zoom:.1f}倍 · 滚轮缩放，右键拖动，左键框选"))
        column.addLayout(view_bar)
        column.addWidget(self.canvas, 1)
        self.filename = QLabel("尚未导入照片")
        self.filename.setWordWrap(True)
        column.addWidget(self.filename)
        splitter.addWidget(left)
        right = QWidget()
        right.setMinimumWidth(390)
        side = QVBoxLayout(right)
        side.setContentsMargins(4, 0, 0, 0)
        side.setSpacing(4)
        self.settings = QWidget()
        self.settings.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.grid_setting_rows = []
        form = QFormLayout(self.settings)
        form.setContentsMargins(0, 0, 0, 0)
        form.setVerticalSpacing(4)
        self.preset = QComboBox()
        self.preset.addItems(["连续等大方格", "本软件采样表（17mm格）"])
        form.addRow("分格预设", self.preset)
        self.grid_setting_rows.append(form.rowCount() - 1)
        row = QHBoxLayout()
        self.rows, self.columns = QSpinBox(), QSpinBox()
        for widget, value in ((self.rows, 10), (self.columns, 10)):
            widget.setRange(1, 100)
            widget.setValue(value)
        row.addWidget(QLabel("行"))
        row.addWidget(self.rows)
        row.addWidget(QLabel("列"))
        row.addWidget(self.columns)
        form.addRow("照片格数", row)
        self.grid_setting_rows.append(form.rowCount() - 1)
        self.gap_x, self.gap_y = QDoubleSpinBox(), QDoubleSpinBox()
        for widget in (self.gap_x, self.gap_y):
            widget.setRange(0, 100)
            widget.setSuffix(" %")
            widget.setDecimals(3)
        row = QHBoxLayout()
        row.addWidget(QLabel("横"))
        row.addWidget(self.gap_x)
        row.addWidget(QLabel("纵"))
        row.addWidget(self.gap_y)
        form.addRow("格间空隙/格宽高", row)
        self.grid_setting_rows.append(form.rowCount() - 1)
        self.processing = QComboBox()
        self.processing.addItem("自动去底与二值化（推荐）", "auto")
        self.processing.addItem("手动阈值（保留原方式）", "manual")
        form.addRow("照片处理", self.processing)
        self.threshold, self.margin = QSpinBox(), QDoubleSpinBox()
        self.threshold.setRange(40, 250)
        self.threshold.setValue(210)
        self.threshold.setToolTip("自动模式：210为默认浓淡，调低会减少墨迹、调高会增加墨迹；手动模式：直接使用灰度阈值。改变后请重新提取。")
        self.margin.setRange(0, 35)
        self.margin.setValue(3)
        self.margin.setSuffix(" %")
        row = QHBoxLayout()
        row.addWidget(QLabel("阈值"))
        row.addWidget(self.threshold)
        self.margin_label = QLabel("格内裁边")
        row.addWidget(self.margin_label)
        row.addWidget(self.margin)
        form.addRow("黑白提取", row)
        self.adaptive = QCheckBox("修正光照不均/阴影")
        self.adaptive.setChecked(True)
        self.remove_colored = QCheckBox("去红蓝格线（请用黑色笔）")
        self.remove_colored.setChecked(True)
        form.addRow(self.adaptive)
        form.addRow(self.remove_colored)
        self.separate_neighbors = QCheckBox("沿字间空白分离邻字残痕")
        self.separate_neighbors.setChecked(True)
        form.addRow(self.separate_neighbors)
        self.trajectory = QComboBox()
        self.trajectory.addItem("笔形单线（改善端头，推荐）", "pen_shape")
        self.trajectory.addItem("连贯单线（保留闭环，减少抬笔）", "continuous")
        self.trajectory.addItem("墨迹还原（轮廓＋填线，较慢）", "ink")
        self.trajectory.addItem("原中线（旧方式对照）", "legacy")
        form.addRow("照片轨迹", self.trajectory)
        row = QHBoxLayout()
        self.reference_size, self.pen_width = QDoubleSpinBox(), QDoubleSpinBox()
        self.reference_size.setRange(2, 50)
        self.reference_size.setValue(8)
        self.reference_size.setSuffix(" mm")
        self.pen_width.setRange(.05, 1.5)
        self.pen_width.setDecimals(2)
        self.pen_width.setValue(.3)
        self.pen_width.setSuffix(" mm")
        row.addWidget(QLabel("字框"))
        row.addWidget(self.reference_size)
        row.addWidget(QLabel("笔迹"))
        row.addWidget(self.pen_width)
        form.addRow("还原/模拟参考", row)
        self.pen_width.setToolTip("填写笔在纸上实际画出的线宽，不是笔芯标称直径。单线方式只用于模拟；墨迹还原按它生成填线。")
        self.reference_size.setToolTip("对应文字窗口的字框大小，不是字的墨迹外接框。墨迹还原应以同样字框和实际笔宽绘制；改变字号需重新生成。")
        self.trajectory_hint = QLabel()
        self.trajectory_hint.setWordWrap(True)
        self.trajectory_hint.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.trajectory_hint.setVisible(False)
        form.addRow(self.trajectory_hint)
        self.characters = QPlainTextEdit()
        self.characters.setPlaceholderText("手工模式填写字符；自动识字无需预先标注，识别后在下方列表核对并修改本字。")
        self.characters.setMaximumHeight(42)
        self.character_label = QLabel("字符顺序（手工模式）")
        form.addRow(self.character_label, self.characters)
        self.page_plan = QComboBox()
        for start in range(0, len(plan or []), 100):
            self.page_plan.addItem(f"采样表第{start // 100 + 1}页：{start + 1}～{min(start + 100, len(plan))}格", "".join(plan[start:start + 100]))
        if self.page_plan.count():
            row = QHBoxLayout()
            row.addWidget(self.page_plan, 1)
            self.add_button(row, "使用本页", self.use_page_plan)
            form.addRow("从录字清单取字符", row)
            self.grid_setting_rows.append(form.rowCount() - 1)
        side.addWidget(self.settings)
        row = QHBoxLayout()
        self.preview_button = self.add_button(row, "提取本张候选", self.generate)
        self.auto_button = self.add_button(row, "自动识字并分字", self.auto_generate)
        self.cancel_button = self.add_button(row, "取消提取", self.cancel)
        self.cancel_button.setEnabled(False)
        side.addLayout(row)
        self.results = QListWidget()
        self.results.setMinimumHeight(80)
        self.results.setMaximumHeight(110)
        side.addWidget(self.results, 1)
        row = QHBoxLayout()
        self.correct_character = QLineEdit()
        self.correct_character.setMaxLength(2)
        self.correct_character.setMaximumWidth(70)
        row.addWidget(QLabel("本字"))
        row.addWidget(self.correct_character)
        self.correct_button = self.add_button(row, "修改本字", self.correct_sample)
        row.addStretch()
        side.addLayout(row)
        row = QHBoxLayout()
        self.recrop_button = self.add_button(row, "重框本字", self.recrop_sample)
        self.clean_button = self.add_button(row, "清理本字…", self.clean_sample)
        self.delete_button = self.add_button(row, "删除当前字", self.delete_sample)
        self.delete_button.setToolTip("移除列表中当前选中的候选；不删除照片、已收录批次或已保存字库。需要恢复时可重新提取。")
        self.return_button = self.add_button(row, "放弃重框并返回", self.return_review)
        self.return_button.setVisible(False)
        side.addLayout(row)
        self.select_valid_button = self.add_button(side, "已核对，勾选有效项", self.select_valid)
        self.preview = SamplePreview()
        self.simulate_pen = QCheckBox("按参考笔宽模拟（仅显示，可取消查看分段）")
        self.simulate_pen.setChecked(True)
        side.addWidget(self.simulate_pen)
        side.addWidget(self.preview)
        self.add_batch_button = self.add_button(side, "收录勾选字格（可继续导入下一张）", self.collect)
        self.add_batch_button.setEnabled(False)
        self.review_scroll = QScrollArea()
        self.review_scroll.setWidgetResizable(True)
        self.review_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.review_scroll.setMinimumWidth(410)
        self.review_scroll.setWidget(right)
        splitter.addWidget(self.review_scroll)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.status = QLabel("建议先用10～20字测试。支持多张照片累计录入；每字最多16个版本。")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        footer = QHBoxLayout()
        self.font_name = QLineEdit("我的照片手写体")
        footer.addWidget(QLabel("新字库名"))
        footer.addWidget(self.font_name, 1)
        self.undo_button = self.add_button(footer, "撤销上一批收录", self.undo_batch)
        self.export_button = self.add_button(footer, "导出单字照片样本…", self.export_samples)
        self.save_button = self.add_button(footer, "另存为个人字库并返回", self.save_font)
        self.add_button(footer, "关闭", self.reject)
        layout.addLayout(footer)
        self.collection_status = QLabel()
        layout.addWidget(self.collection_status)
        self.canvas.changed.connect(self.invalidate)
        self.corner.toggled.connect(self.set_corner_mode)
        self.preset.currentIndexChanged.connect(self.apply_preset)
        self.mode.currentIndexChanged.connect(self.update_mode)
        self.characters.textChanged.connect(self.invalidate)
        for widget in (self.rows, self.columns, self.gap_x, self.gap_y, self.threshold, self.margin):
            widget.valueChanged.connect(self.invalidate)
        for widget in (self.adaptive, self.remove_colored):
            widget.toggled.connect(self.invalidate)
        self.results.currentRowChanged.connect(self.show_sample)
        self.results.itemDoubleClicked.connect(lambda item: self.canvas.focus_sample())
        self.processing.currentIndexChanged.connect(self.invalidate)
        self.trajectory.currentIndexChanged.connect(self.invalidate)
        self.trajectory.currentIndexChanged.connect(self.update_trajectory_hint)
        self.reference_size.valueChanged.connect(self.update_pen_reference)
        self.pen_width.valueChanged.connect(self.update_pen_reference)
        self.simulate_pen.toggled.connect(self.update_pen_reference)
        self.separate_neighbors.toggled.connect(self.invalidate)
        self.base_checkbox.toggled.connect(self.change_base)
        self.change_base()
        self.update_collection()
        self.update_mode()
        self.update_trajectory_hint()
        self.update_pen_reference()

    def update_trajectory_hint(self):
        hint = "按参考字框与实际笔迹宽度生成多条线，重现粗细外观；较慢，换字号/笔宽须重新提取。" if self.trajectory.currentData() == "ink" else "单线保留本人字形，粗细由实际笔尖决定；照片不含真实笔顺。识字分数不代表裁切或轨迹完整度。"
        self.trajectory_hint.setText(hint)
        self.trajectory.setToolTip(hint)
        self.trajectory_hint.setVisible(self.trajectory.currentData() == "ink")

    def update_pen_reference(self):
        self.preview.pen_width = self.pen_width.value() / self.reference_size.value()
        self.preview.simulate_pen = self.simulate_pen.isChecked()
        self.preview.update()
        # 笔宽在单线方式中仅改变预览；墨迹还原则必须重算路径。
        if self.sender() is not self.simulate_pen and self.trajectory.currentData() == "ink":
            self.invalidate()

    def add_button(self, layout, text, callback):
        button = QPushButton(text)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def choose_photo(self, checked=False, path=None):
        if self.recrop_review is not None:
            self.return_review()
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "选择手写照片", str(ROOT / "exports"), "照片 (*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff)")
        if path:
            try:
                self.canvas.set_image(read_image(path))
                self.filename.setText(str(path))
                self.status.setText("已导入照片，可直接点“自动识字并分字”，也可先框选清晰的一两行正文。原方格/单字手工模式仍可使用，已有收录保留。")
            except (OSError, GenerationError) as error:
                self.status.setText(str(error))

    def rotate(self):
        if self.canvas.image is not None:
            self.canvas.set_image(self.canvas.image.transformed(QTransform().rotate(90)))

    def set_corner_mode(self, enabled):
        self.canvas.corner_mode = enabled
        self.canvas.reset_region()

    def apply_preset(self):
        self.gap_x.setValue(2 / 17 * 100 if self.preset.currentIndex() == 1 else 0)
        self.gap_y.setValue(8 / 17 * 100 if self.preset.currentIndex() == 1 else 0)
        self.invalidate()

    def use_page_plan(self):
        chars = self.page_plan.currentData()
        if chars:
            self.mode.setCurrentIndex(self.mode.findData("grid"))
            self.preset.setCurrentIndex(1)
            self.rows.setValue((len(chars) + 9) // 10)
            self.columns.setValue(10)
            self.characters.setPlainText(chars)
            self.status.setText("已载入采样表本页字符和分格参数，请框选整排方格，包括末行空白格，勿选标题。")

    def active_base(self):
        return self.base_package if self.base_checkbox.isChecked() else None

    def change_base(self):
        base = self.active_base()
        self.font_name.setText((base.name[:50] + "（照片补录）") if base else "我的照片手写体")
        self.update_collection(self.worker is not None)

    def update_mode(self):
        mode = self.mode.currentData()
        grid = mode == "grid"
        self.separate_neighbors.setVisible(mode == "auto")
        for index in self.grid_setting_rows:
            self.settings.layout().setRowVisible(index, grid)
        self.margin.setVisible(mode != "auto")
        self.margin_label.setVisible(mode != "auto")
        for widget in (self.rows, self.columns, self.gap_x, self.gap_y, self.preset):
            widget.setEnabled(grid)
        self.characters.setReadOnly(mode == "auto")
        self.character_label.setText("识字内容（在下方修改本字）" if mode == "auto" else "字符顺序（手工模式）")
        self.instructions.setText("自动模式可识别整张；建议框选清晰的一两行正文，减少标题干扰。识别后点击列表，橙框显示对应字的位置。" if mode == "auto" else "单字模式框选一个字；方格模式只用于规整采样表。普通手稿不要用等大方格硬切。")
        self.invalidate()

    def invalidate(self):
        self.candidates = []
        self.results.clear()
        self.delete_button.setEnabled(False)
        self.preview.sample = None
        self.preview.update()
        self.add_batch_button.setEnabled(False)
        self.canvas.sample_boxes = []
        self.canvas.grid = (self.rows.value(), self.columns.value(), self.gap_x.value(), self.gap_y.value()) if self.mode.currentData() == "grid" else (1, 1, 0, 0)
        self.canvas.update()

    def generate(self):
        if self.worker is not None:
            return
        try:
            if self.canvas.image is None:
                raise GenerationError("请先导入照片。")
            single = self.mode.currentData() == "single"
            automatic = self.mode.currentData() == "auto"
            chars = "".join(char for char in self.characters.toPlainText() if not char.isspace())
            if single and len(chars) != 1:
                raise GenerationError("逐字框选模式只输入当前的一个字。")
            if not automatic and not self.canvas.points:
                raise GenerationError("请先框选字格区域，避免把标题和纸张边缘当作笔迹。")
            settings = dict(characters=chars, rows=1 if single else self.rows.value(), columns=1 if single else self.columns.value(), threshold=self.threshold.value(), margin=self.margin.value(),
                            gap_x=0 if single else self.gap_x.value(), gap_y=0 if single else self.gap_y.value(), region=self.canvas.region(), adaptive=self.adaptive.isChecked(), remove_colored=self.remove_colored.isChecked())
            settings["processing"] = self.processing.currentData()
            settings["trajectory"] = self.trajectory.currentData()
            settings["ink_width"] = self.pen_width.value() / self.reference_size.value()
            if automatic:
                for key in ("characters", "rows", "columns", "gap_x", "gap_y", "margin"):
                    settings.pop(key)
                settings["automatic"] = True
                settings["separate_neighbors"] = self.separate_neighbors.isChecked()
            self.invalidate_results()
            self.worker = PhotoWorker(self.canvas.image, settings, self)
            self.worker.ready.connect(self.generated)
            self.worker.failed.connect(self.status.setText)
            self.worker.progress.connect(self.show_progress)
            self.worker.finished.connect(self.worker_finished)
            self.set_busy(True)
            self.status.setText("正在本机识字和分字，可取消；识字结果需要人工核对后勾选。" if automatic else "正在逐格提取，可取消；不会改动已有字库。")
            self.worker.start()
        except GenerationError as error:
            self.status.setText(str(error))

    def invalidate_results(self):
        self.candidates = []
        self.results.clear()
        self.delete_button.setEnabled(False)
        self.add_batch_button.setEnabled(False)
        self.canvas.sample_boxes = []
        self.preview.sample = None
        self.preview.update()

    def auto_generate(self):
        self.mode.setCurrentIndex(self.mode.findData("auto"))
        self.generate()

    def generated(self, samples):
        if self.close_pending or self.worker.isInterruptionRequested():
            return
        if self.recrop_review is not None:
            if len(samples) != 1 or samples[0].error:
                self.status.setText("重框提取未成功，请调整区域后重试，或点击“放弃重框并返回”。其他候选保留。")
                return
            sample = samples[0]
            sample.box = tuple((point.x(), point.y()) for point in self.canvas.points)
            sample.note = "已手工重框，请核对新裁切的原字、黑白和轨迹，再勾选收录。"
            self.return_review(replacement=sample)
            return
        self.show_candidates(samples)

    def show_candidates(self, samples):
        self.results.clear()
        # 候选列表独立于调用方；删除或清理候选不应改动识字结果缓存或测试/重框暂存列表。
        self.candidates = list(samples)
        samples = self.candidates
        if self.trajectory.currentData() == "ink":
            for sample in samples:
                sample.note += f" 墨迹还原参考：字框{self.reference_size.value():g}mm，实际笔迹{self.pen_width.value():g}mm；改变字号/笔宽请重新生成。"
        if self.mode.currentData() == "auto":
            self.characters.blockSignals(True)
            self.characters.setPlainText("".join(sample.character for sample in samples))
            self.characters.blockSignals(False)
        self.canvas.sample_boxes = samples
        for index, sample in enumerate(samples):
            item = QListWidgetItem(self.sample_label(index))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked if sample.error or sample.confidence is not None else Qt.CheckState.Checked)
            item.setToolTip(sample.error or sample.note)
            if sample.error:
                item.setToolTip(sample.error)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            self.results.addItem(item)
        self.results.setCurrentRow(0)
        bad = sum(bool(sample.error) for sample in samples)
        self.status.setText(f"提取{len(samples)}个候选，{bad}个提取异常。自动识字项默认不勾选；点击列表核对原字、黑白和轨迹，错字用“修改本字”，裁错用“重框本字”，核对后勾选收录。")

    def sample_label(self, index):
        sample = self.candidates[index]
        detail = "需重拍/调整" if sample.error else f"{len(sample.strokes)}条落笔路径"
        if sample.confidence is not None:
            detail += f" · 识字分数{sample.confidence:.2f} · 待核对"
        if "边界穿过" in sample.note:
            detail += " · 连字边界需核对"
        return f"{index + 1:04d}　{sample.character}　{detail}"

    def show_sample(self, index):
        self.preview.sample = self.candidates[index] if 0 <= index < len(self.candidates) else None
        self.correct_character.setText(self.preview.sample.character if self.preview.sample else "")
        self.delete_button.setEnabled(self.preview.sample is not None and self.worker is None)
        self.canvas.selected_sample = index
        self.canvas.update()
        self.preview.update()
        if self.preview.sample and self.preview.sample.error:
            self.status.setText(self.preview.sample.error)
        elif self.preview.sample and self.preview.sample.note:
            self.status.setText(self.preview.sample.note)

    def correct_sample(self):
        index = self.results.currentRow()
        char = self.correct_character.text()
        if not 0 <= index < len(self.candidates):
            return
        if len(char) != 1 or char.isspace() or ord(char) < 33:
            self.status.setText("请填写一个可绘制的字符。")
            return
        self.candidates[index].character = char
        self.results.item(index).setText(self.sample_label(index))
        if self.mode.currentData() == "auto":
            self.characters.blockSignals(True)
            self.characters.setPlainText("".join(sample.character for sample in self.candidates))
            self.characters.blockSignals(False)
        self.status.setText(f"已将当前字标注为“{char}”，保留照片的原始轨迹；请核对后勾选。")

    def delete_sample(self):
        """删除当前提取候选，不影响原照片、已收录批次或已有字库。"""
        index = self.results.currentRow()
        if self.worker is not None or not 0 <= index < len(self.candidates):
            return
        character = self.candidates.pop(index).character
        # 先同步列表与索引，再刷新当前字，避免删到最后一项时预览仍指向旧字。
        blocked = self.results.blockSignals(True)
        try:
            self.results.takeItem(index)
            for row in range(len(self.candidates)):
                self.results.item(row).setText(self.sample_label(row))
            self.results.setCurrentRow(min(index, len(self.candidates) - 1))
        finally:
            self.results.blockSignals(blocked)
        self.canvas.sample_boxes = self.candidates
        if self.mode.currentData() == "auto":
            self.characters.blockSignals(True)
            self.characters.setPlainText("".join(sample.character for sample in self.candidates))
            self.characters.blockSignals(False)
        self.show_sample(self.results.currentRow())
        self.add_batch_button.setEnabled(bool(self.candidates) and self.worker is None)
        self.status.setText(f"已删除候选“{character}”，剩余{len(self.candidates)}项。" + ("可继续删除问题字，核对后点击“已核对，勾选有效项”。" if self.candidates else "本张候选已清空，需要时可重新提取。"))

    def select_valid(self):
        for index, sample in enumerate(self.candidates):
            if not sample.error:
                self.results.item(index).setCheckState(Qt.CheckState.Checked)

    def clean_sample(self):
        index = self.results.currentRow()
        if not 0 <= index < len(self.candidates):
            return
        dialog = PhotoSampleEditor(self.candidates[index], self.trajectory.currentData(), self)
        if dialog.exec() and dialog.result_sample is not None:
            self.candidates[index] = dialog.result_sample
            self.results.item(index).setText(self.sample_label(index))
            self.results.item(index).setFlags(self.results.item(index).flags() | Qt.ItemFlag.ItemIsUserCheckable)
            self.results.item(index).setToolTip(dialog.result_sample.note)
            self.results.item(index).setCheckState(Qt.CheckState.Unchecked)
            self.canvas.sample_boxes = self.candidates
            self.show_sample(index)

    def recrop_sample(self):
        index = self.results.currentRow()
        if not 0 <= index < len(self.candidates):
            return
        sample = self.candidates[index]
        char, box = sample.character, sample.box
        self.canvas.focus_sample()
        # 单字重框暂存整张候选和已勾选状态，成功只替换一项；取消仍可返回原列表。
        self.recrop_review = dict(samples=self.candidates, checked=[self.results.item(i).checkState() for i in range(len(self.candidates))],
                                  index=index, mode=self.mode.currentData(), points=list(self.canvas.points), corner=self.corner.isChecked(), characters=self.characters.toPlainText())
        self.return_button.setVisible(True)
        self.mode.setCurrentIndex(self.mode.findData("single"))
        self.mode.setEnabled(False)
        self.auto_button.setEnabled(False)
        self.corner.setChecked(False)
        self.characters.setPlainText(char)
        if len(box) == 4:
            self.canvas.points = [QPointF(*point) for point in box]
        self.canvas.update()
        self.status.setText("请重新拖动框选完整字形，再点“提取本张候选”；成功后仅替换本字并返回原列表。其他候选和已勾选项保留。")

    def return_review(self, checked=False, replacement=None):
        review, self.recrop_review = self.recrop_review, None
        if review is None:
            return
        self.return_button.setVisible(False)
        self.mode.setEnabled(True)
        self.auto_button.setEnabled(True)
        self.mode.setCurrentIndex(self.mode.findData(review["mode"]))
        self.corner.setChecked(review["corner"])
        self.canvas.points = review["points"]
        self.characters.blockSignals(True)
        self.characters.setPlainText(review["characters"])
        self.characters.blockSignals(False)
        if replacement is not None:
            review["samples"][review["index"]] = replacement
            review["checked"][review["index"]] = Qt.CheckState.Unchecked
        self.show_candidates(review["samples"])
        for index, state in enumerate(review["checked"]):
            self.results.item(index).setCheckState(state)
        self.results.setCurrentRow(review["index"])
        self.status.setText("已返回本张候选，其余字形和勾选状态保留。" + (" 本字已替换，请核对后勾选。" if replacement is not None else ""))

    def show_progress(self, value, total):
        self.progress.setRange(0, total)
        self.progress.setValue(value)

    def set_busy(self, busy):
        for widget in (self.controls, self.settings, self.canvas, self.preview_button, self.auto_button, self.font_name, self.base_checkbox, self.correct_character, self.correct_button, self.recrop_button, self.clean_button, self.delete_button, self.return_button, self.select_valid_button, self.results):
            widget.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        self.mode.setEnabled(not busy and self.recrop_review is None)
        self.auto_button.setEnabled(not busy and self.recrop_review is None)
        self.delete_button.setEnabled(not busy and 0 <= self.results.currentRow() < len(self.candidates))
        self.add_batch_button.setEnabled(not busy and bool(self.candidates))
        self.update_collection(busy)

    def worker_finished(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        if self.progress.maximum() == 0:
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
        self.set_busy(False)
        if self.close_pending:
            self.close_pending = False
            self.reject()

    def cancel(self):
        if self.worker is not None:
            self.worker.requestInterruption()
            self.status.setText("正在取消，当前识字计算结束后停止；已收录字形保留。")

    def collected(self):
        return [sample for batch in self.batches for sample in batch]

    def collect(self):
        samples = [sample for index, sample in enumerate(self.candidates) if not sample.error and self.results.item(index).checkState() == Qt.CheckState.Checked]
        try:
            if not samples:
                raise GenerationError("请至少勾选一个有效字格。")
            build_photo_package(self.collected() + samples, self.font_name.text(), self.active_base())
            self.batches.append(samples)
            self.add_batch_button.setEnabled(False)
            self.candidates = []
            self.results.clear()
            self.delete_button.setEnabled(False)
            self.canvas.sample_boxes = []
            self.canvas.update()
            self.preview.sample = None
            self.preview.update()
            self.update_collection()
            self.status.setText(f"已收录{len(samples)}格。可换照片/框选下一个字继续录入，完成后另存为个人字库。")
        except GenerationError as error:
            self.status.setText(str(error))

    def undo_batch(self):
        if self.batches:
            self.batches.pop()
            self.update_collection()
            self.status.setText("已撤销上一批收录；原有字库没有改变。")

    def update_collection(self, busy=False):
        samples = self.collected()
        base_count = len(self.active_base().glyphs) if self.active_base() else 0
        self.collection_status.setText(f"本次收录：{len(set(sample.character for sample in samples))}种字 / {len(samples)}版 / {len(self.batches)}批；保留原库{base_count}字。每字最多16版，整套最多20000种字及200万轨迹点。")
        for button in (self.save_button, self.export_button, self.undo_button):
            button.setEnabled(bool(samples) and not busy)

    def save_font(self):
        try:
            package = build_photo_package(self.collected(), self.font_name.text(), self.active_base())
            self.selected_id = self.store.save(package)
            super().accept()
        except (OSError, GenerationError) as error:
            self.status.setText(str(error))

    def export_samples(self, checked=False, path=None):
        if path is None:
            path = QFileDialog.getExistingDirectory(self, "选择一个空目录保存照片单字样本", str(ROOT / "exports"))
        if path:
            try:
                export_photo_samples(self.collected(), path)
                self.status.setText("已导出单字PNG、samples.json与photo-font.json。可作为SDT图片参考；这不是带真实笔顺的手录数据。")
            except (OSError, GenerationError) as error:
                self.status.setText(str(error))

    def reject(self):
        if self.worker is not None:
            self.close_pending = True
            self.cancel()
            return
        if self.batches:
            if not confirm_font_action(self, "照片录字尚未保存", "已收录字形还没有另存为个人字库，确定放弃并关闭吗？", "放弃录字"):
                return
        super().reject()

    def closeEvent(self, event):
        self.reject()
        # 拒绝放弃或仍在取消任务时保留窗口；确认关闭后也让系统关闭事件完成。
        event.setAccepted(not self.isVisible())
