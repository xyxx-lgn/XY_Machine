"""正文文件导入与可编辑文稿；不读取或改写设备参数。"""

from collections import Counter
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from app.generation import GenerationError
from app.stroke_fonts import LATIN_STYLES
from app.text_layout import FONT_SIZE_RANGE, LETTER_SPACING_RANGE
from app.text_writing import is_latin_or_punctuation, resolve_glyph


MAX_CHARACTERS = 20000
NUMBER_SETTINGS = {
    "width": (.05, 1000), "height": (.05, 1000), "x": (0, 1000), "y": (0, 1000),
    "spacing": (.02, 5), "tolerance": (0, 1), "minimum": (0, 10),
    "draw_feed": (1, 30000), "travel_feed": (1, 30000), "up_feed": (1, 30000), "down_feed": (1, 30000),
    "pen_up": (-1000000, 1000000), "pen_down": (-1000000, 1000000), "threshold": (0, 255),
    "line_spacing": (.5, 3), "text_pen_width": (.05, 1.5), "natural_strength": (0, 100),
    "natural_spacing": (0, 100), "natural_baseline": (0, 100), "natural_seed": (1, 2147483647),
    "font_size": FONT_SIZE_RANGE, "margin_left": (0, 999), "margin_right": (0, 999),
    "margin_top": (0, 999), "margin_bottom": (0, 999), "letter_spacing": LETTER_SPACING_RANGE,
    "paragraph_spacing": (0, 30), "indent": (0, 4), "direction": (0, 1),
}
BOOLEAN_SETTINGS = {"invert", "crop", "aspect", "optimize", "bold", "stroke_fallback", "natural_enabled"}
CHOICE_SETTINGS = {
    "mode": {"strokes", "outline", "outline_fill", "fill", "centerline"},
    "alignment": {"left", "center", "right"}, "layout_mode": {"page", "artwork"},
    "latin_style": set(dict(LATIN_STYLES)),
}


@dataclass
class ImportedText:
    text: str
    notes: list
    paragraph_alignments: tuple | None = None
    page_margins: tuple | None = None
    paragraph_font_sizes: tuple | None = None


def normalize_newlines(text):
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\u2028", "\n").replace("\u2029", "\n")


def validate_body(text, allow_empty=False):
    if not isinstance(text, str):
        raise GenerationError("文稿正文格式无效。")
    text = normalize_newlines(text)
    if not allow_empty and not text.strip():
        raise GenerationError("文档没有可导入的正文文字。")
    if len(text) > MAX_CHARACTERS or len(text.splitlines()) > 1000:
        raise GenerationError("正文一次最多20000字符、1000个手动换行，请将长文档分开导入。")
    if any((ord(char) < 32 and char not in "\n\t") or 0xD800 <= ord(char) <= 0xDFFF for char in text):
        raise GenerationError("正文包含无法使用的控制字符，请先另存为UTF-8文本。")
    return text


def import_text(path):
    """读取TXT、Word正文或PDF文字层，交由上位机排版。"""
    path = Path(path)
    if path.suffix.lower() not in (".txt", ".docx", ".pdf"):
        raise GenerationError("目前支持TXT、DOCX和带文字层的PDF。旧版DOC请先在Word中另存为DOCX。")
    if path.stat().st_size > 20_000_000:
        raise GenerationError("文档超过20MB，请拆分后导入。")
    if path.suffix.lower() == ".docx":
        return import_docx(path)
    if path.suffix.lower() == ".pdf":
        return import_pdf(path)
    raw = path.read_bytes()
    if len(raw) > 4_000_000:
        raise GenerationError("文本文件过大，请拆分后导入。")
    encodings = ("utf-16",) if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig", "gb18030")
    for encoding in encodings:
        try:
            text = raw.decode(encoding)
            break
        except UnicodeError:
            continue
    else:
        raise GenerationError("无法识别文本编码，请另存为UTF-8文本后重试。")
    notes = ["保留段落和空行，按当前字号与纸张重新排版。"]
    if "\f" in text:
        text = text.replace("\f", "\n")
        notes.append("原文分页符已转换为换行；页码按上位机重新计算。")
    return ImportedText(validate_body(text), notes)


