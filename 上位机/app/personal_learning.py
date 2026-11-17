"""离线学习真实录字的笔形和部件，按结构生成未录字；独立于自然书写和设备。"""

from functools import lru_cache
import gzip
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from app.personal_strokes import apply_affine, contact_positions, fit_recorded_style, glyph_bounds, reference_strokes, resample


SAMPLES = 25
ASSET = Path(__file__).resolve().parent.parent / "assets/strokes/personal-components.json.gz"


@lru_cache(maxsize=1)
def component_index():
    return json.loads(gzip.decompress(ASSET.read_bytes()))["characters"]


def decomposition_tree(text):
    """将IDS前缀表达式解析为树；路径与字典matches字段的0/1/2分支一致。"""
    position = 0

    def node():
        nonlocal position
        if position >= len(text):
            raise ValueError("部件表达式不完整。")
        value = text[position]
        position += 1
        count = 3 if value in "⿲⿳" else 2 if "\u2ff0" <= value <= "\u2ffb" else 0
        return (value, [node() for index in range(count)])

    result = node()
    if position != len(text):
        raise ValueError("部件表达式有多余内容。")
    return result


@lru_cache(maxsize=1024)
def component_groups(character):
    record = component_index().get(character)
    paths = reference_strokes(character)
    if not record or paths is None or len(record["matches"]) != len(paths):
        return []
    groups = []

    def visit(node, prefix):
        symbol, children = node
        if not children and symbol not in "？?":
            indices = [i for i, match in enumerate(record["matches"]) if match is not None and list(match[:len(prefix)]) == prefix]
            if indices:
                groups.append((symbol, indices))
        for index, child in enumerate(children):
            visit(child, prefix + [index])

    try:
        visit(decomposition_tree(record["decomposition"]), [])
    except ValueError:
        return []
    # 例如“想”的“相”继续分成“木、目”，才能复用真正录过的部件。
    pending = [(symbol, indices, {character}) for symbol, indices in groups]
    expanded = list(groups)
    while pending:
        symbol, indices, visited = pending.pop()
        child = component_index().get(symbol)
        if symbol in visited or not child or len(child["matches"]) != len(indices) or len(visited) >= 6:
            continue
        local = []

        def children(node, prefix):
            label, descendants = node
            if not descendants and label not in "？?":
                selected = [indices[i] for i, match in enumerate(child["matches"]) if match is not None and list(match[:len(prefix)]) == prefix]
                if selected:
                    local.append((label, selected))
            for i, descendant in enumerate(descendants):
                children(descendant, prefix + [i])

        try:
            children(decomposition_tree(child["decomposition"]), [])
        except ValueError:
            continue
        expanded.extend(local)
        pending.extend((label, selected, visited | {symbol}) for label, selected in local)
    return list({(symbol, tuple(indices)): (symbol, indices) for symbol, indices in expanded}.values())


def paired_indices(source, target):
    # 几何匹配允许录字笔顺不同；合笔数不同也只取可靠的单笔，绝不强拆用户轨迹。
    low_a, size_a = glyph_bounds(source)
    low_b, size_b = glyph_bounds(target)
    aa = [(resample(path) - low_a) / size_a for path in source]
    bb = [(resample(path) - low_b) / size_b for path in target]
    candidates = []
    for i, left in enumerate(aa):
        for j, right in enumerate(bb):
            forward = float(np.linalg.norm(left - right, axis=1).mean())
            backward = float(np.linalg.norm(left - right[::-1], axis=1).mean())
            candidates.append((min(forward, backward), i, j, backward < forward))
    used_a, used_b, result = set(), set(), []
    for error, i, j, reverse in sorted(candidates):
        if error > .19 or i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        result.append((i, j, reverse))
    return result


def descriptor(path):
    points = resample(path)
    length = float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum())
    if length < 1e-9:
        raise ValueError("录字包含零长度笔画。")
    shape = (points - points[0]) / length
    return np.r_[shape.ravel() * 6 / math.sqrt(SAMPLES), points.mean(axis=0) * 1.5, math.log(length) * .4]


