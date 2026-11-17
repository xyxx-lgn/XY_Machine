"""照片分格、去底色和中线录字；不联网，不推断未拍到的字或真实笔顺。"""

import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtGui import QImage

from app.generation import GenerationError, PlotOptions, centerline_paths, checkpoint, join_touching_paths, order_paths, read_image
from app.stroke_fonts import package_data, validate_package
from app.photo_paths import continuous_photo_paths, ink_reproduction_paths


@dataclass
class PhotoSample:
    character: str
    image: QImage
    strokes: list
    error: str = ""
    original: QImage | None = None
    box: tuple = ()
    confidence: float | None = None
    note: str = ""
    trajectory: str = "continuous"
    ink_width: float = .03


def photo_characters(value):
    chars = [char for char in str(value) if not char.isspace()]
    if not chars or len(chars) > 20000 or any(ord(char) < 33 for char in chars):
        raise GenerationError("请按格子顺序输入1～20000个可绘制字符；重复字会作为不同版本。")
    return chars


def image_pixels(image):
    image = image.convertToFormat(QImage.Format.Format_RGB888)
    return np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.bytesPerLine())[:, :image.width() * 3].reshape(image.height(), image.width(), 3).copy()


def gray_image(pixels):
    pixels = np.ascontiguousarray(pixels)
    return QImage(pixels.data, pixels.shape[1], pixels.shape[0], pixels.strides[0], QImage.Format.Format_Grayscale8).copy()


def rgb_image(pixels):
    pixels = np.ascontiguousarray(pixels)
    return QImage(pixels.data, pixels.shape[1], pixels.shape[0], pixels.strides[0], QImage.Format.Format_RGB888).copy()


def grid_cells(width, height, rows, columns, gap_x=0, gap_y=0):
    """间隔按格宽/高的百分比计；采样表的横/纵间隔为2/17和8/17。"""
    if type(rows) is not int or type(columns) is not int or not 1 <= rows <= 100 or not 1 <= columns <= 100:
        raise GenerationError("照片行列数应为1～100。")
    if any(not math.isfinite(value) or not 0 <= value <= 100 for value in (gap_x, gap_y)):
        raise GenerationError("格子间隔应为0～100%。")
    cw = width / (columns + (columns - 1) * gap_x / 100)
    ch = height / (rows + (rows - 1) * gap_y / 100)
    return [(round(c * cw * (1 + gap_x / 100)), round(r * ch * (1 + gap_y / 100)),
             round(c * cw * (1 + gap_x / 100) + cw), round(r * ch * (1 + gap_y / 100) + ch))
            for r in range(rows) for c in range(columns)]


def crop_photo(pixels, region=None):
    """region为像素矩形或左上、右上、右下、左下四角；四角模式做透视校正。"""
    height, width = pixels.shape[:2]
    if region is None:
        return pixels
    values = np.asarray(region, dtype=np.float64)
    if not np.isfinite(values).all():
        raise GenerationError("照片选区包含无效坐标。")
    if values.shape == (4,):
        x0, y0, x1, y1 = map(round, values)
        if not 0 <= x0 < x1 <= width or not 0 <= y0 < y1 <= height:
            raise GenerationError("请在照片内框选完整字格区域。")
        return pixels[y0:y1, x0:x1]
    if values.shape != (4, 2) or np.any(values < 0) or np.any(values[:, 0] > width) or np.any(values[:, 1] > height):
        raise GenerationError("请依次选取照片内的左上、右上、右下、左下四角。")
    points = values.astype(np.float32)
    contour = points.reshape(-1, 1, 2)
    if not cv2.isContourConvex(contour) or cv2.contourArea(contour) < 256:
        raise GenerationError("四角顺序不正确或区域过小，请重新选择。")
    w = round(max(math.dist(points[0], points[1]), math.dist(points[3], points[2])))
    h = round(max(math.dist(points[0], points[3]), math.dist(points[1], points[2])))
    if min(w, h) < 16 or w * h > 25_000_000:
        raise GenerationError("校正区域过小或过大。")
    matrix = cv2.getPerspectiveTransform(points, np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]]))
    return cv2.warpPerspective(pixels, matrix, (w, h), borderValue=(255, 255, 255))


def paper_gray(cell, remove_colored=True, flatten=True):
    """用局部纸面亮度去除黄纸/阴影；彩色格线按色差消除，黑色墨迹仍保留。

    背景由灰度闭运算估计，仅用于亮度除法，不对最终墨迹做闭运算，避免相邻笔画粘连。
    """
    cell = cell.copy()
    if remove_colored:
        red, green, blue = [cell[:, :, index].astype(np.float32) for index in range(3)]
        yellow = np.maximum(0, (red + green) / 2 - blue)
        red_ink = (red - green > np.maximum(14, yellow * .65)) & (red > blue + 8)
        blue_ink = (blue - np.minimum(red, green) > 22) & (blue > red + 10)
        colored = red_ink | blue_ink
        # 替换彩色像素为邻近纸面亮度；不能直接铺白后再让背景估计被白洞干扰。
        gray = cv2.cvtColor(cell, cv2.COLOR_RGB2GRAY)
        if colored.any():
            gray = cv2.inpaint(gray, colored.astype(np.uint8) * 255, 3, cv2.INPAINT_TELEA)
    else:
        gray = cv2.cvtColor(cell, cv2.COLOR_RGB2GRAY)
    if flatten:
        size = max(9, min(61, round(min(gray.shape) * .3))) | 1
        background = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size)))
        background = cv2.GaussianBlur(background, (0, 0), max(1, size / 6))
        gray = cv2.divide(gray, np.maximum(background, 1), scale=255)
    return gray


