import logging
import re
import time
from collections import deque
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSplitter,
    QVBoxLayout, QWidget, QFileDialog
)

from app.controller import GrblController
from app.job import JobRunner
from app.loader import ProgramLoader
from app.profile import ROOT, MachineProfile
from app.transport import SIMULATOR, SerialTransport
from app.ui.preview_widget import PreviewWidget
from app.ui.settings_dialog import SettingsDialog
from app.ui.generation_dialog import GenerationDialog
from app.generation import GenerationError, save_gcode


STYLE = """
QMainWindow, QDialog { background: #eef3f9; }
QWidget { font-family: 'Microsoft YaHei UI'; font-size: 9pt; color: #25344c; }
QGroupBox { background: white; border: 1px solid #d8e2f0; border-radius: 5px; margin-top: 9px; padding: 5px 7px 4px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
QPushButton { background: #e8effb; border: 1px solid #c9d8ee; border-radius: 4px; padding: 2px 6px; min-height: 16px; }
QPushButton:hover { background: #d6e5fc; }
QPushButton:pressed { background: #c0d6fb; }
QPushButton:disabled { color: #98a6b8; background: #edf1f6; border-color: #e0e6ee; }
QPushButton#primary { color: white; background: #2563eb; border-color: #2563eb; }
QPushButton#primary:disabled { background: #b4c7ec; border-color: #b4c7ec; }
QPushButton#danger { background: #fee8e7; color: #b42318; border-color: #f5c3bf; }
QPushButton#danger:disabled { color: #c7a6a4; background: #f6eeee; }
QLineEdit, QComboBox, QDoubleSpinBox { background: white; border: 1px solid #cbd7e7; border-radius: 3px; padding: 2px; min-height: 16px; }
QPlainTextEdit { border: 1px solid #cbd7e7; border-radius: 5px; }
QProgressBar { border: 1px solid #d5dfed; border-radius: 4px; background: white; text-align: center; min-height: 14px; max-height: 18px; font-size: 9pt; }
QProgressBar::chunk { background: #60a5fa; }
QScrollArea { border: none; background: transparent; }
QSplitter::handle { background: #e0e7f1; }
QCheckBox { font-weight: normal; }
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.profile, warning = MachineProfile.load()
        self.profile_loaded_clean = not warning
        self.controller = GrblController(self.profile, self)
        self.job = JobRunner(self.controller, self)
        self.loader = None
        self.loading = False
        self.log_buffer = deque(maxlen=2000)
        self.suppressed_lines = 0
        self.summary_at = time.monotonic()
        self.banner_style = None
        self.displayed_program = None
        self.view_timer = QTimer(self)
        self.view_timer.setSingleShot(True)
        self.view_timer.setInterval(50)
        self.view_timer.timeout.connect(self.update_view)
        self.log_timer = QTimer(self)
        self.log_timer.setInterval(100)
        self.log_timer.timeout.connect(self.flush_log)
        self.setWindowTitle("XY写字机上位机 · 第三阶段")
        self.resize(1400, 930)
        self.setMinimumSize(1120, 760)
        self.setStyleSheet(STYLE)
        self.logger = logging.getLogger("xy_host")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        (ROOT / "logs").mkdir(exist_ok=True)
        self.log_handler = RotatingFileHandler(ROOT / "logs" / "host.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        self.log_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
        self.logger.addHandler(self.log_handler)
        self.build_ui()
        self.log_timer.start()
        self.bind_signals()
        self.refresh_ports()
        self.update_view()
        self.append_log("APP", "第三阶段就绪：文字/图片生成、连续填充、中线提取、G代码导出；生成后预览，手动开始发送。")
        self.append_log("APP", "所有配置、日志和运行文件保存在当前上位机目录。连接后不会自动运动。")
        if warning:
            self.show_notice(warning)

    def button(self, text, callback=None, primary=False):
        widget = QPushButton(text)
        if primary:
            widget.setObjectName("primary")
        if callback:
            widget.clicked.connect(callback)
        return widget

    def number(self, value, minimum, maximum, decimals=2):
        widget = QDoubleSpinBox()
        widget.setDecimals(decimals)
        widget.setRange(minimum, maximum)
        widget.setValue(value)
        return widget

    def build_ui(self):
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(10, 8, 10, 4)
        layout.setSpacing(5)
        self.setCentralWidget(root)

        header = QHBoxLayout()
        title_column = QHBoxLayout()
        title = QLabel("XY 写字机")
        title.setStyleSheet("font-size: 21px; font-weight: 700; color: #162c50;")
        title_column.addWidget(title)
        title_column.addWidget(QLabel("STM32G431 · GRBL 1.1h"))
        header.addLayout(title_column)
        header.addStretch()
        self.import_button = self.button("导入G代码", self.import_file, True)
        header.addWidget(self.import_button)
        self.image_button = self.button("图片生成", lambda: self.open_generator("image"))
        self.text_button = self.button("文字生成", lambda: self.open_generator("text"))
        header.addWidget(self.image_button)
        header.addWidget(self.text_button)
        self.start_button = self.button("开始发送", self.start_job, True)
        self.pause_button = self.button("暂停 !", self.controller.pause)
        self.resume_button = self.button("继续 ~", self.controller.resume)
        self.stop_button = self.button("中止任务", self.stop_job)
        self.stop_button.setObjectName("danger")
        for widget in (self.start_button, self.pause_button, self.resume_button, self.stop_button):
            header.addWidget(widget)
        self.settings_button = self.button("设备 / Z配置", self.edit_settings)
        header.addWidget(self.settings_button)
        layout.addLayout(header)

        progress_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.execution_progress = QProgressBar()
        progress_row.addWidget(QLabel("发送"))
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(QLabel("绘制"))
        progress_row.addWidget(self.execution_progress, 1)
        layout.addLayout(progress_row)

        self.connection_banner = QLabel("未连接 · 请先选择真实串口，或使用模拟设备验证界面")
        self.connection_banner.setStyleSheet("background: #e4ecf9; border-radius: 4px; padding: 4px; color: #27466f;")
        layout.addWidget(self.connection_banner)

        split = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(split, 1)
        sidebar = QWidget()
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 5, 0)
        sidebar_layout.setSpacing(4)
        sidebar_layout.addWidget(self.build_connection())
        sidebar_layout.addWidget(self.build_paper())
        sidebar_layout.addWidget(self.build_controls())
        sidebar_layout.addWidget(self.build_console())
        sidebar_layout.addWidget(self.build_file_info())
        sidebar_layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(sidebar)
        scroll.setMinimumWidth(345)
        scroll.setMaximumWidth(420)
        self.sidebar_scroll = scroll
        split.addWidget(scroll)

        right = QSplitter(Qt.Orientation.Vertical)
        preview_group = QGroupBox("XY轨迹预览 · G54工作坐标")
        preview_layout = QVBoxLayout(preview_group)
        preview_toolbar = QHBoxLayout()
        self.travel_check = QCheckBox("显示空行程")
        preview_toolbar.addWidget(self.travel_check)
        preview_toolbar.addStretch()
        self.preview = PreviewWidget()
        preview_toolbar.addWidget(self.button("−", lambda: self.preview.change_zoom(1 / 1.2)))
        preview_toolbar.addWidget(self.button("+", lambda: self.preview.change_zoom(1.2)))
        preview_toolbar.addWidget(self.button("适配", self.preview.fit))
        preview_toolbar.addWidget(self.button("放大图案", self.preview.fit_job))
        self.travel_check.toggled.connect(self.toggle_travel)
        preview_layout.addLayout(preview_toolbar)
        preview_layout.addWidget(self.preview, 1)
        self.modal_label = QLabel("模态：未知")
        self.modal_label.setWordWrap(True)
        preview_layout.addWidget(self.modal_label)
        right.addWidget(preview_group)

        log_group = QGroupBox("通信与操作日志")
        log_layout = QVBoxLayout(log_group)
        bar = QHBoxLayout()
        self.show_status = QCheckBox("显示周期状态报告")
        bar.addWidget(self.show_status)
        self.show_job_details = QCheckBox("显示逐行收发")
        self.show_job_details.setToolTip("排查具体指令时开启；默认每秒显示一次任务摘要，减少大文件日志开销。")
        bar.addWidget(self.show_job_details)
        bar.addStretch()
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        self.log_view.setFont(QFont("Consolas", 10))
        self.log_view.setStyleSheet("background: #101827; color: #dbeafe; padding: 6px;")
        bar.addWidget(self.button("清空显示", self.clear_log))
        log_layout.addLayout(bar)
        log_layout.addWidget(self.log_view)
        right.addWidget(log_group)
        right.setSizes([530, 230])
        split.addWidget(right)
        split.setSizes([355, 1025])
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)

        self.notice_label = QLabel("先在模拟设备中熟悉流程；真实设备运动使用已验证参数。")
        self.notice_label.setWordWrap(True)
        self.notice_label.setStyleSheet("color: #8a4b08; padding: 4px;")
        layout.addWidget(self.notice_label)

    def build_connection(self):
        group = QGroupBox("串口连接与状态")
        layout = QGridLayout(group)
        layout.setVerticalSpacing(2)
        self.port_combo = QComboBox()
        self.refresh_button = self.button("刷新", self.refresh_ports)
        self.baud_combo = QComboBox()
        for baud in (115200, 230400, 57600, 38400, 19200, 9600):
            self.baud_combo.addItem(str(baud), baud)
        self.baud_combo.setCurrentIndex(self.baud_combo.findData(self.profile.baud_rate))
        self.connect_button = self.button("连接", self.toggle_connection, True)
        self.query_button = self.button("查询 ?", self.controller.query_status)
        layout.addWidget(QLabel("端口"), 0, 0)
        layout.addWidget(self.port_combo, 0, 1)
        layout.addWidget(self.refresh_button, 0, 2)
        layout.addWidget(QLabel("波特率"), 1, 0)
        layout.addWidget(self.baud_combo, 1, 1)
        layout.addWidget(self.connect_button, 1, 2)
        self.state_label = QLabel()
        self.machine_label = QLabel()
        self.work_label = QLabel()
        self.pins_label = QLabel()
        self.limits_label = QLabel("速度上限：未读取")
        layout.addWidget(self.query_button, 2, 2)
        layout.addWidget(self.state_label, 2, 0, 1, 2)
        for row, label in enumerate((self.machine_label, self.work_label, self.pins_label, self.limits_label), 3):
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(label, row, 0, 1, 3)
        return group

    def build_paper(self):
        group = QGroupBox("纸张区域 · mm")
        layout = QGridLayout(group)
        self.paper_width = self.number(self.profile.paper_width, 1, 1000)
        self.paper_height = self.number(self.profile.paper_height, 1, 1000)
        layout.addWidget(QLabel("宽 mm"), 0, 0)
        layout.addWidget(self.paper_width, 0, 1)
        layout.addWidget(QLabel("高 mm"), 0, 2)
        layout.addWidget(self.paper_height, 0, 3)
        layout.addWidget(self.button("应用并保存", self.save_paper), 1, 0, 1, 4)
        return group

    def build_controls(self):
        group = QGroupBox("设备控制")
        layout = QGridLayout(group)
        layout.setVerticalSpacing(2)
        self.jog_step = self.number(self.profile.jog_step, 0.01, 50)
        self.jog_feed = self.number(self.profile.jog_feed, 1, 10000, 0)
        row = QHBoxLayout()
        row.addWidget(QLabel("步长mm"))
        row.addWidget(self.jog_step)
        row.addWidget(QLabel("进给mm/min"))
        row.addWidget(self.jog_feed)
        layout.addLayout(row, 0, 0, 1, 3)
        self.baseline_check = QCheckBox("已建立Z基准（勾选不清零）")
        self.baseline_check.setToolTip("仅确认STM32当前机器Z坐标已对应纸面基准。不能靠勾选改变坐标，也不会发送G92或G10。")
        self.z_mode_label = QLabel()
        baseline_row = QHBoxLayout()
        baseline_row.addWidget(self.baseline_check, 1)
        baseline_row.addWidget(self.z_mode_label)
        layout.addLayout(baseline_row, 1, 0, 1, 3)
        self.jog_buttons = []
        for text, axis, sign, row, column in (
            ("Y− ↑", "Y", -1, 3, 1), ("X− ←", "X", -1, 4, 0),
            ("X+ →", "X", 1, 4, 2), ("Y+ ↓", "Y", 1, 5, 1),
            ("Z+", "Z", 1, 3, 2), ("Z−", "Z", -1, 5, 2)
        ):
            button = self.button(text, lambda checked=False, axis=axis, sign=sign: self.jog(axis, sign))
            self.jog_buttons.append((axis, button))
            layout.addWidget(button, row, column)
        self.pen_up_button = self.button("抬笔", lambda: self.controller.pen(True))
        self.pen_down_button = self.button("落笔", lambda: self.controller.pen(False))
        self.home_button = self.button("XY回零 $H", self.home)
        self.return_button = self.button("回G54 XY原点", self.controller.return_origin)
        self.origin_button = self.button("设置G54 XY原点", self.set_origin)
        self.read_settings_button = self.button("读取设置 $$", lambda: self.controller.send("$$", "read"))
        self.unlock_button = self.button("解锁 $X", self.unlock)
        self.reset_button = self.button("软复位 0x18", self.reset)
        self.reset_button.setObjectName("danger")
        pairs = (
            (self.pen_up_button, self.pen_down_button),
            (self.home_button, self.return_button),
            (self.origin_button, self.read_settings_button),
            (self.unlock_button, self.reset_button)
        )
        for row, (first, second) in enumerate(pairs, 6):
            layout.addWidget(first, row, 0)
            layout.addWidget(second, row, 1, 1, 2)
        self.jog_cancel_button = self.button("取消点动", self.controller.cancel_jog)
        layout.addWidget(self.jog_cancel_button, 5, 0)
        return group

    def build_console(self):
        group = QGroupBox("控制台 · 单行命令")
        layout = QGridLayout(group)
        self.command_input = QLineEdit()
        self.command_input.setPlaceholderText("例如 $$、$G、$#、$I")
        self.command_send = self.button("发送", self.send_console)
        self.command_input.returnPressed.connect(self.send_console)
        layout.addWidget(self.command_input, 0, 0, 1, 3)
        layout.addWidget(self.command_send, 0, 3)
        self.read_buttons = []
        for column, (label, command) in enumerate((("帮助 $", "$"), ("模态 $G", "$G"), ("坐标 $#", "$#"), ("信息 $I", "$I"))):
            button = self.button(label, lambda checked=False, command=command: self.controller.send(command, "read"))
            self.read_buttons.append(button)
            layout.addWidget(button, 1, column)
        return group

    def bind_signals(self):
        self.controller.changed.connect(self.schedule_view)
        self.controller.log.connect(self.append_log)
        self.controller.notice.connect(self.show_notice)
        self.job.changed.connect(self.schedule_view)
        self.baseline_check.toggled.connect(self.controller.confirm_baseline)
        self.jog_step.valueChanged.connect(lambda value: setattr(self.profile, "jog_step", value))
        self.jog_feed.valueChanged.connect(lambda value: setattr(self.profile, "jog_feed", value))

    def schedule_view(self):
        # 串口应答只推进发送状态，界面最多每50ms合并刷新一次。
        if not self.view_timer.isActive():
            self.view_timer.start()

    def build_file_info(self):
        group = QGroupBox("G代码任务")
        layout = QVBoxLayout(group)
        layout.setSpacing(4)
        self.file_label = QLabel("未载入文件 · 点击顶部“导入G代码”")
        self.file_label.setWordWrap(True)
        self.file_details = QLabel("支持 .nc / .ngc / .gcode / .tap / .txt")
        self.file_details.setWordWrap(True)
        self.job_label = QLabel("等待文件")
        self.job_label.setWordWrap(True)
        self.file_warning = QLabel()
        self.file_warning.setWordWrap(True)
        self.file_warning.setStyleSheet("color: #9a5a10; font-size: 9pt;")
        for widget in (self.file_label, self.file_details, self.job_label, self.file_warning):
            layout.addWidget(widget)
        self.export_button = self.button("导出当前G代码…", self.export_program)
        layout.addWidget(self.export_button)
        return group

    def open_generator(self, kind):
        if self.loading or self.job.active:
            return
        dialog = GenerationDialog(self.profile, kind, self)
        if dialog.exec() == GenerationDialog.DialogCode.Accepted and dialog.loaded_task:
            self.load_generated(dialog.loaded_task)
        dialog.deleteLater()

    def load_generated(self, task):
        if self.loading or self.job.active:
            return
        self.job.load(task.program)
        self.preview.set_program(task.program)
        self.preview.fit_job()
        self.append_log("生成", f"{task.program.path.name}：{task.path_count}条路径，{len(task.program.commands)}条命令，未发送。")
        self.show_notice("生成任务已载入。Z/F固定在本次文件中；确认机器原点、Z基准并抬笔后，点击“开始发送”。")
        self.update_view()

    def export_program(self, checked=False, path=None):
        if not self.job.program or self.job.active or self.loading:
            return
        if path is None:
            folder = ROOT / "exports"
            folder.mkdir(exist_ok=True)
            path, _ = QFileDialog.getSaveFileName(self, "导出当前G代码（上位机目录内）", str(folder / self.job.program.path.name), "G代码 (*.ngc *.nc)")
        if not path:
            return
        try:
            target = save_gcode(self.job.program, path)
            self.show_notice(f"已导出：{target}。未发送。")
        except (OSError, GenerationError) as error:
            self.show_notice("导出失败：" + str(error))

    def toggle_travel(self, checked):
        self.preview.show_travel = checked
        self.preview.update()

    def import_file(self, checked=False, path=None):
        if self.loading or self.job.active:
            return
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "导入G代码（不会自动发送）", str(ROOT), "G代码 (*.nc *.ngc *.gcode *.gc *.tap *.txt);;所有文件 (*)")
        if not path:
            return
        self.begin_load(path)

    def begin_load(self, path, for_start=False):
        self.loading = True
        self.show_notice("正在分析文件…" if not for_start else "正在按当前机器坐标检查任务…")
        context = {"pen_threshold": (self.profile.pen_up + self.profile.pen_down) / 2}
        source = None
        if for_start:
            controller = self.controller
            if self.job.program.preview_pen_threshold is not None:
                context["pen_threshold"] = self.job.program.preview_pen_threshold
            context["initial_work"] = controller.work
            context["g54_offset"] = controller.g54_offset
            source = self.job.program.source_text
            self.start_snapshot = (controller.transport, controller.machine, controller.offset)
        self.loader = ProgramLoader(path, source, context, self)
        loader = self.loader
        loader.loaded.connect(lambda program: self.program_loaded(program, for_start))
        loader.failed.connect(self.program_failed)
        loader.finished.connect(lambda: self.release_loader(loader))
        loader.start()
        self.update_view()

    def release_loader(self, loader):
        if self.loader is loader:
            self.loader = None
        loader.deleteLater()

    def program_failed(self, message):
        self.loading = False
        # 导入失败时保留原文件，但明确说明没有替换成功。
        self.show_notice("文件分析失败，未载入新任务：" + message)
        self.update_view()

    def program_loaded(self, program, for_start):
        self.loading = False
        if not for_start:
            self.job.load(program)
            self.preview.set_program(program)
            self.preview.fit()
            self.append_log("FILE", f"已载入 {program.path.name}，{len(program.commands)} 条命令；原文件保持不变。")
            self.show_notice("导入完成，仅预览；检查范围及Z高度后点击“开始发送”。")
        else:
            program.preview_pen_threshold = self.job.program.preview_pen_threshold
            if not self.start_context_valid():
                self.show_notice("检查期间设备状态/坐标发生变化，请等待Idle后重新点击开始。")
                self.update_view()
                return
            if program.z_bounds and not self.profile.z_min <= program.z_bounds[0] <= program.z_bounds[1] <= self.profile.z_max:
                self.show_notice(f"文件机器Z范围 {program.z_bounds} 超出设备配置 {self.profile.z_min}～{self.profile.z_max}，未发送。")
                self.update_view()
                return
            issues = list(program.warnings)
            if program.bounds:
                x0, y0, x1, y1 = program.bounds
                if x0 < -0.01 or y0 < -0.01 or x1 > self.profile.paper_width + 0.01 or y1 > self.profile.paper_height + 0.01:
                    issues.append("部分路径（含当前位置的进场线）超出预览纸张，请核实实际可用行程。")
            summary = f"文件：{program.path.name}\n命令：{len(program.commands)}条\n机器Z范围：{program.z_bounds}\nZ/F按文件原值执行；手动按钮参数不会改写文件。\n"
            if issues:
                summary += "\n" + "\n".join(issues) + "\n"
            summary += "\n已回零、建立G54纸张原点和Z基准，并确认笔尖离纸？开始发送？"
            if self.confirm("开始文件任务", summary) and self.start_context_valid():
                self.job.load(program)
                self.preview.set_program(program)
                self.job.start()
        self.update_view()

    def start_context_valid(self):
        controller = self.controller
        snapshot = (controller.transport, controller.machine, controller.offset)
        return controller.available and controller.baseline_confirmed and controller.pen_clear() and snapshot == self.start_snapshot

    def start_job(self):
        controller = self.controller
        if self.loading or not self.job.program or not controller.require_idle():
            return
        if self.profile.z_mode != "stepper":
            self.show_notice("本阶段文件通道支持步进Z。舵机手动命令已预留，文件转换需配套固件协议，暂不把Z文件直接发给舵机。")
            return
        if not controller.require_z_ready() or not controller.pen_clear():
            self.show_notice("请按既定流程建立Z基准、勾选确认并手动抬笔，等待Idle后再开始文件。")
            return
        if controller.work is None or controller.offset is None or controller.g54_offset is None or controller.modal == "未知":
            self.show_notice("请在左侧控制台点击“模态 $G”和“坐标 $#”，等待完整状态后再开始。")
            return
        if len(controller.g54_offset) != 3 or any(abs(a - b) > 0.002 for a, b in zip(controller.offset, controller.g54_offset)):
            self.show_notice("当前WCO与G54偏移不一致。请确认使用G54、清除G92/刀长偏移，再查询$G、$#和?。")
            return
        self.begin_load(self.job.program.path, True)

    def stop_job(self):
        if self.job.active:
            self.controller.pause()
            if self.confirm("中止任务", "已请求暂停。确认后发Ctrl+X软复位。设备可能停在纸面；请重新确认坐标和Z基准。不会从中断行自动继续。是否中止？"):
                self.job.stop()

    def update_job_view(self):
        job = self.job
        total = len(job.program.commands) if job.program else 0
        if job.program and self.displayed_program is not job.program:
            self.displayed_program = job.program
            self.file_label.setText(job.program.path.name)
            self.file_label.setToolTip(str(job.program.path))
            details = f"{total}条命令 · 抬高Z {job.program.lift_count}次"
            if job.program.bounds:
                x0, y0, x1, y1 = job.program.bounds
                details += f"\nX {x0:.2f}～{x1:.2f}  Y {y0:.2f}～{y1:.2f}"
            if job.program.z_bounds:
                details += f"\n机器Z {job.program.z_bounds[0]:g}～{job.program.z_bounds[1]:g} · Z/F取文件值"
            self.file_details.setText(details)
            self.file_warning.setText("文件含配置/输出命令，开始前请核对" if job.program.warnings else "")
            self.file_warning.setToolTip("；".join(job.program.warnings))
        status = "已暂停" if job.paused else "等待Idle确认" if job.active and job.accepted == total else "正在发送" if job.active else "已完成" if job.result == "complete" else "已中止" if job.result == "aborted" else "等待开始"
        self.job_label.setText(f"{status} · {job.elapsed:.0f}s\n已发 {job.sent}/{total} · 应答 {job.accepted}")
        self.job_label.setToolTip(job.message)
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(job.sent)
        self.progress.setFormat(f"已发 {job.sent}/{total} · 应答 {job.accepted}/{total}")
        if job.active:
            self.execution_progress.setRange(0, 0)
            self.execution_progress.setFormat("绘制暂停" if job.paused else "设备执行中，等待Idle")
        else:
            self.execution_progress.setRange(0, 100)
            self.execution_progress.setValue(100 if job.result == "complete" else 0)
            self.execution_progress.setFormat("已确认完成" if job.result == "complete" else "已中止" if job.result == "aborted" else "等待开始")

    def refresh_ports(self):
        previous = self.port_combo.currentData()
        self.port_combo.clear()
        self.port_combo.addItem("请选择串口或模拟设备", None)
        for name, description in SerialTransport.available_ports():
            self.port_combo.addItem(f"{name} · {description}", name)
        self.port_combo.addItem("模拟设备（不连接硬件）", SIMULATOR)
        index = self.port_combo.findData(previous)
        self.port_combo.setCurrentIndex(max(0, index))

    def toggle_connection(self):
        if self.controller.connected:
            if self.job.active or self.controller.pending or self.controller.state not in ("Idle", "Alarm", "Connecting"):
                if not self.confirm("断开连接", "断开串口不等于停止电机。是否先发送暂停，再断开？"):
                    return
                self.controller.pause()
            self.controller.disconnect_device()
            return
        name = self.port_combo.currentData()
        if not name:
            self.show_notice("请先选择串口；也可以选择模拟设备进行软件练习。")
            return
        self.profile.baud_rate = self.baud_combo.currentData()
        self.controller.connect_device(name, self.profile.baud_rate)
        if self.controller.connected:
            self.controller.send("$G", "read")
            self.controller.send("$#", "read")

    def append_log(self, kind, message):
        ordinary_job = self.job.active and (kind == "RX" and message == "ok" or kind == "TX" and not message.startswith(("!", "~", "0x")))
        if ordinary_job and not self.show_job_details.isChecked():
            self.suppressed_lines += 1
            return
        if kind != "STATUS" or self.show_status.isChecked():
            self.log_buffer.append(f"[{kind}] {message}")
        if kind != "STATUS":
            self.logger.info("[%s] %s", kind, message)

    def flush_log(self):
        if self.suppressed_lines and time.monotonic() - self.summary_at >= 1:
            message = f"已发{self.job.sent} · 应答{self.job.accepted} · {self.controller.state}（省略{self.suppressed_lines}条逐行收发）"
            self.log_buffer.append("[任务] " + message)
            self.logger.info("[任务] %s", message)
            self.suppressed_lines = 0
            self.summary_at = time.monotonic()
        if self.log_buffer:
            messages = "\n".join(self.log_buffer)
            self.log_buffer.clear()
            self.log_view.appendPlainText(messages)

    def clear_log(self):
        self.log_buffer.clear()
        self.log_view.clear()

    def show_notice(self, message):
        self.notice_label.setText(message.replace("\n", "  "))
        self.append_log("提示", message)

    @staticmethod
    def coordinate_text(position):
        return "未知（等待坐标/WCO报告）" if position is None else "   ".join(f"{axis} {value:.3f}" for axis, value in zip("XYZ", position))

    def update_view(self):
        self.view_timer.stop()
        controller = self.controller
        connected = controller.connected
        available = controller.available
        normal = connected and controller.fresh and not controller.fault and not controller.awaiting_reset
        can_read = normal and not self.job.active and controller.pending is None and not controller.queue and controller.state in ("Idle", "Alarm", "Check")
        self.connect_button.setText("断开" if connected else "连接")
        for widget in (self.port_combo, self.baud_combo, self.refresh_button):
            widget.setEnabled(not connected)
        self.query_button.setEnabled(connected)
        self.settings_button.setEnabled(not self.loading and (not connected or available))
        label = "模拟设备 · 不会驱动硬件" if connected and controller.simulated else "真实串口 · GRBL控制" if connected else "未连接 · 请选择串口，或使用模拟设备验证界面"
        if connected and not controller.fresh:
            label += " · 等待有效状态报告"
        if controller.fault:
            label += " · 应答失步/报警，普通发送已锁定"
        self.connection_banner.setText(label)
        style = "background: #fff1cf; padding: 4px; border-radius: 4px; color: #814600;" if controller.simulated and connected else "background: #e4ecf9; padding: 4px; border-radius: 4px; color: #27466f;"
        if style != self.banner_style:
            self.connection_banner.setStyleSheet(style)
            self.banner_style = style
        self.state_label.setText(f"状态：{controller.state}" + ("（状态过期）" if connected and not controller.fresh else ""))
        self.machine_label.setText("机器：" + self.coordinate_text(controller.machine))
        self.work_label.setText("工作：" + self.coordinate_text(controller.work))
        self.pins_label.setText(f"输入：{controller.pins or '无有效触发上报'}    进给：{controller.feed if controller.feed is not None else '未知'}")
        if controller.settings:
            values = [f"{axis} {controller.settings.get(key, '?')}" for axis, key in zip("XYZ", (110, 111, 112))]
            self.limits_label.setText("限速mm/min：" + " / ".join(values))
        else:
            self.limits_label.setText("速度上限：未读取（点击读取设置）")
        self.modal_label.setText("模态：" + controller.modal)
        self.preview.set_paper(self.profile.paper_width, self.profile.paper_height)
        self.preview.set_position(controller.work if controller.fresh else None)
        self.baseline_check.blockSignals(True)
        self.baseline_check.setChecked(controller.baseline_confirmed)
        self.baseline_check.blockSignals(False)
        self.baseline_check.setEnabled(available and self.profile.z_mode == "stepper")
        z_ready = controller.baseline_confirmed if self.profile.z_mode == "stepper" else self.profile.servo_firmware_confirmed
        self.z_mode_label.setText("步进Z" if self.profile.z_mode == "stepper" else "舵机Z")
        for axis, button in self.jog_buttons:
            button.setEnabled(available and z_ready and (axis != "Z" or self.profile.z_mode == "stepper"))
        for button in (self.pen_up_button, self.pen_down_button):
            button.setEnabled(available and z_ready)
        for button in (self.home_button, self.return_button, self.origin_button):
            button.setEnabled(available)
        self.read_settings_button.setEnabled(can_read)
        self.unlock_button.setEnabled(can_read and controller.state == "Alarm")
        for button in self.read_buttons:
            button.setEnabled(can_read)
        self.command_input.setEnabled(can_read)
        self.command_send.setEnabled(can_read)
        self.reset_button.setEnabled(connected)
        self.pause_button.setEnabled(connected)
        self.resume_button.setEnabled(normal and controller.state == "Hold:0")
        self.jog_cancel_button.setEnabled(connected and controller.state == "Jog")
        self.import_button.setEnabled(not self.job.active and not self.loading)
        self.image_button.setEnabled(not self.job.active and not self.loading)
        self.text_button.setEnabled(not self.job.active and not self.loading)
        self.export_button.setEnabled(not self.job.active and not self.loading and self.job.program is not None)
        self.start_button.setEnabled(available and not self.loading and self.job.program is not None and z_ready and self.profile.z_mode == "stepper")
        self.stop_button.setEnabled(self.job.active and connected)
        self.update_job_view()
        self.statusBar().showMessage("第三阶段：文字 / 图片 → 路径 → G代码    |    Z/F取文件值    |    状态查询2Hz")

    def confirm(self, title, message):
        return QMessageBox.question(self, title, message, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def jog(self, axis, sign):
        self.controller.jog(axis, sign)

    def home(self):
        if self.controller.pen_clear() and self.confirm("XY回零", "确认笔尖已离纸，XY回零方向和限位均已验证？将发送$H，当前固件只回零XY。"):
            self.controller.home_xy()
        elif not self.controller.pen_clear():
            self.show_notice("先确认Z基准并抬笔；等待Idle后再进行XY回零。")

    def set_origin(self):
        if self.confirm("设置工作原点", "将当前位置设为G54的X0、Y0，并写入设备EEPROM。不会修改Z偏移。是否继续？"):
            self.controller.set_xy_origin()

    def unlock(self):
        if self.confirm("解锁报警", "确认限位已释放且故障原因已排除？$X只解除锁定，不执行回零、不恢复失去的物理位置。"):
            self.controller.send("$X", "unlock")

    def reset(self):
        if self.confirm("软复位", "发送原始0x18并丢弃待发命令。运动中复位后不能认定坐标可靠，需要重新检查/回零。是否继续？"):
            self.controller.reset()

    def send_console(self):
        command = self.command_input.text().strip()
        if command in ("?", "!", "~"):
            {"?": self.controller.query_status, "!": self.controller.pause, "~": self.controller.resume}[command]()
            return
        if self.job.active or not self.controller.fresh or self.controller.pending or self.controller.state not in ("Idle", "Alarm", "Check"):
            self.show_notice("当前仅允许实时查询、暂停和复位；等待空闲和应答后再发普通命令。")
            return
        if command == "$H":
            self.home()
            return
        if command.startswith("$H"):
            self.show_notice("本阶段只通过XY回零按钮发送$H，不提供Z或单轴回零。")
            return
        if command.startswith("$J="):
            self.show_notice("请使用点动按钮，以保留Z基准、范围和运行状态检查。")
            return
        if command == "$X":
            self.unlock()
            return
        if re.search(r"[XYZHV]", command.upper()) and not command.startswith("$"):
            if not self.controller.require_z_ready():
                return
            if not self.confirm("原样发送运动相关命令", "控制台不会对这条命令进行完整轨迹检查。请确认坐标、Z范围及笔尖状态正确。是否原样发送？"):
                return
        if command.startswith("$RST") and not self.confirm("恢复设备参数", "该命令可能清除已有设置或工作原点。是否确定发送？"):
            return
        if self.controller.send(command, "console"):
            self.command_input.clear()

    def save_paper(self):
        self.profile.paper_width = self.paper_width.value()
        self.profile.paper_height = self.paper_height.value()
        self.save_profile(explicit=True)
        self.update_view()
        self.append_log("APP", "纸张尺寸已保存，仅影响电脑预览，未修改机器行程。")

    def save_profile(self, explicit=False):
        # 读取失败的原配置不在退出时自动覆盖，需用户明确保存。
        if not self.profile_loaded_clean and not explicit:
            return
        try:
            self.profile.save()
            self.profile_loaded_clean = True
        except (ValueError, OSError) as error:
            self.show_notice(f"本地配置未保存：{error}")

    def edit_settings(self):
        dialog = SettingsDialog(self.profile, self)
        if dialog.exec() and dialog.result_profile:
            self.profile = dialog.result_profile
            self.profile_loaded_clean = True
            self.controller.profile = self.profile
            self.controller.baseline_confirmed = False
            self.controller.servo_raised = False
            self.paper_width.setValue(self.profile.paper_width)
            self.paper_height.setValue(self.profile.paper_height)
            self.append_log("APP", "设备配置已保存。Z基准确认已清除；软件切换不会改变STM32固件。")
            self.update_view()

    def closeEvent(self, event):
        if self.loader is not None:
            self.show_notice("文件分析即将完成，请稍后关闭窗口。")
            event.ignore()
            return
        if self.controller.connected and (self.job.active or self.controller.pending or self.controller.state not in ("Idle", "Alarm", "Connecting")):
            if not self.confirm("关闭上位机", "设备可能仍在运动。是否发送暂停并关闭？这不会自动断开电机电源。"):
                event.ignore()
                return
            self.controller.pause()
        self.controller.disconnect_device()
        self.view_timer.stop()
        self.log_timer.stop()
        self.flush_log()
        self.save_profile()
        self.logger.removeHandler(self.log_handler)
        self.log_handler.close()
        event.accept()
