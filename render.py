"""把学习模式的 Markdown 输出渲染成排版整齐的 Qt 富文本（QTextEdit 只支持 HTML 的一个子集：表格 + 行内样式）。"""
import html
import re

ACCENT, TEXT, DIM, READING = "#58a6ff", "#e6edf3", "#8b949e", "#d29922"
ROW_BG = ("#262c36", "#1f2329")
KANA_PAREN = re.compile(r"[（(]([ぁ-ゟァ-ヿー・\s]+)[）)]")


def _inline(s: str) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", rf'<b style="color:{TEXT}">\1</b>', s)
    s = s.replace("`", "")
    # 纯假名的括号 = 注音，用小号黄字与正文区分
    return KANA_PAREN.sub(rf'<span style="color:{READING};font-size:12px">（\1）</span>', s)


def parse(md: str) -> list[tuple[str, list[tuple[str, str]]]]:
    """-> [(章节标题, [("item"|"para", 文本)])]；缩进的续行并入上一条。"""
    sections: list[tuple[str, list[tuple[str, str]]]] = []
    for raw in md.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            sections.append((line.lstrip("#").strip(), []))
            continue
        if not sections:
            sections.append(("", []))
        entries = sections[-1][1]
        m = re.match(r"[-*•]\s+(.*)", line)
        if m:
            entries.append(("item", m.group(1)))
        elif line.startswith("**"):  # 模型有时漏掉「- 」，以加粗开头的行也当作一条
            entries.append(("item", line))
        elif raw[:1] in " \t" and entries and entries[-1][0] == "item":
            entries[-1] = ("item", entries[-1][1] + " " + line)
        else:
            entries.append(("para", line))
    return sections


def _split_head(text: str) -> tuple[str, str]:
    m = re.match(r"(.+?)\s*[：:]\s*(.*)", text)
    return (m.group(1), m.group(2)) if m else ("", text)


def _left_cell(head: str) -> str:
    """「**词（读音）**（补充）」→ 词加粗、读音小号黄字、补充说明小号灰字。"""
    m = re.match(r"\*\*(.+?)\*\*\s*(.*)", head)
    bold, extra = (m.group(1), m.group(2)) if m else (head, "")
    reading = ""
    rm = re.match(r"(.+?)[（(](.+?)[）)]$", bold)
    if rm:
        bold, reading = rm.group(1), rm.group(2)
    out = f'<b style="color:{ACCENT};font-size:14px">{html.escape(bold, quote=False)}</b>'
    if reading:
        out += f'<br><span style="color:{READING};font-size:12px">{html.escape(reading, quote=False)}</span>'
    if extra:
        out += f'<br><span style="color:{DIM};font-size:12px">{html.escape(extra, quote=False)}</span>'
    return out


def _table(rows: list[str]) -> str:
    return f'<table width="100%" cellspacing="0" cellpadding="6">{"".join(rows)}</table>'


def _two_col(entries) -> str:
    rows = []
    for i, (_, text) in enumerate(entries):  # 漏掉「- 」的行（para）也要显示，不能丢
        head, body = _split_head(text)
        bg = ROW_BG[i % 2]
        if head:
            rows.append(
                f'<tr bgcolor="{bg}"><td width="150" valign="top">{_left_cell(head)}</td>'
                f'<td valign="top" style="color:{TEXT}">{_inline(body)}</td></tr>'
            )
        else:
            rows.append(f'<tr bgcolor="{bg}"><td colspan="2" style="color:{TEXT}">{_inline(body)}</td></tr>')
    return _table(rows)


def _examples(entries) -> str:
    rows = []
    for i, (_, text) in enumerate(entries):
        text = text.replace("**", "")
        parts = re.split(r"\s*[—–]+\s*|\s+-\s+", text, maxsplit=1)
        jp = f'<span style="color:{TEXT};font-size:14px">{_inline(parts[0])}</span>'
        zh = f'<br><span style="color:{DIM};font-size:13px">{_inline(parts[1])}</span>' if len(parts) > 1 else ""
        rows.append(f'<tr bgcolor="{ROW_BG[i % 2]}"><td>{jp}{zh}</td></tr>')
    return _table(rows)


SEG_COLORS = ("#e6edf3", "#79c0ff")


def _segmented(entries) -> str:
    """断句：含「／」的那一行渲染成彩色分段卡片，其余条目走两列表格。"""
    cards, rest = [], []
    for e in entries:
        (cards if e[0] == "para" and "／" in e[1] else rest).append(e)
    out = ""
    for _, text in cards:
        segs = [s.strip() for s in text.split("／") if s.strip()]
        sep = f'<span style="color:{DIM}"> ／ </span>'
        body = sep.join(
            f'<span style="color:{SEG_COLORS[i % 2]};font-size:15px">{_inline(s)}</span>' for i, s in enumerate(segs)
        )
        out += _table([f'<tr bgcolor="{ROW_BG[0]}"><td>{body}</td></tr>'])
    return out + (_two_col(rest) if rest else "")


def _plain(entries) -> str:
    out = []
    for kind, text in entries:
        bullet = f'<span style="color:{ACCENT}">•</span> ' if kind == "item" else ""
        out.append(f'<p style="margin:3px 0;color:{TEXT}">{bullet}{_inline(text)}</p>')
    return "".join(out)


def _translation(entries) -> str:
    body = "<br>".join(_inline(t) for _, t in entries)
    return _table([f'<tr bgcolor="{ROW_BG[0]}"><td style="color:{TEXT};font-size:15px">{body}</td></tr>'])


def md_to_html(md: str) -> str:
    parts = ['<div style="font-size:14px">']
    for title, entries in parse(md):
        if not entries:  # 空章节（模型没写内容，或流式输出刚到标题）不显示空标题
            continue
        if title:
            parts.append(
                f'<p style="margin:14px 0 4px 0"><span style="color:{ACCENT};font-size:12px;'
                f'font-weight:bold">▍{html.escape(title, quote=False)}</span></p>'
            )
        if "译文" in title:
            parts.append(_translation(entries))
        elif "断句" in title:
            parts.append(_segmented(entries))
        elif "词汇" in title or "语法" in title or "变形" in title:
            parts.append(_two_col(entries))
        elif "例句" in title:
            parts.append(_examples(entries))
        else:
            parts.append(_plain(entries))
    parts.append("</div>")
    return "".join(parts)
