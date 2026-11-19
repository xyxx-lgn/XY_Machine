"""照片字库的整批检查与校准；识字分数不代替墨迹和轨迹完整度。"""

from collections import defaultdict
from dataclasses import dataclass, replace
import math
import unicodedata

import cv2
import numpy as np
from PySide6.QtGui import QImage

from app.generation import GenerationError, checkpoint, thin_mask
from app.photo_samples import gray_image, trace_sample


@dataclass(frozen=True)
class SampleQuality:
    index: int
    ink_width: float = 0.0
    ink_height: float = 0.0
    top: float = 0.0
    bottom: float = 0.0
    center_x: float = 0.5
    density: float = 0.0
    score: float = 0.0
    flags: tuple = ()
    blocking: bool = True
    coverage: float = 1.0
    missing_parts: int = 0


@dataclass(frozen=True)
class PhotoQualityReport:
    items: tuple
    median_height: float = 0.0
    median_width: float = 0.0
    median_bottom: float = 0.0
    valid_count: int = 0

    @property
    def blocking_count(self):
        return sum(item.blocking for item in self.items)

    @property
    def warning_count(self):
        return sum(bool(item.flags) and not item.blocking for item in self.items)

    def summary(self):
        return (f"共{len(self.items)}个样本；重点核对{self.blocking_count}项，"
                f"一般提醒{self.warning_count}项。自动检查不能确认错字或真实笔顺。")


def gray_pixels(image):
    if image is None or image.isNull():
        return None
    gray = image.convertToFormat(QImage.Format.Format_Grayscale8)
    return np.frombuffer(gray.constBits(), np.uint8).reshape(gray.height(), gray.bytesPerLine())[:, :gray.width()].copy()


def sample_metrics(sample):
    pixels = gray_pixels(sample.image)
    if pixels is None:
        return None
    mask = pixels < 128
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return None
    h, w = pixels.shape
    bounds = (float(xs.min() / max(1, w - 1)), float(ys.min() / max(1, h - 1)),
              float(xs.max() / max(1, w - 1)), float(ys.max() / max(1, h - 1)))
    return dict(mask=mask, bounds=bounds, width=bounds[2] - bounds[0], height=bounds[3] - bounds[1],
                center=(bounds[0] + bounds[2]) / 2, density=float(mask.mean()))


def full_body(character):
    # 横画字和标点天然矮，不能为了“等高”把一、二、逗号放大到整字高度。
    return unicodedata.category(character)[0] in "LN" and character not in "一二三丿乀丨亅丶乁乚"


def trajectory_integrity(sample, metric, cancel=None):
    """检查黑白墨迹骨架到输出路径的距离，发现漏掉一边或整个独立部件。"""
    mask = metric["mask"]
    painted = np.zeros(mask.shape, np.uint8)
    for path in sample.strokes:
        points = np.rint(np.asarray(path, dtype=float) * [mask.shape[1] - 1, mask.shape[0] - 1]).astype(np.int32)
        cv2.polylines(painted, [points], False, 255, 1)
    if not np.any(painted):
        return 0.0, 0
    skeleton = thin_mask(mask.astype(np.uint8), cancel)
    distance = cv2.distanceTransform(255 - painted, cv2.DIST_L2, 5)
    covered = distance <= 3
    coverage = float(np.mean(covered[skeleton > 0])) if np.any(skeleton) else 1.0
    # 整体覆盖率可能掩盖一个小点或独立偏旁，单独检查有足够面积的墨迹部件。
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    missing = 0
    part_limit = max(10, mask.sum() * .005)
    for label in range(1, count):
        if stats[label, cv2.CC_STAT_AREA] < part_limit:
            continue
        part = (labels == label) & (skeleton > 0)
        if np.any(part) and float(np.mean(covered[part])) < .65:
            missing += 1
    return coverage, missing


def _crop_flags(sample, metric):
    flags = list(sample.source_flags)
    if len(sample.source_frame) == 4 and sample.original is not None:
        ox, oy, sx, sy = sample.source_frame
        l, t, r, b = metric["bounds"]
        w, h = sample.original.width(), sample.original.height()
        # 样本四周的白边不代表裁切完整，应逆映射回原始单字框。
        if sx > 0 and sy > 0 and (l <= ox + sx * .8 or t <= oy + sy * .8 or
                                 r >= ox + sx * (w - 1.8) or b >= oy + sy * (h - 1.8)):
            flags.append("墨迹贴裁切边，可能缺笔")
    elif min(metric["bounds"][:2]) < .008 or max(metric["bounds"][2:]) > .992:
        flags.append("墨迹贴裁切边，可能缺笔")
    return list(dict.fromkeys(flags))


