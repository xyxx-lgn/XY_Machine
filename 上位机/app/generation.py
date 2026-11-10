import math
from dataclasses import dataclass, replace
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetricsF, QImage, QImageReader, QPainter, QPainterPath

from app.gcode import parse_program
from app.profile import ROOT


class GenerationError(ValueError):
    pass


class GenerationCancelled(Exception):
    pass


@dataclass
class PlotOptions:
    paper_width: float = 210.0
    paper_height: float = 297.0
    x: float = 10.0
    y: float = 10.0
    width: float = 40.0
    height: float = 15.0
    mode: str = "outline"
    threshold: int = 128
    invert: bool = False
    crop: bool = True
    spacing: float = 0.18
    vertical: bool = False
    tolerance: float = 0.03
    min_length: float = 0.08
    optimize: bool = True
    draw_feed: float = 400.0
    travel_feed: float = 1200.0
    pen_up: float = 2.0
    pen_down: float = -0.4
    up_feed: float = 240.0
    down_feed: float = 120.0
    z_min: float = -1.5
    z_max: float = 5.0

    def validate(self):
        for name, value in vars(self).items():
            if isinstance(value, (float, int)) and not math.isfinite(value):
                raise GenerationError(f"{name}不是有效数值。")
        if self.mode not in ("outline", "outline_fill", "fill", "centerline"):
            raise GenerationError("不支持的路径模式。")
        if not 0 <= self.threshold <= 255:
            raise GenerationError("阈值应为0～255。")
        if not 0.05 <= self.width <= 1000 or not 0.05 <= self.height <= 1000:
            raise GenerationError("图案尺寸应为0.05～1000mm。")
        if not 0 < self.paper_width <= 1000 or not 0 < self.paper_height <= 1000:
            raise GenerationError("纸张尺寸应为0～1000mm。")
        if self.x < 0 or self.y < 0 or self.x + self.width > self.paper_width + 1e-6 or self.y + self.height > self.paper_height + 1e-6:
            raise GenerationError("图案超出纸张：请减小尺寸或调整左上角X/Y。")
        if not 0.02 <= self.spacing <= 5 or not 0 <= self.tolerance <= 1 or not 0 <= self.min_length <= 10:
            raise GenerationError("填线间距应为0.02～5mm，简化误差0～1mm，短线过滤0～10mm。")
        if any(not 1 <= value <= 30000 for value in (self.draw_feed, self.travel_feed, self.up_feed, self.down_feed)):
            raise GenerationError("进给速度应为1～30000单位/分钟。")
        if not self.z_min <= self.pen_down < self.pen_up <= self.z_max:
            raise GenerationError("应满足设备Z下界 ≤ 落笔位置 < 抬笔位置 ≤ Z上界。")


@dataclass
class GeneratedTask:
    program: object
    image: QImage
    path_count: int
    point_count: int
    travel_length: float
    minimum_seconds: float
    resolution: float
    notes: list


def checkpoint(cancel):
    if cancel and cancel():
        raise GenerationCancelled()


def image_gray(image):
    # 透明背景先铺白，避免透明PNG被识别成整块黑色。
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    pixels = np.frombuffer(rgba.constBits(), np.uint8).reshape(rgba.height(), rgba.bytesPerLine())
    pixels = pixels[:, :rgba.width() * 4].reshape(rgba.height(), rgba.width(), 4)
    alpha = pixels[:, :, 3:4].astype(np.float32) / 255
    rgb = np.rint(pixels[:, :, :3] * alpha + 255 * (1 - alpha)).astype(np.uint8)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)


def read_image(path):
    path = Path(path)
    if path.stat().st_size > 40_000_000:
        raise GenerationError("图片超过40MB，请先缩小后导入。")
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    if not size.isValid() or size.width() * size.height() > 25_000_000:
        raise GenerationError("无法读取图片尺寸，或图片超过2500万像素。请使用PNG/JPG/BMP。")
    image = reader.read()
    if image.isNull():
        raise GenerationError("图片读取失败：" + reader.errorString())
    return image


