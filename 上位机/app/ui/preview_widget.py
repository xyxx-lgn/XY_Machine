import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget


class PreviewWidget(QWidget):
    """第一阶段仅显示纸张与状态坐标，不声称已经实现文件轨迹预览。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.paper_width = 210.0
        self.paper_height = 297.0
        self.position = None
        self.zoom = 1.0
        self.setMinimumSize(350, 280)

    def set_paper(self, width, height):
        self.paper_width, self.paper_height = width, height
        self.update()

    def set_position(self, position):
        self.position = position
        self.update()

    def change_zoom(self, factor):
        self.zoom = min(4.0, max(0.5, self.zoom * factor))
        self.update()

    def fit(self):
        self.zoom = 1.0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#f5f8fc"))
        margin = 45
        scale = min((self.width() - 2 * margin) / self.paper_width, (self.height() - 2 * margin) / self.paper_height) * self.zoom
        width, height = self.paper_width * scale, self.paper_height * scale
        left, top = (self.width() - width) / 2, (self.height() - height) / 2
        paper = QRectF(left, top, width, height)
        painter.fillRect(paper, QColor("white"))
        painter.save()
        painter.setClipRect(paper)
        painter.setPen(QPen(QColor("#e2e9f4"), 1))
        interval = 10 if self.paper_width <= 400 and self.paper_height <= 400 else 50
        for x in range(0, math.ceil(self.paper_width) + 1, interval):
            painter.drawLine(QPointF(left + x * scale, top), QPointF(left + x * scale, top + height))
        for y in range(0, math.ceil(self.paper_height) + 1, interval):
            painter.drawLine(QPointF(left, top + y * scale), QPointF(left + width, top + y * scale))
        if self.position:
            x, y, _ = self.position
            if 0 <= x <= self.paper_width and 0 <= y <= self.paper_height:
                point = QPointF(left + x * scale, top + y * scale)
                painter.setPen(QPen(QColor("#f97316"), 2))
                painter.drawLine(point + QPointF(-8, 0), point + QPointF(8, 0))
                painter.drawLine(point + QPointF(0, -8), point + QPointF(0, 8))
                painter.drawEllipse(point, 4, 4)
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
        painter.drawText(QRectF(12, self.height() - 28, self.width() - 24, 24), Qt.AlignmentFlag.AlignCenter, "当前显示纸张与工作坐标；文件轨迹预览将在第二阶段实现")