def analyze_photo_samples(samples, cancel=None, progress=None, refresh_paths=True):
    """遍历每个样本；blocking只表示一键勾选时跳过，需要重点核对。

    覆盖率只检查黑白样本到路径，已经裁掉的部件仍需对照原照片。
    """
    samples = list(samples or [])
    raw = [sample_metrics(sample) for sample in samples]
    valid = [m for m in raw if m is not None]
    bodies = [m for s, m in zip(samples, raw) if m and full_body(s.character) and not s.error]
    peer_side = float(np.median([max(m["width"], m["height"]) for m in bodies])) if bodies else 0.0
    peer_bottom = float(np.median([m["bounds"][3] for m in bodies])) if bodies else 0.0
    items = []
    for index, (sample, metric) in enumerate(zip(samples, raw)):
        checkpoint(cancel)
        flags, blocking, score, coverage, missing = [], bool(sample.error), 1.0, 1.0, 0
        if sample.error:
            flags.append("提取失败")
            score = 0.0
        if metric is None:
            flags.append("黑白样本为空")
            blocking = not bool(sample.strokes) or bool(sample.error)
            items.append(SampleQuality(index, flags=tuple(flags), score=.0 if blocking else .5, blocking=blocking))
            continue
        flags.extend(_crop_flags(sample, metric))
        if flags:
            # 沿明显空白成功分离上下行属于处理记录；不能因此把整行的好字全部禁选。
            severe = any(not flag.startswith("行框已隔离") for flag in flags)
            score -= .25 if severe else .05
            blocking |= severe
        if metric["density"] > .45:
            flags.append("黑色面积过大")
            score -= .45
            blocking = True
        side = max(metric["height"], metric["width"])
        if len(bodies) >= 3 and full_body(sample.character):
            if side < peer_side * .65 or side > peer_side * 1.45:
                flags.append("字形尺寸偏离整批")
                score -= .15
            if side < peer_side * .3 or side > peer_side * 2.2:
                blocking = True
            if abs(metric["bounds"][3] - peer_bottom) > .18 and metric["height"] > side * .55:
                flags.append("基线偏移")
                score -= .10
        if metric["width"] > max(.4, metric["height"] * 1.85) and full_body(sample.character):
            flags.append("字框过宽，核对是否并入邻字")
            score -= .18
            blocking = True
        if sample.confidence is not None and sample.confidence < .85:
            flags.append("识字标签需核对")
            score -= .10
            blocking |= sample.confidence < .55
        if "边界穿过" in sample.note:
            flags.append("邻字粘连或边界穿笔")
            score -= .20
            blocking = True
        elif "细连笔分离已复核" in sample.note:
            flags.append("字间细连笔已分离并复核，仍需核对")
            score -= .05
        if not sample.strokes:
            flags.append("轨迹为空")
            coverage = 0.0
            blocking = True
            score = 0.0
        else:
            if refresh_paths or not sample.quality_checked:
                coverage, missing = trajectory_integrity(sample, metric, cancel)
            else:
                coverage, missing = sample.quality_coverage, sample.quality_missing_parts
            if coverage < .94:
                flags.append(f"轨迹覆盖不足{coverage:.0%}，可能漏笔")
                blocking = True
                score -= .4
            if missing:
                flags.append(f"{missing}个独立墨迹部件缺少轨迹")
                blocking = True
                score -= .25
            if len(sample.strokes) > 48:
                flags.append("碎线较多")
                score -= .10
        items.append(SampleQuality(index, metric["width"], metric["height"], metric["bounds"][1], metric["bounds"][3],
                                   metric["center"], metric["density"], max(0.0, score), tuple(flags), blocking, coverage, missing))
        if progress:
            progress(index + 1, len(samples))
    return PhotoQualityReport(tuple(items), float(np.median([m["height"] for m in valid])) if valid else 0.0,
                              float(np.median([m["width"] for m in valid])) if valid else 0.0,
                              peer_bottom, len(valid))


def annotate_photo_quality(samples, report=None, cancel=None, refresh_paths=True):
    report = report or analyze_photo_samples(samples, cancel, refresh_paths=refresh_paths)
    for sample, item in zip(samples, report.items):
        sample.quality_score, sample.quality_flags, sample.quality_blocking = item.score, item.flags, item.blocking
        sample.quality_checked = True
        sample.quality_coverage = item.coverage
        sample.quality_missing_parts = item.missing_parts
    return report


def _copy_sample(sample):
    return replace(sample, image=sample.image.copy(), strokes=[list(path) for path in sample.strokes])


