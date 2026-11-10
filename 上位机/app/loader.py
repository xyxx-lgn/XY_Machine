from PySide6.QtCore import QThread, Signal

from app.gcode import load_program, parse_program


class ProgramLoader(QThread):
    loaded = Signal(object)
    failed = Signal(str)

    def __init__(self, path, source_text=None, context=None, parent=None):
        super().__init__(parent)
        self.path = path
        self.source_text = source_text
        self.context = context or {}

    def run(self):
        try:
            if self.source_text is None:
                program = load_program(self.path, **self.context)
            else:
                program = parse_program(self.source_text, self.path, **self.context)
            self.loaded.emit(program)
        except (OSError, ValueError, UnicodeError) as error:
            self.failed.emit(str(error))
