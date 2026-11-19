"""文字窗口的文档入口、缺字核对、文稿保存和逐页导出。"""

from dataclasses import replace
from datetime import datetime
from pathlib import Path
import json
import re

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget, QFileDialog

from app.generation import GenerationCancelled, GenerationError, checkpoint, save_gcode
from app.page_batches import REPORT_NAME, page_filename
from app.profile import ROOT
from app.text_documents import font_coverage, import_text, read_manuscript, save_manuscript
from app.text_layout import generate_text_task
from app.ui.dialog_window import FunctionDialog
from app.ui.font_messages import confirm_font_action


class PageExportWorker(QThread):
    progress = Signal(int, str)
    completed = Signal(object, str)

    def __init__(self, document, options, first, last, folder, parent=None):
        super().__init__(parent)
        self.document, self.options = document, options
        self.first, self.last, self.folder = first, last, Path(folder)

    def run(self):
        """一页失败不伪装为整批成功；取消保留已写好的文件，并记录未处理页。"""
        records, stopped = [], False
        total = self.last - self.first + 1
        for position, number in enumerate(range(self.first, self.last + 1)):
            record = {"page": number, "status": "未处理"}
            records.append(record)
            if stopped or self.isInterruptionRequested():
                stopped = True
                continue
            page = self.document.pages[number - 1]
            if page.bounds.isEmpty():
                record["status"] = "空白页，跳过"
                continue
            try:
                def progress(value, message):
                    self.progress.emit(round((position + value / 100) / total * 100), f"第{number}页：{message}")
                task = generate_text_task(page, self.document.options, self.options, f"正文任务_第{number:03d}页", self.isInterruptionRequested, progress)
                checkpoint(self.isInterruptionRequested)
                target = save_gcode(task.program, self.folder / page_filename(number))
                record.update(status="成功", file=target.name, commands=len(task.program.commands), bounds=task.program.bounds)
            except GenerationCancelled:
                record["status"] = "已取消"
                stopped = True
            except Exception as error:
                record.update(status="失败", error=str(error) or type(error).__name__)
        error = ""
        try:
            report = {"paper_mm": [self.document.options.paper_width, self.document.options.paper_height], "first": self.first, "last": self.last, "pages": records,
                      "note": "每个文件仅含一页。换纸、检查原点和Z基准后手动开始；没有连接或发送到设备。"}
            (self.folder / REPORT_NAME).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as failure:
            error = "导出结果清单未保存：" + str(failure)
        if not stopped:
            self.progress.emit(100, "分页导出处理结束")
        self.completed.emit(records, error)


