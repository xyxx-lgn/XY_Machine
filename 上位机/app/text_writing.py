"""离线单线字库和正文排版；笔顺、方向来自数据，不由轮廓推断。"""

import json
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from zipfile import BadZipFile, ZipFile

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage, QPainter, QPainterPath, QPainterPathStroker, QTransform

from app.generation import GenerationError
from app.profile import ROOT
from app.stroke_fonts import LATIN_STYLES, WritingOptions, natural_glyph, seeded_random
from app.builtin_stroke_fonts import BUILTIN_FONTS, builtin_paths
from app.text_layout import TextCharacter, TextDocument, TextLine, TextOptions, TextPage


DATA = ROOT / "assets" / "strokes"
STYLE = "单线楷体笔画＋Hershey英文"
NO_START = set("，。！？；：、）》】〕〉”’…,.!?;:%)]}")
NO_END = set("（《【〔〈“‘([{")


@dataclass(frozen=True)
class StrokeGlyph:
    strokes: tuple
    advance: float = 1.0


def svg_path(source):
    # 字库只使用绝对M/L/Q/C/Z；其他命令直接报错，不猜测字形。
    tokens = re.findall(r"[A-Za-z]|[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?", source)
    path, index, command = QPainterPath(), 0, None
    sizes = {"M": 2, "L": 2, "Q": 4, "C": 6}
    while index < len(tokens):
        if tokens[index].isalpha():
            command = tokens[index]
            index += 1
        if command == "Z":
            path.closeSubpath()
            command = None
            continue
        if command not in sizes:
            raise GenerationError("离线笔画字库包含不支持的路径命令。")
        count = sizes[command]
        values = [float(value) for value in tokens[index:index + count]]
        {"M": path.moveTo, "L": path.lineTo, "Q": path.quadTo, "C": path.cubicTo}[command](*values)
        index += count
        if command == "M":
            command = "L"
    return path


def nearest_point(point, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    t = max(0, min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length)) if length else 0
    return a[0] + dx * t, a[1] + dy * t


def contact_strokes(medians, outlines):
    """只补原字形中确实相接、且沿笔端方向的小缺口。

    连接须有原轮廓交叠支持，整段位于两笔墨迹内；不跨白区，不移动原点，
    不合并两笔或改变笔顺。折笔仍然是一条连续路径。
    """
    result = [list(map(tuple, stroke)) for stroke in medians]
    for index, stroke in enumerate(medians):
        for end, neighbor in ((0, 1), (-1, -2)):
            point = stroke[end]
            outward = (point[0] - stroke[neighbor][0], point[1] - stroke[neighbor][1])
            candidates = []
            for other, target in enumerate(medians):
                if other == index or not outlines[index].intersects(outlines[other]):
                    continue
                union = outlines[index].united(outlines[other])
                for a, b in zip(target, target[1:]):
                    joint = nearest_point(point, a, b)
                    distance = math.dist(point, joint)
                    if not 1 < distance <= 96:
                        continue
                    dx, dy = joint[0] - point[0], joint[1] - point[1]
                    if dx * outward[0] + dy * outward[1] < 0.1 * distance * math.hypot(*outward):
                        continue
                    samples = [QPointF(point[0] + dx * t / 16, point[1] + dy * t / 16) for t in range(17)]
                    if not any(outlines[index].contains(p) and outlines[other].contains(p) for p in samples):
                        continue
                    if all(union.contains(p) for p in samples):
                        candidates.append((distance, joint))
            if candidates:
                joint = min(candidates)[1]
                result[index].insert(0, joint) if end == 0 else result[index].append(joint)
    return result


@lru_cache(maxsize=1)
def hanzi_archive():
    return ZipFile(DATA / "ordered-kaiti-v1.zip")


@lru_cache(maxsize=4)
def latin_data(style="futural"):
    if style == "futural":
        return json.loads((DATA / "hershey-futural.json").read_text(encoding="utf-8"))["font"]["chars"]
    return json.loads((DATA / "hershey-styles.json").read_text(encoding="utf-8"))["fonts"][style]["chars"]