def fit_personal_model(glyphs):
    """用真实笔画监督学习非线性残差，同时建立可直接复用的个人部件库。

    核回归学习每笔25个位置的位移，端点也参与学习；不是只拟合整字缩放。
    部件优先使用原始手写轨迹，笔形模型补足未录部件；字的身份由结构条件限定。
    """
    if len(glyphs) < 15:
        raise ValueError("个人笔迹学习至少需要15种真实录字，建议覆盖不同笔画与结构。")
    for char, glyph in glyphs.items():
        if not isinstance(glyph.get("variants"), list) or not glyph["variants"]:
            raise ValueError("录字缺少笔画版本：" + char)
        for variant in glyph["variants"]:
            if not isinstance(variant.get("strokes"), list) or not variant["strokes"]:
                raise ValueError("录字缺少有效轨迹：" + char)
            for path in variant["strokes"]:
                points = np.asarray(path, dtype=float)
                if points.ndim != 2 or points.shape[1] != 2 or len(points) < 2 or not np.isfinite(points).all() or points.min() < 0 or points.max() > max(1, glyph.get("advance", 1)):
                    raise ValueError("录字包含无效坐标：" + char)
    style = fit_recorded_style(glyphs)
    features, targets, components, learned_chars = [], [], {}, set()
    for char, glyph in glyphs.items():
        source = reference_strokes(char)
        if source is None:
            continue
        for version_index, version in enumerate(glyph["variants"]):
            raw = version["strokes"]
            matches = paired_indices(source, raw)
            by_index = {i: (j, reverse) for i, j, reverse in matches}
            for i, j, reverse in matches:
                left, right = resample(source[i]), resample(raw[j])
                if reverse:
                    right = right[::-1]
                base = apply_affine(left, style["matrix"])
                length = float(np.linalg.norm(np.diff(base, axis=0), axis=1).sum())
                if length < .025:
                    continue
                features.append(descriptor(left))
                targets.append(((right - base) / length).ravel())
                learned_chars.add(char)
            # 整个已录字可用作其他字的部件，即使它的个人合笔数与标准结构不同。
            groups = [(char, list(range(len(source))))] + component_groups(char)
            for symbol, indices in groups:
                if symbol == char and len(indices) == len(source):
                    handwriting = raw
                elif all(index in by_index for index in indices):
                    targets_in_order = sorted(by_index[index][0] for index in indices)
                    if len(set(targets_in_order)) != len(targets_in_order):
                        continue
                    handwriting = [raw[index] for index in targets_in_order]
                else:
                    continue
                if len(indices) < 2:
                    continue
                canonical = [source[index].tolist() for index in indices]
                # 小部件的错配会被放大；从字中抽取时必须与实际位置可靠对应。
                if symbol != char:
                    errors = [np.linalg.norm(apply_affine(resample(source[i]), style["matrix"]) - (resample(raw[by_index[i][0]])[::-1] if by_index[i][1] else resample(raw[by_index[i][0]])), axis=1).mean() for i in indices]
                    if float(np.mean(errors)) > .065:
                        continue
                components.setdefault(symbol, []).append({"character": char, "version": version_index, "symbol": symbol, "canonical": canonical, "strokes": handwriting})
    if len(features) < 30:
        raise ValueError("可靠匹配的真实笔画不足30笔，请补录清晰的横、竖、撇、捺、折、钩和不同结构的字。")
    x, y = np.asarray(features), np.asarray(targets)
    width, ridge = .4, 1.
    kernel = np.exp(-np.maximum(((x[:, None] - x[None, :]) ** 2).sum(axis=2), 0) / (2 * width * width))
    alpha = np.linalg.solve(kernel + ridge * np.eye(len(kernel)), y)
    return {"schema": 2, "algorithm": "personal-stroke-context-constraints-v2", "matrix": style["matrix"].tolist(), "features": x.tolist(), "weights": alpha.tolist(), "width": width, "components": components, "training_characters": sorted(learned_chars), "training_strokes": len(features), "recorded_characters": len(glyphs)}


def stroke_positions(path, anchors=()):
    original = np.asarray(path)
    distance = np.r_[0, np.cumsum(np.linalg.norm(np.diff(original, axis=0), axis=1))]
    return np.unique(np.r_[np.linspace(0, 1, SAMPLES), distance / distance[-1], anchors])


def smooth_residual(points):
    # 只平滑迁移的位移，不平滑目标结构，保留折角并去掉录字微抖动。
    kernel = np.array([1., 2., 3., 2., 1.]) / 9
    return np.stack([np.convolve(np.pad(points[:, axis], (2, 2), mode="edge"), kernel, mode="valid") for axis in range(2)], axis=1)


def bounded_residual(base, residual, fraction):
    length = float(np.linalg.norm(np.diff(base, axis=0), axis=1).sum())
    maximum = float(np.linalg.norm(residual, axis=1).max())
    # 短点不能继承独体字长捺的位移，复杂字的细小结构也不能被大残差冲散。
    return residual * min(1, min(.026, length * fraction) / max(maximum, 1e-9))