def render_text(text, family, bold=False):
    """在界面线程将系统字体转成黑白源图；后续轮廓、填充和中线处理共用图片引擎。"""
    if not text.strip() or len(text) > 2000 or len(text.splitlines()) > 60:
        raise GenerationError("请输入文字；一次最多2000字符、60行。")
    font = QFont(family)
    font.setPixelSize(160)
    font.setBold(bold)
    metrics = QFontMetricsF(font)
    path = QPainterPath()
    for index, line in enumerate(text.splitlines()):
        path.addText(0, index * metrics.lineSpacing(), font, line)
    bounds = path.boundingRect()
    if bounds.isEmpty():
        raise GenerationError("所选字体没有可绘制的文字轮廓。")
    # 长文本限制栅格大小，避免生成源图时阻塞主界面。
    scale = min(1.0, 5000 / max(bounds.width(), bounds.height()), math.sqrt(10_000_000 / (bounds.width() * bounds.height())))
    width, height = math.ceil(bounds.width() * scale) + 8, math.ceil(bounds.height() * scale) + 8
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(4, 4)
    painter.scale(scale, scale)
    painter.translate(-bounds.left(), -bounds.top())
    painter.fillPath(path, Qt.GlobalColor.black)
    painter.end()
    return image


def threshold_image(image, options):
    gray = image_gray(image)
    mask = (gray <= options.threshold).astype(np.uint8) * 255
    if options.invert:
        mask = 255 - mask
    if not np.any(mask):
        raise GenerationError("阈值处理后没有黑色图案，请调整阈值或反相。")
    if options.crop:
        x, y, width, height = cv2.boundingRect(mask)
        mask = mask[y:y + height, x:x + width]
    return mask


def source_ratio(image, threshold=128, invert=False, crop=True):
    mask = threshold_image(image, PlotOptions(threshold=threshold, invert=invert, crop=crop))
    return mask.shape[1] / max(1, mask.shape[0])


def prepare_mask(image, options):
    mask = threshold_image(image, options)
    # 目标约25像素/mm，长边最多2400像素，所有后续误差均换算成物理尺寸。
    density = min(25.0, 2399 / max(options.width, options.height))
    width, height = max(2, round(options.width * density) + 1), max(2, round(options.height * density) + 1)
    mask = cv2.resize(mask, (width, height), interpolation=cv2.INTER_NEAREST)
    return mask, options.width / (width - 1), options.height / (height - 1)


def path_length(path):
    return sum(math.dist(a, b) for a, b in zip(path, path[1:]))


def simplify(path, tolerance, closed=False):
    if tolerance <= 0 or len(path) <= 2:
        return list(path)
    points = np.asarray(path, np.float32).reshape(-1, 1, 2)
    result = [tuple(map(float, point[0])) for point in cv2.approxPolyDP(points, tolerance, closed)]
    if closed and result and result[-1] != result[0]:
        result.append(result[0])
    return result


