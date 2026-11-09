import re

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtSerialPort import QSerialPort, QSerialPortInfo


SIMULATOR = "__simulator__"


class SerialTransport(QObject):
    received = Signal(bytes)
    opened = Signal()
    closed = Signal()
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.port = QSerialPort(self)
        self.port.readyRead.connect(self.read_ready)
        self.port.errorOccurred.connect(self.handle_error)
        self._closing = False

    @staticmethod
    def available_ports():
        return [(port.portName(), port.description()) for port in QSerialPortInfo.availablePorts()]

    def open(self, name, baud_rate):
        self.port.setPortName(name)
        self.port.setBaudRate(baud_rate)
        self.port.setDataBits(QSerialPort.DataBits.Data8)
        self.port.setParity(QSerialPort.Parity.NoParity)
        self.port.setStopBits(QSerialPort.StopBits.OneStop)
        self.port.setFlowControl(QSerialPort.FlowControl.NoFlowControl)
        if not self.port.open(QSerialPort.OpenModeFlag.ReadWrite):
            self.failed.emit(f"无法打开{name}：{self.port.errorString()}。请先关闭Candle/XCOM。")
            return False
        # 不主动切换DTR/RTS，不通过控制线复位PCB。
        self.opened.emit()
        return True

    def read_ready(self):
        self.received.emit(bytes(self.port.readAll()))

    def write(self, data):
        if not self.port.isOpen():
            self.failed.emit("串口未连接。")
            return False
        count = self.port.write(data)
        if count != len(data):
            self.failed.emit("串口写入失败，已暂停后续命令。")
            return False
        return True

    def handle_error(self, error):
        fatal = (QSerialPort.SerialPortError.ResourceError, QSerialPort.SerialPortError.DeviceNotFoundError)
        if error in fatal and self.port.isOpen() and not self._closing:
            self.failed.emit(f"串口异常断开：{self.port.errorString()}")
            self.close()

    def close(self):
        if self.port.isOpen():
            self._closing = True
            self.port.close()
            self._closing = False
            self.closed.emit()


class SimulatorTransport(QObject):
    """进程内模拟设备，仅用于软件验证；不打开串口，也不代表硬件验收。"""

    received = Signal(bytes)
    opened = Signal()
    closed = Signal()
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.active = False
        self.epoch = 0
        self.state = "Idle"
        self.position = [-180.0, -270.0, 0.0]
        self.offset = [-190.0, -280.0, 0.0]
        self.settings = {110: 1500.0, 111: 1500.0, 112: 240.0, 120: 30.0, 121: 30.0, 122: 20.0, 130: 210.0, 131: 315.0, 132: 200.0}

    def open(self, name, baud_rate):
        self.active = True
        self.opened.emit()
        return True

    def reply(self, message):
        if self.active:
            self.received.emit((message + "\r\n").encode("ascii"))

    def status(self):
        position = ",".join(f"{value:.3f}" for value in self.position)
        offset = ",".join(f"{value:.3f}" for value in self.offset)
        self.reply(f"<{self.state}|MPos:{position}|FS:0,0|WCO:{offset}>")

    def write(self, data):
        if not self.active:
            return False
        if data == b"?":
            self.status()
        elif data == b"!":
            self.state = "Hold:0"
            self.status()
        elif data == b"~":
            self.state = "Idle"
            self.status()
        elif data == b"\x85":
            self.state = "Idle"
            self.status()
        elif data == b"\x18":
            self.epoch += 1
            self.state = "Alarm"
            self.reply("Grbl 1.1h ['$' for help]")
        else:
            command = data.decode("ascii").strip()
            epoch = self.epoch
            QTimer.singleShot(60, lambda: self.execute(command, epoch))
        return True

    def execute(self, command, epoch):
        if not self.active or epoch != self.epoch:
            return
        if command == "$G":
            self.reply("[GC:G0 G54 G17 G21 G90 G94 M5 M9 T0 F0 S0]")
        elif command == "$#":
            self.reply("[G54:" + ",".join(f"{value:.3f}" for value in self.offset) + "]")
        elif command == "$I":
            self.reply("[VER:1.1h:XY_HOST_SIMULATOR]")
        elif command == "$$":
            for key, value in sorted(self.settings.items()):
                self.reply(f"${key}={value:.3f}")
        elif command == "$X":
            self.state = "Idle"
            self.reply("[MSG:Caution: Unlocked]")
        elif command == "$H":
            self.position[0:2] = [-207.0, -312.0]
            self.state = "Idle"
        elif self.state == "Alarm" and not command.startswith("$"):
            self.reply("error:9")
            return
        elif "G10" in command:
            self.offset[0:2] = self.position[0:2]
        elif command.startswith("$") and "=" in command and not command.startswith("$J="):
            key, value = command[1:].split("=", 1)
            try:
                self.settings[int(key)] = float(value)
            except ValueError:
                self.reply("error:3")
                return
        elif any(axis in command for axis in "XYZ"):
            values = {axis: float(value) for axis, value in re.findall(r"([XYZ])(-?\d+(?:\.\d+)?)", command)}
            relative = "G91" in command
            machine = "G53" in command
            for index, axis in enumerate("XYZ"):
                if axis in values:
                    self.position[index] = self.position[index] + values[axis] if relative else values[axis] + (0 if machine else self.offset[index])
        self.reply("ok")
        self.status()

    def close(self):
        if self.active:
            self.active = False
            self.epoch += 1
            self.closed.emit()
