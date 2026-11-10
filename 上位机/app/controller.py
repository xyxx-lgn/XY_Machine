import re
import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer, Signal

from app.grbl import normalize_command, parse_status, vector
from app.transport import SIMULATOR, SerialTransport, SimulatorTransport


@dataclass
class PendingCommand:
    text: str
    action: str = "console"
    timeout_ms: int = 8000


class GrblController(QObject):
    changed = Signal()
    log = Signal(str, str)
    notice = Signal(str)
    acknowledged = Signal(str, str, str)
    status_received = Signal()
    interrupted = Signal(str)
    pause_requested = Signal()
    resume_requested = Signal()

    def __init__(self, profile, parent=None):
        super().__init__(parent)
        self.profile = profile
        self.transport = None
        self.connected = False
        self.simulated = False
        self.state = "Disconnected"
        self.machine = None
        self.work = None
        self.offset = None
        self.g54_offset = None
        self.pins = ""
        self.feed = None
        self.modal = "未知"
        self.settings = {}
        self.buffer = bytearray()
        self.pending = None
        self.queue = []
        self.last_status = 0.0
        self.baseline_confirmed = False
        self.servo_raised = False
        self.fault = False
        self.awaiting_reset = False
        self.job_active = False
        self.poll = QTimer(self)
        self.poll.setInterval(500)
        self.poll.timeout.connect(self.poll_status)
        self.deadline = QTimer(self)
        self.deadline.setSingleShot(True)
        self.deadline.timeout.connect(self.command_timeout)

    @property
    def fresh(self):
        return self.connected and time.monotonic() - self.last_status < 3.0

    @property
    def idle(self):
        return self.fresh and self.state == "Idle" and not self.fault and not self.awaiting_reset

    @property
    def available(self):
        return self.idle and self.pending is None and not self.queue and not self.job_active

    def connect_device(self, name, baud_rate):
        if self.connected:
            return
        self.simulated = name == SIMULATOR
        self.transport = SimulatorTransport(self) if self.simulated else SerialTransport(self)
        self.transport.received.connect(self.receive)
        self.transport.opened.connect(self.on_opened)
        self.transport.closed.connect(self.on_closed)
        self.transport.failed.connect(self.on_failure)
        if not self.transport.open(name, baud_rate):
            self.transport.deleteLater()
            self.transport = None

    def on_opened(self):
        self.connected = True
        self.state = "Connecting"
        self.fault = False
        self.awaiting_reset = False
        self.baseline_confirmed = False
        self.servo_raised = False
        self.buffer.clear()
        self.settings.clear()
        self.last_status = 0.0
        self.machine = self.work = self.offset = None
        self.g54_offset = None
        self.modal = "未知"
        self.log.emit("SYS", "模拟设备已连接，不会驱动硬件。" if self.simulated else "串口已打开，仅自动查询，不自动运动、解锁或复位。")
        self.poll.start()
        self.query_status()
        self.changed.emit()

    def disconnect_device(self):
        if self.transport:
            self.transport.close()
            self.transport.deleteLater()
            self.transport = None

    def on_closed(self):
        self.interrupted.emit("串口已断开，文件任务已结束，不会自动续传。")
        self.connected = False
        self.state = "Disconnected"
        self.poll.stop()
        self.clear_pending()
        self.buffer.clear()
        self.machine = self.work = self.offset = None
        self.g54_offset = None
        self.settings.clear()
        self.modal = "未知"
        self.feed = None
        self.pins = ""
        self.last_status = 0.0
        self.baseline_confirmed = False
        self.servo_raised = False
        self.log.emit("SYS", "连接已断开，Z基准确认已清除。")
        self.changed.emit()

    def on_failure(self, message):
        self.interrupted.emit(message)
        self.fault = True
        self.clear_pending()
        self.log.emit("ERROR", message)
        self.notice.emit(message)
        self.changed.emit()

    def clear_pending(self):
        self.deadline.stop()
        self.pending = None
        self.queue.clear()

    def command_timeout(self):
        text = self.pending.text if self.pending else "未知命令"
        self.interrupted.emit(f"等待应答超时：{text}")
        self.clear_pending()
        self.fault = True
        self.log.emit("ERROR", f"等待应答超时：{text}。不自动重发，以免重复运动。")
        self.notice.emit("指令应答超时。请检查设备；重新同步前仅允许查询、暂停和复位。")
        self.changed.emit()

    def write_realtime(self, value, label):
        if not self.connected or not self.transport:
            return False
        if value != b"?":
            self.log.emit("TX", label)
        return self.transport.write(value)

    def query_status(self):
        self.write_realtime(b"?", "?")

    def poll_status(self):
        self.query_status()
        self.changed.emit()

    def pause(self):
        self.pause_requested.emit()
        self.write_realtime(b"!", "! 暂停")

    def resume(self):
        if self.fresh and not self.fault and self.state == "Hold:0":
            self.resume_requested.emit()
            self.write_realtime(b"~", "~ 继续")

    def cancel_jog(self):
        self.write_realtime(b"\x85", "0x85 取消点动")

    def reset(self):
        self.interrupted.emit("软复位中止任务；重新确认坐标和Z基准后从文件开头重新开始。")
        self.clear_pending()
        self.fault = False
        self.awaiting_reset = True
        self.state = "Resetting"
        self.machine = self.work = self.offset = None
        self.g54_offset = None
        self.baseline_confirmed = False
        self.servo_raised = False
        self.last_status = 0.0
        self.write_realtime(b"\x18", "0x18 软复位；等待启动横幅")
        self.changed.emit()

    def send(self, command, action="console", timeout_ms=8000):
        if self.job_active and action != "job":
            self.notice.emit("文件任务正在占用普通命令通道，请先完成或中止任务。")
            return False
        if not self.connected or self.fault or self.awaiting_reset:
            self.notice.emit("设备未连接、等待复位或应答失步，暂不能发送普通命令。")
            return False
        try:
            command = normalize_command(command)
        except ValueError as error:
            self.notice.emit(str(error))
            return False
        if len(self.queue) >= 16:
            self.notice.emit("命令队列已满，请等待应答。")
            return False
        self.queue.append(PendingCommand(command, action, timeout_ms))
        self.pump()
        return True

    def pump(self):
        if self.pending or not self.queue or self.fault or not self.connected:
            return
        self.pending = self.queue.pop(0)
        self.deadline.start(self.pending.timeout_ms)
        self.log.emit("TX", self.pending.text)
        if not self.transport.write((self.pending.text + "\n").encode("ascii")):
            self.on_failure("命令发送失败，发送队列已清除。")
        self.changed.emit()

    def receive(self, chunk):
        # 串口一次接收可能是半行或多行，必须自行组帧。
        self.buffer.extend(chunk)
        if len(self.buffer) > 65536:
            self.buffer.clear()
            self.on_failure("接收数据长期缺少换行，已停止普通发送。请确认波特率。")
            return
        while b"\n" in self.buffer:
            raw, _, rest = self.buffer.partition(b"\n")
            self.buffer = bytearray(rest)
            line = raw.decode("ascii", errors="replace").strip()
            if line:
                self.handle_line(line)

    def handle_line(self, line):
        self.log.emit("STATUS" if line.startswith("<") else "RX", line)
        if line.startswith("<"):
            try:
                report = parse_status(line, self.offset)
            except (ValueError, IndexError):
                self.log.emit("ERROR", "状态格式异常，已忽略本条报告。")
                return
            if report:
                self.state = report.state
                self.machine, self.work, self.offset = report.machine, report.work, report.offset
                self.pins, self.feed = report.pins, report.feed
                self.last_status = time.monotonic()
                self.status_received.emit()
        elif line.startswith("Grbl "):
            self.interrupted.emit("检测到GRBL重启，任务已中止，不会续传。")
            self.clear_pending()
            self.fault = False
            self.awaiting_reset = False
            self.machine = self.work = self.offset = None
            self.g54_offset = None
            self.baseline_confirmed = False
            self.servo_raised = False
            self.last_status = 0.0
            self.modal = "未知"
            self.state = "Connecting"
            self.log.emit("SYS", "检测到GRBL启动，旧命令已丢弃。请重新确认坐标和Z基准。")
        elif line.startswith("ALARM:"):
            self.interrupted.emit(f"设备{line}，任务已中止。")
            self.clear_pending()
            self.state = "Alarm"
            self.baseline_confirmed = False
            self.servo_raised = False
            # 限位报警后可能仍处在等待复位循环，不能直接接受新命令。
            self.fault = True
            self.notice.emit(f"设备报警：{line}。先排除原因并软复位，再重新确认坐标。")
        elif line == "ok" or line.startswith("error:"):
            command = self.pending
            if command:
                self.deadline.stop()
                self.pending = None
                if line.startswith("error:"):
                    self.queue.clear()
                    self.notice.emit(f"{command.text}\n设备回复：{line}")
                else:
                    if command.action == "servo_up":
                        self.servo_raised = True
                    elif command.action == "servo_down":
                        self.servo_raised = False
                    if command.action in ("origin", "home", "console"):
                        self.offset = None
                        self.g54_offset = None
                        self.modal = "未知"
                        QTimer.singleShot(0, self.refresh_parameters)
                QTimer.singleShot(0, self.pump)
                if command.action != "job":
                    self.last_status = 0.0
                # 文件流只使用500ms周期查询，避免每个ok额外产生一整份状态报告。
                if command.action != "job":
                    QTimer.singleShot(0, self.query_status)
                self.acknowledged.emit(command.text, command.action, line)
            else:
                self.log.emit("SYS", "收到未配对的应答，未将其用于其他命令。")
        elif line.startswith("[GC:"):
            self.modal = line[4:-1]
        elif line.startswith("[G54:"):
            try:
                self.g54_offset = vector(line[5:-1])
            except ValueError:
                self.g54_offset = None
        elif re.match(r"^\$\d+=", line):
            key, value = line[1:].split("=", 1)
            try:
                self.settings[int(key)] = float(value)
            except ValueError:
                pass
        self.changed.emit()

    def refresh_parameters(self):
        if self.connected and not self.fault and not self.awaiting_reset and not self.job_active:
            self.send("$G", "read")
            self.send("$#", "read")

    def require_idle(self):
        if not self.available:
            self.notice.emit("请等待设备处于Idle、状态有效且普通命令应答完成。")
            return False
        return True

    def confirm_baseline(self, confirmed):
        self.baseline_confirmed = bool(confirmed and self.fresh)
        self.changed.emit()

    def require_z_ready(self):
        if self.profile.z_mode == "stepper":
            if not self.baseline_confirmed or self.machine is None:
                self.notice.emit("先按既定流程建立Z机器坐标基准，并勾选确认。勾选本身不会清零坐标。")
                return False
        elif not self.profile.servo_firmware_confirmed:
            self.notice.emit("请在设备配置中填写舵机命令，并确认固件已切换为舵机模式。")
            return False
        return True

    def jog(self, axis, sign):
        if not self.require_idle() or not self.require_z_ready():
            return False
        if axis == "Z" and self.profile.z_mode == "servo":
            self.notice.emit("舵机模式使用抬笔/落笔，暂不提供Z坐标点动。")
            return False
        step = self.profile.z_jog_step if axis == "Z" else self.profile.jog_step
        feed = self.profile.z_feed if axis == "Z" else self.profile.jog_feed
        if axis == "Z":
            target = self.machine[2] + sign * step
            if not self.profile.z_min <= target <= self.profile.z_max:
                self.notice.emit("Z点动超出上位机配置范围，未发送。")
                return False
        return self.send(f"$J=G21 G91 {axis}{sign * step:.4f} F{feed:.1f}", "jog")

    def pen(self, raised):
        if not self.require_idle() or not self.require_z_ready():
            return False
        profile = self.profile
        if profile.z_mode == "servo":
            command = profile.servo_up_command if raised else profile.servo_down_command
            return self.send(command, "servo_up" if raised else "servo_down")
        target = profile.pen_up if raised else profile.pen_down
        feed = profile.pen_up_feed if raised else profile.pen_down_feed
        # G53明确使用机器Z坐标，不受G54、G92和刀长补偿偏移影响。
        return self.send(f"G21 G90 G94 G53 G1 Z{target:.4f} F{feed:.1f}", "pen")

    def pen_clear(self):
        if self.profile.z_mode == "servo":
            return self.profile.servo_firmware_confirmed and self.servo_raised
        return self.baseline_confirmed and self.machine is not None and self.machine[2] >= self.profile.pen_up - 0.01

    def home_xy(self):
        if not self.require_idle():
            return False
        if not self.pen_clear():
            self.notice.emit("请先确认Z基准并抬笔，收到Idle状态后再执行XY回零。报警时需先检查并解锁。")
            return False
        return self.send("$H", "home", 180000)

    def set_xy_origin(self):
        if not self.require_idle():
            return False
        return self.send("G21 G54 G10 L20 P1 X0 Y0", "origin")

    def return_origin(self):
        if not self.require_idle():
            return False
        if not self.pen_clear():
            self.notice.emit("请先抬笔，确认Idle后再返回G54工作原点。")
            return False
        return self.send(f"G21 G90 G94 G54 G1 X0 Y0 F{self.profile.jog_feed:.1f}", "return")