def contour_paths(mask, sx, sy, options, cancel):
    # 四周补白，保留外边界及O/B等字形的内部孔洞。
    contours, _ = cv2.findContours(cv2.copyMakeBorder(mask, 1, 1, 1, 1, cv2.BORDER_CONSTANT), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    paths = []
    for contour in contours:
        checkpoint(cancel)
        path = [(max(0, float(p[0][0] - 1)) * sx, max(0, float(p[0][1] - 1)) * sy) for p in contour]
        if len(path) > 1:
            path.append(path[0])
            path = simplify(path, options.tolerance, True)
            if len(path) > 2 and path_length(path) >= options.min_length:
                paths.append(path)
        if len(paths) > 30000:
            raise GenerationError("轮廓过多，请清理图片噪点或增加短线过滤。")
    return paths


def ink_connector(mask, start, end, sx, sy):
    # 连接线逐像素验证，禁止穿过字母孔洞或不同笔画之间的空白。
    count = max(2, math.ceil(max(abs(end[0] - start[0]) / sx, abs(end[1] - start[1]) / sy) * 2) + 1)
    xs = np.rint(np.linspace(start[0], end[0], count) / sx).astype(int)
    ys = np.rint(np.linspace(start[1], end[1], count) / sy).astype(int)
    return bool(np.all(mask[np.clip(ys, 0, mask.shape[0] - 1), np.clip(xs, 0, mask.shape[1] - 1)]))


def fill_connector(mask, path, start, end, sx, sy):
    previous = path[-1]
    if ink_connector(mask, previous, start, sx, sy):
        return [start]
    # 曲线边缘呈栅格台阶时，先沿黑色内部折入，再连接下一行，避免无谓抬笔。
    left = max(min(path[-2][0], previous[0]), min(start[0], end[0]))
    right = min(max(path[-2][0], previous[0]), max(start[0], end[0]))
    if left > right:
        return None
    for x in (min(right, max(left, start[0])), (left + right) / 2):
        route = [previous, (x, previous[1]), (x, start[1]), start]
        if all(ink_connector(mask, a, b, sx, sy) for a, b in zip(route, route[1:])):
            return [point for a, point in zip(route, route[1:]) if a != point]
    return None


def fill_paths(mask, sx, sy, options, cancel):
    if options.vertical:
        paths = fill_paths(mask.T.copy(), sy, sx, replace(options, vertical=False), cancel)
        return [[(y, x) for x, y in path] for path in paths]
    active, completed = [], []
    height = (mask.shape[0] - 1) * sy
    for row, y in enumerate(np.arange(0, height + 1e-8, options.spacing)):
        checkpoint(cancel)
        pixels = mask[min(mask.shape[0] - 1, round(y / sy))] > 0
        transitions = np.diff(np.pad(pixels.astype(np.int8), (1, 1)))
        starts, ends = np.flatnonzero(transitions == 1), np.flatnonzero(transitions == -1) - 1
        runs = [(start * sx, end * sx) for start, end in zip(starts, ends) if (end - start) * sx >= options.min_length]
        if row % 2:
            runs.reverse()
        following = []
        for left, right in runs:
            start, end = ((left, y), (right, y)) if row % 2 == 0 else ((right, y), (left, y))
            # 相邻扫描行就近接续，连续的实心区域形成蛇形线，减少抬落笔。
            candidates = sorted(range(len(active)), key=lambda i: math.dist(active[i][-1], start))[:8]
            index, route = None, None
            for candidate in candidates:
                if math.dist(active[candidate][-1], start) <= options.spacing * 3 + sx:
                    route = fill_connector(mask, active[candidate], start, end, sx, sy)
                    if route is not None:
                        index = candidate
                        break
            if index is None:
                following.append([start, end])
            else:
                path = active.pop(index)
                path.extend(route)
                path.append(end)
                following.append(path)
        completed.extend(active)
        active = following
        if len(completed) + len(active) > 30000 or sum(len(path) for path in following) > 180000:
            raise GenerationError("填充过于复杂，请增大间距或缩小图案。")
    completed.extend(active)
    return completed


def thin_mask(mask, cancel):
    """Zhang-Suen细化保留连通性；每轮检查取消，避免密集图像长时间占用界面。"""
    image = np.pad((mask > 0).astype(np.uint8), 1)
    for _ in range(max(mask.shape)):
        checkpoint(cancel)
        changed = False
        for phase in (0, 1):
            p = image[1:-1, 1:-1]
            neighbors = (image[:-2, 1:-1], image[:-2, 2:], image[1:-1, 2:], image[2:, 2:], image[2:, 1:-1], image[2:, :-2], image[1:-1, :-2], image[:-2, :-2])
            count = sum(neighbors)
            transitions = sum(((a == 0) & (b == 1)).astype(np.uint8) for a, b in zip(neighbors, neighbors[1:] + neighbors[:1]))
            n, _, e, _, s, _, w, _ = neighbors
            test = (n * e * s == 0) & (e * s * w == 0) if phase == 0 else (n * e * w == 0) & (n * s * w == 0)
            remove = (p == 1) & (count >= 2) & (count <= 6) & (transitions == 1) & test
            if np.any(remove):
                p[remove] = 0
                changed = True
        if not changed:
            break
    return image[1:-1, 1:-1]


def centerline_paths(mask, sx, sy, options, cancel):
    skeleton = thin_mask(mask, cancel)
    ys, xs = np.nonzero(skeleton)
    if len(xs) > 180000:
        raise GenerationError("中线节点过多，请先清理噪点或缩小图片。")
    nodes = set(zip(map(int, xs), map(int, ys)))
    graph = {}
    for index, (x, y) in enumerate(sorted(nodes)):
        if index % 1000 == 0:
            checkpoint(cancel)
        adjacent = []
        for dx, dy in ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)):
            target = (x + dx, y + dy)
            if target in nodes and not (dx and dy and ((x + dx, y) in nodes or (x, y + dy) in nodes)):
                adjacent.append(target)
        graph[x, y] = adjacent
    visited, paths = set(), []
    ordered = [node for node in graph if len(graph[node]) != 2] + [node for node in graph if len(graph[node]) == 2]
    for node in ordered:
        checkpoint(cancel)
        for following in graph[node]:
            edge = tuple(sorted((node, following)))
            if edge in visited:
                continue
            path, previous, current = [node], node, following
            visited.add(edge)
            while True:
                path.append(current)
                if len(graph[current]) != 2 or current == node:
                    break
                target = next(point for point in graph[current] if point != previous)
                edge = tuple(sorted((current, target)))
                if edge in visited:
                    break
                visited.add(edge)
                previous, current = current, target
            path = simplify([(x * sx, y * sy) for x, y in path], options.tolerance, path[0] == path[-1])
            if len(path) > 1 and path_length(path) >= options.min_length:
                paths.append(path)
    return paths


