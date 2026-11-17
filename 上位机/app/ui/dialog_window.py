"""功能窗口共用标准标题栏，可最大化、还原和调整大小。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog


class FunctionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        flags = self.windowFlags() | Qt.WindowType.WindowMinMaxButtonsHint | Qt.WindowType.WindowCloseButtonHint | Qt.WindowType.WindowSystemMenuHint
        # 某些PySide6版本的枚举取反会同时清掉高位关闭标志，使用整数位运算。
        self.setWindowFlags(Qt.WindowType(int(flags) & ~int(Qt.WindowType.WindowContextHelpButtonHint)))
        self.setSizeGripEnabled(True)
