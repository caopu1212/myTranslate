"""划词翻译：选中文字 → 热键 → 浮窗显示 DeepSeek 译文 / 学习讲解。托盘常驻。"""
import os
import sys
import threading

from PySide6.QtCore import QObject, QPoint, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtGui import QAction, QActionGroup, QColor, QCursor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from capture import DOUBLE_COPY, grab_selection, make_trigger, read_copied_text
from llm import (
    LANGUAGES, ensure_config_file, fetch_balance, is_deepseek, is_local, load_config, record_usage,
    save_config_value, stream_translate, usage_summary,
)
from popup import Popup
from settings import SettingsDialog


class Bridge(QObject):
    """工作线程 → 主线程。gen 用来丢弃已被新一次触发取代的旧结果。"""
    started = Signal(int, str, str)  # gen, 原文, 模式（translate / learn / summary）
    chunk = Signal(int, str)
    done = Signal(int, str)
    error = Signal(int, str)
    info = Signal(str)


def make_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#1f6feb"))
    p.setPen(QColor(0, 0, 0, 0))
    p.drawRoundedRect(2, 2, 60, 60, 14, 14)
    p.setPen(QColor("white"))
    p.setFont(QFont("Microsoft YaHei UI", 30, QFont.Bold))
    p.drawText(pm.rect(), 0x84, "译")  # AlignCenter
    p.end()
    return QIcon(pm)


SERVER_NAME = "selection-translator-single-instance"


