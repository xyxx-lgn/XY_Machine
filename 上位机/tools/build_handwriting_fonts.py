"""下载开放手写字体并预制中线字库；日常运行不需要联网或字体转换依赖。"""

import argparse
import base64
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import struct
import sys
import time
from urllib.parse import quote
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["TEMP"] = str(ROOT / ".runtime" / "tmp")
os.environ["TMP"] = os.environ["TEMP"]
CACHE = ROOT / ".runtime" / "font-sources" / "handwriting"
TARGET = ROOT / "assets" / "strokes" / "centerline"
SOURCES = json.loads(Path(__file__).with_name("handwriting_sources.json").read_text(encoding="utf-8"))["fonts"]


def curl(url, destination=None, headers=()):
    command = ["curl.exe", "-fL", "--connect-timeout", "12", "--max-time", "120", "--retry", "1", "--silent", "--show-error"]
    for header in headers:
        command.extend(["-H", header])
    if destination:
        command.extend(["-o", str(destination)])
    result = subprocess.run([*command, url], capture_output=True, timeout=260)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", "replace")[-600:])
    return result.stdout


def fetch_font(source):
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / (source["id"] + ".ttf")
    if path.exists() and path.stat().st_size > 10000:
        return path
    temporary = path.with_suffix(".download")
    errors = []
    urls = [source["url"]] if "url" in source else []
    if "path" in source:
        urls.append(f"https://raw.githubusercontent.com/{source['repo']}/{source['ref']}/{quote(source['path'])}")
    for url in urls:
        try:
            curl(url, temporary)
            if temporary.read_bytes()[:4] not in (b"\x00\x01\x00\x00", b"OTTO"):
                raise ValueError("下载内容不是字体文件")
            temporary.replace(path)
            return path
        except Exception as error:
            errors.append(str(error))
    if source.get("asset"):
        try:
            url = f"https://api.github.com/repos/{source['repo']}/releases/assets/{source['asset']}?download=1"
            curl(url, temporary, ("Accept: application/octet-stream",))
            if temporary.read_bytes()[:4] not in (b"\x00\x01\x00\x00", b"OTTO"):
                raise ValueError("发布附件未返回字体内容")
            temporary.replace(path)
            return path
        except Exception as error:
            errors.append(str(error))
    if source.get("blob"):
        try:
            data = json.loads(curl(f"https://api.github.com/repos/{source['repo']}/git/blobs/{source['blob']}"))
            raw = base64.b64decode(data["content"])
            actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            if actual != source["blob"]:
                raise ValueError("Git字体内容校验失败")
            path.write_bytes(raw)
            return path
        except Exception as error:
            errors.append(str(error))
    raise RuntimeError(source["id"] + ": " + "; ".join(errors))


def fetch_license(source, font):
    folder = TARGET / "handwriting-licenses"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (source["id"] + ".txt")
    if not path.exists():
        # 完整保留字体本身的版权、作者和许可记录；不依赖下载服务的访问配额。
        table = bytes(font.fontTable("name"))
        _, count, start = struct.unpack_from(">HHH", table)
        records = []
        for index in range(count):
            platform, encoding, language, name, length, offset = struct.unpack_from(">6H", table, 6 + index * 12)
            if name not in (0, 7, 8, 9, 10, 13, 14):
                continue
            text = table[start + offset:start + offset + length].decode("utf-16-be" if platform in (0, 3) else "latin-1", errors="replace")
            if text and text not in records:
                records.append(text)
        standard = (TARGET / "Source-Han-Sans-OFL.txt").read_text(encoding="utf-8-sig")
        standard = standard[standard.index("SIL OPEN FONT LICENSE"):]
        attribution = f"源字体：{source['source_font']}\n来源：https://github.com/{source['repo']}\n版本：{source['ref']}\n转换字库：{source['name']}\n以下版权和作者记录摘自原始字体name表，原样保留。\n\n"
        path.write_text(attribution + "\n\n".join(records) + "\n\n" + standard, encoding="utf-8")
    return str(path.relative_to(TARGET)).replace("\\", "/")


