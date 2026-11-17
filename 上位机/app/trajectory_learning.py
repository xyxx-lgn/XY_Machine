"""离线轨迹模型的数据准备和解码；不导入PyTorch，也不操作串口。"""

import hashlib
import json
from pathlib import Path

import numpy as np

from app.personal_strokes import glyph_bounds, reference_strokes, resample


ROOT = Path(__file__).resolve().parent.parent
MODEL_ROOT = ROOT / ".runtime/olhwg"
GENERATED_SOURCES = ("SDT本地模型生成", "个人轨迹校准", "个人笔迹学习", "OLHWG离线轨迹模型生成", "照片中线导入")


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_resources(folder):
    folder = Path(folder)
    try:
        manifest = json.loads((folder / "resources.json").read_text(encoding="utf8"))
        if manifest.get("schema") != 1:
            raise ValueError("模型资源版本无效。")
        for name in ("unetddpm450000.pth", "style_encoder450000.pth", "characters.json"):
            if not (folder / name).is_file() or file_hash(folder / name) != manifest["sha256"][name]:
                raise ValueError("轨迹模型资源缺失或校验失败：" + name)
        content = json.loads((folder / "characters.json").read_text(encoding="utf8"))
        if len(content["dictionary"]) != 4055 or not content["lengths"]:
            raise ValueError("轨迹模型字典或长度先验无效。")
        return content, manifest
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("轨迹模型未准备好，请先运行准备轨迹扩字模型.ps1：" + str(error)) from None


def read_recordings(folder):
    path = Path(folder) / "recorded-font.json"
    if not path.is_file() or path.stat().st_size > 64_000_000:
        raise ValueError("轨迹模型需要原始录字目录中的recorded-font.json。")
    data = json.loads(path.read_text(encoding="utf8"))
    if data.get("schema") != 1 or not isinstance(data.get("glyphs"), dict):
        raise ValueError("原始录字格式无效。")
    if str(data.get("source", "")).startswith(GENERATED_SOURCES):
        raise ValueError("请使用最初真实录字，不能将生成字重新当作本人样本。")
    if len(data["glyphs"]) < 15:
        raise ValueError("轨迹模型至少需要15种真实录字，建议先使用30～100种不同结构的字。")
    for char, glyph in data["glyphs"].items():
        variants = glyph.get("variants", [])
        if not variants:
            raise ValueError("录字缺少版本：" + char)
        for variant in variants:
            paths = variant.get("strokes", [])
            if not paths or len(paths) > 128:
                raise ValueError("录字笔画无效：" + char)
            for path in paths:
                p = np.asarray(path, dtype=float)
                if p.ndim != 2 or p.shape[1] != 2 or len(p) < 2 or not np.isfinite(p).all() or p.min() < 0 or p[:, 0].max() > glyph.get("advance", 1) or p[:, 1].max() > 1:
                    raise ValueError("录字坐标无效：" + char)
                if np.linalg.norm(np.diff(p, axis=0), axis=1).sum() < 1e-9:
                    raise ValueError("录字包含零长度笔画：" + char)
    return data


def encode_reference(paths, wanted):
    """按训练格式编码绝对轨迹为XY增量及末点抬笔；Y向下，不能翻转。

    模型训练使用每个字分别归一化的X/Y字框。重采样按弧长分配点数，
    各笔至少两点；不把两笔之间的空移混入落笔，也不在字尾遗漏抬笔。
    """
    lengths = np.array([np.linalg.norm(np.diff(path, axis=0), axis=1).sum() for path in paths])
    counts = np.maximum(2, np.rint(lengths / lengths.sum() * wanted).astype(int))
    if counts.sum() > 118:
        # 超复杂参考字不截断为半个字，应在采样前换一个参考。
        raise ValueError("参考字过于复杂，超过模型单字118点限制。")
    low, size = glyph_bounds(paths)
    records = []
    for path, count in zip(paths, counts):
        points = (resample(path, np.linspace(0, 1, count)) - low) / size
        records.extend([float(x), float(y), -1. if i == count - 1 else 1.] for i, (x, y) in enumerate(points))
    result = np.asarray(records, dtype=np.float32)
    result[:, :2] = np.diff(result[:, :2], axis=0, prepend=[[0, 0]])
    return result


