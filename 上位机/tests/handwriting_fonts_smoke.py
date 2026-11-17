"""真实界面选字库、记忆、导出和模拟执行；所有测试配置都放在运行目录。"""

import hashlib
import json
import os
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["TEMP"] = str(ROOT / ".runtime/tmp")
os.environ["TMP"] = os.environ["TEMP"]

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from app.appearance import configure_appearance
from app.builtin_stroke_fonts import CATALOG, DATA, font_info
from app.profile import MachineProfile
from app.stroke_fonts import NaturalOptions, WritingOptions
from app.text_layout import TextOptions, render_page
from app.text_writing import layout_stroke_text
from app.transport import SIMULATOR
from app.ui import generation_dialog
from app.ui.generation_dialog import GenerationDialog
from app.ui.main_window import MainWindow


def wait_until(condition, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if condition():
            return
        time.sleep(.005)
    raise AssertionError("等待界面或模拟设备超时")


def configuration_hashes():
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in (ROOT / "config").rglob("*") if path.is_file()}


application = QApplication.instance() or QApplication([])
configure_appearance(application)
before = configuration_hashes()
folder = ROOT / ".runtime" / "handwriting-review"
folder.mkdir(parents=True, exist_ok=True)
generation_dialog.PREFERENCES = folder / "test-preferences.json"
generation_dialog.PREFERENCES.unlink(missing_ok=True)
catalog = json.loads(CATALOG.read_text(encoding="utf-8"))["fonts"]
dialog = GenerationDialog(MachineProfile(), "text")
dialog.show()
dialog.text.setPlainText("你好世界，永田中文。\nHello 123")
dialog.natural_enabled.setChecked(True)
dialog.natural_strength.setValue(45)
dialog.natural_spacing.setValue(30)
dialog.natural_baseline.setValue(15)
dialog.natural_seed.setValue(2026)
report = []
for entry in catalog:
    identity = "builtin:" + entry["id"]
    index = dialog.stroke_font.findData(identity)
    assert index >= 0, identity
    dialog.stroke_font.setCurrentIndex(index)
    assert entry["source_font"] in dialog.mode_hint.text()
    assert not dialog.stroke_fallback.isEnabled()
    start = time.perf_counter()
    dialog.generate()
    wait_until(lambda: dialog.worker is None)
    assert dialog.task is not None, dialog.status.text()
    dialog.export(path=folder / (entry["id"] + ".ngc"))
    report.append({"font": entry["name"], "source": entry["source_font"], "glyphs": entry["glyphs"], "gb2312_hanzi": entry["gb2312_hanzi"], "generation_seconds": round(time.perf_counter() - start, 3), "paths": dialog.task.path_count, "points": dialog.task.point_count})

# 验证选中的字库和自然书写设置可以保存，真实用户配置与个人录字保持原样。
last_identity = dialog.stroke_font.currentData()
task = dialog.task
dialog.grab().save(str(folder / "text-dialog.png"))
dialog.reject()
dialog = GenerationDialog(MachineProfile(), "text")
assert dialog.stroke_font.currentData() == last_identity
assert (dialog.natural_strength.value(), dialog.natural_spacing.value(), dialog.natural_baseline.value()) == (45, 30, 15)

# 不完整字库必须在生成之前指出缺字，不能回退成楷体或漏字导出。
limited = next(entry for entry in catalog if entry["gb2312_hanzi"] < 6763)
identity = "builtin:" + limited["id"]
dialog.stroke_font.setCurrentIndex(dialog.stroke_font.findData(identity))
missing = font_info(identity)["gb2312_missing"][0]
dialog.text.setPlainText("你好" + missing)
dialog.generate()
wait_until(lambda: dialog.worker is None)
assert dialog.task is None
assert "缺少1种字符" in dialog.status.text(), dialog.status.text()
dialog.reject()

window = MainWindow()
window.profile = MachineProfile()
window.controller.profile = window.profile
window.save_profile = lambda: None
window.confirm = lambda title, message: True
try:
    window.load_generated(task)
    window.port_combo.setCurrentIndex(window.port_combo.findData(SIMULATOR))
    window.toggle_connection()
    wait_until(lambda: window.controller.available)
    assert window.controller.simulated
    window.baseline_check.setChecked(True)
    wait_until(lambda: window.pen_up_button.isEnabled())
    window.pen_up_button.click()
    wait_until(lambda: window.controller.available and window.controller.machine[2] == window.profile.pen_up)
    window.start_job()
    # 模拟器每条命令固定延迟60ms，测试等待时间随完整任务长度增加。
    timeout = max(90, len(task.program.commands) * .1 + 30)
    print(f"Simulator: {len(task.program.commands)} commands, timeout {timeout:.1f}s", flush=True)
    wait_until(lambda: window.job.result == "complete", timeout)
    assert window.controller.state == "Idle"
finally:
    window.controller.disconnect_device()
    window.close()

# 直接绘制最终排版轨迹，图中展示的就是生成G代码所用的单线字形。
for group in range(2):
    entries = catalog[group * 8:(group + 1) * 8]
    image = QImage(1800, len(entries) * 210, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    painter.setFont(QFont("Microsoft YaHei UI", 14))
    for row, entry in enumerate(entries):
        y = row * 210
        painter.setPen(QColor("#25344c"))
        painter.drawText(25, y + 30, f"{entry['name']}  /  来源：{entry['source_font']}  /  简体覆盖 {entry['gb2312_hanzi']}/6763")
        options = TextOptions(paper_width=180, paper_height=15, margin_left=3, margin_right=3, margin_top=1, margin_bottom=1, font_size=24)
        document = layout_stroke_text("你好世界，永田中文。Hello123", options, writing=WritingOptions(builtin_font="builtin:" + entry["id"]))
        painter.drawImage(0, y + 42, render_page(document.pages[0], options, 1800))
        painter.setPen(QColor("#dce3ec"))
        painter.drawLine(15, y + 202, 1785, y + 202)
    painter.end()
    image.save(str(DATA / f"手写字库效果预览{group + 1}.png"))

dialog = GenerationDialog(MachineProfile(), "text")
dialog.show()
dialog.stroke_font.setCurrentIndex(dialog.stroke_font.findData("builtin:hw_yozai"))
dialog.text.setPlainText("你好世界，永田中文。\nHello 123")
dialog.generate()
wait_until(lambda: dialog.worker is None)
assert dialog.task is not None
assert dialog.font_samples_button.isEnabled()
dialog.grab().save(str(folder / "text-dialog.png"))
gallery = []


def inspect_gallery():
    from PySide6.QtWidgets import QLabel

    modal = application.activeModalWidget()
    if modal is not None:
        labels = [label for label in modal.findChildren(QLabel) if label.pixmap() is not None and not label.pixmap().isNull()]
        gallery.append(len(labels))
        modal.grab().save(str(folder / "font-comparison-dialog.png"))
        modal.accept()


QTimer.singleShot(250, inspect_gallery)
dialog.font_samples_button.click()
assert gallery == [2], gallery
dialog.reject()

assert configuration_hashes() == before, "测试不得更改真实配置或个人字库"
(folder / "report.json").write_text(json.dumps({"fonts": report, "simulator": "completed and Idle", "gallery": "two actual trace sheets displayed", "configuration": "unchanged"}, ensure_ascii=False, indent=2), encoding="utf-8")
print("Handwriting fonts smoke passed: all fonts selected, generated and exported; preferences retained; missing glyph blocked; simulator finished and Idle; real configuration unchanged.")
print(json.dumps(report, ensure_ascii=False))
