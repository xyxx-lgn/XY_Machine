from dataclasses import dataclass

import cv2
import numpy as np
from PySide6.QtGui import QImage


class ImageProcessingError(ValueError):
    pass


class ProcessingCancelled(Exception):
    pass


@dataclass
class ProcessedImage:
    mask: np.ndarray
    notes: list

    @property
    def ratio(self):
        return self.mask.shape[1] / max(1, self.mask.shape[0])

    def image(self):
        gray = np.ascontiguousarray(255 - self.mask)
        return QImage(gray.data, gray.shape[1], gray.shape[0], gray.strides[0], QImage.Format.Format_Grayscale8).copy()


def checkpoint(cancel):
    if cancel and cancel():
        raise ProcessingCancelled()


def image_rgb(image):
    # 透明图先铺白，防止透明背景被当成黑色主体。
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    pixels = np.frombuffer(rgba.constBits(), np.uint8).reshape(rgba.height(), rgba.bytesPerLine())
    pixels = pixels[:, :rgba.width() * 4].reshape(rgba.height(), rgba.width(), 4)
    alpha = pixels[:, :, 3:4].astype(np.float32) / 255
    return np.rint(pixels[:, :, :3] * alpha + 255 * (1 - alpha)).astype(np.uint8)


def normalize_size(rgb, long_edge=1000):
    scale = long_edge / max(rgb.shape[:2])
    return cv2.resize(rgb, (max(2, round(rgb.shape[1] * scale)), max(2, round(rgb.shape[0] * scale))), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)


def binary_thumbnail(image, width, height):
    # 缩略图保留细线覆盖；直接抽样会让一像素线条在小预览里看起来断裂。
    gray_image = image.convertToFormat(QImage.Format.Format_Grayscale8)
    gray = np.frombuffer(gray_image.constBits(), np.uint8).reshape(gray_image.height(), gray_image.bytesPerLine())[:, :gray_image.width()]
    scale = min(width / gray.shape[1], height / gray.shape[0], 1)
    if scale < 1:
        gray = cv2.resize(gray, (max(1, round(gray.shape[1] * scale)), max(1, round(gray.shape[0] * scale))), interpolation=cv2.INTER_AREA)
        gray = np.where(gray < 250, 0, 255).astype(np.uint8)
    gray = np.ascontiguousarray(gray)
    return QImage(gray.data, gray.shape[1], gray.shape[0], gray.strides[0], QImage.Format.Format_Grayscale8).copy()


def remove_specks(mask, strength, cancel):
    if strength <= 0:
        return mask
    _, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    checkpoint(cancel)
    minimum = max(2, round(strength * 1.2))
    keep = stats[:, cv2.CC_STAT_AREA] >= minimum
    keep[0] = False
    return keep[labels].astype(np.uint8) * 255


def edge_image(rgb, sensitivity, detail, smoothing):
    if smoothing:
        # 保边平滑减弱渐变阴影中的噪点，同时保留眼睛、嘴巴等明显边界。
        rgb = cv2.bilateralFilter(rgb, 5 + 2 * smoothing, 15 + 15 * smoothing, 3 + 2 * smoothing)
    high = max(15, 170 - sensitivity * 1.4) * (1.15 - detail * 0.007)
    return cv2.Canny(rgb, high * 0.4, high, L2gradient=True)


def cartoon_image(rgb, options, cancel):
    # 白底角色先按颜色与白色的差异取主体，避免浅黄色身体被灰度阈值切碎。
    distance = np.max(255 - rgb.astype(np.int16), axis=2)
    foreground = (distance >= 18 + (100 - options.edge_sensitivity) * 0.3).astype(np.uint8) * 255
    foreground = cv2.morphologyEx(foreground, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    foreground = remove_specks(foreground, max(10, options.despeckle), cancel)
    contours, _ = cv2.findContours(foreground, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise ImageProcessingError("没有找到与白底分开的主体，请提高边缘灵敏度或改用彩色边缘。")
    silhouette = np.zeros(foreground.shape, np.uint8)
    cv2.drawContours(silhouette, contours, -1, 255, cv2.FILLED)
    outline = np.zeros_like(silhouette)
    for contour in contours:
        contour = cv2.approxPolyDP(contour, 0.6, True)
        cv2.drawContours(outline, [contour], -1, 255, 1)
    checkpoint(cancel)
    if options.detail_level > 0:
        edges = edge_image(rgb, options.edge_sensitivity, options.detail_level, options.smoothing)
        # 外轮廓已单独生成；内部只保留离边界稍远的特征，避免重复描一圈。
        interior = cv2.erode(silhouette, np.ones((9, 9), np.uint8))
        edges = cv2.bitwise_and(edges, interior)
        edges = remove_specks(edges, options.despeckle, cancel)
        outline = cv2.bitwise_or(outline, edges)
    border = np.concatenate((rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]))
    notes = ["白底卡通提取主体外轮廓和内部边缘；渐变阴影可能保留部分线条，可降低内部细节。"]
    if np.mean(np.min(border, axis=1) >= 235) < 0.65:
        notes.append("图片边缘不接近白色背景，建议尝试彩色边缘，或先裁掉背景。")
    return outline, notes


def process_image(image, options, cancel=None):
    checkpoint(cancel)
    rgb = image_rgb(image)
    notes = []
    processing = options.processing
    invert_output = options.invert
    if processing == "adaptive" and options.invert:
        # 白线黑底先反相输入，再局部判断背景，不能把处理后的整张纸反成黑块。
        rgb = 255 - rgb
        invert_output = False
    if processing == "threshold":
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        mask = (gray <= options.threshold).astype(np.uint8) * 255
    else:
        rgb = normalize_size(rgb)
        checkpoint(cancel)
        if processing == "adaptive":
            gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
            if options.smoothing:
                gray = cv2.GaussianBlur(gray, (3, 3), options.smoothing * 0.3)
            block = min(51, min(gray.shape) // 2 * 2 - 1)
            if block < 3:
                raise ImageProcessingError("图片太窄，无法进行自适应二值化。")
            mask = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, block, 16 - options.detail_level * 0.14)
            notes.append("自适应二值化适合背景不均匀的纸面线稿；内部细节越高，保留的浅色线越多。")
        elif processing == "edges":
            mask = edge_image(rgb, options.edge_sensitivity, options.detail_level, options.smoothing)
            notes.append("彩色边缘包含颜色和明暗变化；建议使用中线路径，减少细线两侧重复描边。")
        elif processing == "cartoon":
            mask, notes = cartoon_image(rgb, options, cancel)
        else:
            raise ImageProcessingError("不支持的图片处理方式。")
        checkpoint(cancel)
        if options.repair_gaps:
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        mask = remove_specks(mask, options.despeckle, cancel)
    checkpoint(cancel)
    if invert_output:
        mask = 255 - mask
    if not np.any(mask):
        raise ImageProcessingError("处理后没有黑色图案，请调整阈值/灵敏度、减少碎点过滤，或更换处理方式。")
    if options.crop:
        x, y, width, height = cv2.boundingRect(mask)
        mask = mask[y:y + height, x:x + width]
    return ProcessedImage(np.ascontiguousarray(mask), notes)