def reference_batch(recordings, content, seed):
    rng = np.random.default_rng(seed)
    characters = sorted(recordings)
    rng.shuffle(characters)
    batch = np.zeros((1, 1000, 3), dtype=np.float32)
    batch[:, :, 2] = -1
    offset, selected = 0, []
    for char in characters:
        variants = recordings[char]["variants"]
        paths = variants[int(rng.integers(len(variants)))]["strokes"]
        try:
            encoded = encode_reference(paths, min(100, content["lengths"].get(char, 45)))
        except ValueError:
            continue
        if offset + len(encoded) > 1000:
            continue
        batch[0, offset:offset + len(encoded)] = encoded
        offset += len(encoded)
        selected.append(char)
        if len(selected) == 15:
            break
    if len(selected) < 15:
        raise ValueError("未能取得15个完整有效参考字，请补录较清晰、复杂度适中的字。")
    return batch, selected


def content_labels(characters, content):
    labels, durations = [], []
    for char in characters:
        if char not in content["lengths"] or char not in content["dictionary"]:
            raise ValueError("此轨迹模型暂不支持扩展字符：" + char)
        count = int(content["lengths"][char])
        if not 4 <= count <= 118:
            raise ValueError("字符的模型长度先验无效：" + char)
        row = [content["dictionary"].index(char)] * count
        row[0], row[-1] = 4053, 4054
        labels.append(row + [4052] * (120 - count))
        durations.append([count, 120 - count])
    return labels, durations


def recorded_extent(recordings):
    # 每字等权估计个人实际占格，避免固定0.7把新字放得明显大于真实录字。
    sizes = []
    for glyph in recordings.values():
        versions = [np.ptp(np.concatenate(version["strokes"]), axis=0).max() for version in glyph["variants"]]
        sizes.append(float(np.median(versions)))
    return float(np.clip(np.median(sizes), .12, .94))


def decode_trajectory(sequence, duration, extent=.7):
    values = np.asarray(sequence, dtype=float)
    if values.ndim != 2 or values.shape[1] != 3 or len(values) != duration or len(values) < 4 or not np.isfinite(values).all():
        raise ValueError("轨迹模型产生无效坐标（形状、长度或NaN），未保存字库。")
    xy = np.cumsum(values[:, :2], axis=0)
    size = np.ptp(xy, axis=0).max()
    if size < 1e-5 or size > 100:
        raise ValueError(f"轨迹模型产生退化或发散字形（累计范围{size:.3g}），未保存字库。")
    xy = (xy - (xy.min(axis=0) + xy.max(axis=0)) / 2) * extent / size + .5
    paths = []
    for path in np.split(xy, np.where(values[:, 2] < 0)[0] + 1):
        if len(path) == 0:
            continue
        if len(path) == 1 or np.linalg.norm(np.diff(path, axis=0), axis=1).sum() < 1e-8:
            # 独立点也保留为极短线，不能像旧实验直接丢掉只有一个点的笔画。
            path = np.vstack([path[0], path[0] + [.0005, 0]])
        paths.append(path.tolist())
    if not paths:
        raise ValueError("模型没有生成有效笔画，未保存字库。")
    return paths


def structure_distance(character, paths):
    reference = reference_strokes(character)
    if reference is None:
        return None
    a, b = np.concatenate([resample(p) for p in paths]), np.concatenate([resample(p) for p in reference])
    for values in (a, b):
        values -= values.min(axis=0)
        values /= np.maximum(np.ptp(values, axis=0), .02)
    distances = np.linalg.norm(a[:, None, :] - b[None, :, :], axis=2)
    return float((distances.min(axis=0).mean() + distances.min(axis=1).mean()) / 2)
