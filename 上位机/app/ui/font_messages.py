"""个人字库确认框显式使用中文，不依赖电脑的Qt翻译包。"""

from PySide6.QtWidgets import QMessageBox


def confirm_font_action(parent, title, text, action="放弃修改"):
    box = QMessageBox(QMessageBox.Icon.Question, title, text, parent=parent)
    confirm = box.addButton(action, QMessageBox.ButtonRole.DestructiveRole)
    cancel = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    box.exec()
    return box.clickedButton() == confirm
