"""准备离线SDT资源；只写本上位机.runtime/sdt，不上传参考图或连接设备。"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import http.client
import json
import math
from pathlib import Path
import shutil
import struct
import time
import urllib.request
import zlib


ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / ".runtime" / "sdt"
COMMIT = "ba1da572c5c9947a192672e7de639987b878603f"
SOURCE_FILES = {
    "LICENSE": "9824345f24a3ce74c1feba709727f575c61cdbff",
    "README.md": "7ead313fd34ecb7f04f42d8e7e702a573d035a42",
    "models/encoder.py": "39cd28b7c312c19733fb049be8f67412d8887c9d",
    "models/gmm.py": "8d9cd9d765b7879e7e280828df7ed0cefd5b5286",
    "models/model.py": "401b6c8c641743853f05c90b06dc9df51c6afe05",
    "models/transformer.py": "65dc7214c8b259ddc5cf5c72a6042b4b418360a5",
    "user_generate.py": "56b749715816521ab8c687d0ac9a05b5e21b487d",
}
CONTENT_SHA = "374779cb759382e3c440c7d689ad280799c72b5d3ca9c63a7cb9b8efaa865047"
MODEL_SHA = "e23ae03af8099c9fa09bc4f1b80dad3f3457cc73e651e4f6e23ff6aa1e41aa89"
TORCH_SHA = "4bcee18f00c43c815efad8efaa3bca584ffdc8d2cd35ef4c44c814f2739d9191"
TORCH_URL = "https://download-r2.pytorch.org/whl/cu121/torch-2.5.1%2Bcu121-cp311-cp311-win_amd64.whl"
TORCH_SIZE = 2449385544
MODEL_SIZE = 273587031


def report(message):
    print(message, flush=True)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def drive_url(identity):
    return "https://drive.usercontent.google.com/download?id=" + identity + "&export=download&confirm=t"


def fetch(url, start=None, end=None):
    """分段下载必须核对Content-Range，防止代理把缓存的其他分段写入模型。"""
    for attempt in range(8):
        try:
            headers = {"Range": f"bytes={start}-{end}"} if start is not None else {}
            address = url.replace("download-r2.pytorch.org", "download.pytorch.org") if attempt % 2 else url
            with urllib.request.urlopen(urllib.request.Request(address, headers=headers), timeout=30) as response:
                if start is not None and (response.status != 206 or not response.headers.get("Content-Range", "").startswith(f"bytes {start}-{end}/")):
                    raise ValueError("服务器未正确返回下载分段。")
                data = response.read(end - start + 2 if start is not None else 32_000_000)
            if start is not None and len(data) != end - start + 1:
                raise ValueError("下载分段长度不符。")
            return data
        except (OSError, ValueError, http.client.HTTPException):
            if attempt == 7:
                raise
            time.sleep(min(1, .2 * (attempt + 1)))


def download_parts(url, target, size, expected, workers=6):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size == size and expected and sha256(target) == expected:
        report("已验证：" + target.name)
        return
    parts = BASE / "downloads" / (target.name + ".parts")
    parts.mkdir(parents=True, exist_ok=True)
    chunk = 8 * 1024 * 1024
    count = math.ceil(size / chunk)

    def part(index):
        first, last = index * chunk, min(size, (index + 1) * chunk) - 1
        path = parts / f"{index:05d}.bin"
        if not path.exists() or path.stat().st_size != last - first + 1:
            data = fetch(url, first, last)
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(data)
            temporary.replace(path)
        return index

    errors = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(part, index) for index in range(count)]
        for done, future in enumerate(as_completed(futures), 1):
            try:
                future.result()
            except (OSError, ValueError, http.client.HTTPException) as error:
                errors.append(str(error))
                report("某个分段未完成，继续保留其他下载进度：" + str(error))
            if done == 1 or done % 8 == 0 or done == count:
                report(f"{target.name} 下载分段 {done}/{count}")
    if errors:
        raise ValueError(f"{len(errors)}个分段下载未完成，请恢复网络后重试。")
    temporary = target.with_suffix(target.suffix + ".tmp")
    with temporary.open("wb") as output:
        for index in range(count):
            with (parts / f"{index:05d}.bin").open("rb") as source:
                shutil.copyfileobj(source, output)
    if temporary.stat().st_size != size or expected and sha256(temporary) != expected:
        for path in parts.glob("[0-9][0-9][0-9][0-9][0-9].bin"):
            path.unlink()
        raise ValueError("下载文件校验不符，未替换现有资源：" + target.name)
    temporary.replace(target)
    report("下载并校验完成：" + target.name + " SHA256=" + sha256(target))


def extract_content(record):
    # 官方ZIP中仅提取约1.2MB的压缩字形；不下载6GB训练集，也不解压其他路径。
    header = struct.unpack_from("<4s5H3I2H", record)
    if header[0] != b"PK\x03\x04" or header[3] != 8 or header[7:9] != (1194738, 27982462):
        raise ValueError("官方内容字形的ZIP记录与固定版本不符。")
    offset = 30 + header[-2] + header[-1]
    content = zlib.decompress(record[offset:offset + header[7]], -15)
    if len(content) != header[8] or zlib.crc32(content) != header[6] or hashlib.sha256(content).hexdigest() != CONTENT_SHA:
        raise ValueError("官方内容字形校验失败。")
    return content


def prepare_assets():
    for name, expected in SOURCE_FILES.items():
        target = BASE / "SDT" / name
        data = target.read_bytes() if target.exists() else fetch(f"https://raw.githubusercontent.com/dailenson/SDT/{COMMIT}/{name}")
        if hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() != expected:
            raise ValueError("官方源码校验失败，未覆盖：" + name)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(data)
    report("官方推理源码已验证：" + COMMIT)
    target = BASE / "data" / "Chinese_content.pkl"
    if not target.exists() or sha256(target) != CONTENT_SHA:
        offset = 5754819617
        record = fetch(drive_url("1-1XO8Mluy0Z9DVCpviWUPKfqZsHJ4f7z"), offset, offset + 1194738 + 500)
        data = extract_content(record)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(data)
        temporary.replace(target)
    report("6763种中文内容字形已验证。")
    download_parts(drive_url("1OAjW463bVgreCuDKT_aT8a94Cx-53ZKu"), BASE / "weights" / "chinese.pth", MODEL_SIZE, MODEL_SHA)
    manifest = {"source_revision": COMMIT, "model_sha256": sha256(BASE / "weights/chinese.pth"), "content_sha256": CONTENT_SHA, "model_url": "https://drive.google.com/file/d/1OAjW463bVgreCuDKT_aT8a94Cx-53ZKu/view", "data_url": "https://drive.google.com/file/d/1-1XO8Mluy0Z9DVCpviWUPKfqZsHJ4f7z/view"}
    (BASE / "resources.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cuda-wheel", action="store_true", help="仅下载并校验Windows Python3.11的CUDA版PyTorch轮子")
    parser.add_argument("--check-runtime", action="store_true", help="仅检查当前Python的AI依赖与CUDA，不下载文件")
    parser.add_argument("--workers", type=int, default=6, choices=range(1, 13), help="下载并发数；网络不稳定时可选2")
    args = parser.parse_args()
    if args.check_runtime:
        import torch
        import torchvision
        import numpy
        import einops
        from PIL import Image
        report(f"PyTorch: {torch.__version__}；CUDA: {torch.cuda.is_available()}")
        if not torch.cuda.is_available():
            raise ValueError("CUDA不可用，请检查是否安装CUDA版PyTorch及支持的NVIDIA显卡驱动。")
        report(torch.cuda.get_device_name(0))
        return 0
    BASE.mkdir(parents=True, exist_ok=True)
    if args.cuda_wheel:
        download_parts(TORCH_URL, BASE / "downloads" / "torch-2.5.1+cu121-cp311-cp311-win_amd64.whl", TORCH_SIZE, TORCH_SHA, workers=args.workers)
    else:
        prepare_assets()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, ImportError, http.client.HTTPException) as error:
        report("准备未完成：" + str(error) + "；已下载分段会保留，重试可复用。")
        raise SystemExit(1)
