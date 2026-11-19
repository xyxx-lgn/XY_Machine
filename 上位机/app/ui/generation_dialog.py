import json
from pathlib import Path
import secrets

from PySide6.QtCore import QThread, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFontComboBox,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSpinBox, QSplitter, QTextBrowser, QVBoxLayout, QWidget
)

from app.generation import (
    GenerationCancelled, GenerationError, PlotOptions, generate_task, read_image,
    render_text, save_gcode, source_ratio
)
from app.image_processing import ProcessingCancelled, binary_thumbnail, process_image
from app.profile import ROOT
from app.ui.preview_widget import PreviewWidget
from app.ui.image_inspection import ImageInspectionDialog
from app.ui.stroke_font_dialog import StrokeFontDialog
from app.ui.dialog_window import FunctionDialog
from app.ui.text_document_tools import TextDocumentTools
from app.stroke_fonts import FontStore, LATIN_STYLES, NaturalOptions, WritingOptions
from app.builtin_stroke_fonts import BUILTIN_FONTS, font_info
from app.text_writing import DATA, STYLE, layout_stroke_artwork, layout_stroke_text, render_stroke_source
from app.text_layout import FONT_SIZE_RANGE, LETTER_SPACING_RANGE, TextOptions, generate_artwork_text_task, generate_text_task, layout_artwork, layout_text, render_page


PREFERENCES = ROOT / "config" / "generation.json"


class GenerationWorker(QThread):
    ready = Signal(object)
    failed = Signal(str)
    progress = Signal(int, str)

    def __init__(self, image, options, name, parent=None, processed=None, text_page=None, text_options=None):
        super().__init__(parent)
        self.image, self.options, self.name = image.copy(), options, name
        self.processed = processed
        self.text_page, self.text_options = text_page, text_options

    def run(self):
        try:
            if self.text_page is not None and self.text_options is None:
                task = generate_artwork_text_task(self.text_page, self.options, self.image, self.name, self.isInterruptionRequested, self.progress.emit)
            elif self.text_page is not None:
                task = generate_text_task(self.text_page, self.text_options, self.options, self.name, self.isInterruptionRequested, self.progress.emit)
            else:
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


