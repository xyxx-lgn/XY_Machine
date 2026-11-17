"""下载固定版本的开放单线字体，校验原始文件并构建可按字读取的离线资源。"""

import hashlib
import json
import math
from pathlib import Path
import urllib.request
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


ROOT = Path(__file__).resolve().parent.parent
REVISION = "87f5e407a36c175c1ab17f2ba4447768a7fffdab"
SOURCES = (
    ("heiti", "STRK-Heiti.json", "1ff2584003f2ca1a9c733c9778c9b602e4b3147e", "XY单线黑体", "Source Han Sans"),
    ("mingti", "STRK-Mingti.json", "d69970132586473594c421ace44dc74cd976fe17", "XY单线明体", "Hanazono"),
)


def download(relative):
    cache = ROOT / ".runtime" / "font-sources" / Path(relative).name
    if cache.exists():
        return cache.read_bytes()
    errors = []
    for prefix in (f"https://raw.githubusercontent.com/LingDong-/chinese-hershey-font/{REVISION}/", f"https://cdn.jsdelivr.net/gh/LingDong-/chinese-hershey-font@{REVISION}/"):
        try:
            with urllib.request.urlopen(prefix + relative, timeout=30) as response:
                raw = response.read()
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(raw)
            return raw
        except OSError as error:
            errors.append(str(error))
    raise RuntimeError("下载失败：" + relative + "; ".join(errors))


def clean_glyph(paths):
    # 保留原有路径顺序，仅移除相邻重复点与不能绘制的零长度路径。
    result = []
    for path in paths:
        points = []
        for point in path:
            if len(point) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) or not -.2 <= v <= 1.2 for v in point):
                raise ValueError("源字体坐标超出归一化字框。")
            if not points or point != points[-1]:
                points.append(point)
        if len(points) >= 2:
            result.append(points)
    return result


def main():
    target = ROOT / "assets" / "strokes" / "centerline"
    target.mkdir(parents=True, exist_ok=True)
    (target / "MIT-LICENSE.txt").write_bytes(download("LICENSE"))
    for identity, filename, blob, name, original in SOURCES:
        raw = download("dist/json/" + filename)
        if hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() != blob:
            raise ValueError("源字体校验不符：" + filename)
        source = json.loads(raw)
        stats = {"schema": 1, "id": identity, "name": name, "ordered": False, "source_font": original, "source_revision": REVISION, "source_blob": blob, "glyphs": 0, "paths": 0, "points": 0, "omitted_empty": 0}
        points = [point for paths in source.values() for path in paths for point in path]
        low_x, low_y = min(0, min(p[0] for p in points)), min(0, min(p[1] for p in points))
        high_x, high_y = max(1, max(p[0] for p in points)), max(1, max(p[1] for p in points))
        scale = .88 / max(high_x - low_x, high_y - low_y)
        dx, dy = .5 - (low_x + high_x) * scale / 2, .5 - (low_y + high_y) * scale / 2
        stats["uniform_transform"] = {"scale": scale, "dx": dx, "dy": dy}
        temporary = target / (identity + ".tmp")
        with ZipFile(temporary, "w", ZIP_DEFLATED, compresslevel=9) as archive:
            for code, paths in sorted(source.items()):
                character = chr(int(code.removeprefix("U+"), 16))
                paths = clean_glyph(paths)
                if not paths or character.isspace():
                    stats["omitted_empty"] += 1
                    continue
                paths = [[(round(x * scale + dx, 8), round(y * scale + dy, 8)) for x, y in path] for path in paths]
                info = ZipInfo(f"{ord(character):X}.json", (2026, 11, 11, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                archive.writestr(info, json.dumps(paths, separators=(",", ":")))
                stats["glyphs"] += 1
                stats["paths"] += len(paths)
                stats["points"] += sum(len(path) for path in paths)
        temporary.replace(target / (identity + ".zip"))
        stats["archive_sha256"] = hashlib.sha256((target / (identity + ".zip")).read_bytes()).hexdigest()
        (target / (identity + ".json")).write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(stats, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