def normalize_cell(cell, threshold=210, adaptive=False, remove_colored=True, processing="manual"):
    # 同一缩放率保留宽高比例和原来的格内位置，不把窄字拉成方块。
    h, w = cell.shape[:2]
    scale = 240 / max(h, w)
    cell = cv2.resize(cell, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
    if processing not in ("auto", "manual"):
        raise GenerationError("照片处理方式无效。")
    gray = paper_gray(cell, remove_colored, processing == "auto" or adaptive)
    if processing == "auto":
        level, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        # 默认210是零偏移；阈值仍能微调，但不会再直接把偏灰纸面压成黑色。
        threshold = max(25, min(240, level + 5 + (threshold - 210) * .55))
    binary = np.where(gray <= threshold, 0, 255).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(255 - binary, connectivity=8)
    for label in range(1, count):
        if stats[label, cv2.CC_STAT_AREA] < 3:
            binary[labels == label] = 255
    canvas = np.full((256, 256), 255, dtype=np.uint8)
    dy, dx = (256 - binary.shape[0]) // 2, (256 - binary.shape[1]) // 2
    canvas[dy:dy + binary.shape[0], dx:dx + binary.shape[1]] = binary
    return canvas


def trace_sample(character, image, cancel=None, trajectory="continuous", ink_width=.03):
    gray = image.convertToFormat(QImage.Format.Format_Grayscale8)
    pixels = np.frombuffer(gray.constBits(), np.uint8).reshape(gray.height(), gray.bytesPerLine())[:, :gray.width()]
    mask = (pixels < 128).astype(np.uint8) * 255
    if np.count_nonzero(mask) < 8:
        raise GenerationError("几乎为空，请检查框选区域、阈值和字符顺序。")
    if np.mean(mask != 0) > .45:
        raise GenerationError("黑色面积过大，请检查阈值或拍摄光线。")
    if trajectory in ("continuous", "pen_shape"):
        paths = continuous_photo_paths(mask, cancel, pen_shape=trajectory == "pen_shape")
    elif trajectory == "ink":
        if not math.isfinite(ink_width) or not .001 <= ink_width <= .2:
            raise GenerationError("笔迹宽度与参考字框的比例应为0.1%～20%，请检查参考字框大小与实际笔迹宽度。")
        paths = ink_reproduction_paths(mask, ink_width * (gray.width() - 1), cancel)
    elif trajectory == "legacy":
        paths = centerline_paths(mask, 1, 1, PlotOptions(tolerance=.65, min_length=1), cancel, preserve_connections=True)
        paths = order_paths(join_touching_paths(paths, cancel), cancel)
    else:
        raise GenerationError("照片轨迹方式无效。")
    if not paths:
        raise GenerationError("轨迹为空或碎线过多，请清理格线、噪点后重新提取。")
    if len(paths) > 128:
        suggestion = "请清理噪点，或改用单线方式；墨迹还原也可在符合实际笔宽时调整参考设置。" if trajectory == "ink" else "请清理格线、噪点后重新提取。"
        raise GenerationError(f"当前字有{len(paths)}条路径，字库每字每版本最多128条，不能完整收录。" + suggestion)
    normalized = []
    for path in paths:
        points = []
        for x, y in path:
            point = (round(float(x) / max(1, gray.width() - 1), 6), round(float(y) / max(1, gray.height() - 1), 6))
            if not points or point != points[-1]:
                points.append(point)
        if len(points) >= 2:
            if len(points) > 4000:
                raise GenerationError("当前字的一条路径超过4000点，不能完整收录；请清理噪点或改用单线方式重新提取。")
            normalized.append(points)
    if not normalized:
        raise GenerationError("未提取到有效线段。")
    return PhotoSample(character, gray.copy(), normalized, trajectory=trajectory, ink_width=ink_width)


def prepare_photo_samples(source, characters, rows, columns, threshold=210, margin=3, gap_x=0, gap_y=0, region=None, adaptive=False, remove_colored=True, cancel=None, progress=None, processing="manual", trajectory="continuous", ink_width=.03):
    """逐格返回可核对的候选；坏格保留错误，不静默错位到后面的字。

    此处只处理内存，不写用户字库。GUI明确收录候选后才允许保存，取消不改变原字库。
    """
    chars = photo_characters(characters)
    if not math.isfinite(threshold) or not 40 <= threshold <= 250 or not math.isfinite(margin) or not 0 <= margin <= 35:
        raise GenerationError("照片阈值应为40～250，格内裁边应为0～35%。")
    image = source.copy() if isinstance(source, QImage) else read_image(source)
    if image.isNull() or image.width() * image.height() > 25_000_000:
        raise GenerationError("照片为空或超过2500万像素。")
    pixels = crop_photo(image_pixels(image), region)
    cells = grid_cells(pixels.shape[1], pixels.shape[0], rows, columns, gap_x, gap_y)
    if len(chars) > len(cells):
        raise GenerationError("字符数量多于格子数量，请检查照片行列数和字符顺序。")
    results = []
    for index, char in enumerate(chars):
        checkpoint(cancel)
        x0, y0, x1, y1 = cells[index]
        ix, iy = round((x1 - x0) * margin / 100), round((y1 - y0) * margin / 100)
        if min(x1 - x0 - ix * 2, y1 - y0 - iy * 2) < 12:
            raise GenerationError("每格有效像素不足12，请提供更清晰的照片或减少分格。")
        cell = pixels[y0 + iy:y1 - iy, x0 + ix:x1 - ix]
        binary = normalize_cell(cell, threshold, adaptive, remove_colored, processing)
        cell_image = gray_image(binary)
        try:
            result = trace_sample(char, cell_image, cancel, trajectory, ink_width)
        except GenerationError as error:
            result = PhotoSample(char, cell_image, [], f"第{index + 1}格“{char}”：{error}")
        result.trajectory, result.ink_width = trajectory, ink_width
        result.original = rgb_image(cell)
        if min(cell.shape[:2]) < 24:
            result.note = "原图单字不足24像素，细节有限，建议放大拍摄后重录。"
        results.append(result)
        if progress:
            progress(index + 1, len(chars))
    return results


def build_photo_package(samples, name="我的照片手写体", base_package=None):
    glyphs = json.loads(json.dumps(base_package.glyphs)) if base_package else {}
    plan = list(base_package.record_plan or base_package.glyphs) if base_package else []
    counts = Counter(plan)
    for sample in samples:
        if sample.error or not sample.strokes:
            raise GenerationError(sample.error or "照片样本没有有效轨迹。")
        glyph = glyphs.setdefault(sample.character, {"advance": 1.0, "variants": []})
        if len(glyph["variants"]) >= 16:
            raise GenerationError(f"“{sample.character}”超过16个版本，请减少重复收录。")
        glyph["variants"].append({"strokes": [[(x * glyph["advance"], y) for x, y in path] for path in sample.strokes]})
        if counts[sample.character] < len(glyph["variants"]):
            plan.append(sample.character)
            counts[sample.character] += 1
    source = "照片中线导入（无真实笔顺）"
    if any(sample.trajectory == "ink" for sample in samples):
        source += "；含墨迹还原多线，请使用收录时的参考字框与实际笔宽"
    return validate_package({"schema": 1, "name": name, "source": source, "ordered": False, "record_plan": plan, "glyphs": glyphs})


def export_photo_samples(samples, destination):
    package = build_photo_package(samples)
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise GenerationError("照片样本输出目录非空，请选择新目录，避免覆盖已有样本。")
    destination.mkdir(parents=True, exist_ok=True)
    records = []
    for index, sample in enumerate(samples):
        name = f"u{ord(sample.character):06x}_{index + 1:05d}.png"
        if not sample.image.save(str(destination / name)):
            raise GenerationError("保存照片样本失败：" + name)
        records.append({"character": sample.character, "file": name, "index": index + 1})
    (destination / "samples.json").write_text(json.dumps({"schema": 1, "source": "photo", "samples": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    (destination / "photo-font.json").write_text(json.dumps(package_data(package), ensure_ascii=False), encoding="utf-8")
    return destination


def extract_photo_samples(source, characters, rows, columns, destination, threshold=210, margin=3, **options):
    samples = prepare_photo_samples(source, characters, rows, columns, threshold, margin, **options)
    export_photo_samples(samples, destination)
    return Path(destination), [sample.character for sample in samples]


def photo_font_package(folder, name="照片手写候选"):
    folder = Path(folder)
    try:
        records = json.loads((folder / "samples.json").read_text(encoding="utf-8"))["samples"]
    except (OSError, ValueError, KeyError, TypeError):
        raise GenerationError("照片样本清单读取失败，请重新导入照片。") from None
    if not isinstance(records, list) or not 1 <= len(records) <= 20000:
        raise GenerationError("照片样本清单数量无效。")
    samples = []
    for record in records:
        if not isinstance(record, dict):
            raise GenerationError("照片样本清单条目无效。")
        char, filename = record.get("character"), record.get("file")
        if not isinstance(char, str) or len(char) != 1 or ord(char) < 33 or not isinstance(filename, str) or Path(filename).name != filename:
            raise GenerationError("照片样本清单包含无效字符或文件名。")
        samples.append(trace_sample(char, read_image(folder / filename)))
    return build_photo_package(samples, name)
