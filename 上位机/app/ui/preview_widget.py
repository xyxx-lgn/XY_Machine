import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap, QTransform
from PySide6.QtWidgets import QWidget


class PreviewWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.paper_width, self.paper_height = 210.0, 297.0
        self.position = None
        self.program = None
        self.zoom = 1.0
        self.pan = QPointF()
        self.drag_start = None
        self.show_travel = False
        self.drawing_path = QPainterPath()
        self.travel_path = QPainterPath()
        self.drawing_chunks = []
        self.travel_chunks = []
        self.scene_cache = None
        self.scene_key = None
        self.scene_builds = 0
        self.cached_geometry = None
        self.interaction_timer = QTimer(self)
        self.interaction_timer.setSingleShot(True)
        self.interaction_timer.setInterval(100)
        self.interaction_timer.timeout.connect(self.update)
        self.setMinimumSize(350, 280)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def set_paper(self, width, height):
        if (width, height) == (self.paper_width, self.paper_height):
            return
        self.paper_width, self.paper_height = width, height
        self.update()

    def set_position(self, position):
        if self.position == position:
            return
        previous = self.marker_rect(self.position)
        self.position = position
        self.update(previous.united(self.marker_rect(position)).toAlignedRect())

    def set_program(self, program):
        self.program = program
        self.drawing_path = QPainterPath()
        self.travel_path = QPainterPath()
        self.drawing_chunks = []
        self.travel_chunks = []
        # 轨迹在载入时缓存，周期状态刷新不重新遍历几万行G代码。
        if program:
            for segment in program.segments:
                path = self.drawing_path if segment.drawing else self.travel_path
                chunks = self.drawing_chunks if segment.drawing else self.travel_chunks
                if not chunks or chunks[-1].elementCount() >= 1000:
                    chunks.append(QPainterPath())
                chunk = chunks[-1]
                path.moveTo(*segment.points[0])
                chunk.moveTo(*segment.points[0])
                for point in segment.points[1:]:
                    path.lineTo(*point)
                    chunk.lineTo(*point)
        self.scene_cache = None
        self.update()

    def view_geometry(self):
        scale = self.base_scale() * self.zoom
        width, height = self.paper_width * scale, self.paper_height * scale
        left = (self.width() - width) / 2 + self.pan.x()
        top = (self.height() - height) / 2 + self.pan.y()
        return scale, left, top, width, height

    def marker_rect(self, position):
        if position is None:
            return QRectF()
        scale, left, top, _, _ = self.view_geometry()
        return QRectF(left + position[0] * scale - 12, top + position[1] * scale - 12, 24, 24)

    def change_zoom(self, factor):
        self.zoom = min(40.0, max(0.25, self.zoom * factor))
        self.interaction_timer.start()
        self.update()

    def fit(self):
        self.interaction_timer.stop()
        self.zoom = 1.0
        self.pan = QPointF()
        self.update()

    def fit_job(self):
        self.interaction_timer.stop()
        if not self.program or not self.program.bounds:
            return self.fit()
        x0, y0, x1, y1 = self.program.bounds
        base = self.base_scale()
        self.zoom = min(40, max(0.25, min((self.width() - 100) / max(x1 - x0, 1), (self.height() - 100) / max(y1 - y0, 1)) / base))
        scale = base * self.zoom
        self.pan = QPointF((self.paper_width - x0 - x1) * scale / 2, (self.paper_height - y0 - y1) * scale / 2)
        self.update()

    def base_scale(self):
        return max(0.01, min((self.width() - 100) / self.paper_width, (self.height() - 90) / self.paper_height))

    def wheelEvent(self, event):
        self.change_zoom(1.2 if event.angleDelta().y() > 0 else 1 / 1.2)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self.drag_start is not None:
            self.pan += event.position() - self.drag_start
            self.drag_start = event.position()
            self.interaction_timer.start()
            self.update()

    def mouseReleaseEvent(self, event):
        self.drag_start = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#f5f8fc"))
        key = (self.width(), self.height(), self.devicePixelRatioF(), self.paper_width, self.paper_height, self.zoom, self.pan.x(), self.pan.y(), self.show_travel)
        if self.scene_cache is None or self.scene_key != key and not self.interaction_timer.isActive():
            ratio = self.devicePixelRatioF()
            self.scene_cache = QPixmap(round(self.width() * ratio), round(self.height() * ratio))
            self.scene_cache.setDevicePixelRatio(ratio)
            self.scene_cache.fill(QColor("#f5f8fc"))
            scene = QPainter(self.scene_cache)
            scene.setRenderHint(QPainter.RenderHint.Antialiasing)
            self.draw_scene(scene)
            scene.end()
            self.scene_key = key
            self.cached_geometry = self.view_geometry()
            self.scene_builds += 1
        # 周期状态和串口应答只复用静态位图，不再描边整份大文件。
        # 连续缩放/拖动期间变换上一帧位图，手势停下后只重建一次精细轨迹。
        scale, left, top, _, _ = self.view_geometry()
        old_scale, old_left, old_top, _, _ = self.cached_geometry
        ratio = scale / old_scale
        painter.save()
        painter.translate(left - old_left * ratio, top - old_top * ratio)
        painter.scale(ratio, ratio)
        painter.drawPixmap(0, 0, self.scene_cache)
        painter.restore()
        if self.position:
            point = self.marker_rect(self.position).center()
            painter.setPen(QPen(QColor("#f97316"), 2))
            painter.drawLine(point + QPointF(-8, 0), point + QPointF(8, 0))
            painter.drawLine(point + QPointF(0, -8), point + QPointF(0, 8))
            painter.drawEllipse(point, 4, 4)

    def draw_scene(self, painter):
        scale, left, top, width, height = self.view_geometry()
        paper = QRectF(left, top, width, height)
        painter.fillRect(paper, QColor("white"))
        painter.save()
        painter.setClipRect(paper)
        painter.setPen(QPen(QColor("#e2e9f4"), 1))
        interval = 10 if self.zoom < 3 else 2
        for x in range(0, math.ceil(self.paper_width) + 1, interval):
            painter.drawLine(QPointF(left + x * scale, top), QPointF(left + x * scale, top + height))
        for y in range(0, math.ceil(self.paper_height) + 1, interval):
            painter.drawLine(QPointF(left, top + y * scale), QPointF(left + width, top + y * scale))
        painter.restore()
        painter.save()
        painter.setTransform(QTransform(scale, 0, 0, scale, left, top))
        if self.show_travel:
            pen = QPen(QColor("#aab8cc"), 1, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
            for path in self.travel_chunks:
                painter.drawPath(path)
        pen = QPen(QColor("#174f9e"), 1.25)
        pen.setCosmetic(True)
        painter.setPen(pen)
        for path in self.drawing_chunks:
            painter.drawPath(path)
        painter.restore()
        painter.setPen(QPen(QColor("#3b82f6"), 1.5))
        painter.drawRect(paper)
        painter.setBrush(QColor("#2563eb"))
        painter.drawEllipse(QPointF(left, top), 4, 4)
        painter.setFont(QFont("Microsoft YaHei UI", 9))
        painter.setPen(QColor("#334155"))
        painter.drawText(QPointF(left, top - 12), f"纸张 {self.paper_width:g} × {self.paper_height:g} mm · 左上角(0,0)")
        painter.drawText(QPointF(left + width + 8, top + 5), "X →")
        painter.drawText(QPointF(left - 30, top + 24), "Y ↓")
        painter.setPen(QColor("#64748b"))
        caption = f"缩放 {self.zoom:.0%} · 滚轮缩放 / 拖动平移 · 蓝色：落笔轨迹 · 橙色：实时位置"
        painter.drawText(QRectF(8, self.height() - 26, self.width() - 16, 24), Qt.AlignmentFlag.AlignCenter, caption)