def notify_running_instance() -> bool:
    """已有实例在运行则通知它弹提示并返回 True。"""
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if sock.waitForConnected(500):
        sock.write(b"again")
        sock.waitForBytesWritten(500)
        sock.disconnectFromServer()
        return True
    return False


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    if notify_running_instance():
        sys.exit(0)
    cfg = load_config()
    popup = Popup(cfg["popup_width"], cfg["popup_max_height"], cfg["learn_popup_width"], cfg["learn_popup_max_height"])
    bridge = Bridge()
    state = {"gen": 0, "pos": QPoint()}

    def job(gen: int, text: str | None = None, mode: str | None = None):
        try:
            # 每次触发都重新读配置：改了 API Key / 语言 / 学习模式无需重启（热键改动仍需重启）
            run_cfg = {**cfg, **load_config()}
            run_cfg["hotkey"] = cfg["hotkey"]
            if text is None:
                text = read_copied_text() if cfg["hotkey"].lower() == DOUBLE_COPY else grab_selection()
            if not text:
                bridge.error.emit(gen, "没有检测到选中的文字。\n（终端等程序里 Ctrl+C 不是复制，暂不支持）")
                return
            if not run_cfg["api_key"] and not is_local(run_cfg):
                bridge.error.emit(gen, "未配置 API Key。请单击托盘图标打开设置，填入 API Key（用本地模型的话，把服务地址改成本机或内网地址即可不填）。")
                return
            notes = []
            if len(text) > run_cfg["max_chars"]:
                notes.append(f"原文 {len(text)} 字，只翻译了前 {run_cfg['max_chars']} 字")
                text = text[: run_cfg["max_chars"]]
            mode = mode or ("learn" if run_cfg["learn_mode"] else "translate")
            bridge.started.emit(gen, text, mode)
            usage: dict = {}
            for piece in stream_translate(run_cfg, text, usage, mode):
                if gen != state["gen"]:
                    return  # 已有新的触发，放弃旧请求
                bridge.chunk.emit(gen, piece)
            if usage.get("finish_reason") == "length":
                notes.append("译文达到输出上限被截断，请分段翻译")
            if usage:
                tokens, cost = record_usage(run_cfg, usage)
                notes.append(f"≈¥{cost:.4f}" if cost is not None else f"{tokens} token")
            bridge.done.emit(gen, " · ".join(notes))
        except Exception as e:  # noqa: BLE001 - 任何失败都要显示给用户
            bridge.error.emit(gen, f"翻译失败：{e}")

    def on_hotkey():
        state["gen"] += 1
        state["pos"] = QCursor.pos()
        threading.Thread(target=job, args=(state["gen"],), daemon=True).start()

    def rerun(src: str, mode: str):
        """浮窗上点「讲解」/「总结」：对同一段原文换个模式重跑，浮窗留在原位置。"""
        state["gen"] += 1
        state["pos"] = popup.pos() - QPoint(8, 12)
        threading.Thread(target=job, args=(state["gen"], src, mode), daemon=True).start()

    bridge.started.connect(lambda g, t, mode: g == state["gen"] and popup.begin(t, state["pos"], mode))
    bridge.chunk.connect(lambda g, s: g == state["gen"] and popup.append(s))
    bridge.done.connect(lambda g, c: g == state["gen"] and popup.finish(c))
    bridge.error.connect(lambda g, m: g == state["gen"] and popup.show_error(m, state["pos"]))
    bridge.info.connect(lambda m: popup.show_error(m, QCursor.pos(), status="余额与用量"))
    popup.explain_requested.connect(lambda src: rerun(src, "learn"))
    popup.summarize_requested.connect(lambda src: rerun(src, "summary"))

    def show_balance():
        def work():
            run_cfg = {**cfg, **load_config()}
            if not is_deepseek(run_cfg):
                bridge.info.emit(f"当前服务（{run_cfg['base_url']}）不支持余额查询，只有 DeepSeek 可以。\n\n{usage_summary()}")
                return
            if not run_cfg["api_key"]:
                bridge.info.emit("未配置 API Key，无法查询余额。")
                return
            try:
                bal = fetch_balance(run_cfg)
            except Exception as e:  # noqa: BLE001
                bal = f"余额查询失败：{e}"
            bridge.info.emit(f"{bal}\n\n{usage_summary()}\n（用量为本软件本地估算）")
        threading.Thread(target=work, daemon=True).start()

    trigger = {"thread": None}

    def start_trigger(spec: str):
        """启动（或重启）热键监听；设置里改了触发方式后即时生效。"""
        if trigger["thread"]:
            trigger["thread"].stop()
        cfg["hotkey"] = spec
        t = make_trigger(spec)
        t.triggered.connect(on_hotkey)
        t.failed.connect(lambda m: popup.show_error(m, QCursor.pos()))
        t.start()
        trigger["thread"] = t
        tray.setToolTip(f"划词翻译（{spec}）")

    # ---- 托盘菜单 ----
    tray = QSystemTrayIcon(make_icon(), app)
    menu = QMenu()
    settings_dlg = {"w": None}

    def open_settings():
        if settings_dlg["w"] and settings_dlg["w"].isVisible():
            settings_dlg["w"].raise_()
            settings_dlg["w"].activateWindow()
            return
        dlg = SettingsDialog(tray.icon())
        dlg.balance_requested.connect(show_balance)
        dlg.saved.connect(lambda: start_trigger(load_config()["hotkey"]) if load_config()["hotkey"] != cfg["hotkey"] else None)
        settings_dlg["w"] = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    set_action = QAction("设置…", menu)
    set_action.triggered.connect(open_settings)
    menu.addAction(set_action)
    menu.addSeparator()
    tray.activated.connect(lambda r: r in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) and open_settings())

    def lang_menu(title: str, key: str) -> dict[str, QAction]:
        sub = menu.addMenu(title)
        group = QActionGroup(sub)
        acts = {}
        for name in LANGUAGES:
            act = QAction(name, sub, checkable=True)
            act.triggered.connect(lambda _=False, n=name: save_config_value(key, n))
            group.addAction(act)
            sub.addAction(act)
            acts[name] = act
        return acts

    target_acts = lang_menu("翻译成…（目标语言）", "target_lang")
    fallback_acts = lang_menu("原文已是目标语言时，翻译成…", "fallback_lang")
    learn_action = QAction("学习模式（讲解语法 / 读音 / 例句）", menu, checkable=True)
    learn_action.triggered.connect(lambda on: save_config_value("learn_mode", bool(on)))
    menu.addAction(learn_action)
    menu.addSeparator()

    def refresh_menu():
        c = load_config()
        for name, act in target_acts.items():
            act.setChecked(name == c["target_lang"])
        for name, act in fallback_acts.items():
            act.setChecked(name == c["fallback_lang"])
        learn_action.setChecked(bool(c["learn_mode"]))

    menu.aboutToShow.connect(refresh_menu)
    refresh_menu()

    bal_action = QAction("余额与用量", menu)
    bal_action.triggered.connect(show_balance)
    menu.addAction(bal_action)
    cfg_action = QAction("打开配置文件（填 API Key）", menu)
    cfg_action.triggered.connect(lambda: os.startfile(ensure_config_file()))
    menu.addAction(cfg_action)
    quit_action = QAction("退出", menu)
    quit_action.triggered.connect(app.quit)
    menu.addAction(quit_action)
    tray.setContextMenu(menu)
    tray.show()
    start_trigger(cfg["hotkey"])

    def how_to_use() -> str:
        c = load_config()
        key = "快速按两次 Ctrl+C" if c["hotkey"].lower() == DOUBLE_COPY else c["hotkey"]
        msg = f"选中文字后，{key} 即可翻译。\n程序常驻在右下角托盘，单击托盘图标打开设置。"
        return msg if c["api_key"] or is_local(c) else msg + "\n\n⚠ 还没填 API Key，请先在设置里填写。"

    # 用自己的浮窗而不是系统 Toast：Toast 会被"专注助手/勿扰"吞掉，浮窗一定看得到
    popup.notice("已启动。\n" + how_to_use(), status="划词翻译", seconds=6)

    # 重复启动：新进程会连上这个本地服务然后退出，这里提示"已在运行"
    server = QLocalServer(app)
    QLocalServer.removeServer(SERVER_NAME)
    server.listen(SERVER_NAME)
    server.newConnection.connect(
        lambda: (server.nextPendingConnection(), popup.notice("已经在运行了，不用重复打开。\n" + how_to_use(), seconds=6))
    )

    app.aboutToQuit.connect(lambda: trigger["thread"].stop())
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
