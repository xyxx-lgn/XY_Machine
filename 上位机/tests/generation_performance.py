import json
import os
import sys
import time
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
from app.profile import MachineProfile
from app.ui import generation_dialog
from app.ui.generation_dialog import GenerationDialog


application = QApplication([])
configure_appearance(application)
generation_dialog.PREFERENCES = ROOT / ".runtime" / "unused_generation_preferences.json"
dialog = GenerationDialog(MachineProfile(), "text")
dialog.text.setPlainText("HOB 123 写字机\nABC 456 运动测试\nDEFG 789 测试完成")
dialog.width.setValue(180)
dialog.mode.setCurrentIndex(dialog.mode.findData("fill"))
dialog.show()
application.processEvents()
intervals = []
last_tick = time.perf_counter()


def heartbeat():
    global last_tick
    now = time.perf_counter()
    intervals.append((now - last_tick) * 1000)
    last_tick = now


timer = QTimer()
timer.setInterval(10)
timer.timeout.connect(heartbeat)
timer.start()
started = time.perf_counter()
dialog.generate()
while dialog.worker is not None and time.perf_counter() - started < 60:
    application.processEvents()
    time.sleep(0.001)
if dialog.worker is not None:
    dialog.cancel_generation()
    while dialog.worker is not None:
        application.processEvents()
        time.sleep(0.005)
    raise AssertionError("大图生成超过60秒")
assert dialog.task is not None, dialog.status.text()
timer.stop()
result = {
    "seconds": time.perf_counter() - started,
    "commands": len(dialog.task.program.commands),
    "paths": dialog.task.path_count,
    "max_heartbeat_ms": max(intervals),
    "heartbeats": len(intervals)
}
assert len(intervals) >= 2, "后台生成期间事件循环必须持续运行"
assert max(intervals) < 1000, "生成期间主界面出现超过1秒的阻塞"
folder = ROOT / ".runtime" / "performance"
folder.mkdir(parents=True, exist_ok=True)
(folder / "generation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
dialog.reject()
print(json.dumps(result))
