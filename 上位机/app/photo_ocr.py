"""RapidOCR本地识字和单字定位；识字标签与原始照片轨迹始终分开。"""

from functools import lru_cache
import threading
import uuid
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtGui import QImage

from app.generation import GenerationError, checkpoint
from app.photo_samples import PhotoSample, cell_frame, crop_photo, gray_image, image_pixels, normalize_cell, paper_gray, rgb_image, trace_sample
from app.photo_separation import isolate_line_band, separate_line_cells
from app.photo_recognition import align_character_positions, faint_line_indices, position_warnings, refine_character_cuts, verify_photo_labels


OCR_LOCK = threading.Lock()


def character_boxes(images, boxes, results):
    """直接映射CTC位置，不用平均字宽加重叠修剪反推字符中心。

    通用OCR的返回框用于显示；混排数字和汉字时框的中心会被重叠修剪移动。
    照片分字仅需原始位置，标签、位置和置信度必须按同一解码顺序取出。
    """
    from rapidocr_onnxruntime.cal_rec_boxes.main import CalRecBoxes
    converter = CalRecBoxes()
    output = []
    for image, box, result in zip(images, boxes, results):
        text, confidence, info = result
        if not text.strip():
            output.append([text, confidence, [], [], []])
            continue
        columns, words, positions, _, scores = info
        labels = [char for word in words for char in word]
        locations = [col for word in positions for col in word]
        if len(labels) != len(locations) or len(labels) != len(scores) or columns <= 0:
            raise GenerationError("识字解码字符与位置不一致，请重新提取。")
        height, width = image.shape[:2]
        local = []
        for col in locations:
            center = float(np.clip((col + .5) * width / columns, 0, width - 1))
            left, right = max(0, center - .5), min(width - 1, center + .5)
            local.append([[left, 0], [right, 0], [right, height - 1], [left, height - 1]])
        # 上游逆变换会原地平移输入框，必须传副本，避免整行坐标被改成从零开始。
        mapped = converter.reverse_rotate_crop_image(np.asarray(box, np.float32).copy(), local, converter.get_box_direction(box)) if local else []
        output.append([text, confidence, mapped, labels, scores])
    return output


@lru_cache(maxsize=2)
def ocr_engine(enhanced=True):
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        raise GenerationError("离线识字组件尚未准备，请运行上位机目录中的“准备照片识字.ps1”；原来的手工录字仍可使用。") from None
    # 增强模型随软件离线保存；基础模型用于独立单字复核。运行中不下载或上传。
    models = Path(__file__).resolve().parent.parent / "assets" / "photo_ocr"
    paths = dict(det_model_path=models / "ch_PP-OCRv5_det_mobile.onnx", rec_model_path=models / "ch_PP-OCRv5_rec_server.onnx")
    options = {key: str(path) for key, path in paths.items()} if enhanced and all(p.is_file() for p in paths.values()) else {}
    engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1, det_limit_side_len=1600, det_limit_type="max", max_side_len=2800, **options)
    engine.cal_rec_boxes = character_boxes
    return engine


def source_transform(region, cropped_width, cropped_height):
    if region is None:
        return np.eye(3, dtype=np.float32)
    points = np.asarray(region, dtype=np.float32)
    if points.shape == (4,):
        x0, y0, x1, y1 = points
        # 矩形裁切右下界不包含在像素里，最后一个像素需减一。
        points = np.float32([[x0, y0], [x1 - 1, y0], [x1 - 1, y1 - 1], [x0, y1 - 1]])
    return cv2.getPerspectiveTransform(np.float32([[0, 0], [cropped_width - 1, 0], [cropped_width - 1, cropped_height - 1], [0, cropped_height - 1]]), points)


