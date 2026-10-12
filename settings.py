"""设置窗口 + 开机自启（注册表 HKCU\\...\\Run）。"""
import sys
import winreg
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSpinBox, QVBoxLayout,
)

from capture import DOUBLE_COPY, parse_hotkey
from llm import LANGUAGES, PROVIDERS, list_models, load_config, save_config_values

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "划词翻译"
HOTKEY_PRESETS = [DOUBLE_COPY, "ctrl+alt+t", "ctrl+shift+t", "alt+q", "f8"]
MODELS = ["deepseek-chat", "deepseek-reasoner"]
# 决定哪些词汇/语法"值得讲"；可手写，如"英语 B2"
LEVELS = ["入门（日语 N5～N4 水平）", "中级（日语 N3～N2 水平）", "高级（日语 N1 水平）"]
DEFAULT_LEVEL = LEVELS[1]


def _autostart_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pythonw}" "{Path(__file__).with_name("main.py")}"'


def is_autostart() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(on: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, _autostart_command())
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except FileNotFoundError:
                pass


class SettingsDialog(QDialog):
    saved = Signal()
    balance_requested = Signal()

    def __init__(self, icon: QIcon | None = None):
        super().__init__()
        self.setWindowTitle("划词翻译 设置")
        if icon:
            self.setWindowIcon(icon)
        self.setMinimumWidth(520)
        cfg = load_config(use_env=False)

        form = QFormLayout()
        self.profiles = {k: dict(v) for k, v in cfg["profiles"].items()}  # 每个服务各存一份
        self._current = cfg["provider"]
        self.provider = QComboBox()
        self.provider.addItems(list(PROVIDERS))
        self.provider.setCurrentText(self._current)
        self.provider.activated.connect(self._on_provider)
        form.addRow("模型服务", self.provider)
        self.base_url = QLineEdit(cfg["base_url"])
        self.base_url.setPlaceholderText("OpenAI 兼容接口地址，如 http://192.168.1.10:1234/v1")
        form.addRow("服务地址", self.base_url)

        self.key = QLineEdit(cfg["api_key"])
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("sk-...（本地模型可留空或随便填）")
        show = QCheckBox("显示")
        show.toggled.connect(lambda on: self.key.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        key_row = QHBoxLayout()
        key_row.addWidget(self.key, 1)
        key_row.addWidget(show)
        form.addRow("API Key", key_row)

        self.model = self._combo([cfg["model"], *MODELS], cfg["model"])
        fetch = QPushButton("获取列表")
        fetch.setToolTip("从上面的服务地址读取可用模型")
        fetch.clicked.connect(self._fetch_models)
        model_row = QHBoxLayout()
        model_row.addWidget(self.model, 1)
        model_row.addWidget(fetch)
        form.addRow("模型", model_row)
        self.target = self._combo(LANGUAGES, cfg["target_lang"])
        form.addRow("翻译成（目标语言）", self.target)
        self.fallback = self._combo(LANGUAGES, cfg["fallback_lang"])
        form.addRow("原文已是目标语言时，翻译成", self.fallback)

        self.no_think = QCheckBox("本地模型：关闭思考模式（Qwen3 等，翻译更快）")
        self.no_think.setChecked(bool(cfg["disable_thinking"]))
        form.addRow("", self.no_think)

        self.learn = QCheckBox("学习模式：讲解重点词汇（带注音）、语法点、地道例句")
        self.learn.setChecked(bool(cfg["learn_mode"]))
        form.addRow("", self.learn)
        self.level = self._combo(LEVELS, cfg["learn_level"])
        form.addRow("学习者水平", self.level)

        self.max_chars = QSpinBox()
        self.max_chars.setRange(200, 100000)
        self.max_chars.setSingleStep(1000)
        self.max_chars.setSuffix(" 字")
        self.max_chars.setValue(int(cfg["max_chars"]))
        self.max_chars.setToolTip("超出的部分不会翻译；译文本身另有约 8K token 的输出上限，超长文本建议分段")
        form.addRow("单次最多翻译", self.max_chars)

        self.hotkey = self._combo(HOTKEY_PRESETS, cfg["hotkey"])
        form.addRow("触发方式", self.hotkey)
        hint = QLabel("ctrl+c+c = 双击 Ctrl+C；也可手写组合键，如 ctrl+alt+t")
        hint.setStyleSheet("color: gray;")
        form.addRow("", hint)

        self.autostart = QCheckBox("开机自动启动")
        self.autostart.setChecked(is_autostart())
        form.addRow("", self.autostart)

        bal = QPushButton("查询余额与用量")
        bal.clicked.connect(self.balance_requested)
        ok = QPushButton("保存")
        ok.setDefault(True)
        ok.clicked.connect(self._save)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(bal)
        buttons.addStretch(1)
        buttons.addWidget(ok)
        buttons.addWidget(cancel)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addLayout(buttons)

    @staticmethod
    def _combo(items: list[str], current: str) -> QComboBox:
        box = QComboBox()
        box.setEditable(True)  # 允许手写列表之外的语言 / 模型 / 热键
        box.addItems(items)
        box.setCurrentText(current)
        return box

    def _stash(self):
        """把界面上当前服务的地址 / key / 模型记进内存，切换服务或保存时用。"""
        self.profiles[self._current] = {
            "base_url": self.base_url.text().strip(),
            "api_key": self.key.text().strip(),
            "model": self.model.currentText().strip(),
        }

    def _on_provider(self, _):
        self._stash()  # 先存下刚才这个服务填的内容，再换成新服务各自保存的内容（没有就用预设）
        self._current = self.provider.currentText()
        url0, model0 = PROVIDERS[self._current]
        p = self.profiles.get(self._current, {})
        self.base_url.setText(p.get("base_url") or url0)
        self.key.setText(p.get("api_key", ""))
        self.model.clear()
        self.model.addItems([m for m in (p.get("model"), model0, *(MODELS if self._current == "DeepSeek" else [])) if m])
        self.model.setCurrentText(p.get("model") or model0)

    def _fetch_models(self):
        url = self.base_url.text().strip()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            models = list_models({"base_url": url, "api_key": self.key.text().strip()})
        except Exception as e:  # noqa: BLE001 - 连不上 / 地址错 / 服务不支持 /models，都如实告诉用户
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(
                self, "获取模型列表失败",
                f"访问 {url} 失败：\n{e}\n\n"
                "请检查：服务地址和端口是否正确、模型服务是否已启动。\n"
                "模型如果跑在另一台电脑上，不能填 localhost，要填那台电脑的 IP（如 http://192.168.1.10:1234/v1）。\n"
                "也可以直接在「模型」框里手写模型名。",
            )
            return
        QApplication.restoreOverrideCursor()
        current = self.model.currentText()
        self.model.clear()
        self.model.addItems(models)
        self.model.setCurrentText(current if current in models or not models else models[0])
        QMessageBox.information(self, "获取模型列表", f"找到 {len(models)} 个模型，已填入下拉框。")

    def _save(self):
        hotkey = self.hotkey.currentText().strip().lower()
        if hotkey != DOUBLE_COPY:
            try:
                parse_hotkey(hotkey)
            except ValueError as e:
                QMessageBox.warning(self, "触发方式无效", str(e))
                return
        try:
            set_autostart(self.autostart.isChecked())
        except OSError as e:
            QMessageBox.warning(self, "开机自启设置失败", str(e))
        self._stash()
        cur = self.profiles[self._current]
        save_config_values({
            "provider": self._current,
            "profiles": self.profiles,
            # 顶层三项与当前服务保持一致，方便直接看文件；运行时以 profiles 为准
            "base_url": cur["base_url"] or PROVIDERS[self._current][0] or PROVIDERS["DeepSeek"][0],
            "api_key": cur["api_key"],
            "model": cur["model"] or PROVIDERS[self._current][1] or "deepseek-chat",
            "target_lang": self.target.currentText().strip(),
            "fallback_lang": self.fallback.currentText().strip(),
            "disable_thinking": self.no_think.isChecked(),
            "learn_mode": self.learn.isChecked(),
            "learn_level": self.level.currentText().strip() or DEFAULT_LEVEL,
            "max_chars": self.max_chars.value(),
            "hotkey": hotkey,
        })
        self.saved.emit()
        self.accept()