def gb2312_hanzi():
    result = set()
    for high in range(0xB0, 0xF8):
        for low in range(0xA1, 0xFF):
            try:
                result.add(bytes((high, low)).decode("gb2312"))
            except UnicodeDecodeError:
                pass
    return result


def join_paths(paths):
    """仅在骨架本来相接的端点接续，优先直行；不跨白区补线，也不重复描边。"""
    ends = {}
    for index, path in enumerate(paths):
        for side in (0, -1):
            ends.setdefault(path[side], []).append((index, side))
    used, result = set(), []
    order = sorted(range(len(paths)), key=lambda i: (min(len(ends[paths[i][0]]), len(ends[paths[i][-1]])), -len(paths[i])))
    for index in order:
        if index in used:
            continue
        used.add(index)
        path = list(paths[index])
        for _ in range(2):
            while True:
                a, joint = path[-2:]
                vx, vy = joint[0] - a[0], joint[1] - a[1]
                choices = []
                for other, side in ends[joint]:
                    if other in used:
                        continue
                    following = paths[other] if side == 0 else paths[other][::-1]
                    dx, dy = following[1][0] - joint[0], following[1][1] - joint[1]
                    score = (vx * dx + vy * dy) / max(1e-9, math.hypot(vx, vy) * math.hypot(dx, dy))
                    if score >= .25:
                        choices.append((score, other, following))
                if not choices:
                    break
                _, other, following = max(choices, key=lambda item: (item[0], -item[1]))
                used.add(other)
                path.extend(following[1:])
            path.reverse()
        result.append(path)
    return result


def glyph_reader(font, source_path):
    """部分旧字体被Windows字体引擎拒绝轮廓时，直接读取原始二次贝塞尔路径。"""
    if not font.pathForGlyph(font.glyphIndexesForString("永")[0]).isEmpty():
        return font.pathForGlyph
    from fontTools.pens.basePen import BasePen
    from fontTools.ttLib import TTFont
    from PySide6.QtGui import QPainterPath, QTransform

    data = TTFont(source_path)
    glyphs = data.getGlyphSet()
    factor = 1024 / data["head"].unitsPerEm

    class PathPen(BasePen):
        def __init__(self):
            super().__init__(glyphs)
            self.path = QPainterPath()

        def _moveTo(self, point):
            self.path.moveTo(*point)

        def _lineTo(self, point):
            self.path.lineTo(*point)

        def _qCurveToOne(self, control, point):
            self.path.quadTo(*control, *point)

        def _curveToOne(self, first, second, point):
            self.path.cubicTo(*first, *second, *point)

        def _closePath(self):
            self.path.closeSubpath()

        def _endPath(self):
            pass

    def read(index):
        pen = PathPen()
        glyphs[data.getGlyphName(index)].draw(pen)
        return QTransform(factor, 0, 0, -factor, 0, 0).map(pen.path)

    return read


