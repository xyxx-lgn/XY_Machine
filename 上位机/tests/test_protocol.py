import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication

from app.controller import GrblController
from app.grbl import normalize_command, parse_status
from app.profile import ROOT, MachineProfile


APPLICATION = QCoreApplication.instance() or QCoreApplication([])


class CaptureTransport:
    def __init__(self):
        self.writes = []

    def write(self, data):
        self.writes.append(data)
        return True


def controller_ready():
    controller = GrblController(MachineProfile())
    controller.transport = CaptureTransport()
    controller.connected = True
    controller.handle_line("<Idle|MPos:-180,-270,0|FS:0,0|WCO:-190,-280,0>")
    return controller


class ProtocolTests(unittest.TestCase):
    def test_machine_and_work_coordinate_conversion(self):
        report = parse_status("<Idle|FS:0,0|WCO:-190,-280,0|MPos:-180,-270,2>")
        self.assertEqual(report.work, (10, 10, 2))

    def test_periodic_offset_is_cached(self):
        report = parse_status("<Run|WPos:10,20,2|FS:800,0>", (-190, -280, 0))
        self.assertEqual(report.machine, (-180, -260, 2))
        self.assertEqual(report.feed, 800)

    def test_missing_offset_does_not_invent_work_origin(self):
        self.assertIsNone(parse_status("<Idle|MPos:0,0,0>").work)

    def test_invalid_coordinates_are_rejected(self):
        with self.assertRaises(ValueError):
            parse_status("<Idle|MPos:nan,0,0>")

    def test_control_bytes_and_multiline_are_rejected(self):
        for command in ("G1 X1\nG1 X2", "\x18", "G1 X1\r", "中文", "G" * 101):
            if command == "G1 X1\r":
                # 行末空白允许归一化，嵌入换行不允许。
                self.assertEqual(normalize_command(command), "G1 X1")
            else:
                with self.assertRaises(ValueError):
                    normalize_command(command)

    def test_fragmented_input_and_multiple_lines(self):
        controller = controller_ready()
        controller.receive(b"<Idle|MPos:-1,-2,")
        self.assertEqual(controller.machine, (-180, -270, 0))
        controller.receive(b"3|WCO:-10,-20,0>\r\n[GC:G0 G54 G21 G90]\r\n")
        self.assertEqual(controller.work, (9, 18, 3))
        self.assertIn("G54", controller.modal)

    def test_status_is_not_command_acknowledgement(self):
        controller = controller_ready()
        controller.send("$G", "read")
        controller.send("$$", "read")
        controller.handle_line("<Idle|MPos:0,0,0>")
        self.assertEqual(controller.pending.text, "$G")
        self.assertEqual(controller.transport.writes, [b"$G\n"])
        controller.handle_line("ok")
        APPLICATION.processEvents()
        self.assertIn(b"$$\n", controller.transport.writes)
        controller.clear_pending()

    def test_error_discards_remaining_commands(self):
        controller = controller_ready()
        controller.send("G1 X1")
        controller.send("G1 X2")
        controller.handle_line("error:15")
        APPLICATION.processEvents()
        self.assertNotIn(b"G1 X2\n", controller.transport.writes)
        self.assertEqual(controller.queue, [])

    def test_realtime_pause_bypasses_pending_command(self):
        controller = controller_ready()
        controller.send("$G", "read")
        controller.pause()
        self.assertEqual(controller.transport.writes[-1], b"!")
        self.assertEqual(controller.pending.text, "$G")
        controller.clear_pending()

    def test_ack_does_not_mean_motion_has_finished(self):
        controller = controller_ready()
        controller.send("G1 X10")
        controller.handle_line("ok")
        self.assertFalse(controller.available)
        controller.handle_line("<Run|MPos:1,0,0>")
        self.assertFalse(controller.available)
        controller.handle_line("<Idle|MPos:10,0,0>")
        self.assertTrue(controller.available)

    def test_unexpected_boot_discards_old_commands(self):
        controller = controller_ready()
        controller.baseline_confirmed = True
        controller.send("G1 X1")
        controller.send("G1 X2")
        controller.handle_line("Grbl 1.1h ['$' for help]")
        self.assertEqual(controller.queue, [])
        self.assertIsNone(controller.pending)
        self.assertFalse(controller.baseline_confirmed)
        self.assertFalse(controller.available)

    def test_timeout_does_not_retry_motion(self):
        controller = controller_ready()
        controller.send("G1 X1")
        controller.command_timeout()
        self.assertTrue(controller.fault)
        self.assertFalse(controller.send("G1 X1"))
        self.assertEqual(controller.transport.writes, [b"G1 X1\n"])

    def test_reset_clears_commands_and_baseline(self):
        controller = controller_ready()
        controller.baseline_confirmed = True
        controller.send("G1 X1")
        controller.send("G1 X2")
        controller.reset()
        self.assertEqual(controller.transport.writes[-1], b"\x18")
        self.assertFalse(controller.baseline_confirmed)
        self.assertFalse(controller.send("G1 X3"))
        controller.handle_line("<Idle|MPos:0,0,0>")
        self.assertFalse(controller.available)
        controller.handle_line("Grbl 1.1h ['$' for help]")
        self.assertFalse(controller.awaiting_reset)
        self.assertIsNone(controller.machine)

    def test_alarm_clears_queue_and_prevents_unlock_before_reset(self):
        controller = controller_ready()
        controller.send("G1 X1")
        controller.send("G1 X2")
        controller.handle_line("ALARM:1")
        self.assertIsNone(controller.pending)
        self.assertTrue(controller.fault)
        self.assertFalse(controller.send("$X"))

    def test_stale_status_blocks_motion(self):
        controller = controller_ready()
        controller.baseline_confirmed = True
        controller.last_status = time.monotonic() - 10
        self.assertFalse(controller.jog("X", 1))
        self.assertEqual(controller.transport.writes, [])

    def test_stepper_requires_baseline_and_uses_machine_coordinates(self):
        controller = controller_ready()
        self.assertFalse(controller.pen(True))
        controller.confirm_baseline(True)
        self.assertTrue(controller.pen(True))
        self.assertEqual(controller.transport.writes[-1], b"G21 G90 G94 G53 G1 Z2.0000 F240.0\n")
        controller.clear_pending()

    def test_z_jog_bounds_are_enforced(self):
        controller = controller_ready()
        controller.baseline_confirmed = True
        controller.machine = (0, 0, 5)
        self.assertFalse(controller.jog("Z", 1))
        self.assertEqual(controller.transport.writes, [])

    def test_jog_does_not_change_normal_coordinate_mode(self):
        controller = controller_ready()
        controller.baseline_confirmed = True
        self.assertTrue(controller.jog("Y", 1))
        self.assertEqual(controller.transport.writes[-1], b"$J=G21 G91 Y1.0000 F300.0\n")
        controller.clear_pending()

    def test_homing_does_not_run_with_pen_down(self):
        controller = controller_ready()
        controller.baseline_confirmed = True
        self.assertFalse(controller.home_xy())
        controller.machine = (-180, -270, 2)
        self.assertTrue(controller.home_xy())
        self.assertEqual(controller.transport.writes[-1], b"$H\n")
        controller.clear_pending()

    def test_servo_mode_requires_firmware_confirmation(self):
        controller = controller_ready()
        controller.profile.z_mode = "servo"
        self.assertFalse(controller.pen(True))
        controller.profile.servo_up_command = "M280 P0 S90"
        controller.profile.servo_down_command = "M280 P0 S10"
        controller.profile.servo_firmware_confirmed = True
        self.assertTrue(controller.pen(True))
        self.assertEqual(controller.transport.writes[-1], b"M280 P0 S90\n")
        controller.clear_pending()

    def test_origin_changes_xy_only(self):
        controller = controller_ready()
        self.assertTrue(controller.set_xy_origin())
        command = controller.transport.writes[-1]
        self.assertIn(b"P1 X0 Y0", command)
        self.assertNotIn(b"Z", command)
        controller.clear_pending()

    def test_servo_mode_does_not_send_z_jog(self):
        controller = controller_ready()
        controller.profile.z_mode = "servo"
        controller.profile.servo_firmware_confirmed = True
        self.assertFalse(controller.jog("Z", 1))
        self.assertEqual(controller.transport.writes, [])


class ProfileTests(unittest.TestCase):
    def test_round_trip_profile_in_allowed_directory(self):
        with TemporaryDirectory(dir=ROOT / ".runtime" / "tmp") as folder:
            path = Path(folder) / "profile.json"
            profile = MachineProfile(paper_width=200, z_mode="servo")
            profile.save(path)
            loaded, warning = MachineProfile.load(path)
            self.assertEqual(loaded, profile)
            self.assertEqual(warning, "")

    def test_invalid_profile_is_preserved(self):
        with TemporaryDirectory(dir=ROOT / ".runtime" / "tmp") as folder:
            path = Path(folder) / "profile.json"
            path.write_text('{"pen_up": -10}', encoding="utf-8")
            loaded, warning = MachineProfile.load(path)
            self.assertTrue(warning)
            self.assertEqual(loaded.pen_up, 2)
            self.assertIn("-10", path.read_text())


if __name__ == "__main__":
    unittest.main()
