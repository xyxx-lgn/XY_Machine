"""对真实整页照片逐字审查并输出全部对照图，避免只验证几个例字。"""

import json
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

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QApplication

from app.generation import read_image
from app.appearance import configure_appearance
from app.photo_ocr import recognize_photo
from app.photo_quality import analyze_photo_samples, calibrate_photo_samples
from app.ui.photo_batch_dialog import PhotoBatchCalibrationDialog


def no_network(*args, **kwargs):
    raise AssertionError("整页照片验证期间不允许联网")


def sheet(samples, folder):
    for first in range(0, len(samples), 48):
        image = QImage(1440, 8 * 154, QImage.Format.Format_RGB32)
        image.fill(QColor("white"))
        painter = QPainter(image)
        for local, sample in enumerate(samples[first:first + 48]):
            row, col = divmod(local, 6)
            x, y = col * 240, row * 154
            painter.setPen(QColor("#b82929" if sample.quality_blocking else "#b27400" if sample.quality_flags else "#222222"))
            painter.drawText(x + 6, y + 18, f"{first + local + 1:04d} {sample.character}")
            for k, source in enumerate((sample.original, sample.image)):
                if source is not None:
                    box = QRectF(x + 5 + k * 78, y + 26, 72, 90)
                    scale = min(box.width() / source.width(), box.height() / source.height())
                    target = QRectF(box.center().x() - source.width() * scale / 2, box.center().y() - source.height() * scale / 2, source.width() * scale, source.height() * scale)
                    painter.drawImage(target, source)
            painter.setPen(QPen(QColor("black"), .8))
            for path in sample.strokes:
                for a, b in zip(path, path[1:]):
                    painter.drawLine(QPointF(x + 162 + a[0] * 72, y + 35 + a[1] * 72), QPointF(x + 162 + b[0] * 72, y + 35 + b[1] * 72))
            painter.setPen(QColor("#555555"))
            painter.drawText(x + 5, y + 136, "原字 / 黑白 / 轨迹")
        painter.end()
        image.save(str(folder / f"sheet-{first // 48 + 1:02d}.png"))


if __name__ == "__main__":
    application = QApplication([])
    configure_appearance(application)
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".runtime/photo-quality/source.png"
    output = ROOT / ".runtime/photo-batch-review" / (sys.argv[2] if len(sys.argv) > 2 else "latest")
    output.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    with patch.object(socket.socket, "connect", no_network), patch.object(socket, "create_connection", no_network):
        samples = recognize_photo(read_image(source), trajectory="pen_shape")
    raw = analyze_photo_samples(samples)
    calibrated, report = calibrate_photo_samples(samples)
    assert len(calibrated) == len(samples) > 100
    assert all(0 <= x <= 1 and 0 <= y <= 1 for s in calibrated for path in s.strokes for x, y in path)
    # 不用测试通过宣称每个字正确；报告保留每项问题与覆盖率，供整批逐页审阅。
    records = [dict(index=i + 1, character=s.character, error=s.error, flags=q.flags, focus=q.blocking,
                    coverage=q.coverage, raw_flags=raw.items[i].flags, original_size=[s.original.width(), s.original.height()])
               for i, (s, q) in enumerate(zip(calibrated, report.items))]
    (output / "report.json").write_text(json.dumps(dict(total=len(samples), seconds=round(time.monotonic() - start, 2),
           raw_summary=raw.summary(), calibrated_summary=report.summary(), samples=records), ensure_ascii=False, indent=2), encoding="utf8")
    sheet(calibrated, output)
    dialog = PhotoBatchCalibrationDialog(calibrated)
    dialog.show()
    end = time.monotonic() + 40
    while dialog.worker is not None or not dialog.preview_samples:
        application.processEvents()
        time.sleep(.01)
        assert time.monotonic() < end
    application.processEvents()
    dialog.grab().save(str(output / "batch-dialog.png"))
    dialog.close()
    print(json.dumps(dict(total=len(samples), summary=report.summary(), output=str(output)), ensure_ascii=False))
