import time

from PySide6.QtCore import QObject, QTimer, Signal


class JobRunner(QObject):
    """逐行等待ok的任务发送器；ok代表接收，最后还必须等待新的Idle状态。"""

    changed = Signal()

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.program = None
        self.active = False
        self.paused = False
        self.resuming = False
        self.sent = 0
        self.accepted = 0
        self.message = "尚未载入文件"
        self.result = "ready"
        self.started_at = 0.0
        self.elapsed = 0.0
        self.idle_since = None
        controller.acknowledged.connect(self.on_ack)
        controller.status_received.connect(self.on_status)
        controller.interrupted.connect(self.abort)
        controller.pause_requested.connect(self.on_pause)
        controller.resume_requested.connect(self.on_resume)
        self.watchdog = QTimer(self)
        self.watchdog.setInterval(500)
        self.watchdog.timeout.connect(self.check_status)

    def load(self, program):
        if self.active:
            return False
        self.program = program
        self.sent = self.accepted = 0
        self.result = "ready"
        self.message = f"已载入 {len(program.commands)} 条命令，等待开始"
        self.elapsed = 0.0
        self.changed.emit()
        return True

    def start(self):
        if not self.program or self.active or not self.controller.available:
            return False
        self.active = True
        self.controller.job_active = True
        self.paused = self.resuming = False
        self.sent = self.accepted = 0
        self.result = "running"
        self.message = "正在逐行发送，等待设备应答"
        self.started_at = time.monotonic()
        self.idle_since = None
        self.watchdog.start()
        self.controller.notice.emit("任务运行中；暂停可继续，中止会软复位并结束本次任务。")
        self.changed.emit()
        self.pump()
        return True

    def pump(self):
        controller = self.controller
        if not self.active or self.paused or self.resuming or controller.pending or controller.queue:
            return
        if not controller.fresh or controller.state.startswith("Hold"):
            return
        if controller.state not in ("Idle", "Run") or controller.fault:
            return
        if self.sent < len(self.program.commands):
            command = self.program.commands[self.sent]
            self.sent += 1
            # 规划缓冲满时，普通行可能等待较长时间；暂停期间单独停用此超时。
            if not controller.send(command.text, "job", 120000):
                self.abort(f"原文件第{command.source_line}行发送失败。")
            self.changed.emit()

    def on_ack(self, text, action, reply):
        if not self.active or action != "job":
            return
        if reply != "ok":
            line = self.program.commands[self.accepted].source_line
            self.controller.write_realtime(b"!", "! 文件错误，停止后续发送并暂停")
            self.controller.fault = True
            self.abort(f"原文件第{line}行 {reply}：{text}。已发暂停；检查后软复位。")
            return
        self.accepted += 1
        if self.accepted == len(self.program.commands):
            self.message = "全部命令已应答，等待设备Idle确认绘制结束"
        QTimer.singleShot(0, self.pump)
        self.changed.emit()

    def on_status(self):
        if not self.active:
            return
        controller = self.controller
        if controller.state.startswith("Hold") and not self.resuming:
            self.on_pause()
        elif self.resuming and controller.state in ("Run", "Idle"):
            self.resuming = self.paused = False
            if controller.pending:
                controller.deadline.start(controller.pending.timeout_ms)
            self.message = "继续逐行发送"
        if self.accepted == len(self.program.commands) and controller.state == "Idle" and not self.paused:
            # 连续两次Idle且间隔至少100ms，避开最后一行入规划器后的短暂Idle。
            if self.idle_since is None:
                self.idle_since = time.monotonic()
            elif time.monotonic() - self.idle_since >= 0.1:
                self.finish()
        else:
            self.idle_since = None
            QTimer.singleShot(0, self.pump)

    def on_pause(self):
        if not self.active:
            return
        self.paused = True
        self.idle_since = None
        self.resuming = False
        self.controller.deadline.stop()
        self.message = "已暂停发送；等待Hold:0后可继续"
        self.changed.emit()

    def on_resume(self):
        if not self.active:
            return
        self.resuming = True
        self.message = "已请求继续，等待设备退出Hold"
        self.changed.emit()

    def check_status(self):
        if not self.active:
            return
        self.elapsed = time.monotonic() - self.started_at
        if not self.controller.fresh:
            self.controller.write_realtime(b"!", "! 状态超时，暂停")
            self.controller.clear_pending()
            self.controller.fault = True
            self.abort("超过3秒未收到有效状态；已请求暂停，不自动续传。检查后软复位。")
        self.changed.emit()

    def finish(self):
        self.active = self.controller.job_active = False
        self.watchdog.stop()
        self.elapsed = time.monotonic() - self.started_at
        self.result = "complete"
        self.message = "绘制完成：全部命令已应答，设备已确认Idle"
        self.controller.log.emit("JOB", self.message)
        self.controller.notice.emit(self.message)
        self.changed.emit()
        QTimer.singleShot(0, self.refresh_modal)

    def refresh_modal(self):
        # 文件可能改变模态和G54偏移，结束后重新读取，避免下一次检查使用旧值。
        if self.controller.available:
            self.controller.send("$G", "read")
            self.controller.send("$#", "read")

    def abort(self, reason):
        if not self.active:
            return
        self.active = self.controller.job_active = False
        self.paused = self.resuming = False
        self.watchdog.stop()
        self.elapsed = time.monotonic() - self.started_at
        self.result = "aborted"
        self.message = reason
        self.controller.notice.emit(reason)
        self.changed.emit()

    def stop(self):
        # 清空主机待发内容并发原始Ctrl+X，不从中断行自动重启。
        if self.active:
            self.controller.reset()
