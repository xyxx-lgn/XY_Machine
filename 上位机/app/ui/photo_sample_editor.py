"""照片单字局部修整：只改待收录样本，不动原照片和已保存字库。"""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget

from app.generation import GenerationError
from app.photo_samples import trace_sample
from app.ui.dialog_window import FunctionDialog


class InkCanvas(QWidget):
    def __init__(self, image, parent=None):
        super().__init__(parent)
        self.image = image.copy()
        self.original = image.copy()
        self.history = []
        self.erase = True
        self.brush = 8
        self.last = None
        self.setMinimumSize(360, 360)
        self.setMouseTracking(True)

    def image_rect(self):
        side = min(self.width(), self.height()) - 20
        return QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)

    def source(self, point):
        box = self.image_rect()
        return QPointF(max(0, min(self.image.width() - 1, (point.x() - box.x()) * self.image.width() / box.width())),
                       max(0, min(self.image.height() - 1, (point.y() - box.y()) * self.image.height() / box.height())))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#eaf0f8"))
        painter.drawImage(self.image_rect(), self.image)

    def draw(self, position):
        current = self.source(position)
        painter = QPainter(self.image)
        color = QColor("white" if self.erase else "black")
        if self.last is None or self.last == current:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(current, self.brush / 2, self.brush / 2)
        else:
            painter.setPen(QPen(color, self.brush, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(self.last, current)
        painter.end()
        self.last = current
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.image_rect().contains(event.position()):
            self.history.append(self.image.copy())
            self.history = self.history[-32:]
            self.draw(event.position())

    def mouseMoveEvent(self, event):
        if self.last is not None:
            self.draw(event.position())

    def mouseReleaseEvent(self, event):
        if self.last is not None and event.button() == Qt.MouseButton.LeftButton:
            self.draw(event.position())
            self.last = None

    def undo(self):
        if self.history:
            self.image = self.history.pop()
            self.update()

    def reset(self):
        self.history.append(self.image.copy())
        self.image = self.original.copy()
        self.update()


class PhotoSampleEditor(FunctionDialog):
    def __init__(self, sample, trajectory="continuous", parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"清理照片字 · {sample.character}")
        self.resize(660, 700)
        self.sample, self.trajectory, self.result_sample = sample, trajectory, None
        layout = QVBoxLayout(self)
        tip = QLabel("擦掉邻字残痕；误删可撤销。黑白图有真实断口时，可切到“补线”少量修补。只修改当前候选，应用后须重新核对实际轨迹。")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        row = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItems(["擦除残痕", "补线（黑色）"])
        row.addWidget(self.mode)
        row.addWidget(QLabel("笔刷宽度（样本像素）"))
        self.size = QSpinBox()
        self.size.setRange(1, 40)
        self.size.setValue(8)
        row.addWidget(self.size)
        undo, reset = QPushButton("撤销"), QPushButton("恢复本次打开时")
        row.addWidget(undo)
        row.addWidget(reset)
        layout.addLayout(row)
        self.canvas = InkCanvas(sample.image)
        layout.addWidget(self.canvas, 1)
        self.status = QLabel("黑色是保留墨迹；白色不绘制。鼠标左键涂抹。")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        apply, cancel = QPushButton("应用并重新生成轨迹"), QPushButton("取消")
        row.addWidget(apply)
        row.addWidget(cancel)
        layout.addLayout(row)
        self.mode.currentIndexChanged.connect(lambda index: setattr(self.canvas, "erase", index == 0))
        self.size.valueChanged.connect(lambda value: setattr(self.canvas, "brush", value))
        undo.clicked.connect(self.canvas.undo)
        reset.clicked.connect(self.canvas.reset)
        apply.clicked.connect(self.apply)
        cancel.clicked.connect(self.reject)

    def apply(self):
        try:
            result = trace_sample(self.sample.character, self.canvas.image, trajectory=self.trajectory, ink_width=self.sample.ink_width)
            result.original, result.box, result.confidence = self.sample.original, self.sample.box, self.sample.confidence
            result.line_id, result.source_frame = self.sample.line_id, self.sample.source_frame
            # 清理结果作为新的校准起点，避免下次批量校准恢复已擦掉的邻字残痕。
            result.note = "本字已手工清理并重算轨迹，请重新核对后勾选。"
            self.result_sample = result
            self.accept()
        except GenerationError as error:
            self.status.setText(str(error))
