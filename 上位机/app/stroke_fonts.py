"""单线字库包与自然书写参数；字库数据使用归一化字框坐标。"""

import hashlib
import json
import math
from pathlib import Path
import random
import re
import uuid
from dataclasses import dataclass, field
from functools import lru_cache

from app.generation import GenerationError
from app.builtin_stroke_fonts import BUILTIN_FONTS
from app.profile import ROOT


FONT_ROOT = ROOT / "config" / "stroke_fonts"
LATIN_STYLES = (("futural", "简洁单线"), ("scripts", "手写单线"), ("scriptc", "花体字形"), ("timesr", "衬线字形"))


@dataclass(frozen=True)
class NaturalOptions:
    enabled: bool = False
    strength: float = 0.5
    seed: int = 1
    spacing_strength: float | None = None
    baseline_strength: float | None = None

    @property
    def spacing(self):
        return self.strength if self.spacing_strength is None else self.spacing_strength

    @property
    def baseline(self):
        return self.strength if self.baseline_strength is None else self.baseline_strength

    def validate(self):
        if type(self.enabled) is not bool or type(self.seed) is not int or not 1 <= self.seed <= 2147483647:
            raise GenerationError("自然书写开关或样式编号无效。")
        for value in (self.strength, self.spacing, self.baseline):
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise GenerationError("字形、字距和行内起伏强度应为0～1。")


@dataclass
class FontPackage:
    name: str
    glyphs: dict = field(default_factory=dict)
    id: str = ""
    source: str = "用户录制"
    record_plan: tuple = field(default_factory=tuple)
    ordered: bool = True


@dataclass
class WritingOptions:
    package: FontPackage | None = None
    latin_style: str = "futural"
    fallback: bool = True
    natural: NaturalOptions = field(default_factory=NaturalOptions)
    builtin_font: str = ""

    def validate(self):
        self.natural.validate()
        if self.latin_style not in dict(LATIN_STYLES) or type(self.fallback) is not bool:
            raise GenerationError("笔画字体参数无效。")
        if self.builtin_font and self.builtin_font not in BUILTIN_FONTS:
            raise GenerationError("内置中线字体无效。")
        if self.package is not None and self.builtin_font:
            raise GenerationError("个人字库与内置中线字体不能同时选择。")


