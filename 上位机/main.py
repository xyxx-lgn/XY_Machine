import os
import sys
from pathlib import Path

# 应用产生的缓存和临时文件全部留在上位机目录内。
ROOT = Path(__file__).resolve().parent
TEMP = ROOT / ".runtime" / "tmp"
TEMP.mkdir(parents=True, exist_ok=True)
os.environ["TEMP"] = str(TEMP)
os.environ["TMP"] = str(TEMP)
os.environ["XDG_CACHE_HOME"] = str(ROOT / ".runtime" / "cache")
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from PySide6.QtWidgets import QApplication

from app.appearance import configure_appearance
from app.ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("XY写字机上位机")
    configure_appearance(app)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
