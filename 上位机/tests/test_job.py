import math
import time
import unittest

from app.gcode import GcodeError, arc_points, clean_line, parse_program
from app.job import JobRunner
from test_protocol import APPLICATION, controller_ready


HEADER = "G21 G90 G94 G17 G54\n"


class GcodeTests(unittest.TestCase):
    def test_comments_percent_and_source_line_numbers(self):
        program = parse_program("%\n(中文注释)\n" + HEADER + "G0 X1 Y2 ;注释\n%")
        self.assertEqual([command.source_line for command in program.commands], [3, 4])
        self.assertEqual(program.commands[-1].text, "G0 X1 Y2")

    def test_comments_do_not_hide_control_bytes_in_command(self):
        with self.assertRaises(ValueError):
            clean_line("G1 X1\x18")
        with self.assertRaises(ValueError):
            clean_line("G1 X1 (oops")

    def test_unknown_gcodes_and_realtime_are_not_sent(self):
        for text in ("G18", "$H", "!", "G92 X0", "G55", "M280 P0 S90", "G1 Xnan"):
            with self.subTest(text=text), self.assertRaises(GcodeError):
                parse_program(HEADER + text)

    def test_file_must_establish_modes_before_first_move(self):
        with self.assertRaisesRegex(GcodeError, "首次运动"):
            parse_program("G1 X10 F300")

    def test_conflicting_and_unused_parameters_are_rejected(self):
        for command in ("G90 G91", "M3 M5", "G4 P1 X10", "G1 X1 F100 R5", "G1 X1 F100 P2"):
            with self.subTest(command=command), self.assertRaises(GcodeError):
                parse_program(HEADER + command)

    def test_relative_moves_and_inch_units(self):
        program = parse_program("G20 G91 G94 G17 G54\nG0 X1 Y1\nG1 X1 F10\n")
        self.assertAlmostEqual(program.bounds[2], 50.8)
        self.assertAlmostEqual(program.bounds[3], 25.4)

    def test_machine_z_and_g10_only_modify_requested_axis(self):
        program = parse_program(HEADER + "G10 L2 P1 Z0\nG0 X5 Y5 Z3\nG53 G1 Z-0.4 F120", g54_offset=(-192, -306, 7))
        self.assertEqual(program.bounds, (5, 5, 5, 5))
        self.assertEqual(program.z_bounds, (-0.4, 3))

    def test_line_uses_machine_offset_for_z_range(self):
        program = parse_program(HEADER + "G0 Z2\nG1 Z-0.4 F120", g54_offset=(0, 0, 1))
        self.assertEqual(program.z_bounds, (0.6, 3))

    def test_arc_contains_cardinal_extrema(self):
        points, length = arc_points((1, 0), (0, 1), {"I": -1, "J": 0}, False)
        self.assertAlmostEqual(length, math.pi / 2)
        self.assertEqual(points[0], (1, 0))
        self.assertEqual(points[-1], (0, 1))
        circle, length = arc_points((1, 0), (1, 0), {"I": -1}, False)
        self.assertAlmostEqual(length, math.tau)
        self.assertAlmostEqual(min(point[0] for point in circle), -1)

    def test_large_r_nearly_straight_arc_stays_near_chord(self):
        points, length = arc_points((51.11824, 162.512474), (49.373654, 162.029101), {"R": 49024.488691}, True)
        self.assertLess(length, 2)
        self.assertTrue(all(49 <= x <= 52 and 162 <= y <= 163 for x, y in points))

    def test_negative_r_uses_major_arc(self):
        _, short = arc_points((0, 0), (1, 0), {"R": 1}, True)
        _, long = arc_points((0, 0), (1, 0), {"R": -1}, True)
        self.assertGreater(long, short * 4)

    def test_invalid_arc_is_rejected_with_original_line(self):
        with self.assertRaisesRegex(GcodeError, "第3行"):
            parse_program(HEADER + "G0 X1 Y0\nG2 X5 Y0 I-1 J0 F200")

    def test_no_commands_after_program_end(self):
        with self.assertRaisesRegex(GcodeError, "M2/M30"):
            parse_program(HEADER + "M2\nG0 X1 Y2")

    def test_source_text_and_feed_are_not_rewritten(self):
        source = HEADER + "G0 X5 Y5 Z3.5\nG1 Z-0.4 F800\nG1 X10 F1400\n"
        program = parse_program(source)
        self.assertEqual(program.source_text, source)
        self.assertEqual(program.commands[-1].text, "G1 X10 F1400")
        self.assertEqual(program.z_bounds, (-0.4, 3.5))