def order_paths(paths, cancel):
    # 有界近邻搜索，避免几万条路径执行全量平方级排序。
    pending, result, current = list(paths), [], (0.0, 0.0)
    while pending:
        checkpoint(cancel)
        choices = [(math.dist(current, path[end]), index, end) for index, path in enumerate(pending[:64]) for end in (0, -1)]
        _, index, end = min(choices)
        path = pending.pop(index)
        if end == -1:
            path = list(reversed(path))
        result.append(path)
        current = path[-1]
    return result


def join_touching_paths(paths, cancel):
    # 仅拼接端点完全重合的路径，绝不为少抬笔而跨越空白。
    endpoints = {}
    for index, path in enumerate(paths):
        for point in (path[0], path[-1]):
            endpoints.setdefault(point, []).append(index)
    unused, result = set(range(len(paths))), []
    starts = sorted(range(len(paths)), key=lambda i: (len(endpoints[paths[i][0]]) % 2 == 0 and len(endpoints[paths[i][-1]]) % 2 == 0, i))
    for index in starts:
        checkpoint(cancel)
        if index not in unused:
            continue
        path = list(paths[index])
        if len(endpoints[path[0]]) % 2 == 0 and len(endpoints[path[-1]]) % 2:
            path.reverse()
        unused.remove(index)
        while True:
            following = next((candidate for candidate in endpoints[path[-1]] if candidate in unused), None)
            if following is None:
                break
            unused.remove(following)
            next_path = paths[following]
            path.extend(next_path[1:] if next_path[0] == path[-1] else reversed(next_path[:-1]))
        result.append(path)
    return result


