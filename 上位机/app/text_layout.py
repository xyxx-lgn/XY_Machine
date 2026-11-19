import math
from dataclasses import dataclass, field, replace

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTextBoundaryFinder
from PySide6.QtGui import QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QTextLayout, QTextOption, QTransform

from app.generation import GenerationError, centerline_paths, checkpoint, fill_paths, join_touching_paths, order_paths, path_length, simplify, task_from_paths


LETTER_SPACING_RANGE = (-5.0, 5.0)
FONT_SIZE_RANGE = (6, 96)


@dataclass
class TextOptions:
    paper_width: float = 210.0
    paper_height: float = 297.0
    font_size: float = 14.0
    margin_left: float = 20.0
    margin_right: float = 20.0
    margin_top: float = 20.0
    margin_bottom: float = 20.0
    line_spacing: float = 1.2
    letter_spacing: float = 0.0
    paragraph_spacing: float = 0.0
    indent: float = 0.0
    alignment: str = "left"

    @property
    def em_mm(self):
        return self.font_size * 25.4 / 72

    @property
    def content_rect(self):
        return QRectF(self.margin_left, self.margin_top, self.paper_width - self.margin_left - self.margin_right, self.paper_height - self.margin_top - self.margin_bottom)

    def validate(self):
        for name, value in vars(self).items():
            if isinstance(value, (int, float)) and (isinstance(value, bool) or not math.isfinite(value)):
                raise GenerationError(f"正文参数{name}不是有效数值。")
        if not 0 < self.paper_width <= 1000 or not 0 < self.paper_height <= 1000:
            raise GenerationError("纸张尺寸应为0～1000mm。")
        if not FONT_SIZE_RANGE[0] <= self.font_size <= FONT_SIZE_RANGE[1] or not 0.5 <= self.line_spacing <= 3:
            raise GenerationError("字号应为6～96pt，行距倍率应为0.5～3。")
        if any(value < 0 for value in (self.margin_left, self.margin_right, self.margin_top, self.margin_bottom)):
            raise GenerationError("页边距不能为负数。")
        if not LETTER_SPACING_RANGE[0] <= self.letter_spacing <= LETTER_SPACING_RANGE[1]:
            raise GenerationError(f"字距微调应为{LETTER_SPACING_RANGE[0]:g}～{LETTER_SPACING_RANGE[1]:g} mm。")
        if not 0 <= self.paragraph_spacing <= 30 or not 0 <= self.indent <= 4:
            raise GenerationError("段后间距应为0～30mm，首行缩进应为0～4字。")
        if self.alignment not in ("left", "center", "right"):
            raise GenerationError("文字对齐方式无效。")
        if self.content_rect.width() <= (self.indent + 1) * self.em_mm or self.content_rect.height() <= self.em_mm:
            raise GenerationError("页边距或字号过大，可写区域不足，请减小字号/页边距。")


@dataclass
class TextCharacter:
    path: QPainterPath
    position: QPointF
    cache_key: tuple
    strokes: tuple | None = None


@dataclass
class TextLine:
    text: str
    path: QPainterPath
    top: float
    height: float
    characters: list = field(default_factory=list)
    font_size: float | None = None


@dataclass
class TextPage:
    lines: list = field(default_factory=list)
    stroke_width: float | None = None
    writing_notes: list = field(default_factory=list)
    stroke_ordered: bool = True

    @property
    def bounds(self):
        result = QRectF()
        for line in self.lines:
            if not line.path.isEmpty():
                result = result.united(line.path.boundingRect())
        return result

    def font_size_label(self, fallback):
        sizes = sorted({line.font_size for line in self.lines if line.font_size is not None and line.text.strip()})
        if len(sizes) > 4:
            return f"{sizes[0]:g}～{sizes[-1]:g} pt"
        return " / ".join(f"{size:g}" for size in sizes or [fallback]) + " pt"


@dataclass
class TextDocument:
    options: TextOptions
    pages: list


