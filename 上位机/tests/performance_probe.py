import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["TEMP"] = str(ROOT / ".runtime" / "tmp")
os.environ["TMP"] = os.environ["TEMP"]

from PySide6.QtWidgets import QApplication

from app.appearance import configure_appearance
from app.gcode import load_program
from app.ui.preview_widget import PreviewWidget


application = QApplication([])
configure_appearance(application)
program = load_program(ROOT.parent / "G代码生成软件" / "生成G代码" / "output_0006.ngc")
preview = PreviewWidget()
preview.resize(1450, 650)
preview.set_program(program)
preview.show_travel = True
initial_at = time.perf_counter()
preview.show()
application.processEvents()
initial_ms = (time.perf_counter() - initial_at) * 1000
samples = []
for index in range(5):
    start = time.perf_counter()
    preview.set_position((40 + index / 10, 250, -0.4))
    preview.repaint()
    samples.append((time.perf_counter() - start) * 1000)
scene_ms = []
for index in range(3):
    start = time.perf_counter()
    preview.change_zoom(1.1)
    preview.interaction_timer.stop()
    preview.repaint()
    scene_ms.append((time.perf_counter() - start) * 1000)
result = {"commands": len(program.commands), "marker_repaint_ms": samples, "median_ms": statistics.median(samples), "max_ms": max(samples), "first_scene_ms": initial_ms, "zoom_scene_ms": scene_ms}
label = sys.argv[1] if len(sys.argv) > 1 else "latest"
folder = ROOT / ".runtime" / "performance"
folder.mkdir(parents=True, exist_ok=True)
(folder / f"{label}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result))
preview.close()
