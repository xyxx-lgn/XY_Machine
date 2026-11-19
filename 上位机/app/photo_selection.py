"""照片候选的重复字择优；不替换已收录批次和原字库。"""

from collections import defaultdict
from dataclasses import dataclass

import cv2
import numpy as np

from app.generation import GenerationError


@dataclass(frozen=True)
class PhotoSelection:
    indices: tuple
    duplicates: int = 0
    overflow: int = 0
    invalid: int = 0

    def summary(self):
        return f"选用{len(self.indices)}版；跳过近似重复{self.duplicates}版、超过每字配额{self.overflow}版、无效{self.invalid}版。"


def stroke_mask(strokes, advance=1.0):
    mask = np.zeros((96, 96), np.uint8)
    for path in strokes:
        points = np.rint(np.asarray(path, dtype=float) * [95 / advance, 95]).astype(np.int32)
        cv2.polylines(mask, [points], False, 1, 2)
    return mask.astype(bool)


def mask_distance(a, b):
    total = np.count_nonzero(a) + np.count_nonzero(b)
    return float(np.count_nonzero(a ^ b) / total) if total else 0.0


def choose_photo_variants(samples, existing=(), base_package=None, max_variants=5):
    """每字先选质量较好的版本，再在相近质量中保留不同写法。

    原库和已收录批次只占配额、参与去重，绝不被新照片静默替换。
    返回原候选索引，收录与勾选使用同一策略，避免常用字超16版阻断整批。
    """
    if type(max_variants) is not int or not 1 <= max_variants <= 16:
        raise GenerationError("每字保留版本数应为1～16。")
    retained, groups = defaultdict(list), defaultdict(list)
    if base_package:
        for char, glyph in base_package.glyphs.items():
            retained[char].extend(stroke_mask(v["strokes"], glyph["advance"]) for v in glyph["variants"])
    for sample in existing:
        retained[sample.character].append(stroke_mask(sample.strokes))
    invalid = 0
    for index, sample in enumerate(samples):
        if sample.error or not sample.strokes:
            invalid += 1
            continue
        # 笔迹完整度和裁切优先于识字分数，高识字分数不等于字形完整。
        quality = (0 if sample.quality_blocking else 1) + sample.quality_score + sample.quality_coverage * .25
        quality += (sample.confidence if sample.confidence is not None else 1) * .10
        groups[sample.character].append((index, quality, stroke_mask(sample.strokes)))
    selected, duplicates, overflow = [], 0, 0
    for char, items in groups.items():
        items.sort(key=lambda item: (-item[1], item[0]))
        kept = retained[char]
        while items:
            if len(kept) >= max_variants:
                for _, _, mask in items:
                    if any(mask_distance(mask, m) < .015 for m in kept):
                        duplicates += 1
                    else:
                        overflow += 1
                break
            # 在质量差不超过0.08的候选中优先保留不同笔迹，避免只留同一种写法。
            top = items[0][1]
            pool = [i for i, item in enumerate(items) if item[1] >= top - .08]
            pick = max(pool, key=lambda i: min((mask_distance(items[i][2], m) for m in kept), default=1.0))
            index, _, mask = items.pop(pick)
            if any(mask_distance(mask, m) < .015 for m in kept):
                duplicates += 1
            elif len(kept) >= max_variants:
                overflow += 1
            else:
                selected.append(index)
                kept.append(mask)
    return PhotoSelection(tuple(sorted(selected)), duplicates, overflow, invalid)