def line_outline(line, paragraph, cache):
    """保留排版后的字形分组；组合音标和连字仍按Qt的字形簇一起绘制。"""
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.WindingFill)
    finder = QTextBoundaryFinder(QTextBoundaryFinder.BoundaryType.Grapheme, paragraph)
    finder.setPosition(line.textStart())
    boundaries = [line.textStart()]
    while 0 <= (position := finder.toNextBoundary()) <= line.textStart() + line.textLength():
        boundaries.append(position)
    groups, seen = {}, set()
    for start, end in zip(boundaries, boundaries[1:]):
        if start >= line.textStart() + line.textLength() or end <= line.textStart():
            continue
        # 按原文簇取字形，避免Qt某些字体回退run将stringIndexes错误地重新从0计数。
        for run in line.glyphRuns(start, end - start):
            font = run.rawFont()
            font_key = (font.familyName(), font.styleName(), font.pixelSize(), font.weight(), font.style())
            for glyph, position in zip(run.glyphIndexes(), run.positions()):
                if glyph == 0:
                    raise GenerationError("文字包含当前系统字体无法绘制的字形，请换用包含这些字的字体。")
                key = (font_key, glyph)
                identity = (key, position.x(), position.y())
                # 一个连字可能被多个原文簇引用，完整字形只生成一次。
                if identity in seen:
                    continue
                seen.add(identity)
                if key not in cache:
                    cache[key] = font.pathForGlyph(glyph)
                outline = cache[key]
                if not outline.isEmpty():
                    path.addPath(outline.translated(position))
                    groups.setdefault(start, []).append((key, outline, position))
    characters = []
    for cluster in sorted(groups):
        entries = groups[cluster]
        origin = entries[0][2]
        local = QPainterPath()
        local.setFillRule(Qt.FillRule.WindingFill)
        key_parts = []
        for key, outline, position in entries:
            offset = position - origin
            local.addPath(outline.translated(offset))
            key_parts.append((key, offset.x(), offset.y()))
        characters.append(TextCharacter(local, origin, tuple(key_parts)))
    return path, characters


