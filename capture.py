"""全局热键 + 抓取当前选中文字（模拟 Ctrl+C 读剪贴板，并尽量还原原剪贴板文本）。"""
import ctypes
import time
from ctypes import wintypes

import win32clipboard
import win32con
from PySide6.QtCore import QThread, Signal

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_NOREPEAT = 0x4000
MODIFIERS = {"alt": 0x1, "ctrl": 0x2, "shift": 0x4, "win": 0x8}
VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN = 0x10, 0x11, 0x12, 0x5B, 0x5C
KEYEVENTF_KEYUP = 0x2


def parse_hotkey(spec: str) -> tuple[int, int]:
    """'ctrl+alt+t' -> (modifier mask, virtual key)。"""
    mods, vk = 0, 0
    for part in (p.strip().lower() for p in spec.split("+")):
        if part in MODIFIERS:
            mods |= MODIFIERS[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            vk = 0x70 + int(part[1:]) - 1
        else:
            raise ValueError(f"无法识别的热键部分: {part!r}")
    if not mods or not vk:
        raise ValueError(f"热键需要至少一个修饰键和一个主键: {spec!r}")
    return mods, vk


class HotkeyThread(QThread):
    triggered = Signal()
    failed = Signal(str)

    def __init__(self, spec: str):
        super().__init__()
        self._mods, self._vk = parse_hotkey(spec)
        self._spec = spec
        self._tid = 0

    def run(self):
        self._tid = kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, 1, self._mods | MOD_NOREPEAT, self._vk):
            self.failed.emit(f"热键 {self._spec} 注册失败，可能已被其他程序占用")
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                self.triggered.emit()
        user32.UnregisterHotKey(None, 1)

    def stop(self):
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        self.wait(1000)


DOUBLE_COPY = "ctrl+c+c"
WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
DOUBLE_COPY_WINDOW = 0.5  # 两次 Ctrl+C 的最大间隔（秒）

_LRESULT = ctypes.c_ssize_t
_HOOKPROC = ctypes.WINFUNCTYPE(_LRESULT, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t]
user32.CallNextHookEx.restype = _LRESULT
kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class DoubleCopyThread(QThread):
    """低级键盘钩子：检测 Ctrl+C 连按两次。不拦截按键，所以两次 Ctrl+C 照常复制。"""
    triggered = Signal()
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self._tid = 0
        self._last = 0.0
        self._c_down = False
        self._proc = _HOOKPROC(self._hook)  # 必须持有引用，否则被 GC 后钩子崩溃

    def _hook(self, code, wparam, lparam):
        if code >= 0:
            info = ctypes.cast(lparam, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
            if info.vkCode == ord("C"):
                if wparam in (WM_KEYUP, WM_SYSKEYUP):
                    self._c_down = False
                elif wparam in (WM_KEYDOWN, WM_SYSKEYDOWN) and not self._c_down:  # 忽略按住的自动重复
                    self._c_down = True
                    if user32.GetAsyncKeyState(VK_CONTROL) & 0x8000:
                        now = time.time()
                        if now - self._last <= DOUBLE_COPY_WINDOW:
                            self._last = 0.0
                            self.triggered.emit()
                        else:
                            self._last = now
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def run(self):
        self._tid = kernel32.GetCurrentThreadId()
        hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        if not hook:
            self.failed.emit("键盘钩子安装失败，无法使用 Ctrl+双击C")
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass
        user32.UnhookWindowsHookEx(hook)

    def stop(self):
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        self.wait(1000)


def make_trigger(spec: str) -> QThread:
    return DoubleCopyThread() if spec.strip().lower() == DOUBLE_COPY else HotkeyThread(spec)


def read_copied_text() -> str | None:
    """双击 Ctrl+C 模式：选中文字已被用户自己复制到剪贴板，直接读，不碰剪贴板。"""
    time.sleep(0.12)  # 等第二次复制完成
    text = _get_clipboard_text()
    return text.strip() if text and text.strip() else None


def _wait_modifiers_released(timeout: float = 1.0):
    """热键触发时修饰键还按着，直接发 Ctrl+C 会变成 Ctrl+Alt+C，先等用户松手。"""
    deadline = time.time() + timeout
    keys = (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN)
    while time.time() < deadline and any(user32.GetAsyncKeyState(k) & 0x8000 for k in keys):
        time.sleep(0.01)


def _get_clipboard_text() -> str | None:
    for _ in range(10):
        try:
            win32clipboard.OpenClipboard()
        except Exception:
            time.sleep(0.02)  # 剪贴板被源程序占用，稍后重试
            continue
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            return None
        finally:
            win32clipboard.CloseClipboard()
    return None


def _set_clipboard_text(text: str):
    for _ in range(10):
        try:
            win32clipboard.OpenClipboard()
        except Exception:
            time.sleep(0.02)
            continue
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
            return
        finally:
            win32clipboard.CloseClipboard()


def _send_ctrl_c():
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(ord("C"), 0, 0, 0)
    user32.keybd_event(ord("C"), 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


def grab_selection() -> str | None:
    """返回当前选中的文字；没有选中（或目标程序不响应 Ctrl+C）返回 None。

    原剪贴板若是文本会被还原；若原本是图片/文件等非文本内容，则会被选中文字覆盖。
    """
    _wait_modifiers_released()
    old = _get_clipboard_text()
    seq = user32.GetClipboardSequenceNumber()
    _send_ctrl_c()
    for _ in range(30):
        time.sleep(0.02)
        if user32.GetClipboardSequenceNumber() != seq:
            break
    else:
        return None
    time.sleep(0.03)
    new = _get_clipboard_text()
    if old is not None and new != old:
        _set_clipboard_text(old)
    return new.strip() if new and new.strip() else None
