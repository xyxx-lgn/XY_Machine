"""沿字间空白分离邻字，保留非矩形边界内的笔迹。"""

import cv2
import numpy as np

from app.generation import checkpoint


def blank_seam(ink, center, left, right, cancel=None):
    """在两字符之间寻找随行变化的低墨迹边界，而非一刀切竖直矩形。

    边界每行最多移动2像素，限制在识字位置之间；惩罚穿过墨迹及大幅偏离字间位置。
    无法从连写墨迹确定归属时仍可能分错，所以另提供单字擦除与重框。
    """
    height, width = ink.shape
    ink = (ink > 0).astype(np.float32)
    lo, hi = max(1, int(left)), min(width - 1, int(right))
    if hi <= lo:
        return np.full(height, min(width - 1, max(1, round(center))), np.int32)
    coordinates = np.arange(lo, hi + 1)
    # 穿过粗笔画比擦过边缘代价更高，避免沿笔画内部走一条貌似低密度的边界。
    depth = cv2.distanceTransform(np.pad(ink.astype(np.uint8), 1), cv2.DIST_L2, 3)[1:-1, 1:-1]
    cost = ink[:, lo:hi + 1] * 15 + depth[:, lo:hi + 1] * 4 + abs(coordinates - center)[None, :] / max(2, (hi - lo) / 2)
    previous = cost[0].copy()
    parents = np.zeros((height, len(coordinates)), np.int16)
    for y in range(1, height):
        if y % 64 == 0:
            checkpoint(cancel)
        choices = np.full((5, len(coordinates)), np.inf)
        for index, offset in enumerate(range(-2, 3)):
            if offset < 0:
                choices[index, -offset:] = previous[:offset] + abs(offset) * .12
            elif offset > 0:
                choices[index, :-offset] = previous[offset:] + abs(offset) * .12
            else:
                choices[index] = previous
        best = np.argmin(choices, axis=0)
        parents[y] = np.arange(len(coordinates)) + best - 2
        previous = cost[y] + choices[best, np.arange(len(coordinates))]
    current = int(np.argmin(previous))
    seam = np.empty(height, np.int32)
    for y in range(height - 1, -1, -1):
        seam[y] = coordinates[current]
        current = int(parents[y, current]) if y else current
    return seam


def remove_tiny_fragments(cell):
    # 仅去掉远小于主要墨迹、位于边缘的独立碎点；不采用“只留最大块”，以免删掉偏旁和点。
    gray = cv2.cvtColor(cell, cv2.COLOR_RGB2GRAY)
    level, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    count, labels, stats, _ = cv2.connectedComponentsWithStats((gray < min(225, level + 8)).astype(np.uint8), 8)
    if count <= 2:
        return cell
    limit = min(8, stats[1:, cv2.CC_STAT_AREA].max() * .006)
    width = cell.shape[1]
    for index in range(1, count):
        x, _, w, _, area = stats[index]
        if area < limit and (x < width * .2 or x + w > width * .8):
            cell[labels == index] = 255
    return cell


def separate_line_cells(pixels, ink, locations, cuts, cancel=None, reviews=None):
    height, width = ink.shape
    seams = [np.zeros(height, np.int32)]
    for index in range(len(locations) - 1):
        a, b = locations[index][0], locations[index + 1][0]
        center = cuts[index + 1]
        radius = min((b - a) * .38, height * .38)
        seams.append(blank_seam(ink, center, max(a + (b - a) * .18, center - radius), min(b - (b - a) * .18, center + radius), cancel))
    seams.append(np.full(height, width, np.int32))
    ownership = np.zeros((height, width), np.int16)
    columns = np.arange(width)[None, :]
    for index in range(len(locations)):
        ownership[(columns >= seams[index][:, None]) & (columns < seams[index + 1][:, None])] = index
    # 小墨迹块只擦过边界时整体保留；深入邻字的细连笔不能凭面积占比全部吞入本字。
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), 8)
    spacing = np.median(np.diff([item[0] for item in locations])) if len(locations) > 1 else width
    touching = set()
    for label in range(1, count):
        checkpoint(cancel)
        x, y, w, h, area = stats[label]
        region = labels[y:y + h, x:x + w] == label
        owners = ownership[y:y + h, x:x + w]
        shares = np.bincount(owners[region], minlength=len(locations))
        winner = int(shares.argmax())
        # 先记下跨界，再考虑整体归属；即使只有少量邻字笔画粘入，也不能静默认定分对了。
        crossed = np.flatnonzero(shares > max(2, area * .015))
        if len(crossed) > 1:
            touching.update(crossed.tolist())
        if shares[winner] >= area * .9 and w < spacing * .95:
            rows, cols = np.nonzero(region & (owners != winner))
            intrusion = np.maximum(seams[winner][rows + y] - (cols + x), cols + x - seams[winner + 1][rows + y] + 1)
            if intrusion.max(initial=0) <= max(2, spacing * .08):
                owners[region] = winner
    for index in range(len(locations)):
        checkpoint(cancel)
        belongs = ownership == index
        active = np.flatnonzero(np.any(belongs & (ink > 0), axis=0))
        left, right = seams[index], np.maximum(seams[index] + 1, seams[index + 1])
        x0 = max(0, min(int(left.min()), int(active.min()) if len(active) else width))
        x1 = min(width, max(int(right.max()), int(active.max()) + 1 if len(active) else 0))
        cell = pixels[:, x0:x1].copy()
        cell[~belongs[:, x0:x1]] = 255
        if reviews is not None:
            reviews.append("字间墨迹相连或边界穿过笔画，可能裁到本字或带入邻字；请放大核对或重框。" if index in touching else "")
        yield x0, x1, remove_tiny_fragments(cell)
