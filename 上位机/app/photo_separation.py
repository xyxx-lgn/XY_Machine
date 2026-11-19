"""沿字间空白分离邻字，保留非矩形边界内的笔迹。"""

import cv2
import numpy as np

from app.generation import checkpoint


def isolate_line_band(pixels, ink, cancel=None):
    """沿行间空白隔离边缘墨迹，保持行坐标；疑似切分必须对照原图。

    OCR偶尔把下一行上半部包进当前行框。只处理横向长行，不在单字内部按横向空隙删笔。
    返回的提示要求对照原图；无法确认的连写部件保留给人工处理。
    """
    height, width = ink.shape
    if width < height * 4 or height < 24 or not np.any(ink):
        return pixels, ink, False
    density = np.sum(ink, axis=1)
    blank = density <= max(1, width * .004)
    transitions = np.diff(np.r_[False, blank, False].astype(np.int8))
    gaps = [(int(a), int(b)) for a, b in zip(np.flatnonzero(transitions == 1), np.flatnonzero(transitions == -1))
            if b - a >= max(2, round(height * .025))]
    top, bottom = 0, height
    total = float(density.sum())
    for a, b in gaps:
        middle = (a + b) / 2
        if height * .12 <= middle <= height * .30 and density[:a].sum() < total * .25:
            top = b
        if height * .68 <= middle <= height * .88 and density[b:].sum() < total * .25:
            bottom = min(bottom, a)
    rows = np.arange(height)[:, None]
    band = np.broadcast_to((rows >= top) & (rows < bottom), ink.shape).copy()
    curved_boundary = False
    # 斜拍时行间空白并非水平线；仅在行框边缘确有大段墨迹时寻找弯曲空白边界。
    edge = max(2, round(height * .08))
    for upper in (True, False):
        if float(np.mean(density[:edge] if upper else density[-edge:])) < width * .12:
            continue
        center, lo, hi = (.18, .06, .32) if upper else (.73, .58, .88)
        seam = blank_seam(ink.T, height * center, height * lo, height * hi, cancel)
        if float(np.mean(ink[seam, np.arange(width)])) > .03:
            continue
        proposal = band & (rows >= seam[None, :] if upper else rows < seam[None, :])
        removed_mass = np.count_nonzero(ink & ~proposal)
        if 0 < removed_mass < total * .45:
            band = proposal
            curved_boundary = True
    if band.all():
        return pixels, ink, False
    count, labels = cv2.connectedComponents(ink.astype(np.uint8), 8)
    keep = np.zeros(count, bool)
    keep[np.unique(labels[band])] = True
    keep[0] = False
    # 明确的斜行间隔允许切开少量连笔，否则一条压在格线上的连笔会带回整行。
    retained = (ink > 0) & band if curved_boundary else keep[labels]
    removed = (ink > 0) & ~retained
    if not np.any(removed):
        return pixels, ink, False
    result = pixels.copy()
    result[removed] = 255
    preserved = cv2.dilate(retained.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    result[~band & ~preserved] = 255
    return result, (ink > 0) & ~removed, True


def blank_seam(ink, center, left, right, cancel=None, prefer_blank=False):
    """在两字符之间寻找随行变化的低墨迹边界，而非一刀切竖直矩形。

    边界每行最多移动2像素，围绕候选切点搜索；惩罚穿过墨迹及大幅偏离字间位置。
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
    # 短字串在扩大搜索后优先保全墨迹，避免为了少绕一点路而横切一笔长撇。
    crossing_cost = height * 4 + 15 if prefer_blank else 15
    depth_cost = height + 4 if prefer_blank else 4
    cost = ink[:, lo:hi + 1] * crossing_cost + depth[:, lo:hi + 1] * depth_cost + abs(coordinates - center)[None, :] / max(2, (hi - lo) / 2)
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
        short_line = len(locations) <= 4
        pitch = max(b - a, width / len(locations) * .8) if short_line else b - a
        radius = min(pitch * .38, height * .38)
        if 0 < center < width and not np.any(ink[:, center]):
            seams.append(np.full(height, center, np.int32))
        else:
            # 已复核的切点必须在搜索范围里，不能因CTC位置偏移再被强行推入偏旁。
            # 短姓名的CTC峰可能落在同一个偏旁附近，不能用过近的峰夹住弯曲分界。
            left = center - radius if short_line else min(center, max(a + (b - a) * .18, center - radius))
            right = center + radius if short_line else max(center, min(b - (b - a) * .18, center + radius))
            seams.append(blank_seam(ink, center, left, right, cancel, prefer_blank=short_line))
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
        if shares[winner] >= area * .9 and w < spacing * .95:
            rows, cols = np.nonzero(region & (owners != winner))
            intrusion = np.maximum(seams[winner][rows + y] - (cols + x), cols + x - seams[winner + 1][rows + y] + 1)
            if intrusion.max(initial=0) <= max(2, spacing * .08):
                owners[region] = winner
        # 完整保留的小部件不再算切断；只提示最终归属仍横跨两字的连笔。
        final_shares = np.bincount(owners[region], minlength=len(locations))
        crossed = np.flatnonzero(final_shares > max(2, area * .015))
        if len(crossed) > 1:
            touching.update(crossed.tolist())
    # 区分字间细连笔与深入主体的切口；细连笔还需独立单字复核后才能降为一般提醒。
    depth = cv2.distanceTransform(ink.astype(np.uint8), cv2.DIST_L2, 3)
    soft, hard = set(), set()
    rows = np.arange(height)
    for boundary in range(1, len(seams) - 1):
        neighbors = {boundary - 1, boundary}
        if not neighbors.issubset(touching):
            continue
        seam = np.clip(seams[boundary], 1, width - 1)
        crossed = ink[rows, seam] & ink[rows, seam - 1]
        if np.count_nonzero(crossed) <= max(3, height * .06) and depth[rows[crossed], seam[crossed]].max(initial=0) <= max(2.5, height * .018):
            soft.update(neighbors)
        else:
            hard.update(neighbors)
    for index in range(len(locations)):
        checkpoint(cancel)
        belongs = ownership == index
        active = np.flatnonzero(np.any(belongs & (ink > 0), axis=0))
        left, right = seams[index], np.maximum(seams[index] + 1, seams[index + 1])
        # 归属边界只是分字依据，不是字形外框。补少量白边避免完整保留的偏旁
        # 正好贴在矩形边上；扩展区仍按ownership置白，绝不重新带入邻字。
        padding = max(2, round(height * .05))
        x0 = max(0, min(int(left.min()), int(active.min()) if len(active) else width) - padding)
        x1 = min(width, max(int(right.max()), int(active.max()) + 1 if len(active) else 0) + padding)
        cell = pixels[:, x0:x1].copy()
        cell[~belongs[:, x0:x1]] = 255
        if reviews is not None:
            if index in touching:
                reviews.append("字间细连笔相连，边界穿过笔画；请复核分离后的字形。" if index in soft and index not in hard else
                               "字间墨迹相连或边界穿过笔画，可能裁到本字或带入邻字；请放大核对或重框。")
            else:
                reviews.append("")
        yield x0, x1, remove_tiny_fragments(cell)