class TextDocumentTools(QWidget):
    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog
        self.manuscript_path = None
        self.source_path = None
        self.saved_snapshot = None
        self.track_changes = False
        self.coverage = None
        self.range_total = 0
        self.export_results = None
        self.export_folder = None
        self.word_margins = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.import_button = QPushButton("导入TXT / Word / PDF…")
        self.open_button = QPushButton("打开文稿…")
        row.addWidget(self.import_button)
        row.addWidget(self.open_button)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.save_button = QPushButton("保存文稿")
        self.save_as_button = QPushButton("文稿另存为…")
        row.addWidget(self.save_button)
        row.addWidget(self.save_as_button)
        layout.addLayout(row)
        self.document_note = QLabel("可导入TXT、DOCX或带文字层的PDF。文稿保存正文与参数，个人字库需单独备份。")
        self.document_note.setWordWrap(True)
        layout.addWidget(self.document_note)
        self.word_margins_button = QPushButton("采用Word页边距")
        self.word_margins_button.setVisible(False)
        layout.addWidget(self.word_margins_button)
        row = QHBoxLayout()
        self.coverage_button = QPushButton("检查字库缺字…")
        self.copy_button = QPushButton("复制补录清单")
        row.addWidget(self.coverage_button)
        row.addWidget(self.copy_button)
        layout.addLayout(row)
        self.coverage_note = QLabel("选择个人字库后，可检查全文还需要补录哪些字。")
        self.coverage_note.setWordWrap(True)
        layout.addWidget(self.coverage_note)
        self.page_controls = QWidget()
        row = QHBoxLayout(self.page_controls)
        row.setContentsMargins(0, 0, 0, 0)
        self.first_page, self.last_page = QSpinBox(), QSpinBox()
        for field in (self.first_page, self.last_page):
            field.setRange(1, 1)
            field.setKeyboardTracking(False)
        self.export_button = QPushButton("批量生成并载入任务…")
        row.addWidget(QLabel("任务页码"))
        row.addWidget(self.first_page)
        row.addWidget(QLabel("至"))
        row.addWidget(self.last_page)
        row.addWidget(self.export_button)
        row.addStretch()
        self.import_button.clicked.connect(self.import_document)
        self.open_button.clicked.connect(self.open_manuscript)
        self.save_button.clicked.connect(self.save_document)
        self.save_as_button.clicked.connect(lambda: self.save_document(save_as=True))
        self.coverage_button.clicked.connect(self.show_coverage)
        self.copy_button.clicked.connect(self.copy_recording)
        self.export_button.clicked.connect(self.export_pages)
        self.word_margins_button.clicked.connect(self.apply_word_margins)

    def invalidate_coverage(self):
        self.coverage = None
        self.coverage_note.setText("正文或参数已改变；更新预览时会检查字库覆盖，也可点击“检查字库缺字”。")

    def update_coverage(self):
        if not self.dialog.is_stroke_mode():
            self.coverage_note.setText("当前使用系统字体轮廓/填充/骨架，个人笔画字库不参与生成。")
            return None
        if self.coverage is None:
            writing = self.dialog.writing_options()
            self.coverage = font_coverage(self.dialog.text.toPlainText(), writing)
            self.coverage_note.setText(self.coverage.summary(writing.package is not None) + " 英文、数字与常用标点可使用内置单线。")
        return self.coverage

    def show_coverage(self):
        try:
            result = self.update_coverage()
            if result is None:
                return
            writing = self.dialog.writing_options()
        except (OSError, GenerationError) as error:
            self.dialog.status.setText(str(error))
            return
        dialog = FunctionDialog(self.dialog)
        dialog.setWindowTitle("全文字库覆盖与补录清单")
        dialog.resize(730, 620)
        layout = QVBoxLayout(dialog)
        label = QLabel(result.summary(writing.package is not None) + "\n补录清单按首次出现顺序去重；次数是全文出现次数，不是字形质量评分。")
        label.setWordWrap(True)
        layout.addWidget(label)
        sections = [("建议补录到个人字库", result.recording), ("当前设置无法生成（需要补录或换字体）", result.unavailable),
                    ("将使用基础楷体补缺", result.fallback), ("使用内置英文、标点或所选基础字体", result.standard)]
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText("\n\n".join(title + f"：{len(chars)}种\n" + (" ".join(f"{char}×{result.counts[char]}" for char in chars) or "无") for title, chars in sections))
        layout.addWidget(text)
        row = QHBoxLayout()
        copy = QPushButton("复制补录清单")
        copy.clicked.connect(self.copy_recording)
        row.addWidget(copy)
        close = QPushButton("关闭")
        close.clicked.connect(dialog.accept)
        row.addWidget(close)
        layout.addLayout(row)
        dialog.exec()

    def copy_recording(self):
        try:
            result = self.update_coverage()
            if result is None:
                return
            if result.recording:
                QApplication.clipboard().setText("".join(result.recording))
            self.dialog.status.setText(f"已复制{len(result.recording)}种待录字符，可粘贴到个人字库的自选录字清单。" if result.recording else "本篇没有需要补录的字符。")
        except (OSError, GenerationError) as error:
            self.dialog.status.setText(str(error))

    def snapshot(self):
        settings = self.dialog.preference_data()
        text = settings.pop("text")
        settings.update(pen_up=self.dialog.pen_up.value(), pen_down=self.dialog.pen_down.value())
        result = {"format": "xy-manuscript", "schema": 1, "text": text, "settings": settings,
                  "paper": {"width": self.dialog.profile.paper_width, "height": self.dialog.profile.paper_height}, "page": self.dialog.page_number.value()}
        if self.dialog.text_paragraph_styles is not None:
            result["paragraph_alignments"] = list(self.dialog.text_paragraph_styles)
        if self.dialog.paragraph_font_sizes is not None:
            result["paragraph_font_sizes"] = list(self.dialog.paragraph_font_sizes)
            result["use_document_font_sizes"] = self.dialog.use_document_font_sizes.isChecked()
        return result

    def dirty(self):
        if not self.track_changes:
            return False
        current = self.snapshot()
        return self.saved_snapshot is None or {k: v for k, v in current.items() if k != "page"} != {k: v for k, v in self.saved_snapshot.items() if k != "page"}

    def confirm_replace(self):
        if self.dialog.text.toPlainText().strip() not in ("", "HOB 123") or self.dirty():
            return confirm_font_action(self.dialog, "替换当前正文", "导入后会替换当前正文。需要保留时，请先取消并保存文稿。", "替换正文")
        return True

    def import_document(self, checked=False, path=None):
        if self.dialog.worker is not None:
            return
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self.dialog, "导入正文（TXT / Word / PDF）", str(ROOT), "正文文档 (*.txt *.docx *.pdf);;文本 (*.txt);;Word文档 (*.docx);;PDF文档 (*.pdf)")
        if not path:
            return
        try:
            imported = import_text(path)
            if not self.confirm_replace():
                return
            self.dialog._loading_document = True
            self.dialog.text.setPlainText(imported.text)
            self.dialog._loading_document = False
            self.dialog.layout_mode.setCurrentIndex(self.dialog.layout_mode.findData("page"))
            self.dialog.set_document_format(imported.paragraph_alignments, imported.paragraph_font_sizes)
            self.dialog.update_text_preview()
            self.word_margins = imported.page_margins
            self.word_margins_button.setVisible(self.word_margins is not None)
            self.source_path, self.manuscript_path = Path(path), None
            self.track_changes, self.saved_snapshot = True, None
            self.document_note.setText(f"已导入 {Path(path).name}，{len(imported.text)}字符，尚未保存文稿。\n" + "\n".join(imported.notes))
        except (OSError, GenerationError) as error:
            self.dialog.status.setText("导入失败，原正文保留：" + str(error))

    def apply_word_margins(self):
        if self.word_margins is None or self.dialog.worker is not None:
            return
        dialog = self.dialog
        for name, value in zip(("left", "right", "top", "bottom"), self.word_margins):
            getattr(dialog, "margin_" + name).setValue(value)
        dialog.update_text_preview()

    def save_document(self, checked=False, path=None, save_as=False):
        if self.dialog.worker is not None:
            return False
        if path is None and not save_as:
            path = self.manuscript_path
        if path is None:
            name = self.source_path.stem if self.source_path else "我的文稿"
            default = self.manuscript_path or ROOT / "exports" / (name + ".xytext")
            path, _ = QFileDialog.getSaveFileName(self.dialog, "保存可编辑文稿（不是G代码）", str(default), "写字文稿 (*.xytext)")
        if not path:
            return False
        try:
            snapshot = self.snapshot()
            self.manuscript_path = save_manuscript(snapshot, path)
            self.saved_snapshot, self.track_changes = snapshot, True
            self.document_note.setText(f"已保存文稿：{self.manuscript_path.name}\n包含正文与排版、速度/Z参数；个人字库需单独备份。")
            self.document_note.setToolTip(str(self.manuscript_path))
            self.dialog.status.setText("文稿已保存，可重新打开继续编辑；未连接或发送到设备。")
            return True
        except (OSError, GenerationError) as error:
            self.dialog.status.setText("文稿保存失败：" + str(error))
            return False

    def open_manuscript(self, checked=False, path=None):
        if self.dialog.worker is not None:
            return
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self.dialog, "打开可编辑文稿", str(ROOT / "exports"), "写字文稿 (*.xytext)")
        if not path:
            return
        try:
            data = read_manuscript(path)
            settings, paper = data["settings"], data["paper"]
            if not self.dialog.profile.z_min <= settings["pen_down"] < settings["pen_up"] <= self.dialog.profile.z_max:
                raise GenerationError("文稿的抬落笔位置超出当前设备Z范围，请先使用匹配的设备配置；本次未替换正文。")
            if not self.confirm_replace():
                return
            dialog = self.dialog
            dialog.profile = replace(dialog.profile, paper_width=paper["width"], paper_height=paper["height"])
            dialog.preview.set_paper(paper["width"], paper["height"])
            paper_text = f"{paper['width']:g} × {paper['height']:g} mm（本文稿纸张，主窗口配置未改动）"
            dialog.text_paper_label.setText(paper_text)
            dialog.paper_label.setText(paper_text)
            dialog._loading_document = True
            dialog.restore_preferences(dict(settings, text=data["text"]))
            dialog._loading_document = False
            dialog.pen_up.setValue(settings["pen_up"])
            dialog.pen_down.setValue(settings["pen_down"])
            dialog.update_mode()
            dialog.set_document_format(tuple(data.get("paragraph_alignments") or ()) or None,
                                       tuple(data.get("paragraph_font_sizes") or ()) or None, data.get("use_document_font_sizes", True))
            dialog.update_text_preview()
            self.word_margins = None
            self.word_margins_button.setVisible(False)
            if dialog.text_document is not None:
                dialog.page_number.setValue(min(data["page"], len(dialog.text_document.pages)))
            self.manuscript_path, self.source_path = Path(path), None
            self.track_changes, self.saved_snapshot = True, self.snapshot()
            self.document_note.setText(f"已打开文稿：{Path(path).name}\n纸张与Z位置仅恢复到本生成窗口；请确认适用于当前纸张和夹笔高度。字库缺失时需先恢复对应字库文件。")
        except (OSError, GenerationError) as error:
            self.dialog.status.setText("打开文稿失败：" + str(error))

    def confirm_close(self):
        if not self.dirty():
            return True
        box = QMessageBox(QMessageBox.Icon.Question, "文稿尚未保存", "本次导入或打开的文稿有未保存内容，是否保存后关闭？", parent=self.dialog)
        save = box.addButton("保存文稿", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("不保存并关闭", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save)
        box.setEscapeButton(cancel)
        box.exec()
        return self.save_document() if box.clickedButton() == save else box.clickedButton() == discard

    def sync_pages(self, total):
        changed = total != self.range_total
        self.range_total = total
        self.first_page.setMaximum(total)
        self.last_page.setMaximum(total)
        if changed:
            self.first_page.setValue(1)
            self.last_page.setValue(total)

    def set_busy(self, busy):
        self.setEnabled(not busy)
        self.page_controls.setEnabled(not busy and self.dialog.text_document is not None)

    def export_pages(self, checked=False, path=None, first=None, last=None):
        dialog = self.dialog
        if dialog.worker is not None or not dialog.is_page_layout():
            return
        try:
            if dialog.profile.z_mode != "stepper":
                raise GenerationError("当前仅支持步进Z任务。")
            dialog.text_timer.stop()
            dialog.refresh_text_source()
            options = dialog.options()
            options.validate()
            first = self.first_page.value() if first is None else first
            last = self.last_page.value() if last is None else last
            if type(first) is not int or type(last) is not int or not 1 <= first <= last <= len(dialog.text_document.pages):
                raise GenerationError("导出页码范围无效：起始页不能大于结束页。")
            if path is None:
                parent = QFileDialog.getExistingDirectory(dialog, "选择任务保存位置（生成后自动返回主窗口）", str(ROOT / "exports"))
                if not parent:
                    return
                name = self.manuscript_path.stem if self.manuscript_path else self.source_path.stem if self.source_path else "正文任务"
                name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:60] or "正文任务"
                path = Path(parent) / f"{name}_分页_{datetime.now():%Y%m%d_%H%M%S_%f}"
            self.export_folder = Path(path)
            self.export_folder.mkdir(parents=True, exist_ok=False)
        except (OSError, GenerationError) as error:
            dialog.status.setText("分页导出未开始：" + str(error))
            return
        self.export_results = None
        self.export_error = ""
        dialog.cancel_requested = False
        dialog.progress.setValue(0)
        dialog.worker = PageExportWorker(dialog.text_document, options, first, last, self.export_folder, dialog)
        dialog.worker.progress.connect(dialog.on_progress)
        dialog.worker.completed.connect(self.export_completed)
        dialog.worker.finished.connect(self.export_finished)
        dialog.set_busy(True)
        dialog.worker.start()

    def export_completed(self, records, error):
        self.export_results, self.export_error = records, error

    def export_finished(self):
        dialog = self.dialog
        dialog.worker.deleteLater()
        dialog.worker = None
        records = self.export_results or []
        success = sum(row["status"] == "成功" for row in records)
        blank = sum(row["status"] == "空白页，跳过" for row in records)
        failed = sum(row["status"] == "失败" for row in records)
        unfinished = len(records) - success - blank - failed
        message = f"分页导出结束：成功{success}页，空白跳过{blank}页，失败{failed}页，取消/未处理{unfinished}页。\n位置：{self.export_folder}。每页独立换纸并手动开始。"
        if failed:
            message += "\n" + "\n".join(f"第{row['page']}页：{row['error']}" for row in records if row["status"] == "失败")[:1200]
        if self.export_error:
            message += "\n" + self.export_error
        self.document_note.setText(f"分页导出：成功{success}页，空白{blank}页，失败{failed}页，取消/未处理{unfinished}页。\n请到所选目录的新建任务文件夹查看；详细路径见下方状态或此处提示。")
        self.document_note.setToolTip(message)
        dialog.status.setText(message)
        dialog.set_busy(False)
        if success and not failed and unfinished == 0 and not dialog.close_pending and not dialog.cancel_requested:
            paths = [self.export_folder / record["file"] for record in records if record["status"] == "成功"]
            dialog.batch_paths = paths
            dialog.save_preferences()
            dialog.preview_timer.stop()
            dialog.text_timer.stop()
            dialog.accept()
        if dialog.close_pending:
            dialog.close_pending = False
            dialog.reject()
