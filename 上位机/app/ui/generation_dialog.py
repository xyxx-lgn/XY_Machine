import json
from pathlib import Path

from PySide6.QtCore import QThread, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFontComboBox,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSpinBox, QSplitter, QVBoxLayout, QWidget
)

from app.generation import (
    GenerationCancelled, GenerationError, PlotOptions, generate_task, read_image,
    render_text, save_gcode, source_ratio
)
from app.image_processing import ProcessingCancelled, binary_thumbnail, process_image
from app.profile import ROOT
from app.ui.preview_widget import PreviewWidget
from app.ui.image_inspection import ImageInspectionDialog


PREFERENCES = ROOT / "config" / "generation.json"


class GenerationWorker(QThread):
    ready = Signal(object)
    failed = Signal(str)
    progress = Signal(int, str)

    def __init__(self, image, options, name, parent=None, processed=None):
        super().__init__(parent)
        self.image, self.options, self.name = image.copy(), options, name
        self.processed = processed

    def run(self):
        try:
            task = generate_task(self.image, self.options, self.name, self.isInterruptionRequested, self.progress.emit, self.processed)
            self.ready.emit(task)
        except GenerationCancelled:
            self.failed.emit("已取消生成，可以修改参数后重试。")
        except Exception as error:
            self.failed.emit(str(error) or type(error).__name__)


class ImagePreviewWorker(QThread):
    ready = Signal(object, int)
    failed = Signal(str, int)

    def __init__(self, image, options, revision, parent=None):
        super().__init__(parent)
        self.image, self.options, self.revision = image.copy(), options, revision

    def run(self):
        try:
            processed = process_image(self.image, self.options, self.isInterruptionRequested)
            if not self.isInterruptionRequested():
                self.ready.emit(processed, self.revision)
        except ProcessingCancelled:
            pass
        except Exception as error:
            self.failed.emit(str(error) or type(error).__name__, self.revision)


