"""重建离线笔画数据。只写本上位机目录；日常生成不执行本脚本，也不联网。"""

import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import urllib.request
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.text_writing import DATA, contact_strokes, svg_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="已下载的hanzi-writer-data-2.0.1.tgz；不指定则从npm下载")
    arguments = parser.parse_args()
    source = arguments.source.read_bytes() if arguments.source else urllib.request.urlopen("https://registry.npmjs.org/hanzi-writer-data/-/hanzi-writer-data-2.0.1.tgz", timeout=30).read()
    if hashlib.sha256(source).hexdigest() != "72baf3d82b114e60d6e40ea05f24d2262a05cd39d544e2f322ba2fceb7beff15":
        raise ValueError("源字库版本或校验值不符，未改动字库。")
    DATA.mkdir(parents=True, exist_ok=True)
    temporary = DATA / "ordered-kaiti-v1.tmp"
    count = 0
    with tarfile.open(fileobj=io.BytesIO(source), mode="r:gz") as archive, ZipFile(temporary, "w", ZIP_DEFLATED, 9) as output:
        for member in archive.getmembers():
            name = Path(member.name).name
            if not member.isfile() or not name.endswith(".json") or len(name[:-5]) != 1:
                continue
            data = json.loads(archive.extractfile(member).read())
            if "medians" not in data:
                continue
            paths = contact_strokes(data["medians"], [svg_path(outline) for outline in data["strokes"]])
            notice = "XY Single-line Kaiti v1, 2026-11-11: derived from Hanzi Writer Data 2.0.1 / Arphic (C)1999; medians with outline-supported endpoint contacts; ARPHICPL.TXT applies; no warranty."
            output.writestr(name, json.dumps({"notice": notice, "strokes": paths}, separators=(",", ":")))
            count += 1
            if count % 500 == 0:
                print(f"Processed {count}/9574", flush=True)
        for src, dst in (("package/ARPHICPL.TXT", "ARPHICPL.TXT"), ("package/APL/zh_CN.UTF-8/ARPHICPL.TXT", "ARPHICPL-zh-CN.txt")):
            (DATA / dst).write_bytes(archive.extractfile(src).read())
    temporary.replace(DATA / "ordered-kaiti-v1.zip")
    print(f"Completed {count} glyphs", flush=True)


if __name__ == "__main__":
    main()
