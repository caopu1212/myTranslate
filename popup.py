"""译文浮窗：无边框、置顶、可拖动，失去焦点自动关闭（可📌固定）。"""
from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QVBoxLayout, QWidget,
)

from render import md_to_html

STYLE = """
#card { background: #1f2329; border: 1px solid #3a4049; border-radius: 10px; }
QLabel#src { color: #8b949e; font-size: 12px; }
QLabel#status { color: #d29922; font-size: 12px; }
QTextEdit { background: transparent; border: none; color: #e6edf3;
            font-family: "Microsoft YaHei UI"; font-size: 15px; selection-background-color: #1f6feb; }
QPushButton { background: transparent; color: #8b949e; border: none; border-radius: 4px;
              padding: 2px 6px; font-size: 13px; }
QPushButton:hover { background: #30363d; color: #e6edf3; }
QPushButton:checked { color: #58a6ff; }
"""


class Popup(QWidget):
    explain_requested = Signal(str)  # 点「讲解」：携带完整原文
    summarize_requested = Signal(str)  # 点「总结」：携带完整原文

    def __init__(self, width: int = 440, max_height: int = 420, learn_width: int = 560, learn_max_height: int = 720):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(STYLE)
        self._sizes = {False: (width, max_height), True: (learn_width, learn_max_height)}
        self._max_h = max_height
        self._markdown = False
        self._text = ""
        self._text_token = None
        self._src = ""
        self._was_active = False
        self._drag: QPoint | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)  # 给阴影留位置
        self.card = QFrame(objectName="card")
        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 140))
        self.card.setGraphicsEffect(shadow)
        outer.addWidget(self.card)

        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(12, 8, 8, 10)
        lay.setSpacing(4)

        head = QHBoxLayout()
        self.src_label = QLabel(objectName="src")
        self.status = QLabel(objectName="status")
        head.addWidget(self.src_label, 1)
        head.addWidget(self.status)
        self.explain_btn = QPushButton("讲解")
        self.explain_btn.setToolTip("用学习模式详细讲解这句话的语法、读音和例句")
        self.explain_btn.clicked.connect(lambda: self.explain_requested.emit(self._src))
        self.summary_btn = QPushButton("总结")
        self.summary_btn.setToolTip("用要点概括这段文字（用你的目标语言写）")
        self.summary_btn.clicked.connect(lambda: self.summarize_requested.emit(self._src))
        self.copy_btn = QPushButton("复制")
        self.copy_btn.clicked.connect(self._copy)
        self.pin_btn = QPushButton("固定")
        self.pin_btn.setCheckable(True)
        close_btn = QPushButton("✕")
        close_btn.clicked.connect(self.hide)
        for b in (self.summary_btn, self.explain_btn, self.copy_btn, self.pin_btn, close_btn):
            b.setCursor(Qt.PointingHandCursor)
            head.addWidget(b)
        lay.addLayout(head)

        self.body = QTextEdit()
        self.body.setReadOnly(True)
        lay.addWidget(self.body)

        self.setFixedWidth(width)

    # ---- 对外接口 ----
    def begin(self, source: str, pos: QPoint, mode: str = "translate"):
        rich = mode != "translate"  # 讲解 / 总结 都是带标题和列表的结构化输出
        self._text = ""
        self._text_token = None
        self._src = source
        self._markdown = rich
        self._max_h = self._sizes[rich][1]
        self.setFixedWidth(self._sizes[rich][0])
        self.explain_btn.setVisible(mode != "learn")
        self.summary_btn.setVisible(mode != "summary")
        self.body.clear()
        self.status.setText({"translate": "翻译中…", "learn": "讲解中…", "summary": "总结中…"}[mode])
        self.src_label.setText(self._elide(source))
        self._fit()
        self._place(pos)
        self.show()
        self.raise_()
        self.activateWindow()

    def append(self, chunk: str):
        self._text += chunk
        if self._markdown:  # 学习模式：渲染 Markdown，并保持用户当前的滚动位置
            bar = self.body.verticalScrollBar()
            pos = bar.value()
            self.body.setHtml(md_to_html(self._text))
            bar.setValue(pos)
        else:
            self.body.setPlainText(self._text)
            self.body.moveCursor(self.body.textCursor().MoveOperation.End)
        self._fit()

    def finish(self, info: str = ""):
        self.status.setText(info)

    def show_error(self, msg: str, pos: QPoint | None = None, status: str = "出错", activate: bool = True):
        self._text = ""
        self._text_token = None  # 取消尚未到期的自动隐藏
        self._markdown = False
        self._max_h = self._sizes[False][1]
        self.setFixedWidth(self._sizes[False][0])
        self.explain_btn.setVisible(False)
        self.summary_btn.setVisible(False)
        self.src_label.setText("")
        self.status.setText(status)
        self.body.setPlainText(msg)
        self._fit()
        if pos is not None:
            self._place(pos)
        self.show()
        self.raise_()
        if activate:
            self.activateWindow()

    def notice(self, msg: str, status: str = "划词翻译", seconds: int = 5):
        """屏幕右下角的临时提示：不抢焦点，几秒后自动消失。"""
        self.show_error(msg, None, status=status, activate=False)
        r = QGuiApplication.primaryScreen().availableGeometry()
        self.move(r.right() - self.width() - 16, r.bottom() - self.height() - 16)
        token = self._text_token = object()
        QTimer.singleShot(seconds * 1000, lambda: self.hide() if self._text_token is token and not self._was_active else None)

    # ---- 内部 ----
    def _elide(self, s: str) -> str:
        s = " ".join(s.split())
        return self.src_label.fontMetrics().elidedText(s, Qt.ElideRight, self.width() - 200)

    def _copy(self):
        QGuiApplication.clipboard().setText(self._text)
        self.copy_btn.setText("已复制")

    def _fit(self):
        self.copy_btn.setText("复制")
        doc = self.body.document()
        doc.setTextWidth(self.body.viewport().width())
        h = min(int(doc.size().height()) + 10, self._max_h)
        self.body.setFixedHeight(max(h, 36))
        self.adjustSize()
        self._clamp()

    def _screen_rect(self, pos: QPoint):
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        return screen.availableGeometry()

    def _place(self, pos: QPoint):
        self.move(pos.x() + 8, pos.y() + 12)
        self._clamp()

    def _clamp(self):
        r = self._screen_rect(self.frameGeometry().center())
        x = min(max(self.x(), r.left()), r.right() - self.width())
        y = min(max(self.y(), r.top()), r.bottom() - self.height())
        self.move(x, y)

    # ---- 事件 ----
    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(e)

    def changeEvent(self, e):
        if e.type() == QEvent.ActivationChange:
            if self.isActiveWindow():
                self._was_active = True
            elif self._was_active and not self.pin_btn.isChecked():
                self.hide()
        super().changeEvent(e)

    def hideEvent(self, e):
        self._was_active = False
        super().hideEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None
