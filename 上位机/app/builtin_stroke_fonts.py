"""额外中文中线字库；保留单字路径，但不将自动提取顺序称为标准笔顺。"""

from functools import lru_cache
import json
import math
from zipfile import BadZipFile, ZipFile

from app.generation import GenerationError
from app.profile import ROOT


DATA = ROOT / "assets" / "strokes" / "centerline"
BUILTIN_FONTS = {"builtin:heiti": "XY单线黑体", "builtin:mingti": "XY单线明体"}
CATALOG = DATA / "handwriting-fonts.json"
if CATALOG.exists():
    for entry in json.loads(CATALOG.read_text(encoding="utf-8"))["fonts"]:
        BUILTIN_FONTS["builtin:" + entry["id"]] = entry["name"]


@lru_cache(maxsize=6)
def font_archive(identity):
    if identity not in BUILTIN_FONTS:
        raise GenerationError("未识别的内置中线字库。")
    return ZipFile(DATA / (identity.split(":")[1] + ".zip"))


@lru_cache(maxsize=64)
def font_info(identity):
    if identity not in BUILTIN_FONTS:
        raise GenerationError("未识别的内置字库。")
    try:
        return json.loads((DATA / (identity.split(":")[1] + ".json")).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise GenerationError("内置字库说明读取失败：" + str(error)) from None


@lru_cache(maxsize=2048)
def builtin_paths(identity, character):
    try:
        archive = font_archive(identity)
        name = f"{ord(character):X}.json"
        if name not in archive.NameToInfo:
            return None
        paths = json.loads(archive.read(name))
        if not isinstance(paths, list) or not paths or any(len(path) < 2 for path in paths):
            raise ValueError("无效的笔迹路径")
        result = tuple(tuple((float(x), float(y)) for x, y in path) for path in paths)
        if any(not math.isfinite(v) or not 0 <= v <= 1 for path in result for point in path for v in point):
            raise ValueError("笔迹坐标超出字框")
        return result
    except (OSError, ValueError, TypeError, KeyError, BadZipFile) as error:
        raise GenerationError("内置中线字库读取失败，请保留assets/strokes/centerline目录：" + str(error)) from None