class GenerationDialog(FunctionDialog):
    def __init__(self, profile, kind="text", parent=None):
        super().__init__(parent)
        self.profile, self.kind = profile, kind
        self.worker = None
        self.task = None
        self.loaded_task = None
        self.batch_paths = None
        self.source = None
        self.text_document = None
        self.text_paragraph_styles = None
        self.paragraph_font_sizes = None
        self._loading_document = False
        self.font_store = FontStore() if kind == "text" else None
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
        self.update_mode()
        if kind == "text":
            self.update_text_preview()
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
            self.text.setPlaceholderText("粘贴正文后自动换行；回车另起段落。正文最多20000字符。")
            self.document_tools = TextDocumentTools(self)
            source_form.addWidget(self.document_tools)
            self.font = QFontComboBox()
            self.font.setCurrentFont(QFont("Microsoft YaHei"))
            self.bold = QCheckBox("粗体")
            self.layout_mode = QComboBox()
            self.layout_mode.addItem("正文排版（固定字号，自动换行）", "page")
            self.layout_mode.addItem("文字图案（整体缩放）", "artwork")
            self.line_spacing = self.number(1.2, 0.5, 3.0, 2, " ×")
            self.alignment = QComboBox()
            for title, value in (("左对齐", "left"), ("居中对齐", "center"), ("右对齐", "right")):
                self.alignment.addItem(title, value)
            source_form.addWidget(self.text)
            source_form.addWidget(self.layout_mode)
            self.stroke_style = QLabel("笔画字形：" + STYLE + "。系统字体选择仅在轮廓/填充/骨架模式生效。\n©1999 文鼎；ArphicPL许可，可再分发、无担保，详见“字库来源与许可”。")
            self.stroke_style.setWordWrap(True)
            source_form.addWidget(self.stroke_style)
            self.stroke_controls = QWidget()
            stroke_form = QFormLayout(self.stroke_controls)
            stroke_form.setContentsMargins(0, 0, 0, 0)
            self.stroke_font = QComboBox()
            self.stroke_font.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            self.stroke_font.setMinimumContentsLength(12)
            self.stroke_font.setMaxVisibleItems(14)
            self.refresh_stroke_fonts()
            self.latin_style = QComboBox()
            for value, title in LATIN_STYLES:
                self.latin_style.addItem(title, value)
            self.stroke_fallback = QCheckBox("个人字库缺字用基础楷体补缺")
            self.stroke_fallback.setToolTip("仅用于个人字库；生成结果会列出使用基础楷体的字符。")
            self.stroke_fallback.setChecked(True)
            self.natural_enabled = QCheckBox("启用自然书写（所有单线字库可用）")
            self.natural_strength = self.number(50, 0, 100, 0, " %")
            self.natural_spacing = self.number(40, 0, 100, 0, " %")
            self.natural_baseline = self.number(20, 0, 100, 0, " %")
            self.natural_seed = QSpinBox()
            self.natural_seed.setRange(1, 2147483647)
            self.natural_seed.setValue(1)
            self.natural_seed.setKeyboardTracking(False)
            font_row = QHBoxLayout()
            font_row.addWidget(self.stroke_font, 1)
            self.font_samples_button = self.button("字形对照…", self.show_handwriting_samples)
            self.font_samples_button.setEnabled((DATA / "centerline/手写字库效果预览1.png").exists())
            font_row.addWidget(self.font_samples_button)
            stroke_form.addRow("中文/个人字库", font_row)
            stroke_form.addRow("英文与数字笔画", self.latin_style)
            stroke_form.addRow(self.stroke_fallback)
            stroke_form.addRow(self.button("个人字库 / 手写与照片录入…", self.edit_stroke_font))
            stroke_form.addRow(self.natural_enabled)
            stroke_form.addRow("字形与笔画变化", self.natural_strength)
            stroke_form.addRow("字距变化", self.natural_spacing)
            stroke_form.addRow("行内起伏", self.natural_baseline)
            stroke_form.addRow("样式编号", self.natural_seed)
            stroke_form.addRow(self.button("换一种自然效果", self.shuffle_natural))
            natural_hint = QLabel("字形变化会轻微调整字内比例和笔画弯曲；字距与行内起伏可分别设为0。保留原数据的分笔和方向，不增加填充。同编号可重现，换编号可对比。手写外观字体和黑体/明体使用中线路径，不保证标准笔顺。")
            natural_hint.setWordWrap(True)
            stroke_form.addRow(natural_hint)
            source_form.addWidget(self.stroke_controls)
            source_form.addWidget(self.font)
            source_form.addWidget(self.bold)
            text_layout = QFormLayout()
            text_layout.addRow("多行行距", self.line_spacing)
            text_layout.addRow("多行对齐", self.alignment)
            source_form.addLayout(text_layout)
            left.addWidget(self.group("文字与字形", source_form))
            form = QFormLayout()
            self.font_size = self.number(14, *FONT_SIZE_RANGE, decimals=1, suffix=" pt")
            self.use_document_font_sizes = QCheckBox("采用Word段落字号")
            self.use_document_font_sizes.setToolTip("使用Word各段的主要字号。取消后用下方字号统一排版；缺少字号的段落也用下方字号。")
            self.use_document_font_sizes.hide()
            self.font_preset = QComboBox()
            for title, value in (("自定义", None), ("五号（10.5pt）", 10.5), ("小四（12pt）", 12), ("四号（14pt）", 14), ("三号（16pt）", 16), ("小二（18pt）", 18), ("二号（22pt）", 22)):
                self.font_preset.addItem(title, value)
            self.font_preset.setCurrentIndex(self.font_preset.findData(14))
            self.font_hint = QLabel()
            self.font_hint.setWordWrap(True)
            self.margin_left = self.number(20, 0, 999, 2, " mm")
            self.margin_right = self.number(20, 0, 999, 2, " mm")
            self.margin_top = self.number(20, 0, 999, 2, " mm")
            self.margin_bottom = self.number(20, 0, 999, 2, " mm")
            self.letter_spacing = self.number(0, *LETTER_SPACING_RANGE, decimals=2, suffix=" mm")
            self.letter_spacing.setSingleStep(.05)
            self.letter_spacing.setToolTip("负值收紧，0保留原字距，正值拉开。实际收紧量受字形和字号限制；笔画模式会保留相邻笔迹的留白。")
            self.paragraph_spacing = self.number(0, 0, 30, 1, " mm")
            self.indent = self.number(0, 0, 4, 1, " 字")
            form.addRow("常用字号", self.font_preset)
            form.addRow(self.use_document_font_sizes)
            form.addRow("字号", self.font_size)
            form.addRow(self.font_hint)
            self.text_paper_label = QLabel(f"{self.profile.paper_width:g} × {self.profile.paper_height:g} mm（取主窗口）")
            self.text_paper_label.setWordWrap(True)
            form.addRow("纸张", self.text_paper_label)
            form.addRow("左页边距", self.margin_left)
            form.addRow("右页边距", self.margin_right)
            form.addRow("上页边距", self.margin_top)
            form.addRow("下页边距", self.margin_bottom)
            form.addRow("字距微调", self.letter_spacing)
            form.addRow("段后间距", self.paragraph_spacing)
            form.addRow("首行缩进", self.indent)
            self.layout_summary = QLabel("按纸张毫米宽度换行，内容超出一页时自动分页。")
            self.layout_summary.setWordWrap(True)
            form.addRow(self.layout_summary)
            self.page_settings = self.group("字号与纸张排版", form)
            left.addWidget(self.page_settings)
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
        if self.kind == "text":
            self.mode.addItem("笔画书写（按笔顺，推荐）", "strokes")
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
        if self.kind == "text":
            self.text_pen_width = self.number(0.18, 0.05, 1.5, 3, " mm")
            self.text_pen_width.setToolTip("用于显示笔迹粗细，并估算字距收紧时需要保留的留白。实际粗细由笔尖和落笔压力决定。")
            form.addRow("预览笔迹线宽", self.text_pen_width)
            form.addRow(self.button("使用笔画书写", lambda: self.mode.setCurrentIndex(self.mode.findData("strokes"))))
            form.addRow(self.button("字库来源与许可…", self.show_stroke_license))
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
        self.artwork_settings = self.group("物理尺寸与位置", form)
        left.addWidget(self.artwork_settings)

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
        if self.kind == "text":
            self.page_bar = QWidget()
            page_row = QHBoxLayout(self.page_bar)
            page_row.setContentsMargins(0, 0, 0, 0)
            self.previous_page = self.button("上一页", lambda: self.page_number.setValue(self.page_number.value() - 1))
            self.next_page = self.button("下一页", lambda: self.page_number.setValue(self.page_number.value() + 1))
            self.page_number = QSpinBox()
            self.page_number.setRange(1, 1)
            self.page_number.setKeyboardTracking(False)
            self.page_total = QLabel("/ 共1页")
            page_row.addWidget(self.previous_page)
            page_row.addWidget(QLabel("当前页"))
            page_row.addWidget(self.page_number)
            page_row.addWidget(self.page_total)
            page_row.addWidget(self.next_page)
            page_row.addStretch()
            page_row.addWidget(QLabel("每页独立生成；载入后手动开始"))
            column.addWidget(self.page_bar)
            column.addWidget(self.document_tools.page_controls)
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
        self.summary = QLabel("尚未生成。正文推荐笔画书写；系统字体轮廓/填充用于字体图案。")
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
            self.stroke_font.currentIndexChanged.connect(self.update_mode)
            self.latin_style.currentIndexChanged.connect(self.text_changed)
            for field in (self.stroke_fallback, self.natural_enabled):
                field.toggled.connect(self.text_changed)
            for field in (self.natural_strength, self.natural_spacing, self.natural_baseline, self.natural_seed):
                field.valueChanged.connect(self.text_changed)
            self.text_pen_width.valueChanged.connect(self.text_changed)
            self.text.textChanged.connect(self.text_content_changed)
            self.font.currentFontChanged.connect(self.text_changed)
            self.bold.toggled.connect(self.text_changed)
            self.line_spacing.valueChanged.connect(self.text_changed)
            self.alignment.currentIndexChanged.connect(self.text_changed)
            self.layout_mode.currentIndexChanged.connect(self.change_text_layout)
            self.font_preset.currentIndexChanged.connect(self.select_font_size)
            self.use_document_font_sizes.toggled.connect(self.text_changed)
            for field in (self.font_size, self.margin_left, self.margin_right, self.margin_top, self.margin_bottom, self.letter_spacing, self.paragraph_spacing, self.indent):
                field.valueChanged.connect(self.text_changed)
            self.page_number.valueChanged.connect(self.change_page)
            self.change_text_layout()
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
        if self.is_page_layout():
            if self.preview.program is not None:
                self.preview.set_program(None)
            self.summary.setText("当前为页面排版预览，点击“生成本页轨迹”后可保存或载入。")
            self.status.setText("参数已改变，请生成当前页轨迹。字号保持不变，内容按可写宽度换行。")

    def is_page_layout(self):
        return self.kind == "text" and self.layout_mode.currentData() == "page"

    def change_text_layout(self):
        paged = self.is_page_layout()
        self.page_settings.setVisible(paged)
        self.page_bar.setVisible(paged)
        self.document_tools.page_controls.setVisible(paged)
        self.artwork_settings.setVisible(not paged)
        for field in (self.threshold, self.invert, self.crop):
            field.setEnabled(not paged and not self.is_stroke_mode())
        self.generate_button.setText("生成本页轨迹" if paged else "生成预览")
        self.save_button.setText("保存本页 .ngc…" if paged else "保存 .ngc…")
        self.load_button.setText("载入当前页" if paged else "载入主窗口")
        self.sync_paragraph_alignment()
        self.text_changed()

    def select_font_size(self):
        value = self.font_preset.currentData()
        if value is not None:
            self.font_size.setValue(value)

    def text_options(self):
        return TextOptions(
            paper_width=self.profile.paper_width, paper_height=self.profile.paper_height, font_size=self.font_size.value(),
            margin_left=self.margin_left.value(), margin_right=self.margin_right.value(), margin_top=self.margin_top.value(), margin_bottom=self.margin_bottom.value(),
            line_spacing=self.line_spacing.value(), letter_spacing=self.letter_spacing.value(), paragraph_spacing=self.paragraph_spacing.value(),
            indent=self.indent.value(), alignment=self.alignment.currentData()
        )

    def change_page(self):
        if self.is_page_layout() and self.text_document is not None:
            self.invalidate()
            self.show_text_page()
            self.preview.fit()

    def show_text_page(self):
        document = self.text_document
        number = self.page_number.value()
        page = document.pages[number - 1]
        self.source = render_page(page, document.options)
        self.show_source(self.source)
        self.show_processed(self.source)
        self.page_total.setText(f"/ 共{len(document.pages)}页")
        self.previous_page.setEnabled(number > 1)
        self.next_page.setEnabled(number < len(document.pages))
        rect, bounds = document.options.content_rect, page.bounds
        self.layout_summary.setText(f"可写区域 {rect.width():.1f} × {rect.height():.1f} mm\n第{number}页 {len(page.lines)}行 · 字迹 {bounds.width():.1f} × {bounds.height():.1f} mm")
        self.binary_status.setText(f"第{number}/{len(document.pages)}页排版预览 · 字号 {page.font_size_label(document.options.font_size)} · 换页后需重新生成轨迹。")
        self.preview.set_text_page(page, document.options.content_rect)

    def is_stroke_mode(self):
        return self.kind == "text" and self.mode.currentData() == "strokes"

    def refresh_stroke_fonts(self, selected=""):
        self.stroke_font.blockSignals(True)
        self.stroke_font.clear()
        self.stroke_font.addItem("XY单线楷体（9574字，内置）", "")
        for identity, name in BUILTIN_FONTS.items():
            info = font_info(identity)
            coverage = info.get("gb2312_hanzi")
            suffix = "，简体常用字覆盖有限" if coverage is not None and coverage < 6500 else ""
            self.stroke_font.addItem(f"{name}（{info['glyphs']}字{suffix}）", identity)
            description = f"来源：{info['source_font']}。离线中线轨迹，逐字书写。"
            if coverage is not None:
                description += f"GB2312汉字覆盖：{coverage}/6763。缺字会提示，不会漏字生成。"
            self.stroke_font.setItemData(self.stroke_font.count() - 1, description, Qt.ItemDataRole.ToolTipRole)
        for package in self.font_store.catalog():
            self.stroke_font.addItem(f"{package.name}（{len(package.glyphs)}字）", package.id)
        index = self.stroke_font.findData(selected)
        if selected and index < 0:
            self.stroke_font.addItem("原字库不存在，请重新选择", selected)
            index = self.stroke_font.count() - 1
        self.stroke_font.setCurrentIndex(max(0, index))
        self.stroke_font.blockSignals(False)

    def writing_options(self):
        identity = self.stroke_font.currentData()
        try:
            package = self.font_store.load(identity) if identity and identity not in BUILTIN_FONTS else None
        except (OSError, GenerationError) as error:
            raise GenerationError("选中的个人字库无法使用，请重新选择或导入：" + str(error)) from None
        natural = NaturalOptions(self.natural_enabled.isChecked(), self.natural_strength.value() / 100, self.natural_seed.value(), self.natural_spacing.value() / 100, self.natural_baseline.value() / 100)
        return WritingOptions(package, self.latin_style.currentData(), self.stroke_fallback.isChecked(), natural, identity if identity in BUILTIN_FONTS else "")

    def edit_stroke_font(self):
        identity = self.stroke_font.currentData()
        dialog = StrokeFontDialog(identity if identity not in BUILTIN_FONTS else "", self, self.font_store)
        dialog.exec()
        self.refresh_stroke_fonts(dialog.selected_id or identity)
        self.update_mode()

    def shuffle_natural(self):
        self.natural_enabled.setChecked(True)
        self.natural_seed.setValue(secrets.randbelow(2147483646) + 1)

    def show_handwriting_samples(self):
        dialog = FunctionDialog(self)
        dialog.setWindowTitle("手写字体 · 实际单线轨迹对照")
        dialog.resize(1020, 760)
        layout = QVBoxLayout(dialog)
        tip = QLabel("以下是各字库生成的实际单线字形；回到文字窗口选择对应字库。英文与数字继续由英文样式控制。")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        column = QVBoxLayout(content)
        for number in (1, 2):
            pixmap = QPixmap(str(DATA / f"centerline/手写字库效果预览{number}.png"))
            if not pixmap.isNull():
                label = QLabel()
                label.setPixmap(pixmap.scaledToWidth(940, Qt.TransformationMode.SmoothTransformation))
                column.addWidget(label)
        scroll.setWidget(content)
        layout.addWidget(scroll)
        layout.addWidget(self.button("关闭", dialog.accept))
        dialog.exec()

    def show_stroke_license(self):
        dialog = FunctionDialog(self)
        dialog.setWindowTitle("离线笔画字库 · 来源与许可")
        dialog.resize(780, 600)
        layout = QVBoxLayout(dialog)
        browser = QTextBrowser()
        files = [DATA / "来源与许可.txt", DATA / "ARPHICPL.TXT", DATA / "HERSHEY-LICENSE.txt", DATA / "centerline/来源与许可.txt", DATA / "centerline/MIT-LICENSE.txt", DATA / "centerline/Source-Han-Sans-OFL.txt", DATA / "centerline/Hanazono-LICENSE.txt"]
        handwriting_notes = DATA / "centerline/手写字库来源与许可.txt"
        if handwriting_notes.exists():
            files.append(handwriting_notes)
            files.extend(sorted((DATA / "centerline/handwriting-licenses").glob("*.txt")))
        browser.setPlainText("\n\n".join(path.read_text(encoding="utf-8") if path.exists() else f"缺少许可文件：{path.name}" for path in files))
        layout.addWidget(browser)
        close = QPushButton("关闭")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def update_mode(self, *args):
        strokes = self.is_stroke_mode()
        filling = self.mode.currentData() in ("fill", "outline_fill")
        self.spacing.setEnabled(filling)
        self.direction.setEnabled(filling)
        hints = {
            "strokes": "使用离线单线楷体笔画＋Hershey英文/数字。整字按字库笔顺写完后再写下一个字；连续折笔不抬笔，不填充，不反转或重排笔画。缺字会明确提示。",
            "outline": "沿黑色区域边界描线；普通字体会得到空心轮廓字。",
            "outline_fill": "先描边再填充；边缘可能有重复着墨。",
            "fill": "连续蛇形填线，保留孔洞；间距直接决定相邻填线的距离。",
            "centerline": "提取黑色图案骨架；不是自动识别手写笔顺，交叉处可能产生分叉。"
        }
        self.mode_hint.setText(hints[self.mode.currentData()])
        if self.kind == "text":
            identity = self.stroke_font.currentData()
            centerline = identity in BUILTIN_FONTS
            self.stroke_fallback.setEnabled(bool(identity) and not centerline)
            self.stroke_style.setText("字形：" + (BUILTIN_FONTS[identity] + "；自动中线数据，保留逐字顺序，不保证标准笔顺。" if centerline else "当前个人字库或内置楷体，使用原数据的分笔、顺序与方向。") + "系统字体选择只对轮廓/填充/骨架模式生效；来源及许可见说明。")
            if strokes and centerline:
                info = font_info(identity)
                coverage = info.get("gb2312_hanzi")
                if info.get("handwriting"):
                    self.stroke_style.setText(f"字形：{info['name']}；来源：{info['source_font']}。\n共{info['glyphs']}个可绘制字符，GB2312汉字覆盖{coverage}/6763；缺字会提示。保留手写外观，逐字书写，不保证标准笔顺。")
                detail = f" 来源：{info['source_font']}；GB2312汉字覆盖{coverage}/6763。" if coverage is not None else ""
                self.mode_hint.setText("使用所选中文中线字体＋Hershey英文；每字完成后再写下一字。中线数据不能保证标准笔顺，可能有分叉或重复描线；优先用小样验证。" + detail)
            for field in (self.font, self.bold, self.minimum, self.tolerance, self.optimize):
                field.setEnabled(not strokes)
            self.stroke_style.setVisible(strokes)
            self.stroke_controls.setVisible(strokes)
            self.text_pen_width.setEnabled(strokes)
            for field in (self.threshold, self.invert, self.crop):
                field.setEnabled(not self.is_page_layout() and not strokes)
            self.optimize.setToolTip("笔画模式固定保留笔顺；其他文字模式仅在同一字内部优化。")
            self.optimize.setText("优化字内路径，保持逐字完成")
            self.minimum.setToolTip("笔画模式保留全部笔画和标点，不做短线过滤。")
            self.text_changed()
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
        self.text_document = None
        index = self.font_preset.findData(self.font_size.value())
        self.font_preset.blockSignals(True)
        self.font_preset.setCurrentIndex(max(0, index))
        self.font_preset.blockSignals(False)
        size_note = "已采用Word段落字号；未读取到字号的段落使用上方设置。" if self.paragraph_font_sizes is not None and self.use_document_font_sizes.isChecked() else "字形实际高度因字体而异。"
        self.font_hint.setText(f"上方字号的字框约{self.font_size.value() * 25.4 / 72:.2f} mm。{size_note}")
        self.page_bar.setEnabled(False)
        if self.is_page_layout():
            self.preview.set_text_page(None)
        self.invalidate()
        self.text_timer.start()
        self.document_tools.invalidate_coverage()

    def text_content_changed(self):
        if not self._loading_document:
            count = self.text.document().blockCount()
            if any(values is not None and len(values) != count for values in (self.text_paragraph_styles, self.paragraph_font_sizes)):
                self.set_document_format(None, None)
                self.document_tools.document_note.setText("正文段落数量已改变，现按上位机的字号和对齐设置排版。需要原Word段落格式时可重新导入。")
        self.text_changed()

    def set_document_format(self, alignments, sizes, use_sizes=True):
        self.text_paragraph_styles = alignments
        self.paragraph_font_sizes = sizes
        self.use_document_font_sizes.setVisible(sizes is not None)
        self.use_document_font_sizes.setChecked(sizes is not None and use_sizes)
        self.sync_paragraph_alignment()

    def sync_paragraph_alignment(self):
        use_word = self.is_page_layout() and self.text_paragraph_styles is not None
        self.alignment.setEnabled(not use_word)
        self.alignment.setToolTip("正文保留Word逐段对齐；全局对齐仅用于无段落格式的正文和文字图案。" if use_word else "适用于正文所有段落。")

    def update_text_preview(self):
        self.text_timer.stop()
        try:
            self.refresh_text_source()
        except GenerationError as error:
            self.source_preview.clear()
            self.binary_preview.clear()
            self.processed_image = None
            self.enlarge_button.setEnabled(False)
            self.status.setText(str(error))

    def refresh_text_source(self):
        self.document_tools.update_coverage()
        if self.is_page_layout():
            if self.text_document is None:
                sizes = self.paragraph_font_sizes if self.use_document_font_sizes.isChecked() else None
                if self.is_stroke_mode():
                    self.text_document = layout_stroke_text(self.text.toPlainText(), self.text_options(), self.text_pen_width.value(), self.writing_options(), self.text_paragraph_styles, sizes)
                else:
                    self.text_document = layout_text(self.text.toPlainText(), self.font.currentFont().family(), self.bold.isChecked(), self.text_options(), self.text_paragraph_styles, sizes)
            self.page_number.blockSignals(True)
            self.page_number.setRange(1, len(self.text_document.pages))
            self.page_number.blockSignals(False)
            self.document_tools.sync_pages(len(self.text_document.pages))
            self.page_bar.setEnabled(True)
            self.show_text_page()
            return
        self.preview.set_text_page(None)
        if self.is_stroke_mode():
            self.source = render_stroke_source(self.text.toPlainText(), self.line_spacing.value(), self.alignment.currentData(), self.text_pen_width.value(), self.writing_options())
            self.ratio = self.source.width() / max(1, self.source.height())
            self.change_size(self.size_from_width)
            self.show_source(self.source)
            self.show_processed(self.source)
            self.binary_status.setText("离线单线笔画预览；无需黑白阈值或填充。")
            return
        self.source = render_text(self.text.toPlainText(), self.font.currentFont().family(), self.bold.isChecked(), self.line_spacing.value(), self.alignment.currentData())
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
            mode="outline" if self.is_stroke_mode() else self.mode.currentData(), threshold=self.threshold.value(), invert=self.invert.isChecked(), crop=self.crop.isChecked(),
            spacing=self.spacing.value(), vertical=self.direction.currentIndex() == 1, tolerance=self.tolerance.value(),
            min_length=self.minimum.value(), optimize=False if self.is_stroke_mode() else self.optimize.isChecked(), draw_feed=self.draw_feed.value(),
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
        if self.is_stroke_mode():
            options.invert, options.crop = False, True
        if self.is_page_layout():
            options.x, options.y = 0, 0
            options.width, options.height = self.profile.paper_width, self.profile.paper_height
            options.crop, options.invert = False, False
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
            artwork_page = None
            if self.kind == "text" and not self.is_page_layout() and not options.invert:
                if self.is_stroke_mode():
                    artwork_page = layout_stroke_artwork(self.text.toPlainText(), self.line_spacing.value(), self.alignment.currentData(), options, self.text_pen_width.value(), self.writing_options())
                else:
                    artwork_page = layout_artwork(self.text.toPlainText(), self.font.currentFont().family(), self.bold.isChecked(), self.line_spacing.value(), self.alignment.currentData(), options)
        except (ValueError, OSError) as error:
            self.status.setText(str(error))
            return
        self.progress.setValue(0)
        self.cancel_requested = False
        self.preview_timer.stop()
        if self.is_page_layout():
            number = self.page_number.value()
            page = self.text_document.pages[number - 1]
            if page.bounds.isEmpty():
                self.status.setText("当前页为空白页，没有可绘制文字，请选择其他页或减少空行。")
                return
            self.worker = GenerationWorker(self.source, options, f"正文任务_第{number:03d}页", self, text_page=page, text_options=self.text_document.options)
        elif self.kind == "text" and not options.invert:
            self.worker = GenerationWorker(self.source, options, "文字任务", self, text_page=artwork_page)
        else:
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
        if self.kind == "text":
            self.page_bar.setEnabled(not busy and self.text_document is not None)
            self.document_tools.set_busy(busy)
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
            f"XY范围 {task.program.bounds} · 路径处理精度约 {task.resolution:.3f} mm\n" + "\n".join(task.notes)
        )
        self.status.setText("生成完成。检查尺寸和轨迹后可保存，或载入主窗口；尚未发送。")
        if self.kind == "text":
            # 结果说明改变预览高度后再定位，避免文字刚生成便落在可见区域外。
            QTimer.singleShot(0, self.preview.fit_job)

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
            self.close_pending = False
            self.reject()

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

    def preference_data(self):
        data = {name: getattr(self, name).value() for name in ("width", "height", "x", "y", "spacing", "tolerance", "minimum", "draw_feed", "travel_feed", "up_feed", "down_feed", "threshold")}
        data.update({name: getattr(self, name).isChecked() for name in ("invert", "crop", "aspect", "optimize")})
        data.update(mode=self.mode.currentData(), direction=self.direction.currentIndex())
        if self.kind == "image":
            data.update(processing=self.processing.currentData(), sensitivity=self.sensitivity.value(), detail=self.detail.value(), smoothing=self.smoothing.value(), despeckle=self.despeckle.value(), repair=self.repair.isChecked())
        if self.kind == "text":
            data.update(text=self.text.toPlainText(), font=self.font.currentFont().family(), bold=self.bold.isChecked(), line_spacing=self.line_spacing.value(), alignment=self.alignment.currentData())
            data.update(text_pen_width=self.text_pen_width.value())
            data.update(stroke_font=self.stroke_font.currentData(), latin_style=self.latin_style.currentData(), stroke_fallback=self.stroke_fallback.isChecked(), natural_enabled=self.natural_enabled.isChecked(), natural_strength=self.natural_strength.value(), natural_seed=self.natural_seed.value())
            data.update(natural_spacing=self.natural_spacing.value(), natural_baseline=self.natural_baseline.value())
            data.update(layout_mode=self.layout_mode.currentData())
            data.update({name: getattr(self, name).value() for name in ("font_size", "margin_left", "margin_right", "margin_top", "margin_bottom", "letter_spacing", "paragraph_spacing", "indent")})
        return data

    def save_preferences(self):
        if not self.remember.isChecked():
            return
        data = self.preference_data()
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

    def restore_preferences(self, stored=None):
        try:
            if stored is None:
                stored = json.loads(PREFERENCES.read_text(encoding="utf-8")).get(self.kind, {})
            for name in ("width", "height", "x", "y", "spacing", "tolerance", "minimum", "draw_feed", "travel_feed", "up_feed", "down_feed", "threshold"):
                if name in stored and type(stored[name]) in (int, float):
                    getattr(self, name).setValue(stored[name])
            for name in ("invert", "crop", "aspect", "optimize"):
                if type(stored.get(name)) is bool:
                    getattr(self, name).setChecked(stored[name])
            index = self.mode.findData(stored.get("mode"))
            # 旧实心书写入口迁移到笔画；显式保存的轮廓/填充/中线偏好仍保留。
            if self.kind == "text" and stored.get("mode") == "font":
                index = self.mode.findData("strokes")
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
                self.refresh_stroke_fonts(stored.get("stroke_font", ""))
                index = self.latin_style.findData(stored.get("latin_style"))
                if index >= 0:
                    self.latin_style.setCurrentIndex(index)
                if type(stored.get("stroke_fallback")) is bool:
                    self.stroke_fallback.setChecked(stored["stroke_fallback"])
                self.natural_enabled.setChecked(stored.get("natural_enabled") is True)
                if type(stored.get("natural_strength")) in (int, float):
                    self.natural_strength.setValue(stored["natural_strength"])
                    self.natural_spacing.setValue(stored["natural_strength"])
                    self.natural_baseline.setValue(stored["natural_strength"])
                for name in ("natural_spacing", "natural_baseline"):
                    if type(stored.get(name)) in (int, float):
                        getattr(self, name).setValue(stored[name])
                if type(stored.get("natural_seed")) is int:
                    self.natural_seed.setValue(stored["natural_seed"])
                if type(stored.get("text_pen_width")) in (int, float):
                    self.text_pen_width.setValue(stored["text_pen_width"])
                if isinstance(stored.get("text"), str):
                    self.text.setPlainText(stored["text"][:20000])
                if isinstance(stored.get("font"), str):
                    self.font.setCurrentFont(QFont(stored["font"]))
                self.bold.setChecked(stored.get("bold") is True)
                if type(stored.get("line_spacing")) in (int, float):
                    self.line_spacing.setValue(stored["line_spacing"])
                index = self.alignment.findData(stored.get("alignment"))
                if index >= 0:
                    self.alignment.setCurrentIndex(index)
                index = self.layout_mode.findData(stored.get("layout_mode"))
                if index >= 0:
                    self.layout_mode.setCurrentIndex(index)
                for name in ("font_size", "margin_left", "margin_right", "margin_top", "margin_bottom", "letter_spacing", "paragraph_spacing", "indent"):
                    if type(stored.get(name)) in (int, float):
                        getattr(self, name).setValue(stored[name])
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
        if self.kind == "text" and not self.document_tools.confirm_close():
            return
        super().reject()

    def closeEvent(self, event):
        self.preview_timer.stop()
        self.text_timer.stop()
        if self.worker is not None or self.image_worker is not None:
            self.close_pending = True
            self.cancel_generation()
            event.ignore()
            return
        # 先阻止原始关闭事件，避免确认框点击“取消”后 Qt 继续接受本次 close。
        event.ignore()
        self.reject()
        if not self.isVisible():
            event.accept()