def adapt_recorded_stroke(canonical, recorded, target, matrix, anchors=()):
    """逐笔适配部件的上下文写法，避免完整独体字统一仿射造成错位。

    目标结构提供长短和折点，真实录字提供非线性笔形；阻尼拟合限制细长笔画
    的病态剪切。相接位置由整字约束统一处理，不在此处逐笔强行接到其他笔。
    """
    source, handwriting, goal = resample(canonical), resample(recorded), resample(target)
    aa, bb = source - source.mean(axis=0), goal - goal.mean(axis=0)
    source_length = float(np.linalg.norm(np.diff(source, axis=0), axis=1).sum())
    target_length = float(np.linalg.norm(np.diff(goal, axis=0), axis=1).sum())
    prior = np.eye(2) * target_length / max(source_length, 1e-9)
    transform = np.linalg.solve(aa.T @ aa + .0015 * np.eye(2), aa.T @ bb + .0015 * prior)
    residual = smooth_residual((handwriting - source) @ transform) @ matrix[:2]
    residual = bounded_residual(apply_affine(goal, matrix), residual, .18)
    positions = stroke_positions(target, anchors)
    transferred = np.stack([np.interp(positions, np.linspace(0, 1, SAMPLES), residual[:, axis]) for axis in range(2)], axis=1)
    return apply_affine(resample(target, positions), matrix) + transferred


def predicted_stroke(path, model, anchors=()):
    points = resample(path)
    base = apply_affine(points, np.asarray(model["matrix"]))
    features, weights = np.asarray(model["features"]), np.asarray(model["weights"])
    distance = ((features - descriptor(points)) ** 2).sum(axis=1)
    kernel = np.exp(-distance / (2 * model["width"] ** 2))
    length = float(np.linalg.norm(np.diff(base, axis=0), axis=1).sum())
    residual = (kernel @ weights).reshape(SAMPLES, 2) * length
    residual = bounded_residual(base, smooth_residual(residual), .12)
    # 补回原折点，避免等弧长采样把转角切成直线或漏掉共享接点。
    original = np.asarray(path)
    positions = stroke_positions(original, anchors)
    base = apply_affine(resample(original, positions), np.asarray(model["matrix"]))
    transfer = np.stack([np.interp(positions, np.linspace(0, 1, SAMPLES), residual[:, axis]) for axis in range(2)], axis=1)
    return base + transfer, float(kernel.max())


def component_strokes(candidates, target, matrix, version=0, anchors=None):
    """将真实部件放入新字的结构位置；个人折钩、合笔和每笔内部形状来自原录字。"""
    eligible = [item for item in candidates if len(item["canonical"]) == len(target)]
    if not eligible:
        return None
    destination = np.concatenate([resample(path) for path in target])
    ranked = []
    for item in eligible:
        source = np.concatenate([resample(path) for path in item["canonical"]])
        design = np.c_[source, np.ones(len(source))]
        transform = np.linalg.lstsq(design, destination, rcond=None)[0]
        singular = np.linalg.svd(transform[:2], compute_uv=False)
        if np.linalg.det(transform[:2]) <= 0 or singular.min() < .05 or singular.max() / singular.min() > 6:
            continue
        error = float(np.linalg.norm(design @ transform - destination, axis=1).mean())
        rank = error + (0 if item["character"] == item.get("symbol") else .025)
        ranked.append((rank, error, item, transform))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (round(item[0], 8), item[2]["character"], item[2]["version"]))
    candidates = [item for item in ranked if item[0] <= ranked[0][0] + .01]
    rank, error, source, transform = candidates[version % len(candidates)]
    if error > .065:
        return None
    matrix = np.asarray(matrix)
    inverse = np.linalg.inv(np.vstack([matrix.T, [0, 0, 1]]))
    canonical = [np.asarray(path) for path in source["canonical"]]
    handwriting = [(np.c_[path, np.ones(len(path))] @ inverse.T)[:, :2] for path in source["strokes"]]
    matches = paired_indices(canonical, handwriting)
    if len(handwriting) == len(canonical) and len(matches) == len(canonical):
        mapping = {i: (j, reverse) for i, j, reverse in matches}
        output = []
        for index, path in enumerate(target):
            j, reverse = mapping[index]
            raw = handwriting[j][::-1] if reverse else handwriting[j]
            contacts = contact_positions(target, index) if anchors is None else anchors[index]
            output.append(adapt_recorded_stroke(canonical[index], raw, path, matrix, contacts))
        return output, source["character"]
    if len(handwriting) == len(canonical):
        # 不能可靠逐笔对应的部件不强行套用，交给已学习的笔形模型。
        return None
    output = []
    for path in source["strokes"]:
        original = np.c_[path, np.ones(len(path))] @ inverse.T
        placed = apply_affine(original[:, :2], transform)
        output.append(apply_affine(placed, np.asarray(matrix)))
    return output, source["character"]


