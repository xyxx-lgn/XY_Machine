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

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from app.appearance import configure_appearance
from app.gcode import parse_program
from app.profile import MachineProfile
from app.transport import SIMULATOR
from app.ui.main_window import MainWindow


def wait_until(condition, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if condition():
            return
        time.sleep(0.005)
    raise AssertionError("等待发送前检查/模拟设备超时")


application = QApplication([])
configure_appearance(application)
window = MainWindow()
window.profile = MachineProfile(pen_up=3)
window.controller.profile = window.profile
window.save_profile = lambda: None
window.confirm = lambda title, message: True
warnings = []
original_warning = QMessageBox.warning
QMessageBox.warning = lambda parent, title, message: warnings.append(message)
window.show()
window.port_combo.setCurrentIndex(window.port_combo.findData(SIMULATOR))
window.toggle_connection()
wait_until(lambda: window.controller.available)
transport = window.controller.transport
# 所有坐标与设置仅改变进程内模拟器，不打开真实串口。
transport.offset[:] = [-192, -306, 0]
transport.position[:] = [-192, -306, 3]
window.controller.query_status()
window.controller.refresh_parameters()
wait_until(lambda: window.controller.available and window.controller.g54_offset == (-192, -306, 0))
window.controller.confirm_baseline(True)
writes = []
original_write = transport.write
read_failure = [False]


def capture(data):
    writes.append(data)
    if read_failure[0] and data == b"$$\n":
        QTimer.singleShot(0, lambda: transport.reply("error:3"))
        return True
    return original_write(data)


transport.write = capture
header = "G21 G90 G94 G17 G54\n"
invalid = parse_program(header + "G53 G1 Z3 F1000\nG1 X193.9120 Y113.9600 F2000")
window.job.load(invalid)
# 旧缓存即使看似允许运动，也必须按设备新读出的真实行程拒绝。
window.controller.settings.update({130: 99999, 131: 99999})
window.start_job()
assert window.loading and window.start_read_pending
window.update_view()
assert not window.jog_buttons[0][1].isEnabled()
window.command_input.setText("G0 X1")
window.send_console()
wait_until(lambda: not window.loading and window.loader is None)
assert not window.job.active and window.job.sent == 0
assert "1.9120" in warnings[-1] and "X轴" in warnings[-1]
assert [data for data in writes if data.endswith(b"\n")] == [b"$$\n"]

# 缺少$130时不能沿用之前的缓存，也不能绕过检查发送。
writes.clear()
transport.settings.pop(130)
window.controller.settings[130] = 210
window.start_job()
wait_until(lambda: not window.loading and window.loader is None)
assert "$130、$131" in warnings[-1]
assert [data for data in writes if data.endswith(b"\n")] == [b"$$\n"]
assert not window.job.active
transport.settings[130] = 210

# 设备拒绝$$时结束检查，不得使用之前的参数继续开始。
read_failure[0] = True
writes.clear()
window.start_job()
wait_until(lambda: not window.loading)
assert not window.start_read_pending and not window.job.active
assert "读取行程失败" in window.notice_label.text()
assert [data for data in writes if data.endswith(b"\n")] == [b"$$\n"]
read_failure[0] = False

# 用户本次报警文件存在时，验证同一条指令会在任何任务行发送前被拦截。
actual = ROOT / "exports" / "图片任务.ngc"
if actual.exists():
    window.import_file(path=actual)
    wait_until(lambda: not window.loading and window.loader is None)
    writes.clear()
    window.start_job()
    wait_until(lambda: not window.loading and window.loader is None)
    assert "第2996行" in warnings[-1] and "第2995条指令" in warnings[-1]
    assert [data for data in writes if data.endswith(b"\n")] == [b"$$\n"]
    assert not window.job.active and window.job.sent == 0

folder = ROOT / ".runtime" / "screenshots"
folder.mkdir(parents=True, exist_ok=True)
window.grab().save(str(folder / "preflight-rejected.png"))

# 确认窗口打开后参数发生变化，本次检查作废，不能继续发送。
valid = parse_program(header + "G53 G1 Z3 F240\nG1 X5 Y5 F300")
window.job.load(valid)
writes.clear()


def change_during_confirmation(title, message):
    window.controller.settings[130] = 220
    return True


window.confirm = change_during_confirmation
window.start_job()
wait_until(lambda: not window.loading and window.loader is None)
assert not window.job.active
assert [data for data in writes if data.endswith(b"\n")] == [b"$$\n"]
assert "确认期间" in window.notice_label.text()

# Z范围继续使用独立配置边界，超限同样弹窗并保持零任务发送。
window.confirm = lambda title, message: True
window.job.load(parse_program(header + "G53 G1 Z6 F240"))
writes.clear()
window.start_job()
wait_until(lambda: not window.loading and window.loader is None)
assert "机器Z范围" in warnings[-1] and "未发送" in warnings[-1]
assert not window.job.active
assert [data for data in writes if data.endswith(b"\n")] == [b"$$\n"]

# 合法任务仍走原发送器，直到连续Idle后确认完成。
window.confirm = lambda title, message: True
window.job.load(valid)
writes.clear()
window.start_job()
wait_until(lambda: window.job.result == "complete")
assert window.controller.settings[130] == 210
assert b"G1 X5 Y5 F300\n" in writes
assert window.controller.machine[:2] == (-187, -301)
wait_until(lambda: window.controller.available)

# 查询行程过程中断开连接，应解除检查状态，不能在重连后自动启动。
window.start_job()
assert window.start_read_pending
window.controller.disconnect_device()
assert not window.loading and not window.start_read_pending and not window.job.active
wait_until(lambda: window.loader is None)
window.close()
QMessageBox.warning = original_warning
print("Preflight GUI: PASS (fresh/missing/failed settings, actual alarm file, zero task writes, context change, Z, valid task, disconnect)")