class GenerationDialog(QDialog):
    def __init__(self, profile, kind="text", parent=None):
        super().__init__(parent)
        self.profile, self.kind = profile, kind
        self.worker = None
        self.task = None
        self.loaded_task = None
        self.source = None
        self.ratio = 3.0
        self.size_from_width = True
        self.image_path = None
        self.close_pending = False
        self.cancel_requested = False
        self.image_worker = None
        self.image_revision = 0
        self.processed_revision = -1
        self.processed = None
        self.processed_image = None
        self.pending_generate = False
        self.preview_needed = False
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(250)
        self.preview_timer.timeout.connect(self.begin_image_preview)
        self.text_timer = QTimer(self)
        self.text_timer.setSingleShot(True)
        self.text_timer.setInterval(300)
        self.text_timer.timeout.connect(self.update_text_preview)
        self.setWindowTitle("文字生成 · 路径与G代码" if kind == "text" else "图片生成 · 彩色转线稿与G代码")
        self.resize(1240, 880)
        self.setMinimumSize(1050, 720)
        self.build_ui()
        self.restore_preferences()
        self.connect_inputs()
        if kind == "text":
            self.update_text_preview()
        self.update_mode()
        self.set_busy(False)

    def button(self, text, callback):
        button = QPushButton(text)
        button.clicked.connect(callback)
        return button

    def number(self, value, minimum, maximum, decimals=2, suffix=""):
        field = QDoubleSpinBox()
        field.setRange(minimum, maximum)
        field.setDecimals(decimals)
        field.setValue(value)
        field.setSuffix(suffix)
        field.setKeyboardTracking(False)
        return field

    def group(self, title, layout):
        group = QGroupBox(title)
        group.setLayout(layout)
        return group

    def build_ui(self):
        layout = QVBoxLayout(self)
        introduction = QLabel("文字/图片 → 黑白结果 → 路径 → G代码。X向右、Y向下；生成和载入均不会自动发送。")
        introduction.setWordWrap(True)
        layout.addWidget(introduction)
        split = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(split, 1)
        self.inputs = QWidget()
        left = QVBoxLayout(self.inputs)
        left.setContentsMargins(0, 0, 6, 0)
        if self.kind == "text":
            source_form = QVBoxLayout()
            self.text = QPlainTextEdit("HOB 123")
            self.text.setMaximumHeight(110)
            self.text.setPlaceholderText("输入中文、英文或多行文字")
            self.font = QFontComboBox()
            self.font.setCurrentFont(QFont("Microsoft YaHei"))
            self.bold = QCheckBox("粗体")
            source_form.addWidget(self.text)
            source_form.addWidget(self.font)
            source_form.addWidget(self.bold)
            left.addWidget(self.group("文字与系统字体", source_form))
        else:
            source_form = QVBoxLayout()
            self.source_label = QLabel("未选择图片；支持PNG / JPG / BMP / WEBP")
            self.source_label.setWordWrap(True)
            source_form.addWidget(self.button("选择图片…", self.choose_image))
            source_form.addWidget(self.source_label)
            left.addWidget(self.group("图片来源", source_form))
        if self.kind == "image":
            processing_form = QFormLayout()
            self.processing = QComboBox()
            for title, value in (("黑白阈值（已有线稿）", "threshold"), ("自适应黑白（扫描/拍照）", "adaptive"), ("彩色边缘（插画/照片）", "edges"), ("白底卡通线稿（彩色角色）", "cartoon")):
                self.processing.addItem(title, value)
            self.sensitivity = QSpinBox()
            self.sensitivity.setRange(0, 100)
            self.sensitivity.setValue(55)
            self.detail = QSpinBox()
            self.detail.setRange(0, 100)
            self.detail.setValue(50)
            self.smoothing = QSpinBox()
            self.smoothing.setRange(0, 5)
            self.smoothing.setValue(2)
            self.despeckle = QSpinBox()
            self.despeckle.setRange(0, 100)
            self.despeckle.setValue(12)
            for field in (self.sensitivity, self.detail, self.smoothing, self.despeckle):
                field.setKeyboardTracking(False)
            self.repair = QCheckBox("修补微小断口（相近细节可能粘连）")
            processing_form.addRow("预处理方式", self.processing)
            processing_form.addRow("边缘灵敏度 0～100", self.sensitivity)
            processing_form.addRow("内部细节 0～100", self.detail)
            processing_form.addRow("去噪强度 0～5", self.smoothing)
            processing_form.addRow("碎点过滤 0～100", self.despeckle)
            processing_form.addRow(self.repair)
            processing_form.addRow(self.button("应用此方式的推荐参数", self.recommended_processing))
            self.processing_hint = QLabel()
            self.processing_hint.setWordWrap(True)
            processing_form.addRow(self.processing_hint)
            left.addWidget(self.group("图片转黑白线稿", processing_form))
        form = QFormLayout()
        self.mode = QComboBox()
        for title, value in (("轮廓（空心字/线稿边界）", "outline"), ("轮廓＋填充（实心）", "outline_fill"), ("仅填充（实心，减少重复描边）", "fill"), ("中线（骨架，适合细线稿）", "centerline")):
            self.mode.addItem(title, value)
        self.threshold = QSpinBox()
        self.threshold.setRange(0, 255)
        self.threshold.setValue(128)
        self.threshold.setKeyboardTracking(False)
        self.invert = QCheckBox("反相：白色部分作为图案")
        self.crop = QCheckBox("裁掉外围白边后设置尺寸")
        self.crop.setChecked(True)
        self.spacing = self.number(0.18, 0.02, 5, 3, " mm")
        self.direction = QComboBox()
        self.direction.addItems(("水平蛇形填线", "垂直蛇形填线"))
        self.tolerance = self.number(0.03, 0, 1, 3, " mm")
        self.minimum = self.number(0.08, 0, 10, 3, " mm")
        self.optimize = QCheckBox("优化路径顺序，减少空行程")
        self.optimize.setChecked(True)
        form.addRow("路径方式", self.mode)
        form.addRow("黑白阈值", self.threshold)
        form.addRow(self.invert)
        form.addRow(self.crop)
        form.addRow("填线间距", self.spacing)
        form.addRow("填线方向", self.direction)
        form.addRow("轮廓/中线简化误差", self.tolerance)
        form.addRow("忽略短于", self.minimum)
        form.addRow(self.optimize)
        self.mode_hint = QLabel()
        self.mode_hint.setWordWrap(True)
        form.addRow(self.mode_hint)
        left.addWidget(self.group("路径处理", form))

        form = QFormLayout()
        self.paper_label = QLabel(f"{self.profile.paper_width:g} × {self.profile.paper_height:g} mm（取主窗口纸张）")
        self.width = self.number(min(40, self.profile.paper_width), 0.05, 1000, 2, " mm")
        self.height = self.number(min(15, self.profile.paper_height), 0.05, 1000, 2, " mm")
        self.aspect = QCheckBox("锁定宽高比例")
        self.aspect.setChecked(True)
        self.x = self.number(min(10, max(0, self.profile.paper_width - 40)), 0, 1000, 2, " mm")
        self.y = self.number(min(10, max(0, self.profile.paper_height - 15)), 0, 1000, 2, " mm")
        form.addRow("纸张宽×高", self.paper_label)
        form.addRow("图案总宽", self.width)
        form.addRow("图案总高", self.height)
        form.addRow(self.aspect)
        form.addRow("左上角X（G54）", self.x)
        form.addRow("左上角Y（G54）", self.y)
        form.addRow(self.button("居中放入纸张（留5mm边）", self.fit_paper))
        left.addWidget(self.group("物理尺寸与位置", form))

        form = QFormLayout()
        self.draw_feed = self.number(400, 1, 30000, 0, " mm/min")
        self.travel_feed = self.number(1200, 1, 30000, 0, " mm/min")
        self.pen_up = self.number(self.profile.pen_up, self.profile.z_min, self.profile.z_max, 3)
        self.pen_down = self.number(self.profile.pen_down, self.profile.z_min, self.profile.z_max, 3)
        self.up_feed = self.number(self.profile.pen_up_feed, 1, 30000, 0)
        self.down_feed = self.number(self.profile.pen_down_feed, 1, 30000, 0)
        form.addRow("XY绘制进给", self.draw_feed)
        form.addRow("XY抬笔移动进给", self.travel_feed)
        form.addRow("机器Z抬笔位置", self.pen_up)
        form.addRow("机器Z落笔位置", self.pen_down)
        form.addRow("抬笔进给F", self.up_feed)
        form.addRow("落笔进给F", self.down_feed)
        z_hint = QLabel("Z高度每次打开取设备配置；抬落笔进给会记住生成窗口上次保存的值。连杆Z单位不是笔尖垂直mm；实际速度受固件$110～$122限制。")
        z_hint.setWordWrap(True)
        form.addRow(z_hint)
        left.addWidget(self.group("速度与步进Z", form))
        self.remember = QCheckBox("载入/保存时记住字体、尺寸和生成参数")
        self.remember.setChecked(True)
        self.remember.setToolTip("保存在config/generation.json；不记住本次Z覆盖值，不修改设备配置和EEPROM。")
        left.addWidget(self.remember)
        left.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(360)
        scroll.setMaximumWidth(440)
        scroll.setWidget(self.inputs)
        split.addWidget(scroll)

        right = QWidget()
        column = QVBoxLayout(right)
        column.setContentsMargins(4, 0, 0, 0)
        image_row = QHBoxLayout()
        for name, title in (("source_preview", "原图"), ("binary_preview", "黑白处理结果 · 自动更新")):
            box = QVBoxLayout()
            box.addWidget(QLabel(title))
            label = QLabel("选择内容后显示")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setMinimumHeight(145)
            label.setMaximumHeight(170)
            label.setMinimumWidth(160)
            label.setStyleSheet("background: white; border: 1px solid #d8e2f0;")
            setattr(self, name, label)
            box.addWidget(label)
            image_row.addLayout(box, 1)
        column.addLayout(image_row)
        image_actions = QHBoxLayout()
        self.binary_status = QLabel("先检查黑白图，再生成下方实际轨迹。")
        self.binary_status.setWordWrap(True)
        image_actions.addWidget(self.binary_status, 1)
        self.enlarge_button = self.button("放大黑白图", self.enlarge_processed)
        self.enlarge_button.setEnabled(False)
        image_actions.addWidget(self.enlarge_button)
        column.addLayout(image_actions)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("实际轨迹 · G54纸张坐标"))
        self.travel = QCheckBox("显示空行程")
        toolbar.addWidget(self.travel)
        toolbar.addStretch()
        self.preview = PreviewWidget()
        self.preview.set_paper(self.profile.paper_width, self.profile.paper_height)
        toolbar.addWidget(self.button("整张纸", self.preview.fit))
        toolbar.addWidget(self.button("放大图案", self.preview.fit_job))
        self.travel.toggled.connect(self.toggle_travel)
        column.addLayout(toolbar)
        column.addWidget(self.preview, 1)
        self.summary = QLabel("尚未生成。轮廓只描边；实心字请选择填充；中线不保证手写笔顺。")
        self.summary.setWordWrap(True)
        self.summary.setMinimumHeight(100)
        column.addWidget(self.summary)
        split.addWidget(right)
        split.setSizes([395, 810])
        split.setStretchFactor(1, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)
        self.status = QLabel("离线也可生成；参数修改后需要重新生成预览。")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        footer = QHBoxLayout()
        self.generate_button = self.button("生成预览", self.generate)
        self.generate_button.setObjectName("primary")
        self.cancel_button = self.button("取消生成", self.cancel_generation)
        self.load_button = self.button("载入主窗口", self.load_task)
        self.save_button = self.button("保存 .ngc…", self.export)
        footer.addWidget(self.generate_button)
        footer.addWidget(self.cancel_button)
        footer.addStretch()
        footer.addWidget(self.save_button)
        footer.addWidget(self.load_button)
        footer.addWidget(self.button("关闭", self.reject))
        layout.addLayout(footer)

    def connect_inputs(self):
        for field in (self.x, self.y, self.spacing, self.tolerance, self.minimum, self.draw_feed, self.travel_feed, self.pen_up, self.pen_down, self.up_feed, self.down_feed):
            field.valueChanged.connect(self.invalidate)
        self.width.valueChanged.connect(lambda: self.change_size(True))
        self.height.valueChanged.connect(lambda: self.change_size(False))
        self.mode.currentIndexChanged.connect(self.update_mode)
        self.direction.currentIndexChanged.connect(self.invalidate)
        self.threshold.valueChanged.connect(self.source_changed)
        for checkbox in (self.invert, self.crop):
            checkbox.toggled.connect(self.source_changed)
        self.aspect.toggled.connect(lambda checked: self.change_size(self.size_from_width) if checked else self.invalidate())
        self.optimize.toggled.connect(self.invalidate)
        if self.kind == "text":
            self.text.textChanged.connect(self.text_changed)
            self.font.currentFontChanged.connect(self.text_changed)
            self.bold.toggled.connect(self.text_changed)
        else:
            self.processing.currentIndexChanged.connect(self.processing_changed)
            for field in (self.sensitivity, self.detail, self.smoothing, self.despeckle):
                field.valueChanged.connect(self.source_changed)
            self.repair.toggled.connect(self.source_changed)
            self.processing_changed(select_path=False)

    def invalidate(self, *args):
        self.task = None
        self.load_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.status.setText("参数已改变，请点击“生成预览”。右侧轨迹暂时保留上次结果。")

    def update_mode(self, *args):
        filling = self.mode.currentData() in ("fill", "outline_fill")
        self.spacing.setEnabled(filling)
        self.direction.setEnabled(filling)
        hints = {
            "outline": "沿黑色区域边界描线；普通字体会得到空心轮廓字。",
            "outline_fill": "先描边再填充；边缘可能有重复着墨。",
            "fill": "连续蛇形填线，保留孔洞；间距直接决定相邻填线的距离。",
            "centerline": "提取黑色图案骨架；不是自动识别手写笔顺，交叉处可能产生分叉。"
        }
        self.mode_hint.setText(hints[self.mode.currentData()])
        if self.kind == "image" and self.processing.currentData() in ("edges", "cartoon") and self.mode.currentData() != "centerline":
            self.mode_hint.setText(self.mode_hint.text() + " 当前结果已是细线，推荐中线；轮廓会描细线两侧，填充只填细线区域。")
        self.invalidate()

    def change_size(self, from_width):
        self.size_from_width = from_width
        if self.aspect.isChecked():
            target = self.height if from_width else self.width
            value = self.width.value() / self.ratio if from_width else self.height.value() * self.ratio
            target.blockSignals(True)
            target.setValue(value)
            target.blockSignals(False)
        self.invalidate()

    def text_changed(self, *args):
        # 合并连续输入，停下300ms后更新文字源图及尺寸比例。
        self.source = None
        self.invalidate()
        self.text_timer.start()

    def update_text_preview(self):
        try:
            self.refresh_text_source()
        except GenerationError as error:
            self.status.setText(str(error))

    def refresh_text_source(self):
        self.source = render_text(self.text.toPlainText(), self.font.currentFont().family(), self.bold.isChecked())
        self.source_changed()

    def source_changed(self, *args):
        self.invalidate()
        if self.kind == "image":
            self.image_revision += 1
            self.processed = None
            self.processed_image = None
            self.enlarge_button.setEnabled(False)
            self.binary_preview.clear()
            self.binary_preview.setText("黑白图待更新…")
            self.preview_needed = self.source is not None
            if self.image_worker is not None:
                self.image_worker.requestInterruption()
            if self.source is not None:
                self.show_source(self.source)
                self.preview_timer.start()
            else:
                self.source_preview.clear()
                self.source_preview.setText("请选择图片")
                self.binary_status.setText("选择图片后自动更新黑白预览。")
            return
        if self.source is not None:
            try:
                self.ratio = source_ratio(self.source, self.threshold.value(), self.invert.isChecked(), self.crop.isChecked())
                self.change_size(self.size_from_width)
                self.show_source(self.source)
                self.show_processed(process_image(self.source, self.options()).image())
                self.binary_status.setText("文字黑白图已更新，路径需点击“生成预览”。")
            except ValueError as error:
                self.status.setText(str(error))

    def show_source(self, image):
        self.source_preview.setPixmap(QPixmap.fromImage(image).scaled(340, 165, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def show_processed(self, image):
        self.processed_image = image
        self.binary_preview.setPixmap(QPixmap.fromImage(binary_thumbnail(image, 340, 165)))
        self.enlarge_button.setEnabled(True)

    def enlarge_processed(self):
        if self.processed_image is not None:
            dialog = ImageInspectionDialog(self.processed_image, self)
            dialog.exec()
            dialog.deleteLater()

    def processing_changed(self, *args, select_path=True):
        value = self.processing.currentData()
        edges = value in ("edges", "cartoon")
        self.threshold.setEnabled(value == "threshold")
        self.invert.setEnabled(not edges)
        self.invert.setText("原图反相（白线黑底）" if value == "adaptive" else "反相：白色部分作为图案")
        if edges:
            self.invert.blockSignals(True)
            self.invert.setChecked(False)
            self.invert.blockSignals(False)
            if select_path:
                self.mode.setCurrentIndex(self.mode.findData("centerline"))
        self.sensitivity.setEnabled(edges)
        self.detail.setEnabled(value != "threshold")
        for field in (self.smoothing, self.despeckle, self.repair):
            field.setEnabled(value != "threshold")
        hints = {
            "threshold": "适合黑线白底。此方式使用下方黑白阈值，保留原版效果。",
            "adaptive": "适合纸面亮度不均的线稿。内部细节越高，保留的浅色线越多。",
            "edges": "按颜色和明暗变化提取细线；灵敏度越高，弱边缘越多。推荐中线路径。",
            "cartoon": "适合白底角色图。内部细节为0时仅留主体外轮廓；提高可补眼睛、嘴巴等，阴影也可能增加。推荐中线路径。"
        }
        self.processing_hint.setText(hints[value])
        self.source_changed()

    def recommended_processing(self):
        sensitivity = 80 if self.processing.currentData() == "cartoon" else 55
        for field, value in ((self.sensitivity, sensitivity), (self.detail, 50), (self.smoothing, 2), (self.despeckle, 12), (self.threshold, 128)):
            field.blockSignals(True)
            field.setValue(value)
            field.blockSignals(False)
        self.repair.blockSignals(True)
        self.repair.setChecked(False)
        self.repair.blockSignals(False)
        self.processing_changed()

    def begin_image_preview(self):
        self.preview_timer.stop()
        if self.source is None or self.close_pending or self.worker is not None:
            return
        if self.image_worker is not None:
            self.preview_needed = self.preview_needed or self.image_worker.revision != self.image_revision
            return
        self.preview_needed = False
        self.cancel_requested = False
        self.image_worker = ImagePreviewWorker(self.source, self.options(), self.image_revision, self)
        self.image_worker.ready.connect(self.image_preview_ready)
        self.image_worker.failed.connect(self.image_preview_failed)
        self.image_worker.finished.connect(self.image_preview_finished)
        self.binary_status.setText("正在后台更新黑白图…")
        self.set_busy(self.pending_generate)
        self.image_worker.start()

    def image_preview_ready(self, processed, revision):
        if revision != self.image_revision or self.close_pending or self.cancel_requested:
            return
        self.processed = processed
        self.processed_revision = revision
        self.ratio = processed.ratio
        self.change_size(self.size_from_width)
        self.show_processed(processed.image())
        self.binary_status.setText("黑白图已更新；" + (" ".join(processed.notes) if processed.notes else "可放大检查细节。"))
        self.status.setText("黑白图已更新，点击“生成预览”计算下方轨迹。")

    def image_preview_failed(self, message, revision):
        if revision == self.image_revision:
            self.processed = None
            self.binary_preview.setText("处理未完成")
            self.binary_status.setText(message)
            self.status.setText(message)

    def image_preview_finished(self):
        self.image_worker.deleteLater()
        self.image_worker = None
        if self.cancel_requested:
            self.status.setText("已取消图片处理，可以修改参数后重试。")
        if self.close_pending:
            if self.worker is None:
                super().reject()
            return
        if self.pending_generate:
            if self.preview_needed:
                self.begin_image_preview()
                return
            self.pending_generate = False
            if self.processed is not None and self.processed_revision == self.image_revision:
                self.generate()
                return
        elif self.preview_needed:
            self.preview_timer.start()
        self.set_busy(self.worker is not None)

    def choose_image(self, checked=False, path=None):
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "选择图片", str(ROOT), "图片 (*.png *.jpg *.jpeg *.bmp *.webp)")
        if not path:
            return
        self.invalidate()
        self.source = None
        self.source_changed()
        try:
            self.source = read_image(path)
            self.image_path = Path(path)
            self.source_label.setText(f"{self.image_path.name} · {self.source.width()}×{self.source.height()}像素")
            self.source_changed()
        except (OSError, GenerationError) as error:
            self.status.setText("图片读取失败：" + str(error))

    def fit_paper(self):
        try:
            if self.kind == "text" and self.source is None:
                self.refresh_text_source()
            width = min(self.profile.paper_width - 10, (self.profile.paper_height - 10) * self.ratio)
            if width <= 0:
                raise GenerationError("纸张太小，无法保留5mm边距。")
            self.width.setValue(width)
            self.change_size(True)
            if not self.aspect.isChecked():
                self.height.setValue(width / self.ratio)
            self.x.setValue((self.profile.paper_width - self.width.value()) / 2)
            self.y.setValue((self.profile.paper_height - self.height.value()) / 2)
        except GenerationError as error:
            self.status.setText(str(error))

    def options(self):
        options = PlotOptions(
            paper_width=self.profile.paper_width, paper_height=self.profile.paper_height,
            x=self.x.value(), y=self.y.value(), width=self.width.value(), height=self.height.value(),
            mode=self.mode.currentData(), threshold=self.threshold.value(), invert=self.invert.isChecked(), crop=self.crop.isChecked(),
            spacing=self.spacing.value(), vertical=self.direction.currentIndex() == 1, tolerance=self.tolerance.value(),
            min_length=self.minimum.value(), optimize=self.optimize.isChecked(), draw_feed=self.draw_feed.value(),
            travel_feed=self.travel_feed.value(), pen_up=self.pen_up.value(), pen_down=self.pen_down.value(),
            up_feed=self.up_feed.value(), down_feed=self.down_feed.value(), z_min=self.profile.z_min, z_max=self.profile.z_max
        )
        if self.kind == "image":
            options.processing = self.processing.currentData()
            options.edge_sensitivity = self.sensitivity.value()
            options.detail_level = self.detail.value()
            options.smoothing = self.smoothing.value()
            options.despeckle = self.despeckle.value()
            options.repair_gaps = self.repair.isChecked()
        return options

    def generate(self):
        if self.worker is not None:
            return
        self.text_timer.stop()
        self.invalidate()
        try:
            if self.profile.z_mode != "stepper":
                raise GenerationError("当前生成器输出步进Z任务。舵机文件需要实际固件动作协议，暂不生成舵机指令。")
            if self.kind == "text":
                self.refresh_text_source()
            if self.source is None:
                raise GenerationError("请先选择图片。")
            if self.kind == "image" and (self.processed is None or self.processed_revision != self.image_revision or self.image_worker is not None):
                self.pending_generate = True
                self.preview_timer.stop()
                self.preview_needed = self.image_worker is None or self.image_worker.revision != self.image_revision
                self.status.setText("先更新黑白图，完成后自动生成轨迹…")
                self.set_busy(True)
                self.begin_image_preview()
                return
            options = self.options()
            options.validate()
        except (ValueError, OSError) as error:
            self.status.setText(str(error))
            return
        self.progress.setValue(0)
        self.cancel_requested = False
        self.preview_timer.stop()
        self.worker = GenerationWorker(self.source, options, "文字任务" if self.kind == "text" else "图片任务", self, self.processed if self.kind == "image" else None)
        self.worker.ready.connect(self.generated)
        self.worker.failed.connect(self.failed)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.worker_finished)
        self.set_busy(True)
        self.worker.start()

    def set_busy(self, busy):
        self.inputs.setEnabled(not busy)
        self.generate_button.setEnabled(not busy and self.profile.z_mode == "stepper")
        self.cancel_button.setEnabled(busy or self.image_worker is not None)
        self.load_button.setEnabled(not busy and self.task is not None)
        self.save_button.setEnabled(not busy and self.task is not None)
        if self.profile.z_mode != "stepper":
            self.status.setText("当前为舵机配置；本版生成器只输出步进Z文件，请配套固件后再增加舵机任务协议。")

    def on_progress(self, value, message):
        self.progress.setValue(value)
        self.status.setText(message)

    def generated(self, task):
        if self.cancel_requested:
            return
        self.task = task
        self.show_processed(task.image)
        self.preview.set_program(task.program)
        self.preview.fit_job()
        self.summary.setText(
            f"{task.path_count}条落笔路径 · {task.point_count}个路径点 · {len(task.program.commands)}条命令\n"
            f"绘制 {task.program.drawing_length:.1f} mm · 空行程 {task.travel_length:.1f} mm · 理论下限 {task.minimum_seconds:.1f}秒\n"
            f"XY范围 {task.program.bounds} · 栅格精度约 {task.resolution:.3f} mm\n" + "\n".join(task.notes)
        )
        self.status.setText("生成完成。检查尺寸和轨迹后可保存，或载入主窗口；尚未发送。")

    def failed(self, message):
        self.task = None
        self.status.setText("生成未完成：" + message)

    def worker_finished(self):
        self.worker.deleteLater()
        self.worker = None
        if self.cancel_requested:
            self.task = None
            self.status.setText("已取消生成，可以修改参数后重试。")
        self.set_busy(False)
        if self.close_pending and self.image_worker is None:
            super().reject()

    def cancel_generation(self):
        self.preview_timer.stop()
        self.pending_generate = self.preview_needed = False
        self.cancel_requested = True
        if self.image_worker is not None:
            self.image_worker.requestInterruption()
            self.cancel_button.setEnabled(False)
            self.status.setText("正在取消图片处理…")
        if self.worker is not None:
            self.cancel_requested = True
            self.worker.requestInterruption()
            self.cancel_button.setEnabled(False)
            self.status.setText("正在取消，请等待当前处理步骤结束…")

    def load_task(self):
        if self.worker is not None or self.image_worker is not None or self.pending_generate or self.task is None:
            return
        self.save_preferences()
        self.loaded_task = self.task
        self.accept()

    def export(self, checked=False, path=None):
        if self.worker is not None or self.image_worker is not None or self.pending_generate or self.task is None:
            return
        if path is None:
            folder = ROOT / "exports"
            folder.mkdir(exist_ok=True)
            path, _ = QFileDialog.getSaveFileName(self, "保存G代码", str(folder / self.task.program.path.name), "G代码 (*.ngc *.nc *.gcode *.tap)")
        if not path:
            return
        try:
            target = save_gcode(self.task.program, path)
            self.save_preferences()
            self.status.setText(f"已保存：{target}。未自动发送。")
        except (OSError, GenerationError) as error:
            self.status.setText("保存失败：" + str(error))

    def save_preferences(self):
        if not self.remember.isChecked():
            return
        data = {name: getattr(self, name).value() for name in ("width", "height", "x", "y", "spacing", "tolerance", "minimum", "draw_feed", "travel_feed", "up_feed", "down_feed", "threshold")}
        data.update({name: getattr(self, name).isChecked() for name in ("invert", "crop", "aspect", "optimize")})
        data.update(mode=self.mode.currentData(), direction=self.direction.currentIndex())
        if self.kind == "image":
            data.update(processing=self.processing.currentData(), sensitivity=self.sensitivity.value(), detail=self.detail.value(), smoothing=self.smoothing.value(), despeckle=self.despeckle.value(), repair=self.repair.isChecked())
        if self.kind == "text":
            data.update(text=self.text.toPlainText(), font=self.font.currentFont().family(), bold=self.bold.isChecked())
        try:
            stored = json.loads(PREFERENCES.read_text(encoding="utf-8")) if PREFERENCES.exists() else {}
            stored[self.kind] = data
            PREFERENCES.parent.mkdir(exist_ok=True)
            temporary = PREFERENCES.with_suffix(".tmp")
            temporary.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(PREFERENCES)
        except (OSError, ValueError, TypeError, AttributeError):
            # 生成成功不依赖偏好文件，配置异常不会影响任务载入。
            self.status.setText("任务已生成，但生成偏好未保存。")

    def restore_preferences(self):
        try:
            stored = json.loads(PREFERENCES.read_text(encoding="utf-8")).get(self.kind, {})
            for name in ("width", "height", "x", "y", "spacing", "tolerance", "minimum", "draw_feed", "travel_feed", "up_feed", "down_feed", "threshold"):
                if name in stored and type(stored[name]) in (int, float):
                    getattr(self, name).setValue(stored[name])
            for name in ("invert", "crop", "aspect", "optimize"):
                if type(stored.get(name)) is bool:
                    getattr(self, name).setChecked(stored[name])
            index = self.mode.findData(stored.get("mode"))
            if index >= 0:
                self.mode.setCurrentIndex(index)
            self.direction.setCurrentIndex(1 if stored.get("direction") == 1 else 0)
            if self.kind == "image":
                index = self.processing.findData(stored.get("processing"))
                if index >= 0:
                    self.processing.setCurrentIndex(index)
                for name in ("sensitivity", "detail", "smoothing", "despeckle"):
                    if type(stored.get(name)) is int:
                        getattr(self, name).setValue(stored[name])
                self.repair.setChecked(stored.get("repair") is True)
            if self.kind == "text":
                if isinstance(stored.get("text"), str):
                    self.text.setPlainText(stored["text"][:2000])
                if isinstance(stored.get("font"), str):
                    self.font.setCurrentFont(QFont(stored["font"]))
                self.bold.setChecked(stored.get("bold") is True)
        except (OSError, ValueError, TypeError, AttributeError, OverflowError):
            pass

    def toggle_travel(self, checked):
        self.preview.show_travel = checked
        self.preview.update()

    def reject(self):
        self.text_timer.stop()
        self.preview_timer.stop()
        if self.worker is not None or self.image_worker is not None:
            self.close_pending = True
            self.cancel_generation()
            return
        super().reject()

    def closeEvent(self, event):
        self.preview_timer.stop()
        self.text_timer.stop()
        if self.worker is not None or self.image_worker is not None:
            self.close_pending = True
            self.cancel_generation()
            event.ignore()
        else:
            super().closeEvent(event)
