import hashlib
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch
from uuid import uuid4
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["TEMP"] = str(ROOT / ".runtime" / "tmp")
os.environ["TMP"] = os.environ["TEMP"]

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QPainter, QPdfWriter
from PySide6.QtWidgets import QApplication, QMessageBox

from app.appearance import configure_appearance
from app.gcode import load_program
from app.generation import GenerationError
from app.page_batches import REPORT_NAME, read_page_batch
from app.profile import MachineProfile
from app.stroke_fonts import FontPackage, FontStore
from app.text_documents import read_manuscript
from app.text_layout import TextDocument, TextPage, generate_text_task
from app.transport import SIMULATOR
from app.ui import generation_dialog
from app.ui.generation_dialog import GenerationDialog
from app.ui.main_window import MainWindow
from app.ui.text_document_tools import PageExportWorker


def wait_until(condition, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if condition():
            return
        time.sleep(.005)
    raise AssertionError("文稿或分页导出等待超时")


def click_message(text):
    for window in application.topLevelWidgets():
        if isinstance(window, QMessageBox) and window.isVisible():
            for button in window.buttons():
                if button.text() == text:
                    button.click()
                    return
    raise AssertionError("没有找到中文确认按钮：" + text)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


application = QApplication([])
configure_appearance(application)
folder = ROOT / ".runtime" / "text-documents-smoke" / uuid4().hex
folder.mkdir(parents=True)
generation_dialog.PREFERENCES = folder / "preferences.json"
protected = {path: digest(path) for path in (ROOT / "config" / "machine.json", ROOT / "config" / "generation.json")}

body = "\n".join(f"第{i + 1}段：中文写字测试，HOB 123。" for i in range(25))
word = folder / "正文测试.docx"
with ZipFile(word, "w") as archive:
    namespace = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    paragraphs = ['<w:p>' + ('<w:pPr><w:pStyle w:val="Title"/></w:pPr>' if i == 0 else '') + f'<w:r><w:t>{line}</w:t></w:r></w:p>' for i, line in enumerate(body.splitlines())]
    section = '<w:sectPr><w:pgMar w:left="1800" w:right="1800" w:top="1440" w:bottom="1440"/></w:sectPr>'
    archive.writestr("word/document.xml", f'<w:document {namespace}><w:body>' + ''.join(paragraphs) + section + '</w:body></w:document>')
    default_size = '<w:docDefaults><w:rPrDefault><w:rPr><w:sz w:val="24"/></w:rPr></w:rPrDefault></w:docDefaults>'
    title_style = '<w:style w:type="paragraph" w:styleId="Title"><w:pPr><w:jc w:val="center"/></w:pPr><w:rPr><w:sz w:val="48"/></w:rPr></w:style>'
    archive.writestr("word/styles.xml", f'<w:styles {namespace}>{default_size}{title_style}</w:styles>')
source_digest = digest(word)

profile = MachineProfile(pen_up=3, pen_down=-.35)
dialog = GenerationDialog(profile, "text")
dialog.show()
tools = dialog.document_tools
tools.confirm_replace = lambda: True
tools.import_document(path=word)
assert dialog.text.toPlainText() == body
assert dialog.is_page_layout() and dialog.worker is None and tools.dirty()
assert "已导入" in tools.document_note.text()
assert dialog.use_document_font_sizes.isVisible() and dialog.use_document_font_sizes.isChecked()
tools.apply_word_margins()
assert dialog.margin_left.value() == 31.75 and dialog.margin_top.value() == 25.4
assert dialog.profile.paper_width == 210 and not dialog.alignment.isEnabled()
dialog.margin_bottom.setValue(235)
dialog.font_size.setValue(14)
dialog.letter_spacing.setValue(-5)
dialog.natural_enabled.setChecked(True)
dialog.natural_seed.setValue(827)
dialog.up_feed.setValue(920)
dialog.down_feed.setValue(870)
dialog.update_text_preview()
assert len(dialog.text_document.pages) >= 3, dialog.status.text()
for mode in ("outline", "strokes"):
    dialog.mode.setCurrentIndex(dialog.mode.findData(mode))
    dialog.indent.setValue(2)
    dialog.update_text_preview()
    title, first_paragraph = dialog.text_document.pages[0].lines[:2]
    assert title.font_size == 24 and first_paragraph.font_size == 12
    assert title.height > first_paragraph.height * 1.8
    assert abs(title.path.boundingRect().center().x() - 105) < 2
    assert first_paragraph.path.boundingRect().left() > 39 and first_paragraph.path.boundingRect().center().x() < 105
dialog.use_document_font_sizes.setChecked(False)
dialog.update_text_preview()
assert all(line.font_size == 14 for line in dialog.text_document.pages[0].lines)
assert abs(dialog.text_document.pages[0].lines[0].path.boundingRect().center().x() - 105) < 2
dialog.use_document_font_sizes.setChecked(True)
dialog.update_text_preview()
dialog.page_number.setValue(2)

# 个人库只含一字时，补缺开启仍能得到完整的去重补录清单。
store = FontStore(folder / "fonts")
package = FontPackage("测试个人体", {"中": {"advance": 1, "variants": [{"strokes": [[(.1, .1), (.8, .8)]]}]}})
identity = store.save(package)
dialog.font_store = store
dialog.refresh_stroke_fonts(identity)
dialog.update_mode()
dialog.update_text_preview()
coverage = tools.update_coverage()
assert "中" in coverage.own and "文" in coverage.recording and not coverage.unavailable
tools.copy_recording()
assert QApplication.clipboard().text() == ''.join(coverage.recording)
assert len(set(QApplication.clipboard().text())) == len(QApplication.clipboard().text())

draft = folder / "我的文稿.xytext"
dialog.page_number.setValue(2)
assert tools.save_document(path=draft), dialog.status.text()
assert not tools.dirty()
data = read_manuscript(draft)
assert data["settings"]["pen_up"] == 3 and data["settings"]["pen_down"] == -.35
assert data["settings"]["natural_seed"] == 827 and data["settings"]["stroke_font"] == identity
assert data["settings"]["letter_spacing"] == -5
assert data["paragraph_font_sizes"] == [24] + [12] * 24 and data["use_document_font_sizes"]
assert data["page"] == 2
assert data["paragraph_alignments"] == ["center"] + ["left"] * 24
application.processEvents()
dialog.grab().save(str(folder / "文档导入与缺字检查.png"))

# 正常关闭已保存文稿不询问；另一设备纸张打开后仅修改生成窗口的副本。
dialog.reject()
other_profile = MachineProfile(paper_width=200, paper_height=280, pen_up=4)
restored = GenerationDialog(other_profile, "text")
restored.font_store = store
restored.document_tools.confirm_replace = lambda: True
restored.document_tools.open_manuscript(path=draft)
assert restored.text.toPlainText() == body
assert restored.profile.paper_width == 210 and other_profile.paper_width == 200
assert restored.profile.paper_height == 297 and other_profile.paper_height == 280
assert restored.pen_up.value() == 3 and restored.pen_down.value() == -.35
assert restored.up_feed.value() == 920 and restored.down_feed.value() == 870
assert restored.natural_seed.value() == 827 and restored.natural_enabled.isChecked()
assert restored.letter_spacing.value() == -5
assert restored.use_document_font_sizes.isChecked() and restored.text_document.pages[0].lines[0].font_size == 24
assert restored.page_number.value() == 2 and not restored.document_tools.dirty()
assert restored.text_paragraph_styles == tuple(data["paragraph_alignments"])

# 损坏文稿或Z超界不能替换当前正文。
invalid = folder / "坏文稿.xytext"
invalid.write_text('{bad', encoding="utf-8")
before = restored.document_tools.snapshot()
restored.document_tools.open_manuscript(path=invalid)
assert restored.document_tools.snapshot() == before
bad_z = json.loads(draft.read_text(encoding="utf-8"))
bad_z["settings"]["pen_up"] = 99
invalid.write_text(json.dumps(bad_z, ensure_ascii=False), encoding="utf-8")
restored.document_tools.open_manuscript(path=invalid)
assert restored.document_tools.snapshot() == before

# 批量生成真正的三页G代码，并逐页重新解析核对Z与纸张范围。
restored.show()
restored.document_tools.export_pages(path=folder / "batch", first=1, last=3)
wait_until(lambda: restored.worker is None)
records = restored.document_tools.export_results
assert len(records) == 3 and all(row["status"] == "成功" for row in records), records
for record in records:
    program = load_program(folder / "batch" / record["file"], pen_threshold=1.3)
    assert program.z_bounds == (-.35, 3)
    assert program.bounds[0] >= 0 and program.bounds[1] >= 0 and program.bounds[2] <= 210 and program.bounds[3] <= 297
    assert "F920" in program.source_text and "F870" in program.source_text
assert len(list((folder / "batch").glob("*.ngc"))) == 3
assert (folder / "batch" / "导出结果.json").exists()
assert not restored.isVisible() and len(restored.batch_paths) == 3
restored.show()

# 取消与无效页码有明确结果，不覆盖已存在的文件夹。
restored.document_tools.export_pages(path=folder / "invalid-range", first=3, last=1)
assert restored.worker is None and not (folder / "invalid-range").exists()
restored.document_tools.export_pages(path=folder / "batch", first=1, last=1)
assert restored.worker is None and len(list((folder / "batch").glob("*.ngc"))) == 3
restored.document_tools.export_pages(path=folder / "cancelled", first=1, last=3)
restored.cancel_generation()
wait_until(lambda: restored.worker is None)
assert any(row["status"] in ("已取消", "未处理") for row in restored.document_tools.export_results)
assert restored.generate_button.isEnabled()

# 逐页失败、空白页、取消状态也写入清单；测试实际成功页仍可导出。
page = restored.text_document.pages[0]
document = TextDocument(restored.text_document.options, [page, page, TextPage()])
output = folder / "partial"
output.mkdir()
worker = PageExportWorker(document, restored.options(), 1, 3, output)
real_task = generate_text_task(page, document.options, restored.options())
with patch("app.ui.text_document_tools.generate_text_task", side_effect=[GenerationError("测试页轨迹超界"), real_task]):
    worker.run()
report = json.loads((output / REPORT_NAME).read_text(encoding="utf-8"))
assert [row["status"] for row in report["pages"]] == ["失败", "成功", "空白页，跳过"]
assert not (output / "第001页.ngc").exists() and (output / "第002页.ngc").exists()
assert read_page_batch(output) == ((210, 297), {2: output / "第002页.ngc"}, {1: "第1页导出状态为“失败”"})

# 未保存文稿的关闭按钮可取消，也可明确放弃；测试不弹给用户。
restored.text.appendPlainText("新增段落")
QTimer.singleShot(20, lambda: click_message("取消"))
restored.close()
assert restored.isVisible()

# 正在生成时点关闭，取消线程后仍需询问未保存文稿，不能绕过保存确认。
answers = []
message_timer = QTimer()
def cancel_when_visible():
    for window in application.topLevelWidgets():
        if isinstance(window, QMessageBox) and window.isVisible():
            click_message("取消")
            answers.append(True)
            message_timer.stop()
            return
message_timer.timeout.connect(cancel_when_visible)
message_timer.start(10)
restored.generate()
assert restored.worker is not None, restored.status.text()
restored.close()
wait_until(lambda: answers and restored.worker is None)
assert restored.isVisible() and not restored.close_pending
QTimer.singleShot(20, lambda: click_message("不保存并关闭"))
restored.close()
assert not restored.isVisible()

# 字库文件不随文稿嵌入；缺库时保留原选择并提示，不悄悄换成另一套字体。
missing = GenerationDialog(profile, "text")
missing.font_store = FontStore(folder / "empty-fonts")
missing.document_tools.confirm_replace = lambda: True
missing.document_tools.open_manuscript(path=draft)
assert missing.stroke_font.currentData() == identity and "不存在" in missing.stroke_font.currentText()
assert missing.text_document is None and missing.task is None
missing.document_tools.track_changes = False
missing.reject()

pdf = folder / "正文测试.pdf"
writer = QPdfWriter(str(pdf))
writer.setResolution(72)
painter = QPainter(writer)
painter.setFont(QFont("Microsoft YaHei", 18))
painter.drawText(40, 50, "一 I")
assert writer.newPage()
painter.drawText(40, 50, "二 T")
painter.end()
del painter, writer
pdf_digest = digest(pdf)

# 从PDF导入开始，验证两页都能通过真实的批量生成和模拟发送流程。
window = MainWindow()
window.profile = window.controller.profile = MachineProfile()
window.save_profile = lambda: None
window.show()
queue_folder = folder / "queue"

def prepare_batch():
    dialog = application.activeModalWidget()
    assert isinstance(dialog, GenerationDialog)
    dialog.mode.setCurrentIndex(dialog.mode.findData("strokes"))
    dialog.stroke_font.setCurrentIndex(dialog.stroke_font.findData(""))
    dialog.margin_top.setValue(20)
    dialog.margin_bottom.setValue(268)
    dialog.document_tools.confirm_replace = lambda: True
    dialog.document_tools.import_document(path=pdf)
    if dialog.text.toPlainText() != "一 I\n二 T" or dialog.use_document_font_sizes.isVisible():
        dialog.document_tools.track_changes = False
        dialog.reject()
        raise AssertionError(repr(dialog.text.toPlainText()))
    dialog.document_tools.export_pages(path=queue_folder, first=1, last=2)
    if dialog.worker is None:
        dialog.document_tools.track_changes = False
        dialog.reject()
        raise AssertionError(dialog.status.text())

QTimer.singleShot(0, prepare_batch)
window.text_button.click()
wait_until(lambda: not window.loading and window.job.program is not None)
assert not window.job.active and window.job.program.path.name == "第001页.ngc"
assert len(window.batch_paths) == 2 and window.batch_prompt is None
confirmations = []
window.confirm = lambda title, message: confirmations.append(title) or True
window.port_combo.setCurrentIndex(window.port_combo.findData(SIMULATOR))
window.toggle_connection()
wait_until(lambda: window.controller.available)
window.baseline_check.setChecked(True)
wait_until(lambda: window.pen_up_button.isEnabled())
window.pen_up_button.click()
wait_until(lambda: window.controller.pen_clear() and window.start_button.isEnabled())
window.start_button.click()
wait_until(lambda: window.batch_prompt is not None and window.batch_next_button.isEnabled() and window.controller.available)
assert window.job.result == "complete" and window.controller.state == "Idle"
assert not window.start_button.isEnabled() and window.origin_button.isEnabled()
assert window.batch_prompt.windowModality().value == 0
assert window.job.program.path.name == "第001页.ngc"
old_origin = window.controller.g54_offset
window.origin_button.click()
wait_until(lambda: window.controller.g54_offset != old_origin and window.batch_next_button.isEnabled() and window.controller.available)
window.batch_next_button.click()
wait_until(lambda: window.job.active and window.job.program.path.name == "第002页.ngc")
wait_until(lambda: not window.batch_paths and window.job.result == "complete")
assert confirmations == ["开始文件任务", "设置工作原点"]
click_message("确定")

# 从主窗口重新打开既有导出，选中真实页码后只预览。
def reopen_batch(task_folder, choice, choices):
    with patch("app.ui.main_window.QFileDialog.getExistingDirectory", return_value=str(task_folder)), patch(
            "app.ui.main_window.QInputDialog.getItem", return_value=(choice, True)) as picker:
        window.open_batch_button.click()
    assert picker.call_args.args[3] == choices


reopen_batch(queue_folder, "第2页", ["第1页", "第2页"])
wait_until(lambda: not window.loading and window.start_button.isEnabled())
assert len(window.batch_paths) == 1 and window.job.program.path.name == "第002页.ngc"
assert "文档第2页" in window.statusBar().currentMessage()
assert not window.job.active and window.job.sent == 0 and not window.open_batch_button.isEnabled()
window.start_button.click()
wait_until(lambda: not window.batch_paths and window.job.result == "complete")
click_message("确定")

# 旧导出中的空白页明确跳过。缺页则阻止从更早位置运行。
gap_folder = folder / "gap"
gap_folder.mkdir()
report_path = gap_folder / REPORT_NAME
report_path.write_text(json.dumps({"paper_mm": [148, 210], "first": 4, "last": 7, "pages": [
    {"page": 4, "status": "成功", "file": "第004页.ngc"},
    {"page": 5, "status": "空白页，跳过"},
    {"page": 6, "status": "成功", "file": "第006页.ngc"},
    {"page": 7, "status": "成功", "file": "第007页.ngc"},
]}, ensure_ascii=False), encoding="utf-8")
source_code = (queue_folder / "第001页.ngc").read_bytes()
for name in ("第004页.ngc", "第007页.ngc"):
    (gap_folder / name).write_bytes(source_code)
reopen_batch(gap_folder, "第4页", ["第4页", "第7页"])
assert "找不到第6页" in window.notice_label.text() and not window.batch_paths
assert window.job.program.path.name == "第002页.ngc" and not window.job.active
reopen_batch(gap_folder, "第7页", ["第4页", "第7页"])
wait_until(lambda: not window.loading and window.start_button.isEnabled())
assert len(window.batch_paths) == 1 and window.job.program.path.name == "第007页.ngc"
window.stop_button.click()

# 补齐缺页后验证真实页码跳转及从换纸提示结束。
(gap_folder / "第006页.ngc").write_bytes(source_code)
reopen_batch(gap_folder, "第4页", ["第4页", "第6页", "第7页"])
wait_until(lambda: not window.loading and window.start_button.isEnabled())
window.start_button.click()
wait_until(lambda: window.batch_prompt is not None)
assert "第4页已完成" in window.batch_prompt.text() and "接下来书写第6页" in window.batch_prompt.text()
click_message("结束分页任务")
assert not window.batch_paths and window.job.program.path.name == "第004页.ngc" and not window.job.active
wait_until(lambda: window.loader is None)
window.close()

assert digest(word) == source_digest
assert digest(pdf) == pdf_digest
for path, original in protected.items():
    assert digest(path) == original, str(path) + "被测试改动"
print("Text documents GUI: PASS (DOCX layout/draft/close/PDF import/simulator pagination, reopen, and stop)")
print("Artifacts: " + str(folder))