def layout_text(text, family, bold=False, options=None, paragraph_alignments=None, paragraph_font_sizes=None):
    """按字号对应的毫米尺寸分行、分页；纸张变窄只换行，不缩小字形。

    以160单位的字体排版，再按pt换算到毫米，避免显示器DPI改变实际字大。
    每行保留字形簇和定位，后续按字生成并缓存，避免整行反复细化和跨字排序。
    """
    options = options or TextOptions()
    options.validate()
    if not text.strip() or len(text) > 20000 or len(text.splitlines()) > 1000:
        raise GenerationError("请输入文字；正文一次最多20000字符、1000个手动换行。")
    font = QFont(family)
    font.setPixelSize(160)
    font.setBold(bold)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    metrics = QFontMetricsF(font)
    advances = {}
    rect = options.content_rect
    text_option = QTextOption()
    text_option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    text_option.setAlignment({"left": Qt.AlignmentFlag.AlignLeft, "center": Qt.AlignmentFlag.AlignHCenter, "right": Qt.AlignmentFlag.AlignRight}[options.alignment])
    pages, y, glyph_cache = [TextPage()], rect.top(), {}
    paragraphs = text.replace("\r\n", "\n").replace("\r", "\n").expandtabs(4).split("\n")
    if paragraph_alignments is not None and len(paragraph_alignments) != len(paragraphs):
        paragraph_alignments = None
    if paragraph_font_sizes is not None and len(paragraph_font_sizes) != len(paragraphs):
        paragraph_font_sizes = None
    for paragraph_index, paragraph in enumerate(paragraphs):
        size = (paragraph_font_sizes[paragraph_index] if paragraph_font_sizes else None) or options.font_size
        em = size * 25.4 / 72
        unit = em / 160
        paragraph_alignment = paragraph_alignments[paragraph_index] if paragraph_alignments else options.alignment
        if paragraph_alignment not in ("left", "center", "right"):
            paragraph_alignment = options.alignment
        text_option.setAlignment({"left": Qt.AlignmentFlag.AlignLeft, "center": Qt.AlignmentFlag.AlignHCenter, "right": Qt.AlignmentFlag.AlignRight}[paragraph_alignment])
        spacing = options.letter_spacing / unit
        if spacing < 0 and paragraph:
            # Qt的绝对字距减得太多时，窄字的起点会倒退。保留一半的最窄字宽。
            for char in set(paragraph) - advances.keys():
                advances[char] = metrics.horizontalAdvance(char)
            widths = [advances[char] for char in set(paragraph) if advances[char] > 0]
            if widths:
                spacing = max(spacing, -min(widths) / 2)
        paragraph_font = QFont(font)
        paragraph_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
        layout = QTextLayout(paragraph, paragraph_font)
        layout.setTextOption(text_option)
        layout.beginLayout()
        rows = []
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            indent = options.indent * em if not rows and paragraph_alignment == "left" else 0
            if indent >= rect.width():
                raise GenerationError("首行缩进超过可写宽度，请减小字号、缩进或页边距。")
            # Qt把末字后的负字距也计入行宽，给末字的实际笔迹留出这部分空间。
            line.setLineWidth(max(0, (rect.width() - indent) / unit + min(0, spacing)))
            line.setPosition(QPointF(indent / unit, 0))
            rows.append(line)
        layout.endLayout()
        encoded = paragraph.encode("utf-16-le")
        # 空行也占用行高，保留用户主动换行形成的段落间隔。
        for line in rows or [None]:
            height = (line.height() if line else metrics.height()) * unit
            outline, characters = line_outline(line, paragraph, glyph_cache) if line else (QPainterPath(), [])
            # j等字母有负侧承；整体平移这一行，保持字号并容纳伸出的笔画。
            bounds = outline.boundingRect()
            if bounds.width() * unit > rect.width() + 1e-7:
                raise GenerationError("单个字形比可写区域更宽，请减小字号或页边距。")
            if paragraph_alignment == "center" and not outline.isEmpty():
                dx = rect.width() / (2 * unit) - bounds.center().x()
            elif paragraph_alignment == "right" and spacing < 0 and not outline.isEmpty():
                dx = rect.width() / unit - bounds.right()
            else:
                dx = max(0, -bounds.left()) + min(0, rect.width() / unit - bounds.right())
            dy = max(0, -bounds.top())
            outline.translate(dx, dy)
            height = max(height, outline.boundingRect().bottom() * unit)
            if height > rect.height() + 1e-7:
                raise GenerationError("当前字号的一行高于可写区域，请减小字号或页边距。")
            if y + height > rect.bottom() + 1e-7:
                if len(pages) >= 100:
                    raise GenerationError("正文超过100页，请分成多个文稿。")
                pages.append(TextPage())
                y = rect.top()
            path = QTransform(unit, 0, 0, unit, rect.left(), y).map(outline)
            characters = [TextCharacter(QTransform().scale(unit, unit).map(char.path), QPointF(rect.left() + (char.position.x() + dx) * unit, y + (char.position.y() + dy) * unit), (unit, char.cache_key)) for char in characters]
            if not path.isEmpty() and not rect.adjusted(-0.001, -0.001, 0.001, 0.001).contains(path.boundingRect()):
                raise GenerationError("字形超出正文边界，请增大可写区域或调整字体/字号。")
            content = encoded[line.textStart() * 2:(line.textStart() + line.textLength()) * 2].decode("utf-16-le") if line else ""
            pages[-1].lines.append(TextLine(content, path, y, height, characters, size))
            y += height * options.line_spacing
        y += options.paragraph_spacing
    if all(page.bounds.isEmpty() for page in pages):
        raise GenerationError("所选字体没有可绘制的文字轮廓。")
    return TextDocument(options, pages)