@lru_cache(maxsize=4)
def latin_metrics(style):
    if style == "futural":
        return -3.0, 32.0
    ys = [point.y() for glyph in latin_data(style)[:94] for path in svg_path(glyph["d"]).toSubpathPolygons() for point in path]
    return min(ys), max(32, max(ys) - min(ys))


def punctuation():
    # 标点直接使用字框内单线，不用系统字体轮廓补画。
    dot = lambda x, y: [(x - 0.012, y), (x + 0.012, y)]
    circle = [(0.23 + 0.055 * math.cos(t * math.tau / 16), 0.78 + 0.055 * math.sin(t * math.tau / 16)) for t in range(17)]
    return {
        "，": [[(0.24, 0.73), (0.23, 0.80), (0.18, 0.85)]], "、": [[(0.18, 0.70), (0.29, 0.81)]], "。": [circle],
        "：": [dot(0.25, 0.39), dot(0.25, 0.74)], "；": [dot(0.25, 0.39), [(0.25, 0.70), (0.23, 0.79), (0.18, 0.84)]],
        "“": [[(x + 0.08, 0.16), (x, 0.24), (x, 0.31)] for x in (0.48, 0.67)],
        "”": [[(x, 0.16), (x, 0.23), (x - 0.08, 0.31)] for x in (0.25, 0.44)],
        "‘": [[(0.65, 0.16), (0.57, 0.24), (0.57, 0.31)]], "’": [[(0.32, 0.16), (0.32, 0.23), (0.24, 0.31)]],
        "《": [[(x + 0.18, 0.18), (x, 0.49), (x + 0.18, 0.80)] for x in (0.35, 0.57)],
        "》": [[(x, 0.18), (x + 0.18, 0.49), (x, 0.80)] for x in (0.25, 0.47)],
        "【": [[(0.68, 0.16), (0.46, 0.16), (0.46, 0.82), (0.68, 0.82)]],
        "】": [[(0.32, 0.16), (0.54, 0.16), (0.54, 0.82), (0.32, 0.82)]],
        "—": [[(0.05, 0.5), (0.95, 0.5)]], "–": [[(0.1, 0.5), (0.65, 0.5)]],
        "…": [dot(x, 0.75) for x in (0.2, 0.5, 0.8)], "·": [dot(0.5, 0.5)], "•": [dot(0.5, 0.5)],
        "×": [[(0.25, 0.3), (0.75, 0.75)], [(0.75, 0.3), (0.25, 0.75)]],
        "÷": [dot(0.5, 0.25), [(0.2, 0.5), (0.8, 0.5)], dot(0.5, 0.75)],
        "±": [[(0.2, 0.42), (0.8, 0.42)], [(0.5, 0.18), (0.5, 0.65)], [(0.2, 0.8), (0.8, 0.8)]],
        "￥": [[(0.2, 0.15), (0.5, 0.45), (0.8, 0.15)], [(0.5, 0.45), (0.5, 0.85)], [(0.25, 0.48), (0.75, 0.48)], [(0.25, 0.64), (0.75, 0.64)]],
    }


PUNCTUATION = punctuation()