def import_pdf(path):
    try:
        from pypdf import PdfReader
        from pypdf.errors import PyPdfError, WrongPasswordError
    except ImportError:
        raise GenerationError("PDF读取组件尚未安装，请运行上位机目录中的“安装依赖.ps1”后重试。") from None

    pages, empty_pages, character_count = [], [], 0
    try:
        with path.open("rb") as stream:
            reader = PdfReader(stream)
            if reader.is_encrypted:
                raise GenerationError("PDF已加密，请用可打开的原件另存为未加密PDF后再导入。")
            if len(reader.pages) > 100:
                raise GenerationError("PDF一次最多导入100页，请拆分后重试。")
            for number, page in enumerate(reader.pages, 1):
                # 依行的位置合并使用不同字体的片段，避免中英文混排被切成两行。
                extracted = page.extract_text(extraction_mode="layout", layout_mode_space_vertically=False)
                if not extracted or not extracted.strip():
                    extracted = page.extract_text() or ""
                text = normalize_newlines(extracted.replace("\f", "\n"))
                text = "\n".join(re.sub(r"[^\S\n]+", " ", line).strip() for line in text.split("\n")).strip()
                if not text:
                    empty_pages.append(number)
                    continue
                character_count += len(text) + (1 if pages else 0)
                if character_count > MAX_CHARACTERS:
                    raise GenerationError("PDF正文超过20000字符，请拆分后重试。")
                pages.append(text)
    except WrongPasswordError:
        raise GenerationError("PDF已加密，请用可打开的原件另存为未加密PDF后再导入。") from None
    except GenerationError:
        raise
    except (PyPdfError, KeyError, ValueError, TypeError, IndexError, RecursionError) as error:
        raise GenerationError("无法读取PDF文字，请重新导出PDF或复制文字到TXT后重试。") from error

    if not pages:
        raise GenerationError("PDF没有可提取的文字。扫描件需要先做文字识别（OCR），再导入识别出的文字。")
    notes = [f"已从PDF导入{len(pages)}页的文字，按上位机字号、页边距重新排版。请核对分栏、表格和页眉页脚的文字顺序。"]
    if empty_pages:
        shown = "、".join(map(str, empty_pages[:12])) + ("等" if len(empty_pages) > 12 else "")
        notes.append(f"第{shown}页没有可提取的文字，已跳过。")
    return ImportedText(validate_body("\n".join(pages)), notes)