def constrain_junctions(paths, output, mapping):
    """让来自不同录字/回归结果的笔画共享原结构接点，同时平滑邻域。

    mapping仅包含保持一笔对应一笔的轨迹。个人合笔部件继续保留原始分段，
    不为凑标准笔数拆开，也不把原本分离的两笔八字脚粘在一起。
    """
    positions = {i: stroke_positions(paths[i], contact_positions(paths, i)) for i in mapping}
    nodes, prepared = {}, {}
    for index, target_index in mapping.items():
        local = positions[index]
        prepared[index] = output[target_index].copy()
        for t in contact_positions(paths, index):
            point = resample(paths[index], [t])[0]
            key = tuple(np.round(point, 7))
            vertex = int(np.argmin(np.abs(local - t)))
            nodes.setdefault(key, []).append((index, vertex))
    changes = {i: [] for i in mapping}
    count = 0
    for vertices in nodes.values():
        if len(vertices) < 2:
            continue
        location = np.mean([prepared[i][vertex] for i, vertex in vertices], axis=0)
        for index, vertex in vertices:
            changes[index].append((vertex, location - prepared[index][vertex]))
        count += 1
    for index, target_index in mapping.items():
        points = prepared[index].copy()
        if changes[index]:
            t = positions[index]
            # 先平滑分配接点修正，再精确设置共享点，不能只挪一个点制造尖刺。
            weight = np.stack([np.maximum(0, 1 - np.abs(t - t[vertex]) / .13) ** 2 for vertex, delta in changes[index]], axis=1)
            delta = np.stack([delta for vertex, delta in changes[index]])
            points += (weight @ delta) / np.maximum(1, weight.sum(axis=1))[:, None]
            for vertex, delta in changes[index]:
                points[vertex] = prepared[index][vertex] + delta
        output[target_index] = points
    return count


def generate_personal_glyph(character, model, version=0):
    paths = reference_strokes(character)
    if paths is None:
        raise ValueError("结构字库缺字：" + character)
    groups = component_groups(character)
    covered, replacements, sources, component_indices = set(), {}, [], {}
    # 完整已学部件优先，避免把真实“子”的两笔合笔再次拆成标准三笔。
    for symbol, indices in sorted(groups, key=lambda item: -len(item[1])):
        if any(index in covered for index in indices):
            continue
        found = component_strokes(model["components"].get(symbol, []), [paths[index] for index in indices], model["matrix"], version, [contact_positions(paths, i) for i in indices])
        if found is not None:
            replacements[min(indices)] = found[0]
            if len(found[0]) == len(indices):
                component_indices[min(indices)] = indices
            covered.update(indices)
            sources.append({"component": symbol, "recorded_character": found[1], "structural_strokes": len(indices), "personal_strokes": len(found[0])})
    output, confidence, origins, learned_indices = [], [], [], {}
    for index, path in enumerate(paths):
        if index in replacements:
            for offset, original_index in enumerate(component_indices.get(index, [])):
                learned_indices[original_index] = len(output) + offset
            output.extend(replacements[index])
            origins.extend(["component"] * len(replacements[index]))
        elif index not in covered:
            predicted, score = predicted_stroke(path, model, contact_positions(paths, index))
            learned_indices[index] = len(output)
            output.append(predicted)
            origins.append("learned")
            confidence.append(score)
    junctions = constrain_junctions(paths, output, learned_indices)
    # 整字共同收纳，不逐点截断。学习后的真实笔形不再强制拉回原楷体端点。
    points = np.concatenate(output)
    low, size = glyph_bounds(output)
    if np.any(points < .01) or np.any(points > .99):
        scale = min(1, .96 / float(size.max()))
        high = low + size
        shift = np.maximum(.02 - low * scale, 0) + np.minimum(.98 - high * scale, 0)
        output = [path * scale + shift for path in output]
    details = {"components": sources, "reused_strokes": origins.count("component"), "learned_strokes": origins.count("learned"), "minimum_similarity": min(confidence, default=1.), "constrained_junctions": junctions, "algorithm": model["algorithm"]}
    return [path.tolist() for path in output], details


def model_fingerprint(glyphs):
    return hashlib.sha256(json.dumps(glyphs, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