@lru_cache(maxsize=1024)
def stroke_glyph(character, latin_style="futural"):
    try:
        if character in (" ", "\u00a0", "\u3000"):
            return StrokeGlyph((), 1.0 if character == "\u3000" else 0.5)
        if character in PUNCTUATION:
            return StrokeGlyph(tuple(tuple(stroke) for stroke in PUNCTUATION[character]))
        fullwidth = "！" <= character <= "～"
        ascii_character = chr(ord(character) - 0xFEE0) if fullwidth else character
        if "!" <= ascii_character <= "~":
            data = latin_data(latin_style)[ord(ascii_character) - 33]
            paths = svg_path(data["d"]).toSubpathPolygons()
            # Hershey原坐标向下为正，32单位一个字框；全角符号居中。
            top, units = latin_metrics(latin_style)
            shift = max(0, -min(p.x() for path in paths for p in path) / units)
            advance = max(data["o"] * 2 / units + shift, max(p.x() for path in paths for p in path) / units + shift + 1 / units)
            factor = min(1, 1 / advance) if fullwidth else 1
            dx = (1 - advance * factor) / 2 if fullwidth else 0
            strokes = tuple(tuple(((p.x() / units + shift) * factor + dx, (p.y() - top) / units) for p in path) for path in paths if len(path) > 1)
            return StrokeGlyph(strokes, 1.0 if fullwidth else advance)
        archive = hanzi_archive()
        if f"{character}.json" not in archive.NameToInfo:
            return None
        # 接点已在构建字库时计算；运行时只解压本次需要的单字并缓存。
        paths = json.loads(archive.read(f"{character}.json"))["strokes"]
        return StrokeGlyph(tuple(tuple((x / 1024, (900 - y) / 1024) for x, y in path) for path in paths))
    except (OSError, ValueError, KeyError, BadZipFile) as error:
        raise GenerationError("离线笔画字库读取失败，请保留assets/strokes目录：" + str(error)) from None


def is_latin_or_punctuation(char):
    return char in PUNCTUATION or char in (" ", "\u00a0", "\u3000") or "!" <= char <= "~" or "！" <= char <= "～"


def resolve_glyph(character, writing, occurrence=0):
    package = writing.package
    if package is not None and character in package.glyphs:
        glyph = package.glyphs[character]
        variants = glyph["variants"]
        index = seeded_random(writing.natural.seed, "variant", occurrence, character).randrange(len(variants)) if writing.natural.enabled and writing.natural.strength else 0
        return StrokeGlyph(tuple(tuple(map(tuple, stroke)) for stroke in variants[index]["strokes"]), glyph["advance"])
    if package is not None and not writing.fallback and not is_latin_or_punctuation(character):
        return None
    if writing.builtin_font and not is_latin_or_punctuation(character):
        paths = builtin_paths(writing.builtin_font, character)
        return StrokeGlyph(paths) if paths is not None else None
    return stroke_glyph(character, writing.latin_style)


def missing_characters(text, writing=None):
    writing = writing or WritingOptions()
    writing.validate()
    return [char for char in dict.fromkeys(text) if char not in "\r\n\t" and resolve_glyph(char, writing) is None]


def validate_text(text, writing=None):
    if not text.strip() or len(text) > 20000 or len(text.splitlines()) > 1000:
        raise GenerationError("请输入文字；正文一次最多20000字符、1000个手动换行。")
    missing = missing_characters(text, writing)
    if missing:
        examples = "、".join(f"{char}（U+{ord(char):04X}）" for char in missing[:16])
        raise GenerationError(f"笔画字库缺少{len(missing)}种字符：{examples}。请替换或在自定义字库补录；本次未生成，不会漏字发送。")


def stroke_ink(strokes, width):
    path = QPainterPath()
    for stroke in strokes:
        path.moveTo(*stroke[0])
        for point in stroke[1:]:
            path.lineTo(*point)
    stroker = QPainterPathStroker()
    stroker.setWidth(width)
    stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
    stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return stroker.createStroke(path)


def wrap_paragraph(paragraph, capacity, first_capacity, em, spacing, advances=None):
    # 优先在空格处换英文单词；连续长单词允许断开，中文避免常见标点挂在行首。
    rows, start = [], 0
    while start < len(paragraph):
        limit = first_capacity if not rows else capacity
        width, end = 0.0, start
        while end < len(paragraph):
            advance = (advances[end] if advances is not None else stroke_glyph(paragraph[end]).advance) * em
            following = width + (spacing if end > start else 0) + advance
            if following > limit + 1e-8:
                break
            width = following
            end += 1
        if end == start:
            raise GenerationError("单个笔画字形比可写区域更宽，请减小字号或页边距。")
        if end < len(paragraph):
            word = re.search(r"[A-Za-z0-9]+$", paragraph[start:end])
            if word and paragraph[end].isascii() and paragraph[end].isalnum() and word.start() > 0:
                end = start + word.start()
            while end > start + 1 and (paragraph[end] in NO_START or paragraph[end - 1] in NO_END):
                end -= 1
        rows.append(paragraph[start:end])
        start = end
        while start < len(paragraph) and paragraph[start] == " ":
            start += 1
    return rows or [""]


