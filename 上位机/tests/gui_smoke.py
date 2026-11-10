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
wait_until(application, lambda: window.pen_up_button.isEnabled())
window.pen_up_button.click()
wait_until(application, lambda: window.controller.available and window.controller.machine[2] == 2)
window.controller.jog("X", 1)
wait_until(application, lambda: window.controller.available and window.controller.work[0] == 11)
window.controller.send("$$", "read")
wait_until(application, lambda: window.controller.available and 110 in window.controller.settings)
window.confirm = lambda title, message: True
window.import_file(path=ROOT / "examples" / "阶段二小方框.nc")
wait_until(application, lambda: not window.loading and window.job.program is not None)
assert window.preview.drawing_path.elementCount() > 0
window.start_job()
wait_until(application, lambda: window.job.active and window.controller.state == "Run", 4)
assert not window.pen_up_button.isEnabled()
assert not window.import_button.isEnabled()
window.controller.pause()
wait_until(application, lambda: window.controller.state == "Hold:0")
assert window.job.paused
wait_until(application, lambda: window.resume_button.isEnabled())
window.controller.resume()
wait_until(application, lambda: window.job.result == "complete", 8)
wait_until(application, lambda: window.controller.available)
assert not window.job.active
assert window.execution_progress.value() == 100
assert tuple(window.controller.transport.offset[:2]) == (-190, -280)
folder = ROOT / ".runtime" / "screenshots"
folder.mkdir(parents=True, exist_ok=True)
window.grab().save(str(folder / "stage2-main.png"))
window.preview.fit_job()
application.processEvents()
window.grab().save(str(folder / "stage2-detail.png"))
dialog = SettingsDialog(window.profile, window)
dialog.show()
application.processEvents()
dialog.grab().save(str(folder / "stage2-settings.png"))
dialog.reject()
window.start_job()
wait_until(application, lambda: window.job.active and window.controller.state == "Run", 4)
window.stop_button.click()
assert window.job.result == "aborted"
assert not window.controller.baseline_confirmed
wait_until(application, lambda: not window.stop_button.isEnabled())
window.grab().save(str(folder / "stage2-stopped.png"))
window.controller.disconnect_device()
assert not window.pen_up_button.isEnabled()
large_file = ROOT.parent / "G代码生成软件" / "生成G代码" / "output_0006.ngc"
if large_file.exists():
    window.import_file(path=large_file)
    wait_until(application, lambda: not window.loading and window.job.program.path == large_file, 10)
    assert len(window.job.program.commands) == 42793
    window.travel_check.setChecked(True)
    wait_until(application, lambda: window.preview.scene_key is not None and window.preview.scene_key[-1] is True)
    builds = window.preview.scene_builds
    for index in range(30):
        window.preview.set_position((40 + index / 100, 252, -0.4))
        application.processEvents()
    assert window.preview.scene_builds == builds, "位置更新不能重建整份轨迹"
    for _ in range(8):
        window.preview.change_zoom(1.01)
        application.processEvents()
    assert window.preview.scene_builds == builds, "连续缩放应复用缓存，不能逐次描边"
    wait_until(application, lambda: window.preview.scene_builds == builds + 1)
    window.preview.fit()
    application.processEvents()
    window.grab().save(str(folder / "stage2-large-preview.png"))
    window.resize(1600, 1050)
    application.processEvents()
    assert window.sidebar_scroll.verticalScrollBar().maximum() == 0, "常用窗口尺寸应完整展示连续分区"
    window.grab().save(str(folder / "stage2-continuous-layout.png"))
    window.toggle_connection()
    wait_until(application, lambda: window.controller.available)
    window.baseline_check.setChecked(True)
    wait_until(application, lambda: window.pen_up_button.isEnabled())
    window.pen_up_button.click()
    wait_until(application, lambda: window.controller.available)
    window.start_job()
    wait_until(application, lambda: window.job.active and window.job.accepted >= 20, 10)
    window.pause_button.click()
    wait_until(application, lambda: window.resume_button.isEnabled())
    before_resume = window.job.accepted
    window.grab().save(str(folder / "stage2-large-paused.png"))
    window.resume_button.click()
    wait_until(application, lambda: window.job.accepted >= before_resume + 5)
    window.stop_button.click()
    assert window.job.result == "aborted"
    window.controller.disconnect_device()
wait_until(application, lambda: window.loader is None)
window.close()
print("GUI smoke: PASS (import/preview/send/pause/resume/stop/Idle completion; simulator only)")
