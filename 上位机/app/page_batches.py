"""读取批量导出清单并保留文档页码。"""

import json
import re
from pathlib import Path


REPORT_NAME = "导出结果.json"


def page_filename(number):
    return f"第{number:03d}页.ngc"


def page_number(path):
    match = re.fullmatch(r"第([0-9]{3,})页\.ngc", path.name)
    return int(match[1]) if match else None


def read_page_batch(folder):
    """
    返回纸张尺寸、可用页和有问题的页。清单明确标记的空白页可以跳过。
    有问题的页留给主窗口检查，用户仍可以从其后的完整页段开始。
    """
    folder = Path(folder)
    try:
        report = json.loads((folder / REPORT_NAME).read_text(encoding="utf-8-sig"))
    except FileNotFoundError as error:
        raise ValueError(f"所选文件夹中找不到{REPORT_NAME}。请选择批量导出生成的任务文件夹。") from error
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{REPORT_NAME}无法读取，请检查清单或重新导出。") from error

    if not isinstance(report, dict):
        raise ValueError("分页任务清单格式不正确。")
    first, last, records = report.get("first"), report.get("last"), report.get("pages")
    if (type(first) is not int or type(last) is not int or not 1 <= first <= last
            or not isinstance(records, list) or len(records) != last - first + 1):
        raise ValueError("分页任务清单的页码范围与记录数量不一致。")
    paper = report.get("paper_mm")
    if not isinstance(paper, list) or len(paper) != 2 or not all(type(size) in (int, float) and 0 < size <= 1000 for size in paper):
        raise ValueError("分页任务清单的纸张尺寸无效。")

    paths, issues = {}, {}
    for number, record in enumerate(records, first):
        if not isinstance(record, dict) or type(record.get("page")) is not int or record["page"] != number:
            raise ValueError(f"分页任务清单的第{number}页记录缺失或顺序不正确。")
        status = record.get("status")
        if status == "空白页，跳过":
            continue
        if status in ("失败", "已取消", "未处理"):
            issues[number] = f"第{number}页导出状态为“{status}”"
        elif status != "成功" or record.get("file") != page_filename(number):
            raise ValueError(f"分页任务清单的第{number}页状态或文件名无效。")
        else:
            path = folder / record["file"]
            if path.is_file():
                paths[number] = path
            else:
                issues[number] = f"找不到第{number}页的文件 {path.name}"
    return tuple(paper), paths, issues
