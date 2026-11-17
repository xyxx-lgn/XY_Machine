"""RapidOCR本地识字和单字定位；识字标签与原始照片轨迹始终分开。"""

from functools import lru_cache
import threading

import cv2
import numpy as np
from PySide6.QtGui import QImage

from app.generation import GenerationError, checkpoint
from app.photo_samples import PhotoSample, crop_photo, gray_image, image_pixels, normalize_cell, paper_gray, rgb_image, trace_sample
from app.photo_separation import separate_line_cells


OCR_LOCK = threading.Lock()


@lru_cache(maxsize=1)
def ocr_engine():
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        raise GenerationError("离线识字组件尚未准备，请运行上位机目录中的“准备照片识字.ps1”；原来的手工录字仍可使用。") from None
    # wheel已包含检测、方向和识别三个模型；不在识字期间下载模型或上传图片。
    return RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1, det_limit_side_len=1600, det_limit_type="max", max_side_len=2800)


def source_transform(region, cropped_width, cropped_height):
    if region is None:
        return np.eye(3, dtype=np.float32)
    points = np.asarray(region, dtype=np.float32)
    if points.shape == (4,):
        x0, y0, x1, y1 = points
        # 矩形裁切右下界不包含在像素里，最后一个像素需减一。
        points = np.float32([[x0, y0], [x1 - 1, y0], [x1 - 1, y1 - 1], [x0, y1 - 1]])
    return cv2.getPerspectiveTransform(np.float32([[0, 0], [cropped_width - 1, 0], [cropped_width - 1, cropped_height - 1], [0, cropped_height - 1]]), points)


def recognize_photo(image, region=None, threshold=210, remove_colored=True, processing="auto", adaptive=True, cancel=None, progress=None, engine=None, separate_neighbors=True, trajectory="continuous", ink_width=.03):
    """检测文本行并用CTC字位置和墨迹投影分字，不按整行等宽硬切。

    所有标签只是候选，界面默认不勾选；使用者必须核对错字、漏字及裁切。
    轨迹提取始终取照片里的墨迹，绝不用识别出的文字重新渲染字体代替本人笔迹。
    """
    checkpoint(cancel)
    if image.isNull() or image.width() * image.height() > 25_000_000:
        raise GenerationError("照片为空或超过2500万像素。")
    pixels = image_pixels(image)
    cropped = crop_photo(pixels, region)
    h, w = cropped.shape[:2]
    scale = min(1, 2800 / max(h, w))
    working = cv2.resize(cropped, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA) if scale < 1 else cropped.copy()
    gray = paper_gray(working, remove_colored, True)
    # 灰度仍保留细节供模型识字，不能用很低的手工阈值先抹掉笔画。
    ocr_pixels = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    if progress:
        progress(0, 0)
    with OCR_LOCK:
        checkpoint(cancel)
        runner = engine or ocr_engine()
        lines, _ = runner(ocr_pixels, return_word_box=True, text_score=.25, use_cls=False)
    checkpoint(cancel)
    if not lines:
        raise GenerationError("未识别到文字。请框选清晰的一两行正文重试，或保留原来的手工标注方式。")
    transform = source_transform(region, w, h)
    samples = []
    for line_index, line in enumerate(lines):
        checkpoint(cancel)
        if len(line) < 6:
            continue
        quad, text, score, boxes, labels, scores = line[:6]
        if len(boxes) != len(labels) or len(labels) != len(scores):
            raise GenerationError("识字组件返回的字符与定位数量不符，本次不收录，请改为手工标注。")
        quad = np.float32(quad) / scale
        # 检测行框留少量上下余量，减少细小点和长钩贴边时被截去的情况。
        vertical = ((quad[3] - quad[0]) + (quad[2] - quad[1])) * .03
        quad[:2] -= vertical
        quad[2:] += vertical
        quad[:, 0] = np.clip(quad[:, 0], 0, w - 1)
        quad[:, 1] = np.clip(quad[:, 1], 0, h - 1)
        # 对每行做局部拉正，行内实际分割边界再由原墨迹的低密度列微调。
        line_rgb = crop_photo(cropped, quad)
        lh, lw = line_rgb.shape[:2]
        mapping = cv2.getPerspectiveTransform(quad, np.float32([[0, 0], [lw - 1, 0], [lw - 1, lh - 1], [0, lh - 1]]))
        inverse = np.linalg.inv(mapping)
        locations = []
        for box, label, confidence in zip(boxes, labels, scores):
            if not isinstance(label, str) or len(label) != 1 or label.isspace() or ord(label) < 33:
                continue
            local = cv2.perspectiveTransform((np.float32(box) / scale).reshape(1, 4, 2), mapping)[0]
            locations.append((float(local[:, 0].mean()), label, float(confidence)))
        if not locations:
            continue
        locations.sort(key=lambda item: item[0])
        gray_line = paper_gray(line_rgb, remove_colored, True)
        level, _ = cv2.threshold(gray_line, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        density = np.sum(gray_line < min(225, level + 8), axis=0).astype(float)
        cuts = [0]
        for index in range(len(locations) - 1):
            a, b = locations[index][0], locations[index + 1][0]
            middle = (a + b) / 2
            radius = max(1, min((b - a) * .23, lh * .22))
            lo, hi = max(cuts[-1] + 1, round(middle - radius)), min(lw - 1, round(middle + radius))
            if lo >= hi:
                cuts.append(min(lw - 1, max(cuts[-1], round(middle))))
            else:
                span = np.arange(lo, hi + 1)
                cost = density[lo:hi + 1] + .05 * abs(span - middle)
                cuts.append(int(span[np.argmin(cost)]))
        cuts.append(lw)
        ink = gray_line < min(225, level + 8)
        reviews = []
        cells = separate_line_cells(line_rgb, ink, locations, cuts, cancel, reviews) if separate_neighbors else ((cuts[i], cuts[i + 1], line_rgb[:, cuts[i]:cuts[i + 1]]) for i in range(len(locations)))
        for index, ((_, char, confidence), (x0, x1, cell)) in enumerate(zip(locations, cells)):
            checkpoint(cancel)
            if x1 <= x0 or min(lh, x1 - x0) < 4:
                continue
            binary = gray_image(normalize_cell(cell, threshold, adaptive, remove_colored, processing))
            try:
                sample = trace_sample(char, binary, cancel, trajectory, ink_width)
            except GenerationError as error:
                sample = PhotoSample(char, binary, [], str(error))
            sample.trajectory, sample.ink_width = trajectory, ink_width
            sample.original = rgb_image(cell)
            local_box = np.float32([[x0, 0], [max(x0, x1 - 1), 0], [max(x0, x1 - 1), lh - 1], [x0, lh - 1]])
            photo_box = cv2.perspectiveTransform(local_box.reshape(1, 4, 2), inverse)
            original_box = cv2.perspectiveTransform(photo_box, transform)[0]
            sample.box = tuple(tuple(map(float, point)) for point in original_box)
            sample.confidence = confidence
            sample.note = "离线识字候选，请核对字符及左右笔画是否裁全。" + (" 识字分数较低。" if confidence < .85 else "")
            if index < len(reviews) and reviews[index]:
                sample.note += " " + reviews[index]
            if min(cell.shape[:2]) < 24:
                sample.note += " 原图单字像素不足，建议近拍。"
            samples.append(sample)
        if len(samples) > 2000:
            raise GenerationError("一次自动识字最多2000个字，请框选较小段落分批录入。")
        if progress:
            progress(line_index + 1, len(lines))
    if not samples:
        raise GenerationError("没有得到可定位的单字，请缩小到一行正文重试。")
    return samples
