"""一次性准备公开轨迹模型；固定校验下载，之后推理完全离线。"""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import shutil
import sys
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import numpy as np
from app.trajectory_learning import file_hash, MODEL_ROOT


FILES = {
    "unetddpm450000.pth": "fedf6840f6b25a5912ce6df49d85a254d13fd66c5ba89dc7f5bf61c3079e45c4",
    "style_encoder450000.pth": "c1d069dd457d0a5fbcaf80c77bc645252598aa8523398480b0db779d9c04c1f5",
    "mydict.npy": "bb1400234a69edc7af3e769a76907ef39811fa469e7cc69aeb3a95f12374356c",
    "test_datas.npy": "698e13d39c333bdee643a9d9bcd0dcfc10a96c373ccb5717d555c6780e2bb3e5",
}
SOURCE = "https://huggingface.co/datasets/Immortalman12/OLHWD/resolve/main/"


def obtain(name, digest, cache, offline):
    target = cache / name
    if target.is_file() and file_hash(target) == digest:
        print("已校验缓存：" + name, flush=True)
        return target
    experiment = ROOT / ".runtime/personal-learning/olhwg" / name
    if experiment.is_file() and file_hash(experiment) == digest:
        shutil.copyfile(experiment, target)
        print("复用已校验资源：" + name, flush=True)
        return target
    if offline:
        raise ValueError("离线准备缺少资源：" + name)
    temporary = target.with_suffix(target.suffix + ".download")
    print("下载公开资源：" + name, flush=True)
    request = urllib.request.Request(SOURCE + name, headers={"User-Agent": "XY-Machine-Model-Setup"})
    with urllib.request.urlopen(request, timeout=45) as response, temporary.open("wb") as stream:
        shutil.copyfileobj(response, stream, length=1024 * 1024)
    if file_hash(temporary) != digest:
        raise ValueError("下载校验失败：" + name)
    temporary.replace(target)
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    cache = MODEL_ROOT / "downloads"
    cache.mkdir(exist_ok=True)
    paths = {name: obtain(name, digest, cache, args.offline) for name, digest in FILES.items()}
    for name in ("unetddpm450000.pth", "style_encoder450000.pth"):
        target = MODEL_ROOT / name
        if not target.is_file() or file_hash(target) != FILES[name]:
            shutil.copyfile(paths[name], target)
    # 固定SHA256的公开辅助数据只在准备时读取；运行时使用无pickle的JSON。
    dictionary = np.load(paths["mydict.npy"], allow_pickle=True).tolist()
    writers = np.load(paths["test_datas.npy"], allow_pickle=True).tolist()
    lengths = defaultdict(list)
    for writer in writers:
        for character, sequence in writer:
            if isinstance(character, str) and len(character) == 1:
                lengths[character].append(len(sequence))
    supported = {char: int(np.median(values)) for char, values in lengths.items() if char in dictionary and 4 <= np.median(values) <= 118}
    content = {"schema": 1, "dictionary": dictionary, "lengths": supported, "source": SOURCE, "lengths_source_sha256": FILES["test_datas.npy"]}
    (MODEL_ROOT / "characters.json").write_text(json.dumps(content, ensure_ascii=False, separators=(",", ":")), encoding="utf8")
    checksums = {name: file_hash(MODEL_ROOT / name) for name in ("unetddpm450000.pth", "style_encoder450000.pth", "characters.json")}
    manifest = {"schema": 1, "algorithm": "OLHWG-DDPM-450000", "source": SOURCE, "paper": "https://openreview.net/forum?id=DhHIw9Nbl1", "sha256": checksums, "supported_characters": len(supported), "source_files_sha256": FILES, "terms": "模型及原始CASIA/ICDAR数据保留原作者和数据集条款；此处独立下载，不作为内置字体重新分发。"}
    (MODEL_ROOT / "resources.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf8")
    print(f"轨迹模型已准备：{len(supported)}种字符。生成阶段无需联网。", flush=True)


if __name__ == "__main__":
    main()