def recognize_photo(image, region=None, threshold=210, remove_colored=True, processing="auto", adaptive=True, cancel=None, progress=None, engine=None, separate_neighbors=True, trajectory="continuous", ink_width=.03, suppress_faint=True, verify_labels=True):
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
    batch_id = uuid.uuid4().hex
    hidden = faint_line_indices(gray, lines) if suppress_faint else set()
    for line_index, line in enumerate(lines):
        checkpoint(cancel)
        if len(line) < 6 or line_index in hidden:
            continue
        quad, text, score, boxes, labels, scores = line[:6]
        if len(boxes) != len(labels) or len(labels) != len(scores):
            raise GenerationError("识字组件返回的字符与定位数量不符，本次不收录，请改为手工标注。")
        quad = np.float32(quad) / scale
        original_quad = quad.copy()
        # 检测行框留少量上下余量，减少细小点和长钩贴边时被截去的情况。
        # 先给长钩和上方点留出余量，再以相邻行中线为界，避免把上下行一起裁入。
        vertical = ((quad[3] - quad[0]) + (quad[2] - quad[1])) * .08
        center_y = float(quad[:, 1].mean())
        left, right = float(quad[:, 0].min()), float(quad[:, 0].max())
        upper, lower = 0.0, float(h - 1)
        for neighbor in lines:
            if neighbor is line or len(neighbor) < 6:
                continue
            other = np.float32(neighbor[0]) / scale
            overlap = min(right, float(other[:, 0].max())) - max(left, float(other[:, 0].min()))
            if overlap < min(right - left, float(np.ptp(other[:, 0]))) * .3:
                continue
            other_y = float(other[:, 1].mean())
            if other_y < center_y:
                upper = max(upper, (other_y + center_y) / 2)
            elif other_y > center_y:
                lower = min(lower, (other_y + center_y) / 2)
        quad[:2] -= vertical
        quad[2:] += vertical
        quad[:, 0] = np.clip(quad[:, 0], 0, w - 1)
        # 原行框本身不缩小，仅约束新增余量；相邻行的点不会被主动吞入。
        quad[:2, 1] = np.maximum(quad[:2, 1], np.minimum(original_quad[:2, 1], upper))
        quad[2:, 1] = np.minimum(quad[2:, 1], np.maximum(original_quad[2:, 1], lower))
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
        suspect_positions = position_warnings(locations)
        gray_line = paper_gray(line_rgb, remove_colored, True)
        level, _ = cv2.threshold(gray_line, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        ink = gray_line < min(225, level + 8)
        isolated = False
        if separate_neighbors and len(locations) >= 5:
            line_rgb, ink, isolated = isolate_line_band(line_rgb, ink, cancel)
        if verify_labels and engine is None:
            with OCR_LOCK:
                locations = align_character_positions(line_rgb, locations, ocr_engine(False).text_rec, cancel)
            suspect_positions = position_warnings(locations)
        density = np.sum(ink, axis=0).astype(float)
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
        if verify_labels and engine is None:
            with OCR_LOCK:
                cuts = refine_character_cuts(line_rgb, locations, cuts, ocr_engine(False).text_rec, cancel)
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
            sample.line_id = f"{batch_id}:{line_index}"
            sample.source_frame = cell_frame(cell)
            local_box = np.float32([[x0, 0], [max(x0, x1 - 1), 0], [max(x0, x1 - 1), lh - 1], [x0, lh - 1]])
            photo_box = cv2.perspectiveTransform(local_box.reshape(1, 4, 2), inverse)
            original_box = cv2.perspectiveTransform(photo_box, transform)[0]
            original_box[:, 0] = np.clip(original_box[:, 0], 0, image.width() - 1)
            original_box[:, 1] = np.clip(original_box[:, 1], 0, image.height() - 1)
            sample.box = tuple(tuple(map(float, point)) for point in original_box)
            sample.confidence = confidence
            sample.note = "离线识字候选，请核对字符及左右笔画是否裁全。" + (" 识字分数较低。" if confidence < .85 else "")
            if isolated:
                sample.source_flags = ("行框已隔离疑似上下行墨迹，请对照原图核对",)
            if index < len(reviews) and reviews[index]:
                sample.note += " " + reviews[index]
            if index in suspect_positions:
                sample.source_flags += ("识字位置异常，可能一字被拆开或漏识字，请对照原行",)
            if min(cell.shape[:2]) < 24:
                sample.note += " 原图单字像素不足，建议近拍。"
            samples.append(sample)
        if len(samples) > 2000:
            raise GenerationError("一次自动识字最多2000个字，请框选较小段落分批录入。")
        if progress:
            progress(line_index + 1, len(lines))
    if not samples:
        raise GenerationError("没有得到可定位的单字，请缩小到一行正文重试。")
    if verify_labels and engine is None:
        with OCR_LOCK:
            verify_photo_labels(samples, ocr_engine(False).text_rec, cancel, progress)
    if hidden:
        for sample in samples:
            sample.note += f" 本页已过滤{len(hidden)}行极淡疑似透印；需保留淡字可关闭透印过滤重试。"
    return samples
