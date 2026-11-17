"""从Make Me a Hanzi字典提取部件标签；保留来源及LGPL，不包含个人笔迹。"""

import argparse
import gzip
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dictionary", type=Path)
    args = parser.parse_args()
    raw = args.dictionary.read_bytes()
    records = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
    result = {record["character"]: {"decomposition": record["decomposition"], "matches": record["matches"]} for record in records}
    package = {"schema": 1, "source": "https://github.com/skishore/makemeahanzi", "dictionary_sha256": hashlib.sha256(raw).hexdigest(), "characters": result}
    target = ROOT / "assets/strokes/personal-components.json.gz"
    target.write_bytes(gzip.compress(json.dumps(package, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), mtime=0))
    print(f"部件标签：{len(result)}字，{target.stat().st_size}字节；不含个人数据。")


if __name__ == "__main__":
    main()
