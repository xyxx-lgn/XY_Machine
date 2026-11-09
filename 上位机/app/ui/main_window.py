import logging
import re
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSplitter,
    QVBoxLayout, QWidget
)

from app.controller import GrblController
from app.profile import ROOT, MachineProfile
from app.transport import SIMULATOR, SerialTransport
from app.ui.preview_widget import PreviewWidget
from app.ui.settings_dialog import SettingsDialog


STYLE = """
QMainWindow, QDialog { background: #eef3f9; }
QWidget { font-family: 'Microsoft YaHei UI'; font-size: 10pt; color: #25344c; }
QGroupBox { background: white; border: 1px solid #d8e2f0; border-radius: 8px; margin-top: 15px; padding: 13px 10px 10px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
QPushButton { background: #e8effb; border: 1px solid #c9d8ee; border-radius: 5px; padding: 5px 9px; min-height: 17px; }
QPushButton:hover { background: #d6e5fc; }
QPushButton:pressed { background: #c0d6fb; }
QPushButton:disabled { color: #98a6b8; background: #edf1f6; border-color: #e0e6ee; }
QPushButton#primary { color: white; background: #2563eb; border-color: #2563eb; }
QPushButton#primary:disabled { background: #b4c7ec; border-color: #b4c7ec; }
QPushButton#danger { background: #fee8e7; color: #b42318; border-color: #f5c3bf; }
QPushButton#danger:disabled { color: #c7a6a4; background: #f6eeee; }
QLineEdit, QComboBox, QDoubleSpinBox { background: white; border: 1px solid #cbd7e7; border-radius: 4px; padding: 3px; min-height: 18px; }
QPlainTextEdit { border: 1px solid #cbd7e7; border-radius: 5px; }
QProgressBar { border: 1px solid #d5dfed; border-radius: 4px; background: white; text-align: center; min-height: 20px; }
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
        self.setWindowTitle("XY写字机上位机 · 第一阶段")
        self.resize(1400, 930)
        self.setMinimumSize(1060, 740)
        self.setStyleSheet(STYLE)
        self.logger = logging.getLogger("xy_host")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        (ROOT / "logs").mkdir(exist_ok=True)
        self.log_handler = RotatingFileHandler(ROOT / "logs" / "host.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        self.log_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
        self.logger.addHandler(self.log_handler)
        self.build_ui()
        self.bind_signals()
        self.refresh_ports()
        self.update_view()
        self.append_log("APP", "第一阶段就绪：串口、状态与手动控制。文件发送、图片和文字生成尚未实现。")
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
        layout.setContentsMargins(16, 12, 16, 10)
        self.setCentralWidget(root)

        header = QHBoxLayout()
        title_column = QVBoxLayout()
        title = QLabel("XY 写字机")
        title.setStyleSheet("font-size: 25px; font-weight: 700; color: #162c50;")
        title_column.addWidget(title)
        title_column.addWidget(QLabel("STM32G431 · GRBL 1.1h · 串口控制"))
        header.addLayout(title_column)
        header.addStretch()
        for text in ("导入G代码", "图片生成", "文字生成", "开始任务"):
            widget = self.button(text)
            widget.setEnabled(False)
            widget.setToolTip("后续阶段实现，本版仅支持状态与手动控制。")
            header.addWidget(widget)
        self.settings_button = self.button("设备 / Z配置", self.edit_settings)
        header.addWidget(self.settings_button)
        layout.addLayout(header)

        self.connection_banner = QLabel("未连接 · 请先选择真实串口，或使用模拟设备验证界面")
        self.connection_banner.setStyleSheet("background: #e4ecf9; border-radius: 5px; padding: 9px; color: #27466f;")
        layout.addWidget(self.connection_banner)

        split = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(split, 1)
        sidebar = QWidget()
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 5, 0)
        sidebar_layout.setSpacing(8)
        sidebar_layout.addWidget(self.build_connection())
        sidebar_layout.addWidget(self.build_controls())
        sidebar_layout.addWidget(self.build_paper())
        sidebar_layout.addWidget(self.build_console())
        sidebar_layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(sidebar)
        scroll.setMinimumWidth(355)
        split.addWidget(scroll)

        right = QSplitter(Qt.Orientation.Vertical)
        preview_group = QGroupBox("XY工作区域")
        preview_layout = QVBoxLayout(preview_group)
        preview_toolbar = QHBoxLayout()
        preview_toolbar.addWidget(QLabel("橙色标记：设备上报的工作坐标"))
        preview_toolbar.addStretch()
        self.preview = PreviewWidget()
        preview_toolbar.addWidget(self.button("−", lambda: self.preview.change_zoom(1 / 1.2)))
        preview_toolbar.addWidget(self.button("+", lambda: self.preview.change_zoom(1.2)))
        preview_toolbar.addWidget(self.button("适配", self.preview.fit))
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
        bar.addStretch()
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        self.log_view.setFont(QFont("Consolas", 10))
        self.log_view.setStyleSheet("background: #101827; color: #dbeafe; padding: 6px;")
        bar.addWidget(self.button("清空显示", self.log_view.clear))
        log_layout.addLayout(bar)
        log_layout.addWidget(self.log_view)
        right.addWidget(log_group)
        right.setSizes([530, 270])
        split.addWidget(right)
        split.setSizes([385, 980])
        split.setStretchFactor(1, 1)

        self.notice_label = QLabel("先在模拟设备中熟悉流程；真实设备运动使用已验证参数。")
        self.notice_label.setWordWrap(True)
        self.notice_label.setStyleSheet("color: #8a4b08; padding: 4px;")
        layout.addWidget(self.notice_label)
        progress_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setFormat("文件任务尚未实现")
        progress_row.addWidget(self.progress)
        layout.addLayout(progress_row)

    def build_connection(self):
        group = QGroupBox("串口连接与状态")
        layout = QGridLayout(group)
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
        layout.addWidget(self.baud_combo, 1, 1, 1, 2)
        layout.addWidget(self.connect_button, 2, 0, 1, 2)
        layout.addWidget(self.query_button, 2, 2)
        self.state_label = QLabel()
        self.machine_label = QLabel()
        self.work_label = QLabel()
        self.pins_label = QLabel()
        self.limits_label = QLabel("固件速度上限：未读取（点击读取设置）")
        self.limits_label.setWordWrap(True)
        for row, label in enumerate((self.state_label, self.machine_label, self.work_label, self.pins_label, self.limits_label), 3):
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(label, row, 0, 1, 3)
        return group

    def build_paper(self):
        group = QGroupBox("纸张区域 · 仅影响电脑预览")
        layout = QGridLayout(group)
        self.paper_width = self.number(self.profile.paper_width, 1, 1000)
        self.paper_height = self.number(self.profile.paper_height, 1, 1000)
        layout.addWidget(QLabel("宽 mm"), 0, 0)
        layout.addWidget(self.paper_width, 0, 1)
        layout.addWidget(QLabel("高 mm"), 1, 0)
        layout.addWidget(self.paper_height, 1, 1)
        layout.addWidget(self.button("应用并保存纸张", self.save_paper), 2, 0, 1, 2)
        return group

    def build_controls(self):
        group = QGroupBox("设备控制")
        layout = QGridLayout(group)
        self.jog_step = self.number(self.profile.jog_step, 0.01, 50)
        self.jog_feed = self.number(self.profile.jog_feed, 1, 10000, 0)
        layout.addWidget(QLabel("XY步长 mm"), 0, 0)
        layout.addWidget(self.jog_step, 0, 1, 1, 2)
        layout.addWidget(QLabel("XY进给"), 1, 0)
        layout.addWidget(self.jog_feed, 1, 1, 1, 2)
        self.baseline_check = QCheckBox("已按既定流程建立Z基准（此项不清零）")
        self.baseline_check.setToolTip("仅确认STM32当前机器Z坐标已对应纸面基准。不能靠勾选改变坐标，也不会发送G92或G10。")
        layout.addWidget(self.baseline_check, 2, 0, 1, 3)
        self.z_mode_label = QLabel()
        layout.addWidget(self.z_mode_label, 3, 0, 1, 3)
        self.jog_buttons = []
        for text, axis, sign, row, column in (
            ("Y− ↑", "Y", -1, 4, 1), ("X− ←", "X", -1, 5, 0),
            ("X+ →", "X", 1, 5, 2), ("Y+ ↓", "Y", 1, 6, 1),
            ("Z+", "Z", 1, 4, 2), ("Z−", "Z", -1, 6, 2)
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
        for row, (first, second) in enumerate(pairs, 7):
            layout.addWidget(first, row, 0)
            layout.addWidget(second, row, 1, 1, 2)
        self.pause_button = self.button("暂停 !", self.controller.pause)
        self.resume_button = self.button("继续 ~", self.controller.resume)
        self.jog_cancel_button = self.button("取消点动", self.controller.cancel_jog)
        layout.addWidget(self.pause_button, 11, 0)
        layout.addWidget(self.resume_button, 11, 1)
        layout.addWidget(self.jog_cancel_button, 11, 2)
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
        self.controller.changed.connect(self.update_view)
        self.controller.log.connect(self.append_log)
        self.controller.notice.connect(self.show_notice)
        self.baseline_check.toggled.connect(self.controller.confirm_baseline)
        self.jog_step.valueChanged.connect(lambda value: setattr(self.profile, "jog_step", value))
        self.jog_feed.valueChanged.connect(lambda value: setattr(self.profile, "jog_feed", value))

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
            if self.controller.pending or self.controller.state not in ("Idle", "Alarm", "Connecting"):
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
        if kind != "STATUS" or self.show_status.isChecked():
            self.log_view.appendPlainText(f"[{kind}] {message}")
        # 不将每秒两条的周期状态写入文件，避免日志刷屏和无意义增长。
        if kind != "STATUS":
            self.logger.info("[%s] %s", kind, message)

    def show_notice(self, message):
        self.notice_label.setText(message.replace("\n", "  "))
        self.append_log("提示", message)

    @staticmethod
    def coordinate_text(position):
        return "未知（等待坐标/WCO报告）" if position is None else "   ".join(f"{axis} {value:.3f}" for axis, value in zip("XYZ", position))

    def update_view(self):
        controller = self.controller
        connected = controller.connected
        available = controller.available
        normal = connected and controller.fresh and not controller.fault and not controller.awaiting_reset
        can_read = normal and controller.pending is None and not controller.queue and controller.state in ("Idle", "Alarm", "Check")
        self.connect_button.setText("断开" if connected else "连接")
        for widget in (self.port_combo, self.baud_combo, self.refresh_button):
            widget.setEnabled(not connected)
        self.query_button.setEnabled(connected)
        self.settings_button.setEnabled(not connected or available)
        label = "模拟设备 · 不会驱动硬件" if connected and controller.simulated else "真实串口 · 手动控制" if connected else "未连接 · 请选择串口，或使用模拟设备验证界面"
        if connected and not controller.fresh:
            label += " · 等待有效状态报告"
        if controller.fault:
            label += " · 应答失步/报警，普通发送已锁定"
        self.connection_banner.setText(label)
        self.connection_banner.setStyleSheet("background: #fff1cf; padding: 9px; border-radius: 5px; color: #814600;" if controller.simulated and connected else "background: #e4ecf9; padding: 9px; border-radius: 5px; color: #27466f;")
        self.state_label.setText(f"状态：{controller.state}" + ("（状态过期）" if connected and not controller.fresh else ""))
        self.machine_label.setText("机器：" + self.coordinate_text(controller.machine))
        self.work_label.setText("工作：" + self.coordinate_text(controller.work))
        self.pins_label.setText(f"输入：{controller.pins or '无有效触发上报'}    进给：{controller.feed if controller.feed is not None else '未知'}")
        if controller.settings:
            values = [f"{axis} {controller.settings.get(key, '?')}" for axis, key in zip("XYZ", (110, 111, 112))]
            self.limits_label.setText("固件速度上限：" + " / ".join(values))
        self.modal_label.setText("模态：" + controller.modal)
        self.preview.set_paper(self.profile.paper_width, self.profile.paper_height)
        self.preview.set_position(controller.work if controller.fresh else None)
        self.baseline_check.blockSignals(True)
        self.baseline_check.setChecked(controller.baseline_confirmed)
        self.baseline_check.blockSignals(False)
        self.baseline_check.setEnabled(available and self.profile.z_mode == "stepper")
        z_ready = controller.baseline_confirmed if self.profile.z_mode == "stepper" else self.profile.servo_firmware_confirmed
        self.z_mode_label.setText(f"Z：{'步进连杆' if self.profile.z_mode == 'stepper' else '舵机'} · 顶部限位回零尚未启用")
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
        self.resume_button.setEnabled(normal and controller.state.startswith("Hold"))
        self.jog_cancel_button.setEnabled(connected and controller.state == "Jog")
        self.statusBar().showMessage("第一阶段：串口与手动控制    |    配置和日志均位于上位机目录    |    当前无文件任务")

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
        if not self.controller.fresh or self.controller.pending or self.controller.state not in ("Idle", "Alarm", "Check"):
            self.show_notice("当前仅允许实时查询、暂停和复位；等待空闲和应答后再发普通命令。")
            return
        if command == "$H":
            self.home()
            return
        if command.startswith("$H"):
            self.show_notice("本阶段只通过XY回零按钮发送$H，不提供Z或单轴回零。")
            return
        if command.startswith("$J="):
            self.show_notice("第一阶段请使用点动按钮，以保留Z基准、范围和运行状态检查。")
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
        if self.controller.connected and (self.controller.pending or self.controller.state not in ("Idle", "Alarm", "Connecting")):
            if not self.confirm("关闭上位机", "设备可能仍在运动。是否发送暂停并关闭？这不会自动断开电机电源。"):
                event.ignore()
                return
            self.controller.pause()
        self.controller.disconnect_device()
        self.save_profile()
        self.logger.removeHandler(self.log_handler)
        self.log_handler.close()
        event.accept()
