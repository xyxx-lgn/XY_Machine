import math
from dataclasses import dataclass


@dataclass
class StatusReport:
    state: str
    machine: tuple | None = None
    work: tuple | None = None
    offset: tuple | None = None
    feed: float | None = None
    spindle: float | None = None
    pins: str = ""


def vector(text):
    values = tuple(float(value) for value in text.split(","))
    if len(values) != 3 or not all(math.isfinite(value) for value in values):
        raise ValueError("坐标应包含三个有限数值。")
    return values


def parse_status(line, cached_offset=None):
    """解析GRBL状态，WCO允许间歇出现，字段次序不作固定假设。"""
    if not line.startswith("<") or not line.endswith(">"):
        return None
    items = line[1:-1].split("|")
    data = {}
    for item in items[1:]:
        key, separator, value = item.partition(":")
        if separator:
            data[key] = value
    report = StatusReport(items[0])
    report.offset = vector(data["WCO"]) if "WCO" in data else cached_offset
    report.machine = vector(data["MPos"]) if "MPos" in data else None
    report.work = vector(data["WPos"]) if "WPos" in data else None
    if report.offset is not None:
        if report.machine is not None and report.work is None:
            report.work = tuple(a - b for a, b in zip(report.machine, report.offset))
        elif report.work is not None and report.machine is None:
            report.machine = tuple(a + b for a, b in zip(report.work, report.offset))
    if "FS" in data:
        values = [float(value) for value in data["FS"].split(",")]
        report.feed = values[0]
        report.spindle = values[1] if len(values) > 1 else None
    elif "F" in data:
        report.feed = float(data["F"])
    report.pins = data.get("Pn", "")
    return report


def normalize_command(command):
    command = command.strip()
    if not command:
        raise ValueError("命令不能为空。")
    try:
        encoded = command.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError("GRBL命令只能包含ASCII字符。") from None
    if any(value < 32 or value > 126 for value in encoded):
        raise ValueError("一次只能发送一条命令，不允许嵌入控制字节。")
    # 留出换行空间，保守限制低于当前固件127字节接收容量。
    if len(encoded) > 100:
        raise ValueError("手动命令最多100字节。")
    return command
