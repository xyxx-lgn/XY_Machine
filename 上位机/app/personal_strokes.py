"""用真实录字校准基础笔画；保留分笔与接点，不训练或调用在线模型。"""

from functools import lru_cache
import json
import math
from pathlib import Path
from zipfile import ZipFile

import numpy as np


ARCHIVE = Path(__file__).resolve().parent.parent / "assets/strokes/ordered-kaiti-v1.zip"
SAMPLES = 25


@lru_cache(maxsize=1024)
def reference_strokes(character):
    with ZipFile(ARCHIVE) as archive:
        name = character + ".json"
        if name not in archive.namelist():
            return None
        paths = json.loads(archive.read(name))["strokes"]
    return [np.asarray([(x / 1024, (900 - y) / 1024) for x, y in path], dtype=np.float64) for path in paths]


def resample(path, positions=None):
    points = np.asarray(path, dtype=np.float64)
    distance = np.r_[0, np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
    if distance[-1] < 1e-9:
        raise ValueError("参考笔画包含零长度轨迹。")
    positions = np.linspace(0, 1, SAMPLES) if positions is None else np.asarray(positions)
    return np.stack([np.interp(positions * distance[-1], distance, points[:, axis]) for axis in range(2)], axis=1)


def glyph_bounds(paths):
    points = np.concatenate(paths)
    low, high = points.min(axis=0), points.max(axis=0)
    return low, np.maximum(high - low, .02)


def apply_affine(points, matrix):
    return np.c_[points, np.ones(len(points))] @ matrix


def match_strokes(source, target):
    # 先按字形相对位置配对，不要求用户的录字顺序与基础字库完全相同。
    if len(source) != len(target):
        return []
    low_a, size_a = glyph_bounds(source)
    low_b, size_b = glyph_bounds(target)
    aa = [(resample(path) - low_a) / size_a for path in source]
    bb = [(resample(path) - low_b) / size_b for path in target]
    candidates = []
    for i, a in enumerate(aa):
        for j, b in enumerate(bb):
            forward = np.linalg.norm(a - b, axis=1).mean()
            backward = np.linalg.norm(a - b[::-1], axis=1).mean()
            candidates.append((min(forward, backward), i, j, backward < forward))
    used_a, used_b, result = set(), set(), []
    for error, i, j, reverse in sorted(candidates):
        if i in used_a or j in used_b or error > .23:
            continue
        used_a.add(i)
        used_b.add(j)
        result.append((resample(source[i]), resample(target[j])[::-1] if reverse else resample(target[j])))
    return result


def fit_recorded_style(glyphs):
    """稳健拟合字框比例、倾斜和同类笔画的内部曲度。

    只使用能与基础笔画可靠配对的真实录字；合笔数不同的字仍原样保存，
    不猜分笔。新字保持基础结构，不能复原样本没有提供的个人特殊写法。
    """
    pairs, characters, skipped = [], [], []
    for char, glyph in glyphs.items():
        source = reference_strokes(char)
        matched = []
        if source is not None:
            for variant in glyph["variants"]:
                matched.extend(match_strokes(source, variant["strokes"]))
        if matched:
            # 每个字总权重相同，避免一个复杂字或很多重复版本主导整体风格。
            pairs.extend((a, b, 1 / len(matched)) for a, b in matched)
            characters.append(char)
        else:
            skipped.append(char)
    if len(characters) < 8 or len(pairs) < 15:
        raise ValueError(f"可靠配对的录字不足：{len(characters)}字、{len(pairs)}笔；请补录不同结构的清晰分笔字。至少需要8个可配对字和15笔，不必重录已有字。")
    a = np.concatenate([left for left, right, weight in pairs])
    b = np.concatenate([right for left, right, weight in pairs])
    weights = np.concatenate([np.full(SAMPLES, weight / SAMPLES) for left, right, weight in pairs])
    design = np.c_[a, np.ones(len(a))]
    prior = np.array([[1., 0.], [0., 1.], [0., 0.]])
    robust = weights.copy()
    for iteration in range(5):
        matrix = np.linalg.solve(design.T @ (robust[:, None] * design) + .03 * np.eye(3), design.T @ (robust[:, None] * b) + .03 * prior)
        residual = np.linalg.norm(design @ matrix - b, axis=1)
        robust = weights * np.minimum(1, .07 / np.maximum(residual, 1e-9))
    # 拒绝退化或翻转的拟合，不能为了追求相似度破坏汉字结构。
    singular = np.linalg.svd(matrix[:2], compute_uv=False)
    if np.linalg.det(matrix[:2]) <= 0 or singular.min() < .2 or singular.max() > 1.8:
        raise ValueError("录字的字框比例或方向差异过大，无法稳定校准；请检查字符标签、笔画和字框。")
    templates = []
    t = np.linspace(0, 1, SAMPLES)[:, None]
    for left, right, weight in pairs:
        base = apply_affine(left, matrix)
        length = np.linalg.norm(np.diff(base, axis=0), axis=1).sum()
        source_length = np.linalg.norm(np.diff(left, axis=0), axis=1).sum()
        residual = right - base
        residual -= (1 - t) * residual[0] + t * residual[-1]
        if length < .04 or np.linalg.norm(residual, axis=1).max() > .16:
            continue
        templates.append({"shape": (left - left[0]) / source_length, "residual": residual / length, "length": source_length})
    return {"matrix": matrix, "templates": templates, "characters": characters, "skipped": skipped, "paired_strokes": len(pairs), "mean_error": float(np.average(np.linalg.norm(design @ matrix - b, axis=1), weights=weights))}


def contact_positions(paths, index):
    # 所有原端点落在另一笔中间的接点也固定，曲度迁移后不能在交叉处造缝。
    path = paths[index]
    lengths = np.r_[0, np.cumsum(np.linalg.norm(np.diff(path, axis=0), axis=1))]
    anchors = [0., 1.]
    ends = [point for other in paths for point in (other[0], other[-1])]
    for segment, (a, b) in enumerate(zip(path, path[1:])):
        delta = b - a
        length2 = float(delta @ delta)
        if length2 < 1e-15:
            continue
        for point in ends:
            fraction = float((point - a) @ delta / length2)
            if 0 < fraction < 1 and np.linalg.norm(a + fraction * delta - point) < 1e-8:
                anchors.append(float((lengths[segment] + fraction * math.sqrt(length2)) / lengths[-1]))
    return sorted(set(anchors))


def calibrated_glyph(character, style, rng=None):
    paths = reference_strokes(character)
    if paths is None:
        raise ValueError("基础笔顺库缺字：" + character)
    result = []
    for index, path in enumerate(paths):
        lengths = np.r_[0, np.cumsum(np.linalg.norm(np.diff(path, axis=0), axis=1))]
        anchors = contact_positions(paths, index)
        positions = np.unique(np.r_[np.linspace(0, 1, SAMPLES), lengths / lengths[-1], anchors])
        base = apply_affine(resample(path, positions), style["matrix"])
        shape = (resample(path) - path[0]) / lengths[-1]
        candidates = []
        for template in style["templates"]:
            error = float(np.linalg.norm(shape - template["shape"], axis=1).mean()) + .025 * abs(math.log(lengths[-1] / template["length"]))
            if error < .24:
                candidates.append((error, template))
        if candidates:
            candidates.sort(key=lambda item: item[0])
            chosen = candidates[:3]
            weights = np.exp(-np.array([error for error, template in chosen]) * 18)
            if rng is not None:
                weights *= np.asarray([rng.uniform(.7, 1.3) for item in chosen])
            residual = sum(weight * template["residual"] for weight, (error, template) in zip(weights / weights.sum(), chosen))
            transfer = np.stack([np.interp(positions, np.linspace(0, 1, SAMPLES), residual[:, axis]) for axis in range(2)], axis=1)
            transfer *= np.linalg.norm(np.diff(base, axis=0), axis=1).sum()
            maximum = float(np.linalg.norm(transfer, axis=1).max())
            transfer *= min(1, .045 / max(maximum, 1e-9))
            for t in anchors:
                # 在接点附近平滑压回0，原接点与端点使用同一仿射坐标。
                transfer *= np.minimum(1, np.abs(positions - t) / .1)[:, None]
            base += transfer
        result.append(base)
    points = np.concatenate(result)
    if np.any(points < 0) or np.any(points > 1):
        # 只在必要时整字共同收纳，避免逐点截断让线条变平或接点错开。
        low, high = points.min(axis=0), points.max(axis=0)
        scale = min(1, .96 / max(float((high - low).max()), 1e-9))
        shift = np.maximum(.02 - low * scale, 0) + np.minimum(.98 - high * scale, 0)
        result = [path * scale + shift for path in result]
    return [path.tolist() for path in result]


def check_generated_strokes(character, strokes):
    source = reference_strokes(character)
    if source is not None and len(source) >= 3 and len(strokes) == 1:
        raise ValueError(f"“{character}”被SDT预测为1笔，基础结构为{len(source)}笔，存在明显连笔风险；本次不保存字库。建议选择“清晰分笔”方式，或手工核对原始笔迹。")
