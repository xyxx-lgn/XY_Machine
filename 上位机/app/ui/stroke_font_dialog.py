"""自定义单线字库：按实际落笔录制，一个字可保存多个版本。"""

import json
import os
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QPlainTextEdit,
    QPushButton, QTabWidget, QVBoxLayout, QWidget
)

from app.generation import GenerationError, simplify
from app.profile import ROOT
from app.stroke_fonts import FontPackage, FontStore, package_data, write_package
from app.handwriting_samples import SAMPLE_PLAN, SAMPLE_PLANS, export_recorded_samples, export_sample_sheet, sample_plan
from app.ui.photo_font_dialog import PhotoFontDialog
from app.ui.sdt_dialog import SdtDialog
from app.ui.dialog_window import FunctionDialog


class StrokeCanvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.strokes, self.active = [], []
        self.advance = 1.0
        self.guide_character = ""
        self.guide_enabled = True
        self.show_numbers = False
        # 显式加载系统楷体，离屏运行和不同Qt字体别名下也有一致的参考字。
        font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/simkai.ttf"
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
        self.replay_count = None
        self.timer = QTimer(self)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self.replay_step)
        self.setMinimumSize(330, 330)
        self.setAttribute(Qt.WidgetAttribute.WA_TabletTracking)

    def square(self):
        side = min(self.width(), self.height()) - 36
        return QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)

    def set_strokes(self, strokes, advance=1):
        self.timer.stop()
        self.replay_count = None
        self.active = []
        self.advance = advance
        self.strokes = [[tuple(point) for point in path] for path in strokes]
        self.update()

    def point(self, position):
        box = self.square()
        return (max(.002, min(.998, (position.x() - box.left()) / box.width())) * self.advance,
                max(.002, min(.998, (position.y() - box.top()) / box.height())))

    def start(self, position):
        if self.square().contains(position):
            self.timer.stop()
            self.replay_count = None
            self.active = [self.point(position)]
            self.update()

    def move(self, position):
        if self.active:
            point = self.point(position)
            if point != self.active[-1]:
                self.active.append(point)
            self.update()

    def finish(self, position):
        if not self.active:
            return
        self.move(position)
        path = simplify(self.active, .001)
        if len(path) == 1:
            x, y = path[0]
            path.append((max(.001, x - .004 * self.advance), y))
        self.strokes.append(path)
        self.active = []
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start(event.position())

    def mouseMoveEvent(self, event):
        self.move(event.position())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.finish(event.position())

    def tabletEvent(self, event):
        if event.type() == event.Type.TabletPress:
            self.start(event.position())
        elif event.type() == event.Type.TabletMove:
            self.move(event.position())
        elif event.type() == event.Type.TabletRelease:
            self.finish(event.position())
        event.accept()

    def undo(self):
        self.timer.stop()
        self.replay_count = None
        if self.strokes:
            self.strokes.pop()
        self.active = []
        self.update()

    def replay(self):
        self.replay_count = 0
        self.timer.start()
        self.update()

    def replay_step(self):
        self.replay_count += 1
        if self.replay_count >= sum(len(path) for path in self.strokes):
            self.timer.stop()
            self.replay_count = None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#f5f8fc"))
        box = self.square()
        painter.fillRect(box, Qt.GlobalColor.white)
        painter.setPen(QPen(QColor("#bfcddd"), 1))
        painter.drawRect(box)
        painter.setPen(QPen(QColor("#dde6ef"), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(box.center().x(), box.top()), QPointF(box.center().x(), box.bottom()))
        painter.drawLine(QPointF(box.left(), box.center().y()), QPointF(box.right(), box.center().y()))
        painter.drawLine(box.topLeft(), box.bottomRight())
        painter.drawLine(box.topRight(), box.bottomLeft())
        if self.guide_enabled and self.guide_character:
            painter.save()
            font = QFont("KaiTi")
            font.setPixelSize(max(12, round(box.height() * .72)))
            metrics = QFontMetricsF(font)
            bounds = metrics.boundingRect(self.guide_character)
            fit = min(1.0, box.width() * .78 / max(1, bounds.width()), box.height() * .78 / max(1, bounds.height()))
            font.setPixelSize(max(12, round(font.pixelSize() * fit)))
            painter.setFont(font)
            painter.setPen(QColor("#d9e0e8"))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, self.guide_character)
            painter.restore()
        number_font = QFont("Microsoft YaHei UI")
        number_font.setPixelSize(12)
        painter.setFont(number_font)
        remaining = self.replay_count
        for index, path in enumerate(self.strokes + ([self.active] if self.active else [])):
            visible = path if remaining is None else path[:max(0, remaining)]
            if remaining is not None:
                remaining -= len(path)
            painter.setPen(QPen(QColor.fromHsv(index * 37 % 360, 180, 150), 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            points = [QPointF(box.left() + x / self.advance * box.width(), box.top() + y * box.height()) for x, y in visible]
            for a, b in zip(points, points[1:]):
                painter.drawLine(a, b)
            if points and self.show_numbers:
                painter.drawText(points[0] + QPointF(3, -4), str(index + 1))
        painter.setPen(QColor("#3b4d65"))
        painter.setFont(QFont("Microsoft YaHei UI", 9))
        painter.drawText(12, self.height() - 5, f"已录{len(self.strokes)}笔 · 按下到抬起算一笔 · 支持鼠标/数位板")
        painter.end()

    def set_guide(self, character, enabled=True):
        self.guide_character = character[:1]
        self.guide_enabled = enabled
        self.update()


class StrokeFontDialog(FunctionDialog):
    def __init__(self, selected="", parent=None, store=None):
        super().__init__(parent)
        self.store = store or FontStore()
        self.package = FontPackage("我的手写体")
        self.saved_data = json.dumps(package_data(self.package), ensure_ascii=False)
        self.selected_id = selected
        self.setWindowTitle("个人字库 · 手写录入 / 照片录入")
        self.resize(1040, 760)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.fonts = QComboBox()
        self.fonts.addItem("新建个人字库", "")
        for package in self.store.catalog():
            self.fonts.addItem(f"{package.name}（{len(package.glyphs)}字）", package.id)
        top.addWidget(self.fonts, 1)
        self.name = QLineEdit(self.package.name)
        top.addWidget(QLabel("字库名称"))
        top.addWidget(self.name, 1)
        self.add_button(top, "保存字库", self.save_font)
        self.delete_button = self.add_button(top, "删除当前字库", self.delete_font)
        self.add_button(top, "导入JSON…", self.import_font)
        self.add_button(top, "导出JSON…", self.export_font)
        layout.addLayout(top)
        tip = QLabel("先输入一个字，再按自己的笔顺书写。每个字可录制多个版本，自然书写时会选用不同版本。完成录字后点击“保存字库”；画板和回放均不会控制机器。")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        edit = QWidget()
        row = QHBoxLayout(edit)
        left = QVBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("查找已录字符")
        left.addWidget(self.search)
        self.characters = QListWidget()
        left.addWidget(self.characters)
        row.addLayout(left, 1)
        column = QVBoxLayout()
        controls = QHBoxLayout()
        self.character = QLineEdit("永")
        self.character.setMaxLength(2)
        self.character.setMaximumWidth(65)
        self.variants = QComboBox()
        self.advance = QDoubleSpinBox()
        self.advance.setRange(.2, 2)
        self.advance.setSingleStep(.1)
        self.advance.setValue(1)
        controls.addWidget(QLabel("当前字"))
        controls.addWidget(self.character)
        controls.addWidget(self.variants, 1)
        controls.addWidget(QLabel("字框宽"))
        controls.addWidget(self.advance)
        column.addLayout(controls)
        self.canvas = StrokeCanvas()
        column.addWidget(self.canvas, 1)
        guide_row = QHBoxLayout()
        self.guide_checkbox = QCheckBox("显示楷体参考字（只作大小和位置参考，不会保存到笔迹）")
        self.guide_checkbox.setChecked(True)
        self.guide_checkbox.stateChanged.connect(lambda: self.canvas.set_guide(self.character.text(), self.guide_checkbox.isChecked()))
        guide_row.addWidget(self.guide_checkbox)
        self.number_checkbox = QCheckBox("显示笔顺编号")
        self.number_checkbox.toggled.connect(self.set_number_visibility)
        guide_row.addWidget(self.number_checkbox)
        guide_row.addStretch()
        column.addLayout(guide_row)
        actions = QHBoxLayout()
        self.add_button(actions, "清空画板", lambda: self.canvas.set_strokes([], self.advance.value()))
        self.add_button(actions, "撤销一笔", self.canvas.undo)
        self.add_button(actions, "回放笔顺", self.canvas.replay)
        self.add_button(actions, "添加为新版本", self.add_variant)
        self.add_button(actions, "替换当前版本", self.replace_variant)
        column.addLayout(actions)
        navigation = QHBoxLayout()
        self.record_progress = QLabel()
        navigation.addWidget(self.record_progress, 1)
        self.add_button(navigation, "录清单下一个未录字", self.begin_unrecorded)
        self.add_button(navigation, "保存此格并录下一格", self.save_and_next)
        column.addLayout(navigation)
        self.add_button(column, "移除当前版本（保存字库后生效）", self.remove_variant)
        row.addLayout(column, 3)
        self.tabs.addTab(edit, "单字录制")
        samples = QWidget()
        column = QVBoxLayout(samples)
        sample_tip = QLabel("100格只是起步计划。支持300、1000、3755、6763字和自选文章；不用一次录完，保存字库后可继续。基础100格保留同字多版本格，自选文章会合并重复字。")
        sample_tip.setWordWrap(True)
        column.addWidget(sample_tip)
        plan_row = QHBoxLayout()
        self.plan_preset = QComboBox()
        for title in SAMPLE_PLANS:
            self.plan_preset.addItem(title, title)
        self.plan_input = QPlainTextEdit()
        self.plan_input.setPlaceholderText("也可以粘贴姓名、文章或字符清单；空白和重复字会自动处理")
        self.plan_input.setMaximumHeight(76)
        self.apply_plan_button = self.add_button(plan_row, "应用录字清单", self.apply_record_plan)
        plan_row.insertWidget(0, self.plan_preset)
        plan_row.insertWidget(1, self.plan_input, 1)
        column.addLayout(plan_row)
        self.plan_status = QLabel()
        self.plan_status.setWordWrap(True)
        column.addWidget(self.plan_status)
        self.sample_plan = list(self.package.record_plan or SAMPLE_PLAN)
        self.samples = QListWidget()
        self.refresh_samples()
        self.samples.setCurrentRow(0)
        column.addWidget(self.samples, 1)
        actions = QHBoxLayout()
        self.add_button(actions, "去画板录这一格", self.begin_sample)
        self.add_button(actions, "保存当前画板到所选格", self.save_sample)
        self.add_button(actions, "导出当前采样表…", self.export_sheet)
        self.add_button(actions, "导出AI参考样本…", self.export_samples)
        column.addLayout(actions)
        self.tabs.addTab(samples, "批量录字清单")
        photos = QWidget()
        photo_layout = QVBoxLayout(photos)
        photo_tip = QLabel("手机照片可离线自动识字和分字，也保留采样表清单、手工逐字框选。新增自动去纸面底色，以及原始单字、黑白和轨迹对照；识字候选需要核对错字和裁切后收录。可累计多张照片再另存字库；照片不含真实笔顺。")
        photo_tip.setWordWrap(True)
        photo_layout.addWidget(photo_tip)
        self.add_button(photo_layout, "导入照片并制作个人字库…", self.import_photo_samples)
        self.add_button(photo_layout, "导出当前录字清单的A4采样表…", self.export_sheet)
        photo_layout.addStretch()
        self.tabs.addTab(photos, "照片录字")
        ai = QWidget()
        ai_layout = QVBoxLayout(ai)
        ai_tip = QLabel("先用真实录字导出参考样本，再选择扩字方式。“离线AI轨迹扩字”使用本地预训练模型推断未录字的笔形和抬落笔；“个人笔迹学习”保留原来的几何笔形与部件迁移。建议先录15～30个不同结构的字，生成后回放核对。结果另存，原始字库保留。")
        ai_tip.setWordWrap(True)
        ai_layout.addWidget(ai_tip)
        self.add_button(ai_layout, "用当前录字生成参考样本并打开AI扩展…", self.open_ai_from_recorded)
        self.add_button(ai_layout, "使用已有单字图片 / 打开AI扩展…", self.open_ai)
        ai_layout.addStretch()
        self.tabs.addTab(ai, "AI扩展（实验）")
        self.status = QLabel("新字库尚未保存。建议先录制姓名或常用字。")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.add_button(layout, "关闭并返回文字生成", self.accept)
        self.fonts.currentIndexChanged.connect(self.select_font)
        self.search.textChanged.connect(self.refresh_characters)
        self.characters.currentTextChanged.connect(lambda text: self.character.setText(text.split("　")[0]) if text else None)
        self.character.textChanged.connect(self.select_character)
        self.variants.currentIndexChanged.connect(self.select_variant)
        self.advance.valueChanged.connect(self.change_advance)
        index = self.fonts.findData(selected)
        if index > 0:
            self.fonts.setCurrentIndex(index)
        else:
            self.select_character()
        self.update_plan_status()
        self.update_delete_button()

    def add_button(self, layout, text, callback):
        button = QPushButton(text)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def set_number_visibility(self, enabled):
        self.canvas.show_numbers = enabled
        self.canvas.update()

    def select_font(self):
        try:
            identity = self.fonts.currentData()
            if not self.confirm_discard():
                self.fonts.blockSignals(True)
                self.fonts.setCurrentIndex(max(0, self.fonts.findData(self.package.id)))
                self.fonts.blockSignals(False)
                return
            self.package = self.store.load(identity) if identity else FontPackage("我的手写体")
            self.saved_data = json.dumps(package_data(self.package), ensure_ascii=False)
            self.selected_id = identity
            self.sample_plan = list(self.package.record_plan or SAMPLE_PLAN)
            self.name.setText(self.package.name)
            self.refresh_samples()
            self.refresh_characters()
            self.select_character()
            self.update_delete_button()
            self.status.setText(f"已载入{len(self.package.glyphs)}种字符；录字修改后请保存字库。")
        except (OSError, GenerationError) as error:
            self.status.setText(str(error))

    def refresh_characters(self):
        self.characters.blockSignals(True)
        self.characters.clear()
        for char, glyph in self.package.glyphs.items():
            if not self.search.text() or char in self.search.text():
                self.characters.addItem(f"{char}　{len(glyph['variants'])}版")
        self.characters.blockSignals(False)

    def select_character(self):
        glyph = self.package.glyphs.get(self.character.text())
        self.variants.blockSignals(True)
        self.variants.clear()
        for index, version in enumerate(glyph["variants"] if glyph else []):
            self.variants.addItem(f"版本{index + 1} · {len(version['strokes'])}笔")
        self.variants.blockSignals(False)
        self.advance.blockSignals(True)
        self.advance.setValue(glyph["advance"] if glyph else 1)
        self.advance.blockSignals(False)
        self.advance.setEnabled(glyph is None)
        self.canvas.set_guide(self.character.text(), self.guide_checkbox.isChecked())
        self.select_variant()

    def select_variant(self):
        glyph = self.package.glyphs.get(self.character.text())
        index = self.variants.currentIndex()
        paths = glyph["variants"][index]["strokes"] if glyph and index >= 0 else []
        self.canvas.set_strokes(paths, self.advance.value())

    def change_advance(self, value):
        ratio = value / self.canvas.advance
        self.canvas.set_strokes([[(x * ratio, y) for x, y in path] for path in self.canvas.strokes], value)

    def store_variant(self, replace=False):
        char = self.character.text()
        if len(char) != 1 or ord(char) < 33 or char.isspace():
            raise GenerationError("请输入一个可绘制字符。")
        if not self.canvas.strokes:
            raise GenerationError("请先在画板录制笔画。")
        glyph = self.package.glyphs.setdefault(char, {"advance": self.advance.value(), "variants": []})
        version = {"strokes": [[tuple(p) for p in path] for path in self.canvas.strokes]}
        index = self.variants.currentIndex()
        if replace and index >= 0:
            glyph["variants"][index] = version
        else:
            if len(glyph["variants"]) >= 16:
                raise GenerationError("每字最多16个版本，请替换现有版本。")
            glyph["variants"].append(version)
            index = len(glyph["variants"]) - 1
        self.select_character()
        self.variants.setCurrentIndex(index)
        self.refresh_characters()
        self.refresh_samples()
        self.status.setText(f"已在草稿中保存“{char}”版本{index + 1}，请点击顶部“保存字库”。")

    def add_variant(self):
        try:
            self.store_variant()
        except GenerationError as error:
            self.status.setText(str(error))

    def replace_variant(self):
        try:
            self.store_variant(True)
        except GenerationError as error:
            self.status.setText(str(error))

    def remove_variant(self):
        char, index = self.character.text(), self.variants.currentIndex()
        if char in self.package.glyphs and index >= 0:
            self.package.glyphs[char]["variants"].pop(index)
            if not self.package.glyphs[char]["variants"]:
                del self.package.glyphs[char]
            self.select_character()
            self.refresh_characters()
            self.refresh_samples()
            self.status.setText("已从草稿移除该版本；点击保存字库后生效。")

    def save_font(self):
        try:
            if not self.package.glyphs:
                raise GenerationError("请先在画板录字，再点击“添加为新版本”，然后保存字库。")
            self.package.name = self.name.text()
            self.package.record_plan = tuple(self.sample_plan)
            self.selected_id = self.store.save(self.package)
            self.saved_data = json.dumps(package_data(self.package), ensure_ascii=False)
            self.refresh_font_list()
            self.update_delete_button()
            self.status.setText(f"已保存“{self.package.name}”：{len(self.package.glyphs)}种字，返回文字生成后可选择。")
            return True
        except (OSError, GenerationError) as error:
            self.status.setText(str(error))
            return False

    def import_font(self, checked=False, path=None):
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "导入单线笔画字库", str(ROOT), "笔画字库 (*.json)")
        if path:
            try:
                if not self.confirm_discard():
                    return
                self.package = self.store.import_file(path)
                self.saved_data = json.dumps(package_data(self.package), ensure_ascii=False)
                self.selected_id = self.package.id
                self.sample_plan = list(self.package.record_plan or SAMPLE_PLAN)
                self.name.setText(self.package.name)
                self.refresh_font_list()
                self.refresh_samples()
                self.refresh_characters()
                self.select_character()
                self.update_delete_button()
                self.status.setText(f"已导入并保存“{self.package.name}”，共{len(self.package.glyphs)}种字。")
            except (OSError, GenerationError) as error:
                self.status.setText(str(error))

    def export_font(self, checked=False, path=None):
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "导出单线笔画字库", str(ROOT / "exports" / "我的手写体.json"), "笔画字库 (*.json)")
        if path:
            try:
                self.package.name = self.name.text()
                self.package.record_plan = tuple(self.sample_plan)
                write_package(self.package, path)
                self.status.setText("已导出字库JSON，包含所有版本、录字清单和轨迹来源标记。")
            except (OSError, GenerationError) as error:
                self.status.setText(str(error))

    def begin_sample(self):
        index = self.samples.currentRow()
        if index < 0 or index >= len(self.sample_plan):
            self.status.setText("请先选择一个录字清单字符。")
            return
        char = self.sample_plan[index]
        self.character.setText(char)
        version = self.sample_versions[index]
        glyph = self.package.glyphs.get(char)
        paths = glyph["variants"][version]["strokes"] if glyph and len(glyph["variants"]) > version else []
        self.variants.setCurrentIndex(version if glyph and len(glyph["variants"]) > version else -1)
        self.canvas.set_strokes(paths, glyph["advance"] if glyph else 1)
        self.tabs.setCurrentIndex(0)
        self.update_plan_status()
        self.status.setText(f"正在录第{index + 1}格“{self.sample_plan[index]}”，可按楷体底稿对照大小；完成后点击“保存此格并录下一格”。")

    def save_sample(self):
        index = self.samples.currentRow()
        if index < 0 or index >= len(self.sample_plan):
            self.status.setText("请先选择一个录字清单字符。")
            return
        if self.character.text() != self.sample_plan[index]:
            self.status.setText("画板字符与所选采样格不同，请先点击“去画板录这一格”。")
            return
        try:
            char = self.sample_plan[index]
            version = self.sample_versions[index]
            glyph = self.package.glyphs.get(char)
            if version > len(glyph["variants"] if glyph else []):
                raise GenerationError("同一字的采样请按格号先后录制，先完成前面这个字的版本。")
            if glyph and version < len(glyph["variants"]):
                # 切换版本会刷新画板，先保留本次录字，再替换对应格的版本。
                paths = list(self.canvas.strokes)
                self.variants.setCurrentIndex(version)
                self.canvas.set_strokes(paths, glyph["advance"])
                self.store_variant(True)
            else:
                self.store_variant()
            self.refresh_samples()
            count = sum(self.sample_exists(i) for i in range(len(self.sample_plan)))
            self.status.setText(f"已录{count}/{len(self.sample_plan)}格；可先导出已录样本。字库仍需点击顶部保存。")
            return True
        except GenerationError as error:
            self.status.setText(str(error))
            return False

    def save_and_next(self):
        index = self.samples.currentRow()
        if not self.save_sample() or not self.save_font():
            return
        if index + 1 < len(self.sample_plan):
            self.samples.setCurrentRow(index + 1)
            self.begin_sample()
        else:
            self.status.setText("录字清单已到最后一格，本格和整套字库已保存。")

    def begin_unrecorded(self):
        for index in range(len(self.sample_plan)):
            if not self.sample_exists(index):
                self.samples.setCurrentRow(index)
                self.begin_sample()
                return
        self.status.setText("这份清单已全部录入，可切换更大的清单或为已有字添加不同版本。")

    def sample_exists(self, index):
        char = self.sample_plan[index]
        return len(self.package.glyphs.get(char, {}).get("variants", [])) > self.sample_versions[index]

    def refresh_samples(self):
        if not hasattr(self, "samples"):
            return
        selected = max(0, self.samples.currentRow())
        # 一次统计版本格号，6763字清单不再逐格扫描前面的全部字符。
        counts, self.sample_versions = {}, []
        for char in self.sample_plan:
            self.sample_versions.append(counts.get(char, 0))
            counts[char] = counts.get(char, 0) + 1
        self.samples.blockSignals(True)
        rebuild = self.samples.count() != len(self.sample_plan)
        if rebuild:
            self.samples.clear()
        for index, char in enumerate(self.sample_plan):
            label = f"{index + 1:04d}　{char}　" + ("已录" if self.sample_exists(index) else "未录")
            if rebuild:
                self.samples.addItem(label)
            else:
                self.samples.item(index).setText(label)
        self.samples.setCurrentRow(min(selected, len(self.sample_plan) - 1))
        self.samples.blockSignals(False)
        self.update_plan_status()

    def update_plan_status(self):
        if hasattr(self, "plan_status"):
            recorded = sum(self.sample_exists(index) for index in range(len(self.sample_plan))) if hasattr(self, "samples") else 0
            self.plan_status.setText(f"当前录字清单：{len(self.sample_plan)}个字符，已完成{recorded}个。字库没有100字上限；最多保存20000种字符。")
            self.record_progress.setText(f"清单已录 {recorded}/{len(self.sample_plan)}格")

    def apply_record_plan(self):
        text = self.plan_input.toPlainText()
        if not text.strip():
            plan = list(sample_plan(self.plan_preset.currentData()))
        else:
            plan = list(dict.fromkeys(char for char in text if not char.isspace() and ord(char) >= 33))
        if not plan:
            self.status.setText("录字清单不能为空。")
            return
        if len(plan) > 20000:
            self.status.setText("录字清单最多20000个不同字符。")
            return
        self.sample_plan = plan
        self.package.record_plan = tuple(plan)
        self.refresh_samples()
        self.samples.setCurrentRow(0)
        self.status.setText(f"已载入{len(plan)}个录字目标；已有字形保留。请保存字库以记住这份清单。")

    def refresh_font_list(self):
        self.fonts.blockSignals(True)
        self.fonts.clear()
        self.fonts.addItem("新建个人字库", "")
        for package in self.store.catalog():
            self.fonts.addItem(f"{package.name}（{len(package.glyphs)}字）", package.id)
        self.fonts.setCurrentIndex(max(0, self.fonts.findData(self.selected_id)))
        self.fonts.blockSignals(False)
        self.update_delete_button()

    def update_delete_button(self):
        self.delete_button.setEnabled(bool(self.selected_id))

    def delete_font(self):
        identity = self.fonts.currentData()
        if not identity:
            self.status.setText("当前是新建个人字库，没有已保存的字库可删除。")
            return
        if identity != self.selected_id:
            self.status.setText("请先载入要删除的字库。")
            return
        if not self.confirm_discard():
            return
        from app.ui.font_messages import confirm_font_action
        if not confirm_font_action(self, "删除个人字库", f"确定删除“{self.package.name}”吗？\n这会删除本机保存的整套个人字库；已导出的JSON文件不会被删除。", "删除字库"):
            return
        try:
            self.store.delete(identity)
            self.package = FontPackage("我的手写体")
            self.selected_id = ""
            self.sample_plan = list(SAMPLE_PLAN)
            self.name.setText(self.package.name)
            self.saved_data = json.dumps(package_data(self.package), ensure_ascii=False)
            self.refresh_font_list()
            self.refresh_samples()
            self.refresh_characters()
            self.select_character()
            self.status.setText("已删除整套个人字库；当前已切换到新建个人字库。")
        except (OSError, GenerationError) as error:
            self.status.setText(str(error))

    def open_ai_from_recorded(self):
        try:
            if self.package.source.startswith(("SDT本地模型生成", "个人轨迹校准", "个人笔迹学习", "OLHWG离线轨迹模型生成", "照片中线导入")):
                raise GenerationError("当前字库含模型生成或照片中线，不是真实手录笔顺。请使用最初真实手录目录；照片单字PNG可在“使用已有单字图片”中作为SDT图片参考。")
            count = sum(len(glyph["variants"]) for glyph in self.package.glyphs.values())
            if count < 15:
                raise GenerationError(f"至少需要15个已添加的单字版本，目前{count}个；建议录15～30个不同字。画板内容先添加为版本，再保存字库。")
            glyph = self.package.glyphs.get(self.character.text())
            index = self.variants.currentIndex()
            expected = glyph["variants"][index]["strokes"] if glyph and 0 <= index < len(glyph["variants"]) else []
            if json.dumps(self.canvas.strokes) != json.dumps(expected):
                raise GenerationError("画板上还有未加入版本的修改，请先添加/替换为版本，再打开AI扩展。")
            self.package.name = self.name.text()
            path = ROOT / "exports" / "AI参考样本" / (self.package.id or "未保存字库")
            export_recorded_samples(self.package, path)
            self.open_ai(samples=path)
        except (OSError, GenerationError) as error:
            self.status.setText(str(error))

    def open_ai(self, checked=False, samples=None, characters="", base_package=None, method=None):
        dialog = SdtDialog(self, samples=samples, base_package=self.package if base_package is None else base_package)
        if method is not None:
            index = dialog.method.findData(method)
            if index >= 0:
                dialog.method.setCurrentIndex(index)
        if characters:
            dialog.characters.setPlainText(characters)
        dialog.exec()
        if dialog.import_path is not None:
            self.import_font(path=dialog.import_path)

    def confirm_discard(self):
        data = package_data(self.package)
        data["name"] = self.name.text()
        glyph = self.package.glyphs.get(self.character.text())
        index = self.variants.currentIndex()
        expected = glyph["variants"][index]["strokes"] if glyph and index >= 0 else []
        canvas_changed = json.dumps(self.canvas.strokes) != json.dumps(expected)
        if json.dumps(data, ensure_ascii=False) == self.saved_data and not canvas_changed:
            return True
        from app.ui.font_messages import confirm_font_action
        return confirm_font_action(self, "字库修改尚未保存", "录字或名称修改尚未保存。是否放弃这些修改？需要保留时请取消：画板先添加/替换为版本，再点击顶部“保存字库”。", "放弃修改")

    def accept(self):
        if self.confirm_discard():
            super().accept()

    def reject(self):
        if self.confirm_discard():
            super().reject()

    def closeEvent(self, event):
        if self.confirm_discard():
            event.accept()
        else:
            event.ignore()

    def export_sheet(self, checked=False, path=None):
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "导出个人采样表", str(ROOT / "exports" / "个人字库采样表.svg"), "SVG (*.svg)")
        if path:
            try:
                result = export_sample_sheet(path, self.sample_plan, f"{self.package.name}手写采样")
                self.status.setText(f"已导出{len(self.sample_plan)}格，按每100格一张A4分页；按100%实际大小打印。位置：{result}")
            except (OSError, GenerationError) as error:
                self.status.setText(str(error))

    def export_samples(self, checked=False, path=None):
        if path is None:
            path = QFileDialog.getExistingDirectory(self, "选择AI参考样本输出目录", str(ROOT / "exports"))
        if path:
            try:
                result = export_recorded_samples(self.package, path)
                self.status.setText(f"已导出{result}个单字参考PNG与清单；这些文件可供SDT读取，不等于已经完成AI扩展。")
            except (OSError, GenerationError) as error:
                self.status.setText(str(error))

    def import_photo_samples(self):
        if not self.confirm_discard():
            return
        base = self.store.load(self.package.id) if self.package.id else None
        dialog = PhotoFontDialog(self, store=self.store, plan=self.sample_plan, base_package=base)
        dialog.exec()
        if dialog.selected_id:
            self.selected_id = dialog.selected_id
            self.refresh_font_list()
            # 照片字库另存，不覆盖原字库；在返回这里前已确认放弃旧草稿。
            self.package = self.store.load(dialog.selected_id)
            self.name.setText(self.package.name)
            self.saved_data = json.dumps(package_data(self.package), ensure_ascii=False)
            self.sample_plan = list(self.package.record_plan or SAMPLE_PLAN)
            self.refresh_samples()
            self.refresh_characters()
            self.character.setText(next(iter(self.package.glyphs)))
            self.select_character()
            self.status.setText(f"照片字库“{self.package.name}”已另存，共{len(self.package.glyphs)}种字。可逐字核对、替换版本或返回文字生成。")
