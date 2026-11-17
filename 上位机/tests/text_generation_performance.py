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
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from app.appearance import configure_appearance
from app.profile import MachineProfile
from app.ui import generation_dialog
from app.ui.generation_dialog import GenerationDialog


application = QApplication([])
configure_appearance(application)
QFontDatabase.addApplicationFont("C:/Windows/Fonts/simhei.ttf")
generation_dialog.PREFERENCES = ROOT / ".runtime" / "unused_text_performance_preferences.json"
text = (
    "写字机应按排版顺序完成每个字，再移动到下一个字。交叉笔画的短连接需要保留，"
    "字母里的孔洞和笔画之间原本的空白也不能随意相连。"
    "这段文字包含中文、English words、数字1234567890，以及常用标点，"
    "用于验证字体回退、自动换行、固定字号、逐字顺序和重复字形缓存。"
    "生成期间，窗口应继续响应取消操作；生成完成后先在模拟设备验证，"
    "确认原点、抬笔高度和行程范围，再由用户手动启动真实机器。"
    "开失利执张因。重复字形：开失利执张因。"
)
results = []
folder = ROOT / ".runtime" / "performance"
folder.mkdir(parents=True, exist_ok=True)
screenshots = ROOT / ".runtime" / "screenshots"
screenshots.mkdir(parents=True, exist_ok=True)

for size in (14, 18):
    dialog = GenerationDialog(MachineProfile(), "text")
    dialog.font.setCurrentFont(QFont("SimHei"))
    dialog.font_size.setValue(size)
    dialog.indent.setValue(2)
    dialog.text.setPlainText(text)
    dialog.mode.setCurrentIndex(dialog.mode.findData("centerline"))
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
    while dialog.worker is not None and time.perf_counter() - started < 30:
        application.processEvents()
        time.sleep(0.001)
    if dialog.worker is not None:
        dialog.cancel_generation()
        while dialog.worker is not None:
            application.processEvents()
            time.sleep(0.005)
        raise AssertionError("正文中线生成超过30秒")
    timer.stop()
    assert dialog.task is not None, dialog.status.text()
    elapsed = time.perf_counter() - started
    assert elapsed < 15, "约200字正文中线不应超过15秒"
    assert intervals and max(intervals) < 1000, "生成期间窗口阻塞超过1秒"
    results.append({"font_size": size, "characters": len(text), "seconds": elapsed, "paths": dialog.task.path_count, "commands": len(dialog.task.program.commands), "max_heartbeat_ms": max(intervals)})
    dialog.grab().save(str(screenshots / f"text-character-order-{size}pt.png"))
    # 验证关闭和重复生成不覆盖用户偏好。
    dialog.save_preferences = lambda: None
    dialog.reject()

(folder / "text_generation.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
print(json.dumps(results))