def emit_program(paths, options, name, cancel):
    lines = ["(XY plotter generated task)", "G21 G90 G94 G17 G54", "G92.1", "G49", f"G53 G1 Z{options.pen_up:.4f} F{options.up_feed:g}"]
    travel = 0.0
    previous = None
    for path in paths:
        checkpoint(cancel)
        first = path[0]
        if previous is not None:
            travel += math.dist(previous, first)
        lines.extend((f"G1 X{first[0] + options.x:.4f} Y{first[1] + options.y:.4f} F{options.travel_feed:g}", f"G53 G1 Z{options.pen_down:.4f} F{options.down_feed:g}"))
        for index, (x, y) in enumerate(path[1:]):
            feed = f" F{options.draw_feed:g}" if index == 0 else ""
            lines.append(f"G1 X{x + options.x:.4f} Y{y + options.y:.4f}{feed}")
        lines.append(f"G53 G1 Z{options.pen_up:.4f} F{options.up_feed:g}")
        previous = path[-1]
        if len(lines) > 195000:
            raise GenerationError("生成结果接近20万行上限，请增大间距/简化误差或分成多个任务。")
    source = "\n".join(lines) + "\n"
    threshold = (options.pen_up + options.pen_down) / 2
    program = parse_program(source, ROOT / "exports" / f"{name}.ngc", pen_threshold=threshold)
    program.preview_pen_threshold = threshold
    return program, travel


def generate_task(image, options, name="生成任务", cancel=None, progress=None):
    options.validate()
    notify = progress or (lambda value, message: None)
    checkpoint(cancel)
    notify(5, "处理黑白图像")
    mask, sx, sy = prepare_mask(image, options)
    checkpoint(cancel)
    paths = []
    if options.mode in ("outline", "outline_fill"):
        notify(20, "提取轮廓")
        paths.extend(contour_paths(mask, sx, sy, options, cancel))
    if options.mode in ("fill", "outline_fill"):
        notify(35, "生成连续填线")
        paths.extend(fill_paths(mask, sx, sy, options, cancel))
    if options.mode == "centerline":
        notify(25, "细化并追踪中线")
        paths.extend(centerline_paths(mask, sx, sy, options, cancel))
    paths = [path for path in paths if len(path) > 1]
    if not paths:
        raise GenerationError("没有可绘制的路径，请降低短线过滤、改变模式或增大图案。")
    points = sum(len(path) for path in paths)
    if points > 180000 or len(paths) > 30000:
        raise GenerationError("路径过多，请增大填线间距/简化误差，或清理图片噪点。")
    notify(70, "优化空行程顺序")
    if options.optimize:
        paths = join_touching_paths(paths, cancel)
        paths = order_paths(paths, cancel)
    notify(85, "生成并检查G代码")
    program, travel = emit_program(paths, options, name, cancel)
    checkpoint(cancel)
    preview = 255 - mask
    image = QImage(preview.data, preview.shape[1], preview.shape[0], preview.strides[0], QImage.Format.Format_Grayscale8).copy()
    z_distance = options.pen_up - options.pen_down
    seconds = 60 * (program.drawing_length / options.draw_feed + travel / options.travel_feed + len(paths) * z_distance * (1 / options.up_feed + 1 / options.down_feed))
    notes = ["耗时仅为匀速理论下限，未计加减速、串口等待和初始进场。", "Z使用G53机器坐标；XY使用G54，未修改EEPROM。"]
    if options.mode == "centerline":
        notes.append("中线是图形骨架，不是手写笔顺；粗体和交叉笔画可能有短分叉。")
    if options.mode in ("fill", "outline_fill") and options.spacing < 2 * max(sx, sy):
        notes.append("间距接近栅格精度，建议缩小图案或增大填线间距。")
    notify(100, "生成完成，尚未发送")
    return GeneratedTask(program, image, len(paths), points, travel, seconds, max(sx, sy), notes)


def save_gcode(program, path):
    # 用户授权仅写上位机目录，导出也统一执行这一约束。
    path = Path(path).resolve()
    if not path.is_relative_to(ROOT.resolve()):
        raise GenerationError("请保存到当前“上位机”文件夹内，例如exports子目录。")
    if path.suffix.lower() not in (".ngc", ".nc", ".gcode", ".tap"):
        raise GenerationError("请使用.ngc、.nc、.gcode或.tap扩展名保存G代码。")
    path.parent.mkdir(parents=True, exist_ok=True)
    source = program.source_text
    if not source:
        raise GenerationError("当前任务没有可导出的源文本。")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(source, encoding="utf-8", newline="\n")
    temporary.replace(path)
    return path
