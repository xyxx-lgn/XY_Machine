import json
import math
from dataclasses import asdict, dataclass, fields
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = ROOT / "config" / "machine.json"


@dataclass
class MachineProfile:
    paper_width: float = 210.0
    paper_height: float = 297.0
    baud_rate: int = 115200
    jog_step: float = 1.0
    jog_feed: float = 300.0
    z_jog_step: float = 0.1
    z_feed: float = 120.0
    z_mode: str = "stepper"
    pen_up: float = 2.0
    pen_down: float = -0.4
    pen_up_feed: float = 240.0
    pen_down_feed: float = 120.0
    z_min: float = -1.5
    z_max: float = 5.0
    servo_up_command: str = ""
    servo_down_command: str = ""
    servo_firmware_confirmed: bool = False

    # Z限位仅保留能力标记，第一阶段不增加Z回零命令。
    z_limit_available: bool = False

    def validate(self):
        positive = (
            "paper_width", "paper_height", "jog_step", "jog_feed",
            "z_jog_step", "z_feed", "pen_up_feed", "pen_down_feed"
        )
        for name in positive:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"参数 {name} 必须是大于零的有限数值。")
        for name in ("pen_up", "pen_down", "z_min", "z_max"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                raise ValueError(f"参数 {name} 必须是有限数值。")
        if not self.z_min <= self.pen_down < self.pen_up <= self.z_max:
            raise ValueError("应满足 Z下界 ≤ 落笔位置 < 抬笔位置 ≤ Z上界。")
        if self.z_mode not in ("stepper", "servo"):
            raise ValueError("Z类型只能是stepper或servo。")
        if self.baud_rate not in (9600, 19200, 38400, 57600, 115200, 230400):
            raise ValueError("不支持的波特率。")
        if type(self.servo_firmware_confirmed) is not bool or type(self.z_limit_available) is not bool:
            raise ValueError("能力确认字段必须为布尔值。")
        for command in (self.servo_up_command, self.servo_down_command):
            if not isinstance(command, str) or len(command) > 80:
                raise ValueError("舵机命令应为不超过80字符的单行文本。")
            if any(ord(char) < 32 or ord(char) > 126 for char in command):
                raise ValueError("舵机命令仅允许可打印ASCII字符，不允许换行。")
        if self.z_mode == "servo" and self.servo_firmware_confirmed:
            if not self.servo_up_command.strip() or not self.servo_down_command.strip():
                raise ValueError("确认舵机固件后，应填写实际支持的抬笔和落笔命令。")

    def save(self, path=PROFILE_PATH):
        self.validate()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    @classmethod
    def load(cls, path=PROFILE_PATH):
        path = Path(path)
        if not path.exists():
            return cls(), ""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            known = {field.name for field in fields(cls)}
            profile = cls(**{key: value for key, value in data.items() if key in known})
            profile.validate()
            return profile, ""
        except (ValueError, TypeError, OSError, AttributeError) as error:
            return cls(), f"配置读取失败，已使用默认值，原文件未覆盖：{error}"
