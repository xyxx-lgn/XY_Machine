import os
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
from app.profile import MachineProfile
from app.transport import SIMULATOR
from app.ui.main_window import MainWindow
from app.ui.settings_dialog import SettingsDialog


def wait_until(application, condition, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("等待界面状态超时")


application = QApplication([])
configure_appearance(application)
window = MainWindow()
# 测试采用内存配置，不改变用户保存的参数。
window.profile = MachineProfile()
window.controller.profile = window.profile
window.save_profile = lambda: None
window.show()
application.processEvents()
assert not window.pen_up_button.isEnabled()
window.port_combo.setCurrentIndex(window.port_combo.findData(SIMULATOR))
window.toggle_connection()
wait_until(application, lambda: window.controller.available)
assert window.controller.simulated
assert not window.pen_up_button.isEnabled()
window.baseline_check.setChecked(True)
assert window.pen_up_button.isEnabled()
window.pen_up_button.click()
wait_until(application, lambda: window.controller.available and window.controller.machine[2] == 2)
window.controller.jog("X", 1)
wait_until(application, lambda: window.controller.available and window.controller.work[0] == 11)
window.controller.send("$$", "read")
wait_until(application, lambda: window.controller.available and 110 in window.controller.settings)
folder = ROOT / ".runtime" / "screenshots"
folder.mkdir(parents=True, exist_ok=True)
window.grab().save(str(folder / "stage1-main.png"))
dialog = SettingsDialog(window.profile, window)
dialog.show()
application.processEvents()
dialog.grab().save(str(folder / "stage1-settings.png"))
dialog.reject()
window.controller.disconnect_device()
assert not window.pen_up_button.isEnabled()
window.close()
print("GUI smoke: PASS (simulator only, no hardware serial opened)")
