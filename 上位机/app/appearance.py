import os
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette


def configure_appearance(application):
    # 显式读取系统字体，保证离屏截图和真实窗口都能正确显示中文。
    font_folder = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for name in ("msyh.ttc", "msyhbd.ttc", "consola.ttf"):
        path = font_folder / name
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    application.setStyle("Fusion")
    application.setFont(QFont("Microsoft YaHei UI", 10))
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#eef3f9"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#25344c"))
    palette.setColor(QPalette.ColorRole.Base, QColor("white"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#25344c"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#e8effb"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#25344c"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#2563eb"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("white"))
    application.setPalette(palette)