def import_docx(path):
    notes = ["导入正文、段落与空行。字形和分页按上位机设置重新排版。"]
    try:
        with ZipFile(path) as archive:
            if sum(info.file_size for info in archive.infolist()) > 64_000_000:
                raise GenerationError("Word解压内容超过64MB，请删减图片或拆分文档。")
            info = archive.getinfo("word/document.xml")
            if info.file_size > 4_000_000:
                raise GenerationError("Word正文结构过大，请拆分文档。")
            raw = archive.read(info)
            extras = archive.namelist()
            styles_raw = archive.read("word/styles.xml") if "word/styles.xml" in extras else None
    except (BadZipFile, KeyError, RuntimeError) as error:
        raise GenerationError("无法读取DOCX，请在Word中重新另存为DOCX后重试。") from error
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise GenerationError("Word正文XML格式不支持，请重新另存文档。")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise GenerationError("Word正文结构损坏，无法导入。") from error
    tag = lambda element: element.tag.rsplit("}", 1)[-1]
    if tag(root) != "document":
        raise GenerationError("文件中没有有效的Word正文。")
    body = next((element for element in root if tag(element) == "body"), None)
    if body is None:
        raise GenerationError("文件中没有有效的Word正文。")
    present = {tag(element) for element in body.iter()}
    def attribute(element, name):
        return next((value for key, value in element.attrib.items() if key.rsplit("}", 1)[-1] == name), None) if element is not None else None

    def child(element, name):
        return next((item for item in element if tag(item) == name), None) if element is not None else None

    def alignment_value(ppr):
        value = attribute(child(ppr, "jc"), "val")
        return {"left": "left", "start": "left", "center": "center", "right": "right", "end": "right", "both": "left", "distribute": "left"}.get(value)

    def font_size_value(rpr):
        value = attribute(child(rpr, "sz"), "val")
        return int(value) / 2 if value and re.fullmatch(r"[0-9]{1,6}", value) else None

    styles, default_style, default_alignment, default_size = {}, None, "left", None
    if styles_raw:
        try:
            if b"<!DOCTYPE" in styles_raw.upper() or b"<!ENTITY" in styles_raw.upper():
                raise ET.ParseError("unsupported styles")
            styles_root = ET.fromstring(styles_raw)
            defaults = child(styles_root, "docDefaults")
            default_alignment = alignment_value(child(child(defaults, "pPrDefault"), "pPr")) or "left"
            default_size = font_size_value(child(child(defaults, "rPrDefault"), "rPr"))
            for style in styles_root.iter():
                kind = attribute(style, "type")
                if tag(style) != "style" or kind not in (None, "paragraph", "character"):
                    continue
                style_id = attribute(style, "styleId")
                if not style_id:
                    continue
                styles[style_id] = style
                if kind != "character" and attribute(style, "default") in {"1", "true", "on"}:
                    default_style = style_id
        except ET.ParseError:
            notes.append("Word样式表无法读取，仅采用正文中直接设置的对齐与字号。")

    def style_value(style_id, element, read):
        # 标题常从基础样式继承格式，结束循环引用并保留直接设置的值。
        seen = set()
        while style_id in styles and style_id not in seen:
            seen.add(style_id)
            style = styles[style_id]
            value = read(child(style, element))
            if value is not None:
                return value
            style_id = attribute(child(style, "basedOn"), "val")
        return None

    def read_margins(section):
        margin = child(section, "pgMar")
        values = tuple(attribute(margin, side) for side in ("left", "right", "top", "bottom"))
        if any(value is None or not re.fullmatch(r"[0-9]{1,6}", value) for value in values):
            return None
        if attribute(margin, "gutter") not in (None, "0"):
            return None
        result = tuple(round(int(value) * 25.4 / 1440, 2) for value in values)
        return result if max(result) <= 999 else None
    if "tbl" in present:
        notes.append("表格已按行提取文字，单元格用制表符分隔；表格边框与合并布局不保留。")
    if present & {"drawing", "pict", "object", "txbxContent", "oMath", "oMathPara", "altChunk"}:
        notes.append("图片、文本框、公式、嵌入对象或外部内容未导入，请对照原文检查。")
    if "numPr" in present:
        notes.append("自动项目符号与自动编号未转换，请检查并补充正文中的序号。")
    if present & {"del", "ins", "moveFrom", "moveTo"}:
        notes.append("修订按当前结果提取：保留插入、忽略删除；请核对原文。")
    if present & {"footnoteReference", "endnoteReference"} or any(re.fullmatch(r"word/(?:header\d+|footer\d+|footnotes|endnotes)\.xml", name) for name in extras):
        notes.append("页眉、页脚、脚注和尾注不属于正文，本次未导入。")
    def inline(element, sizes, inherited_size):
        name = tag(element)
        if name in {"del", "moveFrom", "drawing", "pict", "object", "txbxContent", "oMath", "oMathPara", "instrText", "altChunk"}:
            return ""
        if name == "t":
            text = element.text or ""
            count = sum(not char.isspace() for char in text)
            if count:
                sizes[inherited_size] += count
            return text
        if name == "tab":
            return "\t"
        if name in {"br", "cr"}:
            return "\n"
        if name == "noBreakHyphen":
            sizes[inherited_size] += 1
            return "-"
        if name == "r":
            rpr = child(element, "rPr")
            direct_size = font_size_value(rpr)
            if direct_size is None:
                direct_size = style_value(attribute(child(rpr, "rStyle"), "val"), "rPr", font_size_value)
            if direct_size is not None:
                inherited_size = direct_size
        return "".join(inline(item, sizes, inherited_size) for item in element)

    def paragraph_info(element):
        ppr = child(element, "pPr")
        style_id = attribute(child(ppr, "pStyle"), "val") or default_style
        alignment = alignment_value(ppr) or style_value(style_id, "pPr", alignment_value) or default_alignment
        paragraph_size = font_size_value(child(ppr, "rPr"))
        if paragraph_size is None:
            paragraph_size = style_value(style_id, "rPr", font_size_value)
        if paragraph_size is None:
            paragraph_size = default_size
        sizes = Counter()
        text = inline(element, sizes, paragraph_size)
        # 同一段的少量强调字不改变整段字号，采用正文字符数最多的字号。
        size = sizes.most_common(1)[0][0] if sizes else paragraph_size
        return text, alignment, size

    def blocks(element):
        result = []
        for child in element:
            name = tag(child)
            if name == "p":
                result.append(paragraph_info(child))
            elif name == "tbl":
                for row in child:
                    if tag(row) == "tr":
                        cells = []
                        for cell in row:
                            if tag(cell) == "tc":
                                cells.append("\n".join(text for text, _, _ in blocks(cell)))
                        result.append(("\t".join(cells), "left", None))
            elif name not in {"del", "moveFrom", "sectPr", "altChunk"}:
                result.extend(blocks(child))
        return result
    paragraphs = blocks(body)
    text = validate_body("\n".join(value for value, _, _ in paragraphs))
    # 段内手动换行沿用该段的字号和对齐。
    formats = [(alignment, size) for value, alignment, size in paragraphs for _ in normalize_newlines(value).split("\n")]
    alignments = tuple(alignment for alignment, _ in formats)
    sizes = tuple(size if size is not None and FONT_SIZE_RANGE[0] <= size <= FONT_SIZE_RANGE[1] else None for _, size in formats)
    notes.append("已读取Word段落对齐。居中、左对齐和右对齐分别应用；两端对齐按左对齐处理。")
    if any(size is not None for size in sizes):
        notes.append("已读取Word各段常用字号。可在字号区取消“采用Word段落字号”，改用统一字号。")
    if any(size is not None and not FONT_SIZE_RANGE[0] <= size <= FONT_SIZE_RANGE[1] for _, size in formats):
        notes.append("超出6～96 pt的字号使用上位机的字号设置。")
    sections = [read_margins(item) for item in body.iter() if tag(item) == "sectPr"]
    margins = sections[0] if sections and all(value == sections[0] for value in sections) else None
    if margins is not None:
        left, right, top, bottom = margins
        notes.append(f"Word页边距：左{left:g}、右{right:g}、上{top:g}、下{bottom:g} mm。可点“采用Word页边距”填入；纸张尺寸以主窗口为准。")
    elif sections:
        notes.append("Word各节页边距不一致或包含特殊设置，请在上位机手动设置。")
    return ImportedText(text, notes, alignments, margins, sizes if any(size is not None for size in sizes) else None)


