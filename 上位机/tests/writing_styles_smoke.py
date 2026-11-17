"""新增中文字形、自然参数记忆、实际导出及模拟设备验证，不连接硬件串口。"""

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

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from app.appearance import configure_appearance
from app.builtin_stroke_fonts import BUILTIN_FONTS
from app.profile import MachineProfile
from app.stroke_fonts import NaturalOptions, WritingOptions
from app.text_layout import TextOptions, render_page
from app.text_writing import layout_stroke_text
from app.transport import SIMULATOR
from app.ui import generation_dialog
from app.ui.generation_dialog import GenerationDialog
from app.ui.main_window import MainWindow


def wait_until(condition, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if condition():
            return
        time.sleep(.005)
    raise AssertionError("等待界面或模拟设备超时")


def configuration_hashes():
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / "config").rglob("*") if p.is_file()}


application = QApplication.instance() or QApplication([])
configure_appearance(application)
before = configuration_hashes()
folder = ROOT / ".runtime/writing-styles-smoke"
folder.mkdir(parents=True, exist_ok=True)
generation_dialog.PREFERENCES = folder / "preferences.json"
generation_dialog.PREFERENCES.unlink(missing_ok=True)
dialog = GenerationDialog(MachineProfile(), "text")
dialog.show()
dialog.natural_enabled.setChecked(True)
dialog.natural_strength.setValue(75)
dialog.natural_spacing.setValue(40)
dialog.natural_baseline.setValue(20)
dialog.natural_seed.setValue(2026)
dialog.text.setPlainText("永田中文\nHello 123")
for identity in ("builtin:heiti", "builtin:mingti"):
    dialog.stroke_font.setCurrentIndex(dialog.stroke_font.findData(identity))
    assert "不能保证标准笔顺" in dialog.mode_hint.text()
    assert not dialog.stroke_fallback.isEnabled()
    dialog.generate()
    wait_until(lambda: dialog.worker is None)
    assert dialog.task is not None, dialog.status.text()
    assert any("不承诺标准笔顺" in note for note in dialog.task.notes)
    dialog.export(path=folder / (identity.split(":")[1] + ".ngc"))
    application.processEvents()
    dialog.grab().save(str(folder / (identity.split(":")[1] + "-dialog.png")))
task = dialog.task
dialog.reject()
dialog = GenerationDialog(MachineProfile(), "text")
assert dialog.stroke_font.currentData() == "builtin:mingti"
assert (dialog.natural_strength.value(), dialog.natural_spacing.value(), dialog.natural_baseline.value(), dialog.natural_seed.value()) == (75, 40, 20, 2026)
dialog.reject()

# 旧偏好迁移后仍让三个强度都服从原值；尤其0不能重新启用位置变化。
generation_dialog.PREFERENCES.write_text(json.dumps({"text": {"natural_enabled": True, "natural_strength": 0}}), encoding="utf-8")
dialog = GenerationDialog(MachineProfile(), "text")
assert (dialog.natural_strength.value(), dialog.natural_spacing.value(), dialog.natural_baseline.value()) == (0, 0, 0)
dialog.reject()

window = MainWindow()
window.save_profile = lambda: None
window.confirm = lambda title, message: True
try:
    window.load_generated(task)
    window.port_combo.setCurrentIndex(window.port_combo.findData(SIMULATOR))
    window.toggle_connection()
    wait_until(lambda: window.controller.available)
    window.baseline_check.setChecked(True)
    wait_until(lambda: window.pen_up_button.isEnabled())
    window.pen_up_button.click()
    wait_until(lambda: window.controller.available and window.controller.machine[2] == window.profile.pen_up)
    window.start_job()
    wait_until(lambda: window.job.result == "complete")
    assert window.controller.state == "Idle"
finally:
    window.controller.disconnect_device()
    window.close()

cases = [("楷体 · 原始笔画", WritingOptions()), ("黑体 · 中线，非标准笔顺", WritingOptions(builtin_font="builtin:heiti")), ("明体 · 中线，非标准笔顺", WritingOptions(builtin_font="builtin:mingti")), ("楷体 · 自然字形75 / 字距40 / 起伏20", WritingOptions(natural=NaturalOptions(True, .75, 2026, .4, .2))), ("楷体 · 自然字形100 / 字距40 / 起伏20", WritingOptions(natural=NaturalOptions(True, 1, 2026, .4, .2)))]
image = QImage(1500, 5 * 310, QImage.Format.Format_RGB32)
image.fill(Qt.GlobalColor.white)
painter = QPainter(image)
painter.setFont(QFont("Microsoft YaHei", 14))
for index, (title, writing) in enumerate(cases):
    options = TextOptions(paper_width=180, paper_height=28, margin_left=4, margin_right=4, margin_top=3, margin_bottom=3, font_size=22)
    document = layout_stroke_text("你好世界，天天学习。永永永永田田田田", options, writing=writing)
    painter.drawText(25, index * 310 + 28, title)
    painter.drawImage(0, index * 310 + 45, render_page(document.pages[0], options, 1500))
    painter.setPen(Qt.GlobalColor.lightGray)
    painter.drawLine(15, (index + 1) * 310 - 15, 1485, (index + 1) * 310 - 15)
    painter.setPen(Qt.GlobalColor.black)
painter.end()
image.save(str(folder / "writing-style-comparison.png"))

report = []
for identity in ("", "builtin:heiti", "builtin:mingti"):
    start = time.perf_counter()
    document = layout_stroke_text("你好世界，天天学习。永田口中文测试。" * 12, writing=WritingOptions(builtin_font=identity, natural=NaturalOptions(True, .75, 2026, .4, .2)))
    report.append({"font": identity or "kaiti", "characters": 216, "pages": len(document.pages), "layout_seconds": round(time.perf_counter() - start, 3), "points": sum(len(path) for page in document.pages for line in page.lines for c in line.characters for path in c.strokes)})
assert configuration_hashes() == before, "测试不得更改真实配置或个人字库"
(folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print("Writing styles smoke passed: builtin fonts, independent controls, preference migration, G-code export, simulator Idle, protected configuration unchanged.")
print(json.dumps(report, ensure_ascii=False))
