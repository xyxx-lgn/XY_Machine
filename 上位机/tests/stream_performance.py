import json
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["TEMP"] = str(ROOT / ".runtime" / "tmp")
os.environ["TMP"] = os.environ["TEMP"]

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.appearance import configure_appearance
from app.gcode import load_program
from app.profile import MachineProfile
from app.ui.main_window import MainWindow


class FastDevice:
    """无硬件的快速应答端，只测主机开销，不模拟运动时间。"""

    def __init__(self, controller, total):
        self.controller = controller
        self.total = total
        self.accepted = 0
        self.finished_at = None

    def write(self, data):
        if data == b"?":
            state = "Idle" if self.accepted == self.total else "Run"
            self.controller.handle_line(f"<{state}|MPos:-180,-270,3|WCO:-190,-280,0|FS:0,0>")
        elif data.endswith(b"\n"):
            is_job = self.controller.pending.action == "job"
            QTimer.singleShot(0, lambda: self.ack(is_job))
        return True

    def ack(self, is_job):
        if is_job:
            self.accepted += 1
            if self.accepted == self.total:
                self.finished_at = time.perf_counter()
        self.controller.handle_line("ok")


application = QApplication([])
configure_appearance(application)
window = MainWindow()
window.profile = MachineProfile()
window.controller.profile = window.profile
window.save_profile = lambda: None
large = load_program(ROOT.parent / "G代码生成软件" / "生成G代码" / "output_0006.ngc")
program = replace(large, commands=large.commands[:2000])
window.preview.set_program(large)
window.travel_check.setChecked(True)
window.show()
application.processEvents()
controller = window.controller
device = FastDevice(controller, len(program.commands))
controller.transport = device
controller.connected = controller.simulated = controller.baseline_confirmed = True
controller.handle_line("<Idle|MPos:-180,-270,3|WCO:-190,-280,0|FS:0,0>")
controller.poll.start()
window.job.load(program)
heartbeats = []
last_tick = time.perf_counter()


def heartbeat():
    global last_tick
    now = time.perf_counter()
    heartbeats.append((now - last_tick) * 1000)
    last_tick = now


timer = QTimer()
timer.setInterval(10)
timer.timeout.connect(heartbeat)
timer.start()
started_at = time.perf_counter()
window.job.start()
while window.job.active and time.perf_counter() - started_at < 20:
    application.processEvents()
assert window.job.result == "complete", window.job.message
elapsed = device.finished_at - started_at
result = {"preview_commands": len(large.commands), "sent_commands": device.accepted, "host_send_seconds": elapsed, "host_commands_per_second": device.accepted / elapsed, "max_heartbeat_ms": max(heartbeats), "cache_builds": window.preview.scene_builds}
folder = ROOT / ".runtime" / "performance"
folder.mkdir(parents=True, exist_ok=True)
(folder / "stream.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result))
timer.stop()
controller.poll.stop()
controller.connected = False
controller.transport = None
window.close()
