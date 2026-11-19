"""对给定完整手稿逐项检查，输出所有样本对照和跨页收录结果。"""

import json
import argparse
import os
from pathlib import Path
import socket
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["TEMP"] = str(ROOT / ".runtime/tmp")
os.environ["TMP"] = os.environ["TEMP"]

from PySide6.QtWidgets import QApplication
from app.appearance import configure_appearance
from app.generation import read_image
from app.photo_ocr import recognize_photo
from app.photo_quality import calibrate_photo_samples
from app.photo_samples import build_photo_package, export_photo_samples, load_photo_samples
from app.photo_selection import choose_photo_variants
from tests.photo_batch_review import sheet


def no_network(*args, **kwargs):
    raise AssertionError("整页测试期间不允许联网")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("images", nargs="+")
    parser.add_argument("--output", default="photo-import-review")
    args = parser.parse_args()
    if Path(args.output).name != args.output:
        raise ValueError("测试输出名称不可包含目录。")
    application = QApplication([])
    configure_appearance(application)
    folder = ROOT / ".runtime" / args.output
    folder.mkdir(parents=True, exist_ok=True)
    collected, pages = [], []
    for index, path in enumerate(args.images, 1):
        output = folder / f"page-{index}"
        output.mkdir(parents=True, exist_ok=True)
        image = read_image(path)
        start = time.monotonic()
        with patch.object(socket.socket, "connect", no_network), patch.object(socket, "create_connection", no_network), patch.object(socket, "getaddrinfo", no_network):
            samples = recognize_photo(image, trajectory="pen_shape")
        samples, report = calibrate_photo_samples(samples)
        assert len(samples) > 100
        assert all(0 <= x <= 1 and 0 <= y <= 1 for s in samples for line in s.strokes for x, y in line)
        assert all(0 <= x < image.width() and 0 <= y < image.height() for s in samples for x, y in s.box)
        eligible = [s for s in samples if not s.error and not s.quality_blocking]
        selection = choose_photo_variants(eligible, collected)
        picked = [eligible[i] for i in selection.indices]
        collected.extend(picked)
        package = build_photo_package(collected)
        assert max(len(g["variants"]) for g in package.glyphs.values()) <= 5
        record = dict(page=index, input=str(path), total=len(samples), eligible=len(eligible), added=len(picked),
                      errors=sum(bool(s.error) for s in samples), seconds=round(time.monotonic() - start, 2),
                      selection=selection.summary(), review=report.summary(),
                      samples=[dict(index=i, character=s.character, confidence=s.confidence, suggestion=s.label_suggestion,
                                    note=s.note, flags=s.quality_flags, blocking=s.quality_blocking, coverage=s.quality_coverage,
                                    strokes=len(s.strokes), box=s.box) for i, s in enumerate(samples)])
        (output / "report.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf8")
        sheet(samples, output)
        pages.append({key: value for key, value in record.items() if key != "samples"})
        print(json.dumps(pages[-1], ensure_ascii=False), flush=True)
    if collected:
        # 导出/重载每个实际选中的字，验证字库格式和轨迹没有变化。
        destination = folder / f"roundtrip-{time.time_ns()}"
        export_photo_samples(collected, destination)
        restored = load_photo_samples(destination)
        assert [s.character for s in restored] == [s.character for s in collected]
        assert [s.strokes for s in restored] == [s.strokes for s in collected]
    summary = dict(pages=pages, reviewed=sum(p["total"] for p in pages), collected=len(collected), unique=len(package.glyphs),
                   note="全页程序检查及对照图不代表人工确认每字OCR正确；候选仍需核对。")
    (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)
