from dataclasses import replace

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QLabel, QLineEdit, QMessageBox, QTabWidget, QVBoxLayout, QWidget
)


class SettingsDialog(QDialog):
    def __init__(self, profile, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设备与笔配置 · 仅保存到电脑")
        self.resize(610, 570)
        self.original = profile
        self.result_profile = None
        self.editors = {}
        layout = QVBoxLayout(self)
        note = QLabel("修改此处不会写入STM32 EEPROM，也不会改变固件的Z轴模式。\n实际参数以已验证的硬件和固件为准。")
        note.setWordWrap(True)
        layout.addWidget(note)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        machine = QWidget()
        form = QFormLayout(machine)
        self.mode = QComboBox()
        self.mode.addItem("步进电机 Z（当前硬件）", "stepper")
        self.mode.addItem("舵机 Z（需配套固件及已验证命令）", "servo")
        self.mode.setCurrentIndex(self.mode.findData(profile.z_mode))
        form.addRow("Z驱动类型", self.mode)
        self.add_number(form, "paper_width", "纸张宽度 mm", profile.paper_width, 1, 1000)
        self.add_number(form, "paper_height", "纸张高度 mm", profile.paper_height, 1, 1000)
        self.add_number(form, "z_jog_step", "Z点动步长（固件坐标单位）", profile.z_jog_step, 0.001, 10)
        self.add_number(form, "z_feed", "Z点动进给", profile.z_feed, 1, 10000)
        limit_note = QLabel("Z顶部限位：仅保留未来扩展。当前不启用Z回零，也不发送$HZ。")
        limit_note.setWordWrap(True)
        form.addRow(limit_note)
        tabs.addTab(machine, "设备")

        self.stepper_tab = QWidget()
        form = QFormLayout(self.stepper_tab)
        self.add_number(form, "pen_up", "抬笔机器坐标", profile.pen_up, -100, 100)
        self.add_number(form, "pen_down", "落笔机器坐标", profile.pen_down, -100, 100)
        self.add_number(form, "pen_up_feed", "抬笔进给", profile.pen_up_feed, 1, 10000)
        self.add_number(form, "pen_down_feed", "落笔进给", profile.pen_down_feed, 1, 10000)
        self.add_number(form, "z_min", "上位机Z下界", profile.z_min, -100, 100)
        self.add_number(form, "z_max", "上位机Z上界", profile.z_max, -100, 100)
        tip = QLabel("手动抬落笔使用G53机器坐标和G1独立进给。\n连杆Z的坐标单位不等于笔尖垂直毫米。\n此范围只是上位机检查，不能替代固件限位。")
        tip.setWordWrap(True)
        form.addRow(tip)
        tabs.addTab(self.stepper_tab, "步进Z")

        self.servo_tab = QWidget()
        form = QFormLayout(self.servo_tab)
        for name, label in (("servo_up_command", "抬笔单行命令"), ("servo_down_command", "落笔单行命令")):
            editor = QLineEdit(getattr(profile, name))
            editor.setPlaceholderText("填写配套固件实际支持且已验证的命令")
            self.editors[name] = editor
            form.addRow(label, editor)
        self.servo_confirm = QCheckBox("我已将固件切换为舵机模式，并核实上述命令")
        self.servo_confirm.setChecked(profile.servo_firmware_confirmed)
        self.mode.currentIndexChanged.connect(lambda: self.servo_confirm.setChecked(False))
        for name in ("servo_up_command", "servo_down_command"):
            self.editors[name].textEdited.connect(lambda text: self.servo_confirm.setChecked(False))
        form.addRow(self.servo_confirm)
        servo_note = QLabel("软件切换不等于重新烧录固件。默认不填写H/V，避免把参考工程的私有命令误用于当前固件。\n舵机动作完成时间由固件保证；收到ok仅表示命令被接受。")
        servo_note.setWordWrap(True)
        form.addRow(servo_note)
        tabs.addTab(self.servo_tab, "舵机Z")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def add_number(self, form, name, label, value, minimum, maximum):
        editor = QDoubleSpinBox()
        editor.setDecimals(3)
        editor.setRange(minimum, maximum)
        editor.setValue(value)
        self.editors[name] = editor
        form.addRow(label, editor)

    def save(self):
        values = {}
        for name, editor in self.editors.items():
            values[name] = editor.text().strip() if isinstance(editor, QLineEdit) else editor.value()
        values["z_mode"] = self.mode.currentData()
        values["servo_firmware_confirmed"] = self.servo_confirm.isChecked()
        result = replace(self.original, **values)
        try:
            result.validate()
            result.save()
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "配置未保存", str(error))
            return
        self.result_profile = result
        self.accept()