def render_page(page, options, long_edge=1000):
    # 这张图片仅用于页面预览；生成轨迹读取毫米字形路径，不读取预览像素。
    scale = long_edge / max(options.paper_width, options.paper_height)
    image = QImage(math.ceil(options.paper_width * scale), math.ceil(options.paper_height * scale), QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(scale, scale)
    for line in page.lines:
        painter.fillPath(line.path, Qt.GlobalColor.black)
    painter.end()
    return image


def vector_contours(path, options):
    result = []
    # 放大后细分贝塞尔曲线，再还原毫米坐标，保留小字号弧线和孔洞。
    for polygon in path.toSubpathPolygons(QTransform().scale(50, 50)):
        points = [(point.x() / 50, point.y() / 50) for point in polygon]
        if len(points) > 2:
            if points[0] != points[-1]:
                points.append(points[0])
            points = simplify(points, options.tolerance, True)
            if len(points) > 2 and path_length(points) >= options.min_length:
                result.append(points)
    return result


def line_mask(path, density=50.0):
    bounds = path.boundingRect().adjusted(-0.04, -0.04, 0.04, 0.04)
    width, height = math.ceil(bounds.width() * density) + 1, math.ceil(bounds.height() * density) + 1
    if width * height > 12_000_000:
        raise GenerationError("单行文字过宽或字号过大，请缩小可写宽度/字号后重试。")
    image = QImage(width, height, QImage.Format.Format_Grayscale8)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(density, density)
    painter.translate(-bounds.left(), -bounds.top())
    painter.fillPath(path, Qt.GlobalColor.black)
    painter.end()
    gray = np.frombuffer(image.constBits(), np.uint8).reshape(height, image.bytesPerLine())[:, :width]
    return (gray < 128).astype(np.uint8) * 255, bounds.left(), bounds.top(), 1 / density


def character_paths(page, options, cancel=None, progress=None):
    """按阅读顺序完成每个字；缓存限于本次生成，参数改变后不会复用旧轨迹。"""
    notify = progress or (lambda value, message: None)
    paths, cache = [], {}
    characters = [character for line in page.lines for character in line.characters]
    point_count = 0
    for index, character in enumerate(characters):
        checkpoint(cancel)
        notify(5 + round(60 * index / max(1, len(characters))), f"逐字处理 {index + 1}/{len(characters)}（已计算{len(cache)}种字形）")
        if character.strokes is not None:
            # 有序笔画不拼接、不反转、不按距离重排，折笔保留在同一路径中。
            local = character.strokes
            paths.extend([[(x + character.position.x(), y + character.position.y()) for x, y in stroke] for stroke in local])
            point_count += sum(len(stroke) for stroke in local)
            if len(paths) > 30000 or point_count > 180000:
                raise GenerationError("本页笔画过多，请减小每页字数或分成更多页。")
            continue
        if character.cache_key not in cache:
            local = []
            if options.mode in ("outline", "outline_fill"):
                local.extend(vector_contours(character.path, options))
            if options.mode in ("fill", "outline_fill", "centerline"):
                mask, x, y, step = line_mask(character.path)
                raster = centerline_paths(mask, step, step, options, cancel, preserve_connections=True) if options.mode == "centerline" else fill_paths(mask, step, step, options, cancel)
                local.extend([[(a + x, b + y) for a, b in path] for path in raster])
            if options.optimize:
                local = order_paths(join_touching_paths(local, cancel), cancel)
            cache[character.cache_key] = local
        local = cache[character.cache_key]
        paths.extend([[(a + character.position.x(), b + character.position.y()) for a, b in path] for path in local])
        point_count += sum(len(path) for path in local)
        if len(paths) > 30000 or point_count > 180000:
            raise GenerationError("本页路径过多，请增加填线间距/简化误差，或分成更多页。")
    return paths


def generate_text_task(page, document_options, options, name="正文任务", cancel=None, progress=None):
    options = replace(options, paper_width=document_options.paper_width, paper_height=document_options.paper_height, x=0, y=0, width=document_options.paper_width, height=document_options.paper_height, crop=False, invert=False)
    options.validate()
    document_options.validate()
    paths = character_paths(page, options, cancel, progress)
    rect = document_options.content_rect.adjusted(-0.021, -0.021, 0.021, 0.021)
    if any(not rect.contains(QPointF(x, y)) for path in paths for x, y in path):
        raise GenerationError("生成笔迹超出正文边界，请调整字体或页边距。")
    area = document_options.content_rect
    paths = [[(min(area.right(), max(area.left(), x)), min(area.bottom(), max(area.top(), y))) for x, y in path] for path in paths]
    notes = [f"本页{len(page.lines)}行，字号 {page.font_size_label(document_options.font_size)}，按可写宽度自动换行。", "一条笔画连续走完，整字完成后再写下一字。" if page.stroke_width is not None else "逐行逐字完成；路径优化仅在当前字内部进行，连字/组合音标作为一组。", "本文件仅包含当前页；换纸后需重新确认定位并手动开始下一页。"]
    if options.mode == "centerline":
        notes.append("中线保留交叉处短连接；短线过滤按独立墨迹块尺寸判断，避免笔画断开。")
    if page.stroke_width is not None:
        notes.append("按字库笔顺逐行逐字完成，一笔一次落笔，保留方向和连续折笔；不进行填充或路径重排。" if page.stroke_ordered else "按中线数据逐行逐字完成；保留路径方向，不填充，不承诺标准笔顺。")
        notes.extend(page.writing_notes)
    task = task_from_paths(paths, options, name, render_page(page, document_options), 0.02, notes, cancel, progress, preserve_order=True)
    if page.stroke_width is not None:
        task.program.preview_pen_width = page.stroke_width
    return task


def layout_artwork(text, family, bold, line_spacing, alignment, options):
    # 文字图案保留整体缩放和手动换行，但轨迹仍保存逐字分组。
    if not text.strip() or len(text) > 2000 or len(text.splitlines()) > 60:
        raise GenerationError("请输入文字；文字图案一次最多2000字符、60行。")
    font = QFont(family)
    font.setPixelSize(160)
    font.setBold(bold)
    metrics = QFontMetricsF(font)
    lines = text.splitlines()
    width = max(metrics.horizontalAdvance(line) for line in lines)
    page, cache = TextPage(), {}
    for index, text_line in enumerate(lines):
        layout = QTextLayout(text_line, font)
        layout.beginLayout()
        line = layout.createLine()
        if line.isValid():
            line.setLineWidth(max(1, width))
            x = {"left": 0, "center": (width - metrics.horizontalAdvance(text_line)) / 2, "right": width - metrics.horizontalAdvance(text_line)}[alignment]
            line.setPosition(QPointF(x, index * metrics.lineSpacing() * line_spacing))
        layout.endLayout()
        path, characters = line_outline(line, text_line, cache) if line.isValid() else (QPainterPath(), [])
        page.lines.append(TextLine(text_line, path, index * metrics.lineSpacing() * line_spacing, metrics.height(), characters))
    bounds = page.bounds
    if bounds.isEmpty():
        raise GenerationError("所选字体没有可绘制的文字轮廓。")
    if not options.crop:
        scale = min(1.0, 5000 / max(bounds.width(), bounds.height()), math.sqrt(10_000_000 / (bounds.width() * bounds.height())))
        bounds = bounds.adjusted(-4 / scale, -4 / scale, 4 / scale, 4 / scale)
    sx, sy = options.width / bounds.width(), options.height / bounds.height()
    transform = QTransform(sx, 0, 0, sy, options.x - bounds.left() * sx, options.y - bounds.top() * sy)
    for line in page.lines:
        line.path = transform.map(line.path)
        line.characters = [TextCharacter(QTransform().scale(sx, sy).map(char.path), transform.map(char.position), (sx, sy, char.cache_key)) for char in line.characters]
    return page


def generate_artwork_text_task(page, options, image, name="文字任务", cancel=None, progress=None):
    options.validate()
    paths = character_paths(page, options, cancel, progress)
    area = QRectF(options.x, options.y, options.width, options.height)
    if any(not area.adjusted(-0.021, -0.021, 0.021, 0.021).contains(QPointF(x, y)) for path in paths for x, y in path):
        raise GenerationError("文字轨迹超出图案边界，请检查字号和尺寸。")
    paths = [[(min(area.right(), max(area.left(), x)), min(area.bottom(), max(area.top(), y))) for x, y in path] for path in paths]
    notes = ["文字图案按整体尺寸缩放；逐行逐字完成，路径优化仅在当前字内进行。"]
    if page.stroke_width is not None:
        notes.append("按字库笔顺逐行逐字完成，一笔一次落笔，保留方向和连续折笔；不进行填充或路径重排。" if page.stroke_ordered else "按中线数据逐行逐字完成；保留路径方向，不填充，不承诺标准笔顺。")
        notes.extend(page.writing_notes)
    task = task_from_paths(paths, replace(options, x=0, y=0), name, image, 0.02, notes, cancel, progress, preserve_order=True)
    if page.stroke_width is not None:
        task.program.preview_pen_width = page.stroke_width
    return task