class JobTests(unittest.TestCase):
    def setUp(self):
        self.controller = controller_ready()
        self.job = JobRunner(self.controller)
        self.program = parse_program(HEADER + "G0 X5 Y5\nG1 X10 F300")
        self.job.load(self.program)

    def tearDown(self):
        self.job.watchdog.stop()
        self.controller.deadline.stop()
        self.controller.poll.stop()

    def status(self, state="Run"):
        self.controller.handle_line(f"<{state}|MPos:-180,-270,2|WCO:-190,-280,0>")
        APPLICATION.processEvents()

    def acknowledge(self, reply="ok"):
        self.controller.handle_line(reply)
        APPLICATION.processEvents()

    def lines(self):
        return [value for value in self.controller.transport.writes if value.endswith(b"\n")]

    def test_only_one_line_is_in_flight(self):
        self.assertTrue(self.job.start())
        self.assertEqual(len(self.lines()), 1)
        self.status()
        self.assertEqual(len(self.lines()), 1)
        self.acknowledge()
        self.assertEqual(len(self.lines()), 2)

    def test_all_acknowledged_is_not_drawing_complete(self):
        self.job.start()
        self.status()
        for _ in self.program.commands:
            self.acknowledge()
        self.assertEqual(self.job.accepted, len(self.program.commands))
        self.assertTrue(self.job.active)
        self.status("Run")
        self.assertTrue(self.job.active)
        self.status("Idle")
        self.assertTrue(self.job.active)
        self.job.idle_since = time.monotonic() - 1
        self.status("Idle")
        self.assertEqual(self.job.result, "complete")
        self.assertFalse(self.controller.job_active)

    def test_idle_then_run_does_not_complete_job(self):
        self.job.start()
        for _ in self.program.commands:
            self.acknowledge()
        self.status("Idle")
        self.assertIsNotNone(self.job.idle_since)
        self.status("Run")
        self.assertIsNone(self.job.idle_since)
        self.assertTrue(self.job.active)

    def test_manual_commands_cannot_interleave(self):
        self.job.start()
        self.assertFalse(self.controller.send("$$", "read"))
        self.assertFalse(self.controller.jog("X", 1))
        self.assertEqual(len(self.lines()), 1)

    def test_pause_freezes_sending_and_pending_timeout(self):
        self.job.start()
        self.controller.pause()
        self.assertFalse(self.controller.deadline.isActive())
        self.acknowledge()
        self.status("Hold:0")
        self.assertEqual(len(self.lines()), 1)
        self.controller.resume()
        self.assertTrue(self.job.resuming)
        self.status("Hold:0")
        self.assertTrue(self.job.resuming)
        self.status("Run")
        self.assertEqual(len(self.lines()), 2)

    def test_resume_waits_for_hold_zero(self):
        self.job.start()
        self.controller.pause()
        self.status("Hold:1")
        self.controller.resume()
        self.assertNotIn(b"~", self.controller.transport.writes)

    def test_error_reports_source_line_and_drops_later_motion(self):
        self.job.start()
        self.acknowledge()
        self.acknowledge("error:15")
        self.assertEqual(self.job.result, "aborted")
        self.assertIn("第2行", self.job.message)
        self.assertEqual(len(self.lines()), 2)
        self.assertIn(b"!", self.controller.transport.writes)
        self.assertTrue(self.controller.fault)

    def test_disconnect_aborts_without_resending(self):
        self.job.start()
        self.controller.on_closed()
        self.assertFalse(self.job.active)
        self.assertEqual(self.job.result, "aborted")
        self.assertEqual(len(self.lines()), 1)

    def test_alarm_aborts_job(self):
        self.job.start()
        self.controller.handle_line("ALARM:1")
        self.assertFalse(self.job.active)
        self.assertTrue(self.controller.fault)

    def test_unexpected_boot_aborts_job(self):
        self.job.start()
        self.controller.handle_line("Grbl 1.1h ['$' for help]")
        self.assertFalse(self.job.active)
        self.assertIsNone(self.controller.pending)

    def test_stop_sends_raw_reset_and_clears_baseline(self):
        self.controller.baseline_confirmed = True
        self.job.start()
        self.job.stop()
        self.assertFalse(self.job.active)
        self.assertEqual(self.controller.transport.writes[-1], b"\x18")
        self.assertFalse(self.controller.baseline_confirmed)
        self.assertEqual(self.controller.queue, [])

    def test_timeout_does_not_retry_file_motion(self):
        self.job.start()
        self.controller.command_timeout()
        APPLICATION.processEvents()
        self.assertFalse(self.job.active)
        self.assertEqual(len(self.lines()), 1)

    def test_stale_status_aborts_and_requests_hold(self):
        self.job.start()
        self.controller.last_status = time.monotonic() - 4
        self.job.check_status()
        self.assertFalse(self.job.active)
        self.assertIn(b"!", self.controller.transport.writes)
        self.assertTrue(self.controller.fault)

    def test_ack_does_not_make_recent_status_stale_for_job_watchdog(self):
        self.job.start()
        self.acknowledge()
        self.job.check_status()
        self.assertTrue(self.job.active)

    def test_job_ack_does_not_request_status_for_every_line(self):
        self.job.start()
        self.acknowledge()
        self.assertNotIn(b"?", self.controller.transport.writes)
        self.controller.poll_status()
        self.assertEqual(self.controller.transport.writes[-1], b"?")

    def test_busy_controller_prevents_job_start(self):
        self.controller.send("$$", "read")
        self.assertFalse(self.job.start())


if __name__ == "__main__":
    unittest.main()