@dataclass
class FontCoverage:
    counts: dict
    own: list
    standard: list
    fallback: list
    unavailable: list
    recording: list

    def summary(self, personal=False):
        result = f"正文{sum(self.counts.values())}个非空白字符、{len(self.counts)}种字。"
        if personal:
            result += f"个人字库覆盖{len(self.own)}种；需补录{len(self.recording)}种；"
        result += f"当前设置无法生成{len(self.unavailable)}种。"
        return result


def font_coverage(text, writing):
    """独立统计个人库覆盖与实际可生成范围，补缺打开时也显示待录字符。"""
    text = validate_body(text, allow_empty=True)
    writing.validate()
    result = FontCoverage(dict(Counter(char for char in text if char not in "\r\n\t \u00a0\u3000")), [], [], [], [], [])
    for char in result.counts:
        if writing.package is not None and char in writing.package.glyphs:
            result.own.append(char)
        elif writing.package is not None and not is_latin_or_punctuation(char):
            if not char.isspace():
                result.recording.append(char)
            if resolve_glyph(char, writing) is None:
                result.unavailable.append(char)
            else:
                result.fallback.append(char)
        elif resolve_glyph(char, writing) is None:
            result.unavailable.append(char)
            if writing.package is None and not char.isspace():
                result.recording.append(char)
        else:
            result.standard.append(char)
    return result


