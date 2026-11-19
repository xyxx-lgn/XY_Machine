"""照片识字的透印过滤和独立单字复核；不修改照片中的真实笔迹。"""

import cv2
import numpy as np
import unicodedata

from app.generation import checkpoint
from app.photo_samples import crop_photo, image_pixels, paper_gray


def chinese_character(value):
    return len(value) == 1 and "\u3400" <= value <= "\u9fff"


def same_label(a, b):
    return unicodedata.normalize("NFKC", a).strip() == unicodedata.normalize("NFKC", b).strip()


def faint_line_indices(gray, lines):
    """比较同一页的实际墨色，不能逐框自动拉黑后再判断透印。

    仅隐藏相对正文极淡且识字不可靠的行；整页都是淡笔迹时不删行。
    用户可关闭过滤重提取，原图始终保留。
    """
    contrasts = []
    rgb = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
    for line in lines:
        patch = crop_photo(rgb, np.asarray(line[0], np.float32))[:, :, 0]
        contrasts.append(float(np.percentile(255 - patch, 90)))
    reference = float(np.percentile(contrasts, 75)) if contrasts else 0.0
    if reference < 75:
        return set()
    return {index for index, (line, contrast) in enumerate(zip(lines, contrasts))
            if contrast < reference * .38 and float(line[2]) < .8}


def position_warnings(locations):
    gaps = [b[0] - a[0] for a, b in zip(locations, locations[1:])
            if chinese_character(a[1]) and chinese_character(b[1])]
    if len(gaps) < 4:
        return set()
    pitch = float(np.median(gaps))
    suspects = set()
    for index, (a, b) in enumerate(zip(locations, locations[1:])):
        if chinese_character(a[1]) and chinese_character(b[1]) and not pitch * .53 <= b[0] - a[0] <= pitch * 1.85:
            suspects.update((index, index + 1))
    return suspects


def align_character_positions(pixels, locations, recognizer, cancel=None):
    """CTC出字位置常偏向字的右侧，不能直接当墨迹中心。

    用分散在整行的最多8个汉字比较三个行级偏移，只有独立识字明显改善时才移动。
    偏移整行共用，不逐字扭曲字形；最终仍从原照片沿空白分割。
    """
    candidates = [i for i, (_, char, score) in enumerate(locations) if chinese_character(char) and score >= .80]
    gaps = [b[0] - a[0] for a, b in zip(locations, locations[1:])
            if chinese_character(a[1]) and chinese_character(b[1])]
    if len(candidates) < 2 or not gaps:
        return locations
    indices = [candidates[i] for i in np.unique(np.rint(np.linspace(0, len(candidates) - 1, min(8, len(candidates)))).astype(int))]
    pitch = float(np.median(gaps))
    gray = paper_gray(pixels)
    shifts, images = (0.0, -.15, -.30), []
    for shift in shifts:
        cuts = [0] + [(a[0] + b[0]) / 2 + shift * pitch for a, b in zip(locations, locations[1:])] + [pixels.shape[1]]
        for index in indices:
            checkpoint(cancel)
            x0, x1 = max(0, round(cuts[index])), min(pixels.shape[1], round(cuts[index + 1]))
            if x1 <= x0:
                return locations
            cell = cv2.copyMakeBorder(gray[:, x0:x1], 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=255)
            images.append(cv2.cvtColor(cell, cv2.COLOR_GRAY2BGR))
    results, _ = recognizer(images)
    checkpoint(cancel)
    if len(results) != len(images):
        raise ValueError("行内对齐复核数量不符，请重新提取。")
    scores, matches = [], []
    for offset in range(len(shifts)):
        group = results[offset * len(indices):(offset + 1) * len(indices)]
        matches.append(sum(result[0] == locations[index][1] for index, result in zip(indices, group)))
        scores.append(sum(float(result[1]) ** 2 if result[0] == locations[index][1] else -.05 for index, result in zip(indices, group)))
    best = int(np.argmax(scores))
    if scores[best] < scores[0] + .6 or (matches[best] < matches[0] + min(2, len(indices)) and scores[best] < scores[0] + 1):
        return locations
    return [(center + shifts[best] * pitch, char, confidence) for center, char, confidence in locations]