def seeded_random(seed, *parts):
    # 不使用Python进程相关的hash，保证重新启动后同一样式编号仍生成同一份轨迹。
    key = json.dumps([seed, *parts], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return random.Random(int.from_bytes(hashlib.blake2b(key, digest_size=16).digest(), "big"))


@lru_cache(maxsize=1024)
def contact_vertices(strokes):
    # 把已落在另一笔线段上的端点也加入该线段，形变后相接处仍共用同一个点。
    ends = set(point for path in strokes for point in (path[0], path[-1]))
    result = []
    for path in strokes:
        points = [path[0]]
        for a, b in zip(path, path[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = dx * dx + dy * dy
            cuts = []
            if length:
                for x, y in ends:
                    t = ((x - a[0]) * dx + (y - a[1]) * dy) / length
                    if 1e-9 < t < 1 - 1e-9 and math.hypot(x - a[0] - t * dx, y - a[1] - t * dy) < 1e-9:
                        cuts.append((t, (x, y)))
            points.extend(point for t, point in sorted(cuts))
            points.append(b)
        result.append(tuple(points))
    return tuple(result)


def natural_glyph(strokes, advance, natural, occurrence):
    """全字共用平滑形变场，让内部比例和笔画曲度有变化，接点共同移动。

    长直段适量插值后再形变，保留原始顶点、笔序和方向。形变不增加分笔，
    不逐点加噪声；字距和行内起伏在排版层单独控制。
    """
    if not natural.enabled or natural.strength == 0 or not strokes:
        return strokes, advance
    rng = seeded_random(natural.seed, "glyph", occurrence)
    strength = natural.strength
    sx, sy = 1 + rng.uniform(-.08, .08) * strength, 1 + rng.uniform(-.065, .065) * strength
    angle = math.radians(rng.uniform(-1.8, 1.8) * strength)
    shear = rng.uniform(-.05, .05) * strength
    cosine, sine = math.cos(angle), math.sin(angle)
    coefficients = [rng.uniform(-1, 1) * strength for _ in range(6)]
    strokes = contact_vertices(tuple(tuple(map(tuple, path)) for path in strokes))

    def transform(x, y):
        u = x / advance
        dx = .035 * (coefficients[0] * math.sin(math.pi * y) + coefficients[1] * math.sin(math.tau * y) + coefficients[2] * math.sin(math.pi * u) * math.sin(math.pi * y))
        dy = .035 * (coefficients[3] * math.sin(math.pi * u) + coefficients[4] * math.sin(math.tau * u) + coefficients[5] * math.sin(math.pi * u) * math.sin(math.pi * y))
        x, y = (x + dx - advance / 2) * sx + (y + dy - .5) * shear, (y + dy - .5) * sy
        return x * cosine - y * sine + advance / 2, x * sine + y * cosine + .5

    result = []
    for path in strokes:
        transformed = [transform(*path[0])]
        for a, b in zip(path, path[1:]):
            count = max(1, math.ceil(math.dist(a, b) / .1))
            transformed.extend(transform(a[0] + (b[0] - a[0]) * i / count, a[1] + (b[1] - a[1]) * i / count) for i in range(1, count))
            transformed.append(transform(*b))
        result.append(transformed)
    points = [p for path in result for p in path]
    # 收纳到字框内，避免旋转后的标点顶端伸出页面；同一缩放/平移仍保留连接。
    low_y, high_y = min(p[1] for p in points), max(p[1] for p in points)
    fit = min(1, 1 / max(1e-9, high_y - low_y))
    dy = max(0, -low_y * fit) + min(0, 1 - high_y * fit)
    min_x, max_x = min(p[0] for p in points) * fit, max(p[0] for p in points) * fit
    dx = max(0, -min_x)
    width = max(advance, max_x + dx + .015)
    return tuple(tuple((x * fit + dx, y * fit + dy) for x, y in path) for path in result), width


def validate_package(data):
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise GenerationError("字库格式无效：需要schema=1的单线笔画字库JSON。")
    name, glyphs = data.get("name"), data.get("glyphs")
    if not isinstance(name, str) or not name.strip() or len(name) > 60 or not isinstance(glyphs, dict) or not 1 <= len(glyphs) <= 20000:
        raise GenerationError("请填写字库名称；字库应包含1～20000种字符。")
    result, total = {}, 0
    for char, glyph in glyphs.items():
        if not isinstance(char, str) or len(char) != 1 or ord(char) < 33 or char.isspace() or not isinstance(glyph, dict):
            raise GenerationError("每个字库条目必须是一个可绘制的Unicode字符。")
        advance, variants = glyph.get("advance", 1.0), glyph.get("variants")
        if type(advance) not in (int, float) or not math.isfinite(advance) or not .2 <= advance <= 2:
            raise GenerationError(f"字符{char}的字框宽度应为0.2～2。")
        if not isinstance(variants, list) or not 1 <= len(variants) <= 16:
            raise GenerationError(f"字符{char}需要1～16个笔画版本。")
        versions = []
        for variant in variants:
            paths = variant.get("strokes") if isinstance(variant, dict) else None
            if not isinstance(paths, list) or not 1 <= len(paths) <= 128:
                raise GenerationError(f"字符{char}的版本需要1～128条笔画。")
            strokes = []
            for path in paths:
                if not isinstance(path, list) or not 2 <= len(path) <= 4000:
                    raise GenerationError(f"字符{char}的笔画需要2～4000个点。")
                points = []
                for point in path:
                    if not isinstance(point, (list, tuple)) or len(point) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) for v in point):
                        raise GenerationError(f"字符{char}包含无效坐标。")
                    x, y = point
                    if not 0 <= x <= advance or not 0 <= y <= 1:
                        raise GenerationError(f"字符{char}的笔画超出字框。")
                    if not points or tuple(point) != points[-1]:
                        points.append(tuple(point))
                if len(points) < 2:
                    raise GenerationError(f"字符{char}含零长度笔画，请重新录制。")
                strokes.append(tuple(points))
                total += len(points)
                if total > 2_000_000:
                    raise GenerationError("字库超过200万个轨迹点，请分为多个字库。")
            versions.append({"strokes": tuple(strokes)})
        result[char] = {"advance": float(advance), "variants": versions}
    source = data.get("source", "用户导入")
    record_plan = data.get("record_plan", [])
    if not isinstance(record_plan, list) or len(record_plan) > 20000 or any(not isinstance(char, str) or len(char) != 1 or char.isspace() for char in record_plan):
        raise GenerationError("字库录字清单无效，最多支持20000个非空白字符。")
    ordered = data.get("ordered", True)
    if type(ordered) is not bool:
        raise GenerationError("字库笔顺标记应为布尔值。")
    return FontPackage(name.strip(), result, source=str(source)[:1000], record_plan=tuple(record_plan), ordered=ordered)


def package_data(package):
    data = {"schema": 1, "name": package.name, "source": package.source, "glyphs": package.glyphs}
    if package.record_plan:
        data["record_plan"] = list(package.record_plan)
    if not package.ordered:
        data["ordered"] = False
    return data


def write_package(package, path):
    # 先验证再原子替换，损坏字库不会覆盖现有文件。
    validated = validate_package(json.loads(json.dumps(package_data(package), ensure_ascii=False)))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(package_data(validated), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)
    return path


class FontStore:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else FONT_ROOT

    def read(self, path):
        path = Path(path)
        if path.stat().st_size > 64_000_000:
            raise GenerationError("字库文件超过64MB。")
        try:
            return validate_package(json.loads(path.read_text(encoding="utf-8-sig")))
        except (ValueError, TypeError, AttributeError) as error:
            raise GenerationError("字库读取失败：" + str(error)) from None

    def catalog(self):
        result = []
        for path in sorted(self.root.glob("user_*.json")):
            if not re.fullmatch(r"user_[0-9a-f]{32}", path.stem):
                continue
            try:
                package = self.read(path)
                package.id = path.stem
                result.append(package)
            except (OSError, GenerationError):
                continue
        return result

    def load(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r"user_[0-9a-f]{32}", identity):
            raise GenerationError("自定义字库标识无效。")
        package = self.read(self.root / (identity + ".json"))
        package.id = identity
        return package

    def save(self, package):
        identity = package.id or "user_" + uuid.uuid4().hex
        if not re.fullmatch(r"user_[0-9a-f]{32}", identity):
            raise GenerationError("自定义字库标识无效。")
        write_package(package, self.root / (identity + ".json"))
        package.id = identity
        return identity

    def import_file(self, path):
        package = self.read(path)
        self.save(package)
        return package

    def delete(self, identity):
        """只删除由本地字库管理器创建的个人字库文件。"""
        if not isinstance(identity, str) or not re.fullmatch(r"user_[0-9a-f]{32}", identity):
            raise GenerationError("自定义字库标识无效，不能删除。")
        path = self.root / (identity + ".json")
        try:
            path.unlink()
        except FileNotFoundError:
            raise GenerationError("选中的自定义字库已经不存在，请刷新字库列表。") from None
        return identity
