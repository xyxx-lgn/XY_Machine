import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.grbl import normalize_command


@dataclass(frozen=True)
class ProgramCommand:
    source_line: int
    text: str


@dataclass(frozen=True)
class Segment:
    points: tuple
    rapid: bool
    drawing: bool


@dataclass
class Program:
    path: Path
    source_text: str = ""
    commands: list = field(default_factory=list)
    segments: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    bounds: tuple | None = None
    z_bounds: tuple | None = None
    drawing_length: float = 0.0
    lift_count: int = 0
    # 内部生成任务可保留本次抬落笔分界，开始前重解析时不改用手动按钮参数。
    preview_pen_threshold: float | None = None


class GcodeError(ValueError):
    pass


def clean_line(text):
    # 注释可以含中文；实际发送部分必须为单行ASCII，%文件边界不发给GRBL。
    result = []
    depth = 0
    for character in text:
        if character == ";" and not depth:
            break
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth < 0:
                raise ValueError("注释括号不匹配。")
        elif not depth:
            result.append(character)
    if depth:
        raise ValueError("注释缺少右括号。")
    text = "".join(result).strip().upper()
    if not text or text == "%":
        return ""
    return normalize_command(text)


def arc_points(start, end, words, clockwise):
    """按GRBL的XY平面、增量I/J定义圆弧；采样用于显示，发送仍保留原始G2/G3。"""
    x0, y0 = start
    x1, y1 = end
    dx, dy = x1 - x0, y1 - y0
    chord = math.hypot(dx, dy)
    if "R" in words:
        if "I" in words or "J" in words:
            raise ValueError("圆弧不能同时使用R和I/J。")
        radius = abs(words["R"])
        if chord < 1e-10 or radius < chord / 2 - 1e-7:
            raise ValueError("R圆弧的半径或端点无效。")
        height = math.sqrt(max(0.0, (radius - chord / 2) * (radius + chord / 2)))
        side = -1 if clockwise else 1
        if words["R"] < 0:
            side = -side
        cx = (x0 + x1) / 2 - side * dy / chord * height
        cy = (y0 + y1) / 2 + side * dx / chord * height
    else:
        if "I" not in words and "J" not in words:
            raise ValueError("圆弧缺少I/J或R。")
        cx, cy = x0 + words.get("I", 0), y0 + words.get("J", 0)
        radius = math.hypot(x0 - cx, y0 - cy)
        radius_end = math.hypot(x1 - cx, y1 - cy)
        difference = abs(radius - radius_end)
        if radius < 1e-9 or difference > 0.005 and (difference > 0.5 or difference > 0.001 * radius):
            raise ValueError("圆弧起点和终点半径不一致。")
    angle = math.atan2(y0 - cy, x0 - cx)
    end_angle = math.atan2(y1 - cy, x1 - cx)
    sweep = (end_angle - angle) % math.tau
    if clockwise:
        sweep = sweep - math.tau if sweep > 1e-12 else -math.tau
    elif sweep < 1e-12:
        sweep = math.tau
    # 同时加入四个极值点，范围检查不依赖采样点恰好落在极值上。
    tolerance = 0.02
    max_angle = min(math.pi / 12, 2 * math.acos(max(-1, 1 - tolerance / radius)))
    count = max(1, math.ceil(abs(sweep) / max(max_angle, 1e-6)))
    if count > 20000:
        raise ValueError("单个圆弧过大，超过本版预览范围。")
    fractions = {index / count for index in range(count + 1)}
    for cardinal in (0, math.pi / 2, math.pi, 3 * math.pi / 2):
        delta = -((angle - cardinal) % math.tau) if clockwise else (cardinal - angle) % math.tau
        fraction = delta / sweep
        if 0 < fraction < 1:
            fractions.add(fraction)
    points = [(cx + radius * math.cos(angle + sweep * t), cy + radius * math.sin(angle + sweep * t)) for t in sorted(fractions)]
    points[0], points[-1] = start, end
    return tuple(points), abs(sweep) * radius