def validate_manuscript(data):
    if not isinstance(data, dict) or data.get("format") != "xy-manuscript" or type(data.get("schema")) is not int or data["schema"] != 1:
        raise GenerationError("文稿格式不支持，请选择本软件保存的 .xytext 文件。")
    text = validate_body(data.get("text"), allow_empty=True)
    paragraph_alignments = data.get("paragraph_alignments")
    if paragraph_alignments is not None:
        if not isinstance(paragraph_alignments, list) or len(paragraph_alignments) != len(text.split("\n")) or any(value not in ("left", "center", "right") for value in paragraph_alignments):
            raise GenerationError("文稿段落对齐信息无效。")
    sizes = data.get("paragraph_font_sizes")
    if sizes is not None:
        if not isinstance(sizes, list) or len(sizes) != len(text.split("\n")) or any(value is not None and (type(value) not in (int, float) or not FONT_SIZE_RANGE[0] <= value <= FONT_SIZE_RANGE[1]) for value in sizes):
            raise GenerationError("文稿段落字号信息无效。")
    if "use_document_font_sizes" in data and (type(data["use_document_font_sizes"]) is not bool or sizes is None):
        raise GenerationError("文稿段落字号开关无效。")
    paper, settings = data.get("paper"), data.get("settings")
    if not isinstance(paper, dict) or set(paper) != {"width", "height"} or any(type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 1000 for value in paper.values()):
        raise GenerationError("文稿纸张尺寸无效。")
    required = set(NUMBER_SETTINGS) | BOOLEAN_SETTINGS | set(CHOICE_SETTINGS) | {"font", "stroke_font"}
    if not isinstance(settings, dict) or set(settings) != required:
        raise GenerationError("文稿参数不完整或版本不兼容，本次没有替换当前内容。")
    for name, (low, high) in NUMBER_SETTINGS.items():
        value = settings[name]
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise GenerationError(f"文稿参数{name}超出可用范围。")
        if name in {"natural_seed", "direction", "threshold"} and type(value) is not int:
            raise GenerationError(f"文稿参数{name}需要整数。")
    if any(type(settings[name]) is not bool for name in BOOLEAN_SETTINGS):
        raise GenerationError("文稿开关参数无效。")
    if any(not isinstance(settings[name], str) or settings[name] not in choices for name, choices in CHOICE_SETTINGS.items()):
        raise GenerationError("文稿的排版或字体样式选项无效。")
    if any(not isinstance(settings[name], str) or len(settings[name]) > 200 or any(ord(c) < 32 for c in settings[name]) for name in ("font", "stroke_font")):
        raise GenerationError("文稿字体名称或字库标识无效。")
    if settings["pen_down"] >= settings["pen_up"]:
        raise GenerationError("文稿落笔位置必须低于抬笔位置。")
    if type(data.get("page")) is not int or not 1 <= data["page"] <= 100:
        raise GenerationError("文稿页码无效。")
    return dict(data, text=text)


def read_manuscript(path):
    path = Path(path)
    if path.stat().st_size > 1_000_000:
        raise GenerationError("文稿文件超过1MB，无法打开。")
    try:
        return validate_manuscript(json.loads(path.read_text(encoding="utf-8-sig")))
    except (UnicodeError, ValueError, TypeError) as error:
        if isinstance(error, GenerationError):
            raise
        raise GenerationError("文稿读取失败，文件可能已损坏。") from error


def save_manuscript(data, path):
    data = validate_manuscript(data)
    path = Path(path)
    if not path.suffix:
        path = path.with_suffix(".xytext")
    elif path.suffix.lower() != ".xytext":
        raise GenerationError("请使用 .xytext 扩展名保存文稿。")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".xytext.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)
    return path