def refine_character_cuts(pixels, locations, cuts, recognizer, cancel=None):
    """对仍识不全的相邻汉字尝试左右移界，两个字都得到支持才采用。

    仅调整裁切，不改字符标签；不以某一侧识字变好为代价截掉另一侧的偏旁。
    一批比较所有候选，避免每个边界单独启动一次模型。
    """
    if len(locations) < 2 or any(b <= a for a, b in zip(cuts, cuts[1:])):
        return cuts
    selected = list(range(len(locations)))
    gray = paper_gray(pixels)
    level, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    ink = gray < min(225, level + 8)
    images = []
    def cell(left, right):
        margin = max(3, round(gray.shape[0] * .03))
        g = gray[:, left:right]
        rows, columns = np.nonzero(ink[:, left:right])
        if len(columns):
            g = g[max(0, rows.min() - margin):min(g.shape[0], rows.max() + margin + 1),
                  max(0, columns.min() - margin):min(g.shape[1], columns.max() + margin + 1)]
        g = cv2.copyMakeBorder(g, margin, margin, margin, margin, cv2.BORDER_CONSTANT, value=255)
        return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
    for i in selected:
        images.append(cell(cuts[i], cuts[i + 1]))
    baseline, _ = recognizer(images)
    checkpoint(cancel)
    if len(baseline) != len(selected):
        raise ValueError("初始分字复核数量不符，请重新提取。")
    baseline = dict(zip(selected, baseline))
    proposals, images = [], []
    density = np.sum(ink, axis=0)
    for boundary in range(1, len(cuts) - 1):
        a, b = boundary - 1, boundary
        left, right = baseline[a], baseline[b]
        if all(same_label(text, locations[i][1]) and score >= .90 for i, (text, score, *_) in ((a, left), (b, right))):
            continue
        pitch = locations[b][0] - locations[a][0]
        if pitch < 8:
            continue
        candidates = set()
        for shift in (-.5, -.3, -.15, .15, .3, .5):
            target = cuts[boundary] + shift * pitch
            low = max(cuts[a] + 4, round(target - pitch * .06))
            high = min(cuts[b + 1] - 4, round(target + pitch * .06))
            if high < low:
                continue
            coordinates = np.arange(low, high + 1)
            cost = density[low:high + 1] + abs(coordinates - target) * .05
            candidates.add(int(coordinates[np.argmin(cost)]))
        for candidate in sorted(candidates - {cuts[boundary]}):
            proposals.append((boundary, candidate))
            images.extend((cell(cuts[a], candidate), cell(candidate, cuts[b + 1])))
    if not proposals:
        return cuts
    results = []
    for first in range(0, len(images), 64):
        checkpoint(cancel)
        group, _ = recognizer(images[first:first + 64])
        if len(group) != len(images[first:first + 64]):
            raise ValueError("分字边界复核数量不符，请重新提取。")
        results.extend(group)
    checkpoint(cancel)
    if len(results) != len(images):
        raise ValueError("分字边界复核数量不符，请重新提取。")
    accepted = []
    for i, (boundary, candidate) in enumerate(proposals):
        pair = results[i * 2:i * 2 + 2]
        indices = (boundary - 1, boundary)
        if not all(same_label(result[0], locations[index][1]) and result[1] >= (.35 if unicodedata.category(locations[index][1]).startswith("P") else .60)
                   for index, result in zip(indices, pair)):
            continue
        # 原来已稳定识出的邻字也需保持质量，不能以损坏邻字换取另一侧的改善。
        if any(same_label(baseline[index][0], locations[index][1]) and baseline[index][1] >= .90 and result[1] < baseline[index][1] - .12
               for index, result in zip(indices, pair)):
            continue
        before = sum(float(baseline[index][1]) if same_label(baseline[index][0], locations[index][1]) else -.2 for index in indices)
        after = sum(float(result[1]) for result in pair)
        if after >= before + .30:
            accepted.append((after - before, boundary, candidate))
    result, used = list(cuts), set()
    for _, boundary, candidate in sorted(accepted, reverse=True):
        # 相邻提议共用一个字，不能把分别验证过的边界叠成未经验证的新裁框。
        if not any(b in used for b in (boundary - 1, boundary, boundary + 1)):
            result[boundary] = candidate
            used.add(boundary)
    return result


def verify_photo_labels(samples, recognizer, cancel=None, progress=None):
    """行识字与另一模型单字识别交叉检查，分歧留给用户核对，不盲目改标签。

    按24字分批可取消；单字识别也可能错，建议不是正确性保证。
    这里不重新生成、拼接或替换任何轨迹。
    """
    selected = [s for s in samples if s.original is not None and not s.error]
    for first in range(0, len(selected), 24):
        checkpoint(cancel)
        batch = selected[first:first + 24]
        images = []
        for sample in batch:
            gray = paper_gray(image_pixels(sample.original))
            margin = max(3, round(min(gray.shape) * .05))
            gray = cv2.copyMakeBorder(gray, margin, margin, margin, margin, cv2.BORDER_CONSTANT, value=255)
            images.append(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR))
        results, _ = recognizer(images)
        checkpoint(cancel)
        if len(results) != len(batch):
            raise ValueError("单字复核数量与候选不符，请重新提取。")
        for sample, (text, score, *_) in zip(batch, results):
            score = float(score)
            message = ""
            if same_label(text, sample.character) and score >= .90 and "字间细连笔相连" in sample.note:
                sample.note = sample.note.replace("字间细连笔相连，边界穿过笔画；请复核分离后的字形。", "细连笔分离已复核，仍请对照原图核对字形。")
            if not same_label(text, sample.character) and score >= .85 and len(text) == 1 and unicodedata.category(text)[0] in "LN":
                sample.label_suggestion, sample.label_score = text, score
                message = f"识字复核分歧：行识字“{sample.character}”，单字建议“{text}”（{score:.2f}），请对照原图"
            elif score >= .60 and len(text) > 1 and all(unicodedata.category(c)[0] in "LN" for c in text):
                message = f"识字复核发现框内可能有多个字“{text}”，请检查裁切"
            elif chinese_character(sample.character) and sample.character not in "一二三丿丨丶":
                if not same_label(text, sample.character) and score >= .45:
                    message = f"识字复核存在分歧（单字读作“{text}”，{score:.2f}），请核对标签与裁切"
                elif score < .55:
                    message = "识字复核无法稳定确认单字，可能裁切不完整，请对照原图"
            if message:
                sample.source_flags = tuple(dict.fromkeys((*sample.source_flags, message)))
                sample.note += " " + message
        if progress:
            progress(min(first + len(batch), len(selected)), len(selected))
