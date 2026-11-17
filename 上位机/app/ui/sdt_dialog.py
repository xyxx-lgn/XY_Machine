"""可选SDT本地AI字库试样入口，与上位机串口和任务发送隔离。"""

import codecs
import json
from pathlib import Path
import uuid

from PySide6.QtCore import QProcess, QProcessEnvironment
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QVBoxLayout
)

from app.generation import GenerationError
from app.profile import ROOT
from app.stroke_fonts import FontStore, package_data
from app.ui.dialog_window import FunctionDialog


PREFERENCES = ROOT / "config" / "sdt.json"


class SdtDialog(FunctionDialog):
    def __init__(self, parent=None, samples=None, base_package=None):
        super().__init__(parent)
        self.process = None
        self.output_path = None
        self.import_path = None
        self.buffer = ""
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.cancelled = False
        self.close_pending = False
        self.base_package = json.loads(json.dumps(package_data(base_package), ensure_ascii=False)) if base_package and base_package.glyphs else None
        self.setWindowTitle("个人字库扩展 · 离线笔迹学习")
        self.resize(900, 850)
        layout = QVBoxLayout(self)
        tip = QLabel("新增“离线AI轨迹扩字”：本地预训练轨迹模型根据至少15个真实录字，直接生成带抬落笔状态的XY轨迹。原个人笔迹几何迁移、清晰分笔与SDT仍可选择。结果另存，真实录字保留，不上传数据；模型生成字需回放和小样核对。")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        if self.base_package is not None:
            base_tip = QLabel(f"本次基于“{self.base_package['name']}”的{len(self.base_package['glyphs'])}字扩展。个人笔迹学习和离线AI轨迹扩字会重做目标中的非真实扩展字，真实样本字和其他已有字保留；清晰分笔/SDT保留全部已有字。结果另存，仍须使用最初真实录字目录。")
            base_tip.setWordWrap(True)
            layout.addWidget(base_tip)
        self.inputs = QFormLayout()
        self.method = QComboBox()
        self.method.addItem("个人笔迹学习：真实笔形与部件（离线）", "personal")
        self.method.addItem("清晰分笔：基础楷体校准（原方案）", "recorded")
        self.method.addItem("离线AI轨迹扩字：真实XY笔迹模型", "trajectory")
        self.method.addItem("SDT自由生成（实验，需核对字形）", "sdt")
        self.inputs.addRow("扩字方式", self.method)
        self.fields = {}
        base = ROOT / ".runtime" / "sdt"
        for key, title, value, kind in (
            ("python", "运行Python", str(base / "venv" / "Scripts" / "python.exe"), "file"),
            ("model_root", "OLHWG模型目录", str(ROOT / ".runtime" / "olhwg"), "folder"),
            ("repo", "官方SDT源码目录", str(base / "SDT"), "folder"),
            ("checkpoint", "中文模型权重", str(base / "weights" / "chinese.pth"), "file"),
            ("content", "Chinese_content.pkl", str(base / "data" / "Chinese_content.pkl"), "file"),
            ("samples", "参考目录（录字JSON / 单字PNG）", str(ROOT / "exports" / "个人采样"), "folder"),
        ):
            row = QHBoxLayout()
            field = QLineEdit(value)
            self.fields[key] = field
            row.addWidget(field, 1)
            button = QPushButton("选择…")
            button.clicked.connect(lambda checked=False, k=key, t=title, mode=kind: self.choose(k, t, mode))
            row.addWidget(button)
            self.inputs.addRow(title, row)
        self.font_name = QLineEdit("个人笔迹学习扩展")
        self.inputs.addRow("新字库名称", self.font_name)
        self.characters = QPlainTextEdit("你好世界学习生活")
        self.characters.setMaximumHeight(70)
        self.inputs.addRow("本次试字（最多200种）", self.characters)
        self.variants = QSpinBox()
        self.variants.setRange(1, 4)
        self.seed = QSpinBox()
        self.seed.setRange(1, 2147483647)
        self.seed.setValue(2026)
        self.inputs.addRow("每字生成版本数", self.variants)
        self.inputs.addRow("样式编号", self.seed)
        layout.addLayout(self.inputs)
        actions = QHBoxLayout()
        self.check_button = QPushButton("检查环境与模型")
        self.check_button.clicked.connect(lambda: self.start(True))
        self.run_button = QPushButton("生成AI字库试样")
        self.run_button.clicked.connect(lambda: self.start(False))
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        for button in (self.check_button, self.run_button, self.cancel_button):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.document().setMaximumBlockCount(300)
        layout.addWidget(self.log, 1)
        self.log.setPlainText("从原始手写字库打开，参考目录需含recorded-font.json。新增“离线AI轨迹扩字”使用本地OLHWG预训练轨迹模型和15个真实录字参考，直接生成XY笔迹及抬落笔状态；首次准备模型后生成阶段不联网。旧的个人几何扩展、清晰分笔和SDT仍保留供比较。本页不连接写字机。")
        footer = QHBoxLayout()
        self.import_button = QPushButton("导入本次试样到录字画板核对")
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self.import_result)
        footer.addWidget(self.import_button)
        close = QPushButton("返回字库管理")
        close.clicked.connect(self.reject)
        footer.addWidget(close)
        layout.addLayout(footer)
        self.restore_preferences()
        self.method.currentIndexChanged.connect(self.update_method)
        self.update_method()
        if samples is not None:
            self.fields["samples"].setText(str(samples))
        ready = all(Path(self.fields[key].text()).exists() for key in ("python", "repo", "checkpoint", "content"))
        trajectory_ready = all(Path(self.fields[key].text()).exists() for key in ("python", "model_root"))
        self.log.appendPlainText("离线轨迹模型目录已找到；选择该方式时会继续校验三份模型资源和真实录字。" if trajectory_ready else "离线轨迹模型尚未准备；可运行“准备轨迹扩字模型.ps1”。")
        self.log.appendPlainText("SDT运行文件已找到；选择SDT方式时仍需检查CUDA、权重和参考图。" if ready else "SDT运行文件尚不完整；个人笔迹学习与清晰分笔不依赖SDT。")

    def restore_preferences(self):
        try:
            data = json.loads(PREFERENCES.read_text(encoding="utf-8"))
            for key, field in self.fields.items():
                if isinstance(data.get(key), str):
                    field.setText(data[key])
            for key, widget in (("variants", self.variants), ("seed", self.seed)):
                if type(data.get(key)) is int:
                    widget.setValue(data[key])
            if isinstance(data.get("name"), str):
                self.font_name.setText(data["name"])
            index = self.method.findData(data.get("method", "personal"))
            self.method.setCurrentIndex(max(0, index))
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def save_preferences(self):
        try:
            data = {key: field.text().strip() for key, field in self.fields.items()}
            data.update(name=self.font_name.text().strip(), variants=self.variants.value(), seed=self.seed.value(), method=self.method.currentData())
            PREFERENCES.parent.mkdir(parents=True, exist_ok=True)
            temporary = PREFERENCES.with_suffix(".tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(PREFERENCES)
        except OSError as error:
            self.log.appendPlainText("本地路径偏好未保存：" + str(error))

    def choose(self, key, title, kind):
        if self.process is not None:
            return
        value = self.fields[key].text()
        path = QFileDialog.getExistingDirectory(self, title, value) if kind == "folder" else QFileDialog.getOpenFileName(self, title, value)[0]
        if path:
            self.fields[key].setText(path)

    def start(self, probe=False):
        if self.process is not None:
            return
        self.output_path = self.import_path = None
        self.import_button.setEnabled(False)
        python = Path(self.fields["python"].text().strip())
        if not python.is_file():
            self.log.appendPlainText("未找到所选Python。个人笔迹学习和清晰分笔需要NumPy；SDT方式还需CUDA版PyTorch等依赖。")
            return
        name = self.font_name.text().strip()
        if not name or len(name) > 60:
            self.log.appendPlainText("字库名称应为1～60个字符。")
            return
        self.save_preferences()
        self.progress.setValue(0)
        folder = ROOT / ".runtime" / "sdt" / "jobs" / uuid.uuid4().hex
        try:
            folder.mkdir(parents=True)
            request = {key: self.fields[key].text().strip() for key in ("repo", "checkpoint", "content", "samples", "model_root")}
            request.update(characters=self.characters.toPlainText(), variants=self.variants.value(), seed=self.seed.value(), name=name, method=self.method.currentData(), output=str(folder / "font.json"))
            if self.base_package is not None:
                base_path = folder / "base-font.json"
                base_path.write_text(json.dumps(self.base_package, ensure_ascii=False), encoding="utf-8")
                request["base_font"] = str(base_path)
            target = folder / "request.json"
            target.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        except OSError as error:
            self.log.appendPlainText(str(error))
            return
        self.output_path = folder / "font.json" if not probe else None
        self.cancelled, self.buffer = False, ""
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.process = QProcess(self)
        environment = QProcessEnvironment.systemEnvironment()
        for key, value in {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8", "TORCH_HOME": str(ROOT / ".runtime/sdt/torch"), "TEMP": str(ROOT / ".runtime/tmp"), "TMP": str(ROOT / ".runtime/tmp")}.items():
            environment.insert(key, value)
        self.process.setProcessEnvironment(environment)
        self.process.setWorkingDirectory(str(ROOT))
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.finished.connect(self.on_process_finished)
        self.process.errorOccurred.connect(self.process_error)
        self.check_button.setEnabled(False)
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.enable_inputs(False)
        self.log.appendPlainText("开始检查…" if probe else "开始本地生成试样…")
        arguments = ["-B", "-u", str(ROOT / "tools" / "sdt_adapter.py"), "--request", str(target)]
        if probe:
            arguments.append("--probe")
        self.process.start(str(python), arguments)

    def read_output(self):
        self.buffer += self.decoder.decode(bytes(self.process.readAllStandardOutput()))
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            try:
                event = json.loads(line)
                message = event.get("message") or (f"已生成{event['done']}/{event['total']}：{event['character']}" if event.get("stage") == "progress" else event.get("stage", line))
                if event.get("stage") in ("progress", "model_progress"):
                    self.progress.setValue(round(100 * event["done"] / max(1, event["total"])))
                elif event.get("stage") == "complete":
                    self.progress.setValue(100)
            except (ValueError, TypeError, KeyError, AttributeError):
                message = line
            self.log.appendPlainText(message)

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.log.appendPlainText("AI环境进程启动失败，请检查Python路径。")
            self.on_process_finished(1, QProcess.ExitStatus.CrashExit)

    def on_process_finished(self, code, status):
        if self.process is None:
            return
        self.read_output()
        self.buffer += self.decoder.decode(b"", final=True)
        if self.buffer.strip():
            self.log.appendPlainText(self.buffer.strip())
        success = code == 0 and status == QProcess.ExitStatus.NormalExit and not self.cancelled
        if success and self.output_path is not None:
            try:
                FontStore().read(self.output_path)
                self.import_button.setEnabled(True)
            except (OSError, GenerationError) as error:
                self.log.appendPlainText("生成文件未通过字库验证：" + str(error))
        elif not success:
            self.log.appendPlainText("已取消，现有字库未改变。" if self.cancelled else "本次未完成，现有字库未改变。请按上方具体错误准备环境或数据。")
        self.process.deleteLater()
        self.process = None
        self.check_button.setEnabled(True)
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.enable_inputs(True)
        if self.close_pending:
            super().reject()

    def cancel(self):
        if self.process is not None:
            self.cancelled = True
            self.cancel_button.setEnabled(False)
            self.process.kill()

    def enable_inputs(self, enabled):
        for field in (*self.fields.values(), self.font_name, self.characters, self.seed, self.variants, self.method):
            field.setEnabled(enabled)
        if enabled:
            self.update_method()

    def update_method(self):
        method = self.method.currentData()
        for key in ("repo", "checkpoint", "content"):
            self.fields[key].setEnabled(method == "sdt")
        self.fields["model_root"].setEnabled(method == "trajectory")
        self.check_button.setText("检查录字与目标笔形" if method == "personal" else "检查录字与基础笔顺" if method == "recorded" else "检查轨迹模型与真实样本" if method == "trajectory" else "检查环境与模型")
        self.run_button.setText("学习并扩展个人字库" if method == "personal" else "生成清晰分笔试样" if method == "recorded" else "离线生成个人轨迹字库" if method == "trajectory" else "生成AI字库试样")

    def import_result(self):
        if self.process is None and self.import_button.isEnabled():
            self.import_path = self.output_path
            self.accept()

    def reject(self):
        if self.process is not None:
            self.close_pending = True
            self.cancel()
            return
        super().reject()

    def closeEvent(self, event):
        if self.process is not None:
            self.close_pending = True
            self.cancel()
            event.ignore()
        else:
            super().closeEvent(event)