def layout_stroke_text(text, options=None, pen_width=0.18, writing=None):
    """先确定每个字的版本和自然变化，再按实际宽度换行，预览与发送共用这份轨迹。"""
    options = options or TextOptions()
    writing = writing or WritingOptions()
    options.validate()
    writing.validate()
    if not math.isfinite(pen_width) or not 0.05 <= pen_width <= 1.5:
        raise GenerationError("预览笔迹线宽应为0.05～1.5mm。")
    validate_text(text, writing)
    rect, em, natural = options.content_rect, options.em_mm, writing.natural
    name = writing.package.name if writing.package else BUILTIN_FONTS.get(writing.builtin_font, "单线楷体笔画")
    notes = [f"笔画字库：{name}；英文：{dict(LATIN_STYLES)[writing.latin_style]}。"]
    if writing.package is not None and writing.fallback:
        fallback = [char for char in dict.fromkeys(text) if char not in "\r\n\t" and not is_latin_or_punctuation(char) and char not in writing.package.glyphs]
        if fallback:
            notes.append(f"全文{len(fallback)}种字使用基础楷体补缺：{''.join(fallback[:24])}" + ("…" if len(fallback) > 24 else ""))
    if natural.enabled:
        notes.append(f"自然书写：字形{natural.strength:g}、字距{natural.spacing:g}、行内起伏{natural.baseline:g}，样式编号{natural.seed}；字内使用共同平滑形变，预览即实际轨迹。")
    ordered = not bool(writing.builtin_font) and (writing.package is None or writing.package.ordered)
    if not ordered:
        notes.append("本字体是自动提取的中线路径，不能保证标准笔顺；仍按行逐字完成，不进行填充。")
    pages, cache = [TextPage(stroke_width=pen_width, writing_notes=notes, stroke_ordered=ordered)], {}
    inner = rect.adjusted(pen_width / 2, pen_width / 2, -pen_width / 2, -pen_width / 2)
    reserve = .08 * em * natural.baseline if natural.enabled else 0
    height = em + 2 * reserve
    if inner.height() < height:
        raise GenerationError("可写高度不足以容纳当前字号和自然变化，请减小字号或页边距。")
    y, occurrence, line_index = inner.top(), 0, 0
    for paragraph in text.replace("\r\n", "\n").replace("\r", "\n").expandtabs(4).split("\n"):
        prepared = []
        for char in paragraph:
            key = (char, occurrence) if natural.enabled else char
            if key not in cache:
                glyph = resolve_glyph(char, writing, occurrence)
                strokes, advance = natural_glyph(glyph.strokes, glyph.advance, natural, occurrence)
                if natural.enabled and natural.spacing:
                    delta = seeded_random(natural.seed, "spacing", occurrence).uniform(-.12, .12) * natural.spacing
                    right = max((x for path in strokes for x, y in path), default=0)
                    advance = max(advance + delta, right + pen_width / em + .025)
                strokes = tuple(tuple((x * em, y * em) for x, y in stroke) for stroke in strokes)
                cache[key] = (strokes, advance, stroke_ink(strokes, pen_width))
            prepared.append((key, *cache[key]))
            occurrence += 1
        indent = options.indent * em if options.alignment == "left" else 0
        rows = wrap_paragraph(paragraph, inner.width(), inner.width() - indent, em, options.letter_spacing, [item[2] for item in prepared])
        cursor = 0
        for row, content in enumerate(rows):
            if y + height > inner.bottom() + 1e-7:
                if len(pages) >= 100:
                    raise GenerationError("正文超过100页，请分成多个文稿。")
                pages.append(TextPage(stroke_width=pen_width, writing_notes=notes, stroke_ordered=ordered))
                y = inner.top()
            entries = prepared[cursor:cursor + len(content)]
            cursor += len(content)
            while cursor < len(paragraph) and paragraph[cursor] == " ":
                cursor += 1
            width = sum(item[2] * em for item in entries) + max(0, len(content) - 1) * options.letter_spacing
            dx = {"left": indent if row == 0 else 0, "center": (inner.width() - width) / 2, "right": inner.width() - width}[options.alignment]
            x, path, characters = inner.left() + dx, QPainterPath(), []
            path.setFillRule(Qt.FillRule.WindingFill)
            row_rng = seeded_random(natural.seed, "row", line_index)
            tilt, phase = row_rng.uniform(-.25, .25), row_rng.uniform(0, math.tau)
            for key, strokes, advance, ink in entries:
                baseline = 0
                if natural.enabled:
                    t = (x - inner.left()) / max(1, inner.width())
                    jitter = seeded_random(natural.seed, "baseline", str(key)).uniform(-.25, .25)
                    baseline = reserve * (tilt * (2 * t - 1) + .4 * math.sin(t * math.tau + phase) + jitter)
                position = QPointF(x, y + reserve + baseline)
                if strokes:
                    characters.append(TextCharacter(ink, position, (name, key, em), strokes))
                    path.addPath(ink.translated(position))
                x += advance * em + options.letter_spacing
            if not path.isEmpty() and not rect.adjusted(-.001, -.001, .001, .001).contains(path.boundingRect()):
                raise GenerationError("字形或自然变化超出页边距，请调整字号、强度或字库。")
            pages[-1].lines.append(TextLine(content, path, y, height, characters))
            y += height * options.line_spacing
            line_index += 1
        y += options.paragraph_spacing
    return TextDocument(options, pages)