def build_font(source, limit=0):
    import cv2
    import numpy as np
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter, QRawFont, QTransform
    from PySide6.QtWidgets import QApplication
    from app.generation import PlotOptions, centerline_paths

    application = QApplication.instance() or QApplication([])
    cv2.setNumThreads(1)
    source_path = fetch_font(source)
    raw = source_path.read_bytes()
    if source.get("sha256") and hashlib.sha256(raw).hexdigest() != source["sha256"]:
        raise ValueError("源字体附件校验不符：" + source["id"])
    if source.get("blob") and hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() != source["blob"]:
        raise ValueError("源字体版本校验不符：" + source["id"])
    font = QRawFont(str(source_path), 1024)
    if not font.isValid():
        raise ValueError("Qt无法读取字体：" + str(source_path))
    license_path = fetch_license(source, font)
    read_glyph = glyph_reader(font, source_path)
    # 收录源字体支持的全部基本区和扩展A汉字，另收录常用西文与标点。
    characters = "".join(chr(code) for code in range(0x3400, 0xA000))
    characters += "".join(chr(code) for code in range(33, 127)) + "".join(chr(code) for code in range(0x3001, 0x3040))
    characters += "".join(chr(code) for code in range(0xFF01, 0xFF60)) + "“”‘’—–…·•￥×÷±℃°"
    characters = "".join(dict.fromkeys(characters))
    indexes = font.glyphIndexesForString(characters)
    supported = [(char, index) for char, index in zip(characters, indexes) if index]
    hanzi = [(char, index) for char, index in supported if 0x3400 <= ord(char) <= 0x9FFF]
    boxes = [read_glyph(index).boundingRect() for char, index in hanzi[::max(1, len(hanzi) // 1000)]]
    top = float(np.percentile([box.top() for box in boxes], 1))
    bottom = float(np.percentile([box.bottom() for box in boxes], 99))
    scale = .87 / max(1024, bottom - top)
    dx, dy = .5 - 512 * scale, .5 - (top + bottom) * scale / 2
    resolution = 256
    options = PlotOptions(tolerance=.55, min_length=0)
    output = ROOT / ".runtime" / "handwriting-review" / "sample-data" if limit else TARGET
    output.mkdir(parents=True, exist_ok=True)
    temporary = output / (source["id"] + ".tmp")
    started = time.monotonic()
    stats = {"schema": 1, "id": source["id"], "name": source["name"], "source_font": source["source_font"], "source_url": "https://github.com/" + source["repo"], "source_revision": source["ref"], "source_sha256": hashlib.sha256(raw).hexdigest(), "license": "SIL OFL 1.1", "license_file": license_path, "ordered": False, "handwriting": True, "glyphs": 0, "paths": 0, "points": 0, "raster_size": resolution, "transform": {"scale": scale, "dx": dx, "dy": dy}, "omitted": []}
    available = set()
    sample = set("你好世界天天学习永田口中国文字测试调要周女回谢林明书写春风Hello123，。！？")
    work = [(char, index) for char, index in supported if not limit or char in sample]
    with ZipFile(temporary, "w", ZIP_DEFLATED, compresslevel=9) as archive:
        for number, (char, index) in enumerate(work):
            path = read_glyph(index)
            if path.isEmpty():
                continue
            transform = QTransform(scale, 0, 0, scale, dx, dy)
            path = transform.map(path)
            bounds = path.boundingRect()
            if bounds.left() < .015 or bounds.right() > .985 or bounds.top() < .015 or bounds.bottom() > .985:
                fit = min(1, .95 / max(bounds.width(), bounds.height()))
                cx, cy = bounds.center().x(), bounds.center().y()
                path = QTransform(fit, 0, 0, fit, .5 - cx * fit, .5 - cy * fit).map(path)
            image = QImage(resolution, resolution, QImage.Format.Format_Grayscale8)
            image.fill(0)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.scale(resolution - 1, resolution - 1)
            painter.fillPath(path, Qt.GlobalColor.white)
            painter.end()
            pixels = np.frombuffer(image.constBits(), np.uint8).reshape(resolution, image.bytesPerLine())[:, :resolution]
            mask = (pixels >= 96).astype(np.uint8) * 255
            ys, xs = np.nonzero(mask)
            if not len(xs):
                stats["omitted"].append(char)
                continue
            x0, y0 = max(0, int(xs.min()) - 2), max(0, int(ys.min()) - 2)
            x1, y1 = min(resolution, int(xs.max()) + 3), min(resolution, int(ys.max()) + 3)
            paths = centerline_paths(mask[y0:y1, x0:x1], 1, 1, options, None, preserve_connections=True)
            # 不把交叉处一两个像素的骨架毛刺当成独立落笔，孤立的标点仍完整保留。
            endpoints = {}
            for stroke in paths:
                for point in (stroke[0], stroke[-1]):
                    endpoints[point] = endpoints.get(point, 0) + 1
            paths = [stroke for stroke in paths if sum(math.dist(a, b) for a, b in zip(stroke, stroke[1:])) > 1.7 or not ((endpoints[stroke[0]] == 1) != (endpoints[stroke[-1]] == 1))]
            paths = join_paths(paths)
            paths = [[(round((x + x0) / (resolution - 1), 6), round((y + y0) / (resolution - 1), 6)) for x, y in stroke] for stroke in paths]
            if not paths:
                stats["omitted"].append(char)
                continue
            info = ZipInfo(f"{ord(char):X}.json", (2026, 11, 12, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, json.dumps(paths, separators=(",", ":")))
            available.add(char)
            stats["paths"] += len(paths)
            stats["points"] += sum(map(len, paths))
            if number and number % 1000 == 0:
                print(f"{source['id']} {number}/{len(work)} {time.monotonic() - started:.1f}s", flush=True)
    destination = output / (source["id"] + ".zip")
    temporary.replace(destination)
    missing = sorted(gb2312_hanzi() - available)
    stats.update(glyphs=len(available), gb2312_hanzi=6763 - len(missing), gb2312_missing="".join(missing), archive_sha256=hashlib.sha256(destination.read_bytes()).hexdigest(), build_seconds=round(time.monotonic() - started, 2))
    destination.with_suffix(".json").write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: stats[key] for key in ("id", "glyphs", "paths", "points", "gb2312_hanzi", "build_seconds")}, ensure_ascii=False), flush=True)
    return stats


def write_catalog():
    fonts = []
    notes = ["开放手写字体中线字库", "", "保留源字体的手写外观，转换为单线轨迹；不保证标准笔顺。", "以下均为SIL Open Font License 1.1字体的衍生中线数据。转换后采用新的XY名称；源字体名仅用于来源说明。", "简体覆盖以GB2312的6763个汉字核对，缺字会明确报错，不自动混入其他字体。", "版权和作者记录及许可正文保存在handwriting-licenses目录；部分补字由各上游作者完成，详见其项目说明。", ""]
    for source in SOURCES:
        path = TARGET / (source["id"] + ".json")
        info = json.loads(path.read_text(encoding="utf-8"))
        archive = TARGET / (source["id"] + ".zip")
        if info["glyphs"] < 1000 or not (TARGET / info["license_file"]).is_file():
            raise ValueError("字库或许可尚未构建完成：" + source["id"])
        if hashlib.sha256(archive.read_bytes()).hexdigest() != info["archive_sha256"]:
            raise ValueError("字库内容校验失败：" + source["id"])
        fonts.append({key: info[key] for key in ("id", "name", "source_font", "glyphs", "gb2312_hanzi")})
        notes.extend([f"{info['name']} ← {info['source_font']}", f"来源：{info['source_url']}；版本：{info['source_revision']}", f"可绘制字符：{info['glyphs']}；GB2312汉字：{info['gb2312_hanzi']}/6763", f"原字体SHA256：{info['source_sha256']}", f"许可及原作者信息：{info['license_file']}", ""])
    (TARGET / "handwriting-fonts.json").write_text(json.dumps({"schema": 1, "fonts": fonts}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (TARGET / "手写字库来源与许可.txt").write_text("\n".join(notes) + "\n", encoding="utf-8")
    print(f"CATALOG {len(fonts)} handwriting fonts", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--font", choices=[source["id"] for source in SOURCES])
    parser.add_argument("--sample", action="store_true")
    parser.add_argument("--jobs", type=int, default=1, choices=range(1, 9))
    parser.add_argument("--catalog", action="store_true")
    args = parser.parse_args()
    (ROOT / ".runtime" / "tmp").mkdir(parents=True, exist_ok=True)
    if args.catalog:
        write_catalog()
        return
    selected = [source for source in SOURCES if not args.font or source["id"] == args.font]
    if args.download:
        failures = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(fetch_font, source): source for source in selected}
            for future in as_completed(futures):
                source = futures[future]
                try:
                    path = future.result()
                    print(f"READY {source['id']} {path.stat().st_size}", flush=True)
                except Exception as error:
                    print(f"FAILED {source['id']}: {error}", flush=True)
                    failures.append(source["id"])
        if failures:
            raise SystemExit("下载未完成：" + ", ".join(failures))
        return
    if args.jobs == 1:
        for source in selected:
            build_font(source, args.sample)
    else:
        # 每个进程各自创建Qt与字形缓存，不在线程间共享字体和画板。
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            futures = [pool.submit(build_font, source, args.sample) for source in selected]
            for future in as_completed(futures):
                future.result()
    if not args.font and not args.sample:
        write_catalog()


if __name__ == "__main__":
    main()