def load_program(path, initial_work=None, g54_offset=(0.0, 0.0, 0.0), pen_threshold=0.0):
    path = Path(path)
    if path.stat().st_size > 20_000_000:
        raise GcodeError("文件超过20MB，请先拆分任务。")
    data = path.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("gb18030")
    return parse_program(text, path, initial_work, g54_offset, pen_threshold)


def parse_program(text, path=Path("未命名.nc"), initial_work=None, g54_offset=(0.0, 0.0, 0.0), pen_threshold=0.0):
    program = Program(Path(path), text)
    position = list(initial_work or (0.0, 0.0, 0.0))
    offset = list(g54_offset)
    known_xy = initial_work is not None
    motion, unit, absolute, feed = 0, 1.0, True, 0.0
    initialized = set()
    ended = False
    bounds = []
    z_values = []
    allowed_g = {0, 1, 2, 3, 4, 10, 17, 20, 21, 49, 53, 54, 90, 91, 91.1, 92.1, 94}
    for line_number, original in enumerate(text.splitlines(), 1):
        try:
            command = clean_line(original)
            if not command:
                continue
            if ended:
                raise ValueError("M2/M30之后仍有命令，不允许静默忽略。")
            compact = re.sub(r"\s+", "", command)
            tokens = list(re.finditer(r"([A-Z])([+-]?(?:\d+(?:\.\d*)?|\.\d+))", compact))
            if "".join(token.group() for token in tokens) != compact:
                raise ValueError("包含无法识别的命令或数值。")
            words = {}
            gcodes, mcodes = [], []
            for token in tokens:
                key, value = token[1], float(token[2])
                if not math.isfinite(value) or abs(value) > 1e8:
                    raise ValueError("数值无效或过大。")
                if key == "G":
                    gcodes.append(value)
                elif key == "M":
                    mcodes.append(value)
                elif key in words:
                    raise ValueError(f"{key}重复出现。")
                elif key not in "XYZIJRFSPNLT":
                    raise ValueError(f"暂不支持{key}字。")
                else:
                    words[key] = value
            if any(code not in allowed_g for code in gcodes):
                raise ValueError("本版只支持G54、XY平面和常见直线/圆弧；不支持此行G代码。")
            groups = ({0, 1, 2, 3}, {20, 21}, {90, 91}, {4, 10, 53, 92.1})
            if any(sum(code in group for code in gcodes) > 1 for group in groups):
                raise ValueError("同一模态组有冲突。")
            if any(code not in (2, 3, 4, 5, 7, 8, 9, 30) for code in mcodes):
                raise ValueError("此M代码不在本版文件发送支持范围内。")
            if sum(code in (2, 30) for code in mcodes) > 1 or sum(code in (3, 4, 5) for code in mcodes) > 1 or sum(code in (7, 8, 9) for code in mcodes) > 1:
                raise ValueError("同一M代码组有冲突。")
            if any(key in words and (words[key] < 0 or not words[key].is_integer()) for key in "NT"):
                raise ValueError("N/T需要非负整数。")
            if words.get("S", 0) < 0:
                raise ValueError("S不能为负数。")
            if any(key in words for key in "LP") and 10 not in gcodes and not (4 in gcodes and "L" not in words):
                raise ValueError("此行的L/P参数没有对应的G10/G4。")
            if any(code in (3, 4, 7, 8) for code in mcodes):
                program.warnings.append("文件含主轴/冷却输出命令，请核实预留输出用途。")
            if 20 in gcodes or 21 in gcodes:
                unit = 25.4 if 20 in gcodes else 1.0
                initialized.add("unit")
            if 90 in gcodes or 91 in gcodes:
                absolute = 90 in gcodes
                initialized.add("distance")
            if 54 in gcodes:
                initialized.add("workspace")
            if 94 in gcodes:
                initialized.add("feedmode")
            if 17 in gcodes:
                initialized.add("plane")
            if "F" in words:
                feed = words["F"] * unit
                if feed <= 0:
                    raise ValueError("进给F必须大于0。")
            if any(code in (0, 1, 2, 3) for code in gcodes):
                motion = next(int(code) for code in gcodes if code in (0, 1, 2, 3))
            if 10 in gcodes:
                if "unit" not in initialized:
                    raise ValueError("G10之前需要明确单位G20/G21。")
                if words.get("L") != 2 or words.get("P") != 1 or "Z" not in words or any(axis in words for axis in "XY"):
                    raise ValueError("文件内仅支持G10 L2 P1 Z…；XY纸张原点请通过专用按钮设置。")
                for index, axis in enumerate("XYZ"):
                    if axis in words:
                        new_offset = words[axis] * unit
                        position[index] += offset[index] - new_offset
                        offset[index] = new_offset
                program.warnings.append("文件含G10 L2 P1，会写入设备G54偏移；仅出现的轴会被修改。")
            elif 4 in gcodes:
                if words.get("P", -1) < 0:
                    raise ValueError("G4需要非负P秒数。")
                if any(axis in words for axis in "XYZIJR"):
                    raise ValueError("本版不在G4同一行混用坐标运动。")
            elif any(axis in words for axis in "XYZ") or motion in (2, 3) and any(axis in words for axis in "IJR"):
                required = {"unit", "distance", "workspace", "feedmode"}
                if not required <= initialized:
                    raise ValueError("首次运动前需在文件中明确G20/G21、G90/G91、G54、G94，避免依赖上一任务模态。")
                if motion and feed <= 0:
                    raise ValueError("G1/G2/G3首次运动前缺少正数F进给。")
                if 53 in gcodes and (not absolute or motion not in (0, 1)):
                    raise ValueError("本版G53需要G90和G0/G1。")
                if motion not in (2, 3) and any(key in words for key in "IJR"):
                    raise ValueError("I/J/R只能用于G2/G3圆弧。")
                target = position.copy()
                for index, axis in enumerate("XYZ"):
                    if axis in words:
                        value = words[axis] * unit
                        target[index] = value - offset[index] if 53 in gcodes else value if absolute else position[index] + value
                xy_move = any(axis in words for axis in "XYIJR")
                if motion in (2, 3):
                    if "plane" not in initialized or not known_xy:
                        raise ValueError("圆弧前需明确G17，并建立XY起点。")
                    arc_words = {key: value * unit for key, value in words.items() if key in "IJR"}
                    points, length = arc_points(tuple(position[:2]), tuple(target[:2]), arc_words, motion == 2)
                else:
                    points = (tuple(position[:2]), tuple(target[:2]))
                    length = math.dist(position[:2], target[:2])
                if xy_move:
                    bounds.extend(points if known_xy else (tuple(target[:2]),))
                    if known_xy and length > 1e-9:
                        drawing = motion != 0 and max(position[2], target[2]) + offset[2] <= pen_threshold
                        program.segments.append(Segment(points, motion == 0, drawing))
                        if drawing:
                            program.drawing_length += length
                    known_xy = True
                if "Z" in words:
                    z_values.append(target[2] + offset[2])
                    if target[2] > position[2] + 1e-6:
                        program.lift_count += 1
                position = target
            if any(code in (2, 30) for code in mcodes):
                ended = True
            program.commands.append(ProgramCommand(line_number, command))
            if len(program.commands) > 200000:
                raise ValueError("可发送命令超过20万行，请拆分任务。")
        except ValueError as error:
            raise GcodeError(f"第{line_number}行：{error}\n{original[:140]}") from None
    if not program.commands:
        raise GcodeError("文件没有可发送的G代码。")
    if bounds:
        program.bounds = (min(p[0] for p in bounds), min(p[1] for p in bounds), max(p[0] for p in bounds), max(p[1] for p in bounds))
    if z_values:
        program.z_bounds = (min(z_values), max(z_values))
    program.warnings = list(dict.fromkeys(program.warnings))
    return program
