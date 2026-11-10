from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout

from app.image_processing import binary_thumbnail


class ImageInspectionDialog(QDialog):
    def __init__(self, image, parent=None):
        super().__init__(parent)
        self.image = image
        self.zoom = 1.0
        self.setWindowTitle("黑白处理结果 · 仅查看，不发送")
        self.resize(900, 780)
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        for title, callback in (("适配", self.fit), ("100%", lambda: self.show_zoom(1)), ("−", lambda: self.show_zoom(self.zoom / 1.3)), ("+", lambda: self.show_zoom(self.zoom * 1.3))):
            button = QPushButton(title)
            button.clicked.connect(callback)
            bar.addWidget(button)
        self.scale_label = QLabel()
        bar.addWidget(self.scale_label)
        bar.addStretch()
        layout.addLayout(bar)
        self.scroll = QScrollArea()
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet("background: white;")
        self.scroll.setWidget(self.label)
        layout.addWidget(self.scroll)
        layout.addWidget(QLabel("黑色部分是要转成路径的内容。缩略显示会加深细线，100%可检查原始像素。"))

    def showEvent(self, event):
        super().showEvent(event)
        self.fit()

    def fit(self):
        scale = min((self.scroll.viewport().width() - 20) / self.image.width(), (self.scroll.viewport().height() - 20) / self.image.height(), 1)
        self.show_zoom(max(0.05, scale))

    def show_zoom(self, zoom):
        self.zoom = min(8, max(0.05, zoom))
        width, height = max(1, round(self.image.width() * self.zoom)), max(1, round(self.image.height() * self.zoom))
        if self.zoom < 1:
            pixmap = QPixmap.fromImage(binary_thumbnail(self.image, width, height))
        else:
            pixmap = QPixmap.fromImage(self.image).scaled(width, height, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)
        self.label.setPixmap(pixmap)
        self.label.resize(pixmap.size())
        self.scale_label.setText(f"{self.zoom * 100:.0f}%")