def calibrate_photo_samples(samples, mode="natural", strength=.7, report=None, cancel=None):
    """按行共用纸面尺度和基线，保留窄字、横画字和标点的固有比例。

    有来源映射时先抵消逐字裁框造成的缩放差异。旧样本采用保守等比校正。
    始终从首次校准前的样本计算，重复预览不叠加变形，不编造已缺失的笔画。
    """
    if mode not in ("natural", "uniform", "raw"):
        raise GenerationError("照片批量校准方式无效。")
    if type(strength) not in (int, float) or not math.isfinite(strength) or not 0 <= strength <= 1:
        raise GenerationError("照片批量校准强度应为0～1。")
    inputs = list(samples or [])
    originals = [replace(sample.calibration_source, character=sample.character, confidence=sample.confidence)
                 if sample.calibration_source is not None else sample for sample in inputs]
    metrics = [sample_metrics(sample) for sample in originals]
    groups = defaultdict(list)
    for i, (sample, metric) in enumerate(zip(originals, metrics)):
        if metric is None or sample.error:
            continue
        source = len(sample.source_frame) == 4 and bool(sample.line_id)
        if source:
            _, oy, sx, sy = sample.source_frame
            values = (metric["width"] / sx, metric["height"] / sy, (metric["bounds"][3] - oy) / sy)
        else:
            values = (metric["width"], metric["height"], metric["bounds"][3])
        groups[sample.line_id if source else "legacy"].append((i, values))
    transforms = {}
    for members in groups.values():
        bodies = [(i, v) for i, v in members if full_body(originals[i].character)]
        if not bodies:
            continue
        references = bodies
        body_size = float(np.median([max(v[0], v[1]) for _, v in references]))
        baseline_refs = [v[2] for _, v in references if v[1] > max(v[0], v[1]) * .55]
        baseline = float(np.median(baseline_refs or [v[2] for _, v in references]))
        for i, values in members:
            sample, metric = originals[i], metrics[i]
            source = len(sample.source_frame) == 4 and bool(sample.line_id)
            sy = sample.source_frame[3] if source else 1.0
            target = .74 if source else body_size
            scale = target / max(1e-9, body_size) / sy
            if full_body(sample.character):
                adjustment = body_size / max(1e-9, max(values[:2]))
                amount = 1.0 if mode == "uniform" else strength
                scale *= max(.8, min(1.25, 1 + (adjustment - 1) * amount))
            target_bottom = .84 if source else baseline
            source_baseline = sample.source_frame[1] + baseline * sy if source else baseline
            tx = .5 - metric["center"] * scale
            ty = target_bottom - source_baseline * scale
            if not source and full_body(sample.character) and metric["height"] > max(metric["height"], metric["width"]) * .55:
                amount = 1.0 if mode == "uniform" else strength
                ty += (baseline - metric["bounds"][3]) * scale * amount
            transforms[i] = (scale, tx, ty)
    result = []
    for i, sample in enumerate(originals):
        checkpoint(cancel)
        copy = _copy_sample(sample)
        copy.calibration_source = sample
        if mode == "raw" or i not in transforms or not sample.strokes:
            result.append(copy)
            continue
        metric = metrics[i]
        scale, tx, ty = transforms[i]
        # 按墨迹和路径的联合外界收纳，保证不截去端头，不逐点截断变形。
        points = np.asarray([p for path in sample.strokes for p in path], dtype=float)
        bounds = metric["bounds"]
        lo = np.minimum(points.min(axis=0), bounds[:2]) * scale + [tx, ty]
        hi = np.maximum(points.max(axis=0), bounds[2:]) * scale + [tx, ty]
        fit = min(1.0, .92 / max(1e-9, float((hi - lo).max())))
        scale *= fit
        offset = np.array([tx, ty]) * fit
        lo, hi = lo * fit, hi * fit
        offset += np.maximum(0, .035 - lo) + np.minimum(0, .965 - hi)
        tx, ty = map(float, offset)
        copy.strokes = [[(float(x * scale + tx), float(y * scale + ty)) for x, y in path] for path in sample.strokes]
        pixels = gray_pixels(sample.image)
        h, w = pixels.shape
        matrix = np.float32([[scale, 0, tx * (w - 1)], [0, scale, ty * (h - 1)]])
        copy.image = gray_image(cv2.warpAffine(pixels, matrix, (w, h), flags=cv2.INTER_NEAREST, borderValue=255))
        if sample.trajectory == "ink":
            # 校准改变了字形尺度；按同一实际笔宽重做填线，不能继续缩放旧填线间距。
            try:
                copy.strokes = trace_sample(copy.character, copy.image, cancel, "ink", copy.ink_width).strokes
            except GenerationError as error:
                copy.error, copy.strokes = str(error), []
        if sample.source_frame:
            ox, oy, sx, sy = sample.source_frame
            copy.source_frame = (ox * scale + tx, oy * scale + ty, sx * scale, sy * scale)
        copy.source_flags = tuple(dict.fromkeys(_crop_flags(sample, metric)))
        copy.note = sample.note + " 已按整行共同尺度校准，保留原字比例。"
        result.append(copy)
    checked = annotate_photo_quality(result, cancel=cancel)
    return result, checked