def layout_stroke_artwork(text, line_spacing, alignment, options, pen_width=0.18, writing=None):
    # 图案模式先排单线字框，再变换笔画，绝不经过图片细化。
    if len(text) > 2000 or len(text.splitlines()) > 60:
        raise GenerationError("文字图案一次最多2000字符、60行。")
    layout = TextOptions(paper_width=1000, paper_height=1000, font_size=14, margin_left=0, margin_right=0, margin_top=0, margin_bottom=0, line_spacing=line_spacing, alignment=alignment)
    document = layout_stroke_text(text, layout, pen_width, writing)
    page = document.pages[0]
    if len(document.pages) != 1:
        raise GenerationError("文字图案过长，请改用正文排版。")
    bounds = page.bounds
    if bounds.isEmpty():
        raise GenerationError("没有可绘制的笔画。")
    sx, sy = options.width / bounds.width(), options.height / bounds.height()
    transform = QTransform(sx, 0, 0, sy, options.x - bounds.left() * sx, options.y - bounds.top() * sy)
    for line in page.lines:
        for char in line.characters:
            char.position = transform.map(char.position)
            char.strokes = tuple(tuple((x * sx, y * sy) for x, y in stroke) for stroke in char.strokes)
            char.path = stroke_ink(char.strokes, pen_width)
        line.path = QPainterPath()
        line.path.setFillRule(Qt.FillRule.WindingFill)
        for char in line.characters:
            line.path.addPath(char.path.translated(char.position))
    return page


def render_stroke_source(text, line_spacing, alignment, pen_width, writing=None):
    options = TextOptions(paper_width=1000, paper_height=1000, margin_left=0, margin_right=0, margin_top=0, margin_bottom=0, line_spacing=line_spacing, alignment=alignment)
    document = layout_stroke_text(text, options, pen_width, writing)
    if len(document.pages) != 1 or len(text) > 2000 or len(text.splitlines()) > 60:
        raise GenerationError("文字图案过长，请改用正文排版。")
    page = document.pages[0]
    bounds = page.bounds.adjusted(-0.2, -0.2, 0.2, 0.2)
    scale = min(50, 2400 / max(bounds.width(), bounds.height()))
    image = QImage(math.ceil(bounds.width() * scale), math.ceil(bounds.height() * scale), QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(scale, scale)
    painter.translate(-bounds.left(), -bounds.top())
    for line in page.lines:
        painter.fillPath(line.path, Qt.GlobalColor.black)
    painter.end()
    return image
