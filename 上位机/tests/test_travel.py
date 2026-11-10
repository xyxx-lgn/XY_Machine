import unittest

from app.gcode import GcodeError, parse_program


HEADER = "G21 G90 G94 G17 G54\n"


class TravelTests(unittest.TestCase):
    def parse(self, commands, **context):
        defaults = {"initial_work": (0, 0, 3), "g54_offset": (-192, -306, 0), "xy_travel": (210, 315)}
        defaults.update(context)
        return parse_program(HEADER + commands, **defaults)

    def test_actual_alarm_endpoint_is_rejected_with_line_and_axis(self):
        with self.assertRaises(GcodeError) as caught:
            self.parse("G53 G1 Z3 F1000\nG1 X184.2414 Y105.32 F2000\nG1 X193.9120 Y113.9600")
        message = str(caught.exception)
        for text in ("第4行", "第4条指令", "X轴", "1.9120", "-210.0000～0.0000", "-18.0000～192.0000", "未发送"):
            self.assertIn(text, message)

    def test_y_upper_and_xy_lower_bounds_are_checked(self):
        for command, axis in (("G0 Y307", "Y轴"), ("G0 X-19", "X轴"), ("G0 Y-10", "Y轴")):
            with self.subTest(command=command), self.assertRaisesRegex(GcodeError, axis):
                self.parse(command)

    def test_boundaries_and_small_task_preserve_source(self):
        commands = "G0 X-18 Y-9\nG0 X192 Y306\nG0 X5 Y5\nG1 X185 Y100 F400"
        program = self.parse(commands)
        self.assertEqual(program.source_text, HEADER + commands)
        self.assertEqual(program.commands[-1].text, "G1 X185 Y100 F400")

    def test_relative_move_is_checked_after_accumulation(self):
        with self.assertRaisesRegex(GcodeError, "8.0000 mm"):
            self.parse("G0 X100\nG91\nG1 X100 F200")

    def test_inch_move_is_converted_to_mm(self):
        with self.assertRaisesRegex(GcodeError, "11.2000 mm"):
            self.parse("G20\nG0 X8")

    def test_g53_xy_uses_machine_coordinates(self):
        self.parse("G53 G0 X-1 Y-2")
        with self.assertRaisesRegex(GcodeError, "X轴"):
            self.parse("G53 G0 X1 Y-2")

    def test_z_only_move_also_checks_current_xy(self):
        with self.assertRaisesRegex(GcodeError, "X轴"):
            self.parse("G53 G0 Z3", initial_work=(193, 0, 0))

    def test_entry_segment_is_included(self):
        with self.assertRaisesRegex(GcodeError, "X轴"):
            self.parse("G0 X5 Y5", initial_work=(193, 0, 3))

    def test_arc_middle_can_exceed_limit_with_both_endpoints_inside(self):
        with self.assertRaisesRegex(GcodeError, "X轴"):
            self.parse("G0 X191 Y10\nG3 X191 Y10 I0 J2 F200")

    def test_r_major_arc_is_checked_between_endpoints(self):
        with self.assertRaisesRegex(GcodeError, "Y轴"):
            self.parse("G0 X100 Y305\nG2 X101 Y305 R-2 F200")

    def test_arc_roundoff_at_boundary_does_not_reject_valid_circle(self):
        self.parse("G0 X192 Y10\nG3 X192 Y10 I-1 J0 F200")

    def test_g54_z_change_does_not_change_xy_limits(self):
        self.parse("G10 L2 P1 Z0\nG0 X185 Y100 Z3", g54_offset=(-192, -306, 10))

    def test_missing_invalid_or_nonfinite_travel_never_skips_check(self):
        for travel in ((None, 315), (210, None), (0, 315), (-210, 315), (210, float("nan")), (float("inf"), 315), (True, 315)):
            with self.subTest(travel=travel), self.assertRaisesRegex(GcodeError, r"\$130、\$131"):
                self.parse("G0 X5 Y5", xy_travel=travel)

    def test_offline_import_remains_available_without_machine_context(self):
        program = parse_program(HEADER + "G0 X205 Y205")
        self.assertEqual(program.bounds, (205, 205, 205, 205))

    def test_meaningful_small_overrun_is_not_hidden_by_tolerance(self):
        with self.assertRaisesRegex(GcodeError, "X轴"):
            self.parse("G0 X192.0001")


if __name__ == "__main__":
    unittest.main()
