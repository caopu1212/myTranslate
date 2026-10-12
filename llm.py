"""OpenAI 兼容接口（DeepSeek / OpenAI / 通义千问 / 本地 LM Studio、Ollama 等）流式翻译 / 学习讲解。"""
import json
import os
import sys
import time
from pathlib import Path

import ipaddress
from urllib.parse import urlparse

import httpx
from openai import BadRequestError, OpenAI

# 打包后 config.json 放在 %APPDATA%\划词翻译，重新打包（会清空 dist）也不会丢配置；源码运行时仍用项目目录
if getattr(sys, "frozen", False):
    _BASE = Path(os.environ.get("APPDATA", Path.home())) / "划词翻译"
    _BASE.mkdir(parents=True, exist_ok=True)
else:
    _BASE = Path(__file__).parent
CONFIG_PATH = _BASE / "config.json"
USAGE_PATH = _BASE / "usage.jsonl"
DEFAULTS = {
    "provider": "DeepSeek",  # 当前使用的模型服务，见 PROVIDERS
    "profiles": {},  # {服务名: {base_url, api_key, model}}，每个服务各存一份，切换时不会互相覆盖
    "api_key": "",  # 以下三项仅用于迁移旧配置；运行时由当前 profile 解析得到
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "target_lang": "简体中文",  # 默认译成这个语言（托盘菜单可切换）
    "fallback_lang": "English",  # 原文本身已是 target_lang 时译成这个（托盘菜单可切换）
    "learn_mode": False,  # 学习模式：讲解重点词汇（带注音）、语法点、例句
    "learn_level": "中级（日语 N3～N2 水平）",  # 决定哪些词汇/语法算"值得讲"
    "hotkey": "ctrl+c+c",  # 双击 Ctrl+C；也可写成 "ctrl+alt+t" 这类组合键
    "disable_thinking": True,  # 本地模型：关闭思考模式（Qwen3 等），翻译快很多
    "max_chars": 10000,  # 单次最多翻译的字数，超出部分截掉（设置窗口可改）
    "popup_width": 440,
    "popup_max_height": 420,
    "learn_popup_width": 560,
    "learn_popup_max_height": 720,
    # 单价（元 / 百万 token），仅用于本地估算费用，请按 DeepSeek 官网价格页核对
    "price_hit": 0.2,
    "price_miss": 2.0,
    "price_out": 3.0,
}
# 托盘菜单里可选的语言；config.json 里也可手写其他语言名
LANGUAGES = ["简体中文", "繁體中文", "English", "日本語", "한국어", "Français", "Deutsch", "Español", "Русский", "Italiano", "Português"]


# 设置窗口里的服务商预设：名称 -> (base_url, 默认模型)。都是 OpenAI 兼容接口
PROVIDERS = {
    "DeepSeek": ("https://api.deepseek.com", "deepseek-chat"),
    "OpenAI": ("https://api.openai.com/v1", "gpt-4o-mini"),
    "通义千问（阿里云百炼）": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    "本地 LM Studio": ("http://localhost:1234/v1", ""),
    "本地 Ollama": ("http://localhost:11434/v1", ""),
    "自定义": ("", ""),
}


def _host(cfg: dict) -> str:
    return urlparse(cfg["base_url"]).hostname or ""


def is_deepseek(cfg: dict) -> bool:
    return _host(cfg).endswith("deepseek.com")


def is_local(cfg: dict) -> bool:
    """本机或内网地址：本地部署的模型通常不需要真实的 API Key。"""
    h = _host(cfg)
    if h in ("localhost", "::1") or h.endswith((".local", ".lan")):
        return True
    try:
        return ipaddress.ip_address(h).is_private
    except ValueError:
        return False


def list_models(cfg: dict) -> list[str]:
    """GET {base_url}/models，返回服务端提供的模型 id（用于设置窗口的"获取列表"）。"""
    headers = {"Authorization": f"Bearer {cfg['api_key'] or 'none'}"}
    r = httpx.get(cfg["base_url"].rstrip("/") + "/models", headers=headers, timeout=8)
    r.raise_for_status()
    return sorted(m["id"] for m in r.json().get("data", []))


def _resolve_profiles(cfg: dict) -> None:
    """每个模型服务各存一份 {base_url, api_key, model}（cfg["profiles"]），cfg["provider"] 指向当前用的那份。

    旧版配置只有顶层的 base_url / api_key / model：迁移成一份 profile，不丢数据。
    解析后把当前服务的三项写回 cfg 顶层，其余代码照旧只读 cfg["base_url"] 等。
    """
    profiles = {k: dict(v) for k, v in (cfg.get("profiles") or {}).items()}
    provider = cfg.get("provider")
    if not profiles:
        provider = next((n for n, (u, _) in PROVIDERS.items() if u and u == cfg["base_url"]), "自定义")
        profiles[provider] = {"base_url": cfg["base_url"], "api_key": cfg["api_key"], "model": cfg["model"]}
    if provider not in PROVIDERS:
        provider = "自定义" if "自定义" in profiles else next(iter(profiles))
    url0, model0 = PROVIDERS.get(provider, ("", ""))
    p = profiles.setdefault(provider, {})
    cfg["provider"], cfg["profiles"] = provider, profiles
    cfg["base_url"] = p.get("base_url") or url0 or DEFAULTS["base_url"]
    cfg["api_key"] = p.get("api_key", "")
    cfg["model"] = p.get("model") or model0 or DEFAULTS["model"]


def load_config(use_env: bool = True) -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    _resolve_profiles(cfg)
    # 设置界面传 False，显示文件里真实保存的 key；环境变量只对 DeepSeek 生效，免得把 DeepSeek 的 key 发给别的服务
    if use_env and is_deepseek(cfg):
        cfg["api_key"] = os.environ.get("DEEPSEEK_API_KEY") or cfg["api_key"]
    return cfg


def ensure_config_file() -> Path:
    """配置文件不存在时生成一份模板，方便用户直接填 key。"""
    if not CONFIG_PATH.exists():
        tpl = {k: DEFAULTS[k] for k in ("api_key", "target_lang", "hotkey")}
        CONFIG_PATH.write_text(json.dumps(tpl, ensure_ascii=False, indent=2), encoding="utf-8")
    return CONFIG_PATH


def save_config_values(values: dict) -> None:
    """只改文件里的这几项，保留用户其他手写配置（不会把环境变量里的 key 写进去）。"""
    ensure_config_file()
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    data.update(values)
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_config_value(key: str, value) -> None:
    save_config_values({key: value})


def fetch_balance(cfg: dict) -> str:
    """查询 DeepSeek 账户余额，返回可直接显示的文字。"""
    r = httpx.get(
        cfg["base_url"].rstrip("/") + "/user/balance",
        headers={"Authorization": f"Bearer {cfg['api_key']}"},
        timeout=10,
    )
    r.raise_for_status()
    infos = r.json().get("balance_infos", [])
    if not infos:
        return "账户余额：无数据"
    return "账户余额：" + "，".join(
        f"{i['total_balance']} {i['currency']}（充值 {i['topped_up_balance']} + 赠送 {i['granted_balance']}）"
        for i in infos
    )


def record_usage(cfg: dict, usage: dict) -> tuple[int, float | None]:
    """记录本次 token 用量到 usage.jsonl，返回 (总 token, 估算费用[元])。

    只有 DeepSeek 有已知单价，其他服务（含本地模型）费用返回 None，界面上改显示 token 数。
    """
    hit = usage.get("prompt_cache_hit_tokens") or 0
    miss = usage.get("prompt_cache_miss_tokens")
    if miss is None:
        miss = (usage.get("prompt_tokens") or 0) - hit
    out = usage.get("completion_tokens") or 0
    cost = (hit * cfg["price_hit"] + miss * cfg["price_miss"] + out * cfg["price_out"]) / 1e6 if is_deepseek(cfg) else None
    row = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "in": hit + miss, "out": out, "cost": round(cost or 0.0, 6)}
    with USAGE_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return hit + miss + out, cost


def usage_summary() -> str:
    """汇总本地记录的今日 / 本月用量（估算，仅含本软件的调用）。"""
    today, month = time.strftime("%Y-%m-%d"), time.strftime("%Y-%m")
    agg = {"今日": [0, 0.0, 0], "本月": [0, 0.0, 0]}
    if USAGE_PATH.exists():
        for line in USAGE_PATH.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            for label, prefix in (("今日", today), ("本月", month)):
                if row["ts"].startswith(prefix):
                    agg[label][0] += 1
                    agg[label][1] += row["cost"]
                    agg[label][2] += row.get("in", 0) + row.get("out", 0)
    return "\n".join(f"{k}：{n} 次，{t:,} token，DeepSeek 部分约 ¥{c:.4f}" for k, (n, c, t) in agg.items())


# ---- 语言判断（本地按字符，不交给模型猜）----
def lang_code(name: str) -> str:
    n = name.lower()
    for code, keys in (
        ("zh", ("中文", "简体", "繁体", "繁體", "chinese")),
        ("ja", ("日本", "日语", "日語", "japanese")),
        ("ko", ("한국", "韩", "韓", "korean")),
        ("en", ("english", "英")),
    ):
        if any(k in n for k in keys):
            return code
    return "other"


def detect_lang(text: str) -> str:
    """粗略判断原文语种：zh / ja / ko / en / other。含假名即日语，含谚文即韩语。"""
    han = sum("一" <= c <= "鿿" for c in text)
    kana = sum("぀" <= c <= "ヿ" for c in text)
    hangul = sum("가" <= c <= "힯" for c in text)
    latin = sum(c.isascii() and c.isalpha() for c in text)
    if kana:
        return "ja"
    if hangul:
        return "ko"
    if han and latin <= han:  # 纯汉字且不以英文为主
        return "zh"
    if latin >= 3 and latin > han * 2:
        return "en"
    return "other"


def pick_target(cfg: dict, text: str) -> str:
    """译成什么语言：默认 target_lang；原文本身已是 target_lang 时译成 fallback_lang。

    短片段、日语里的汉字容易让模型误判成"已是中文"，所以由本地判断。
    """
    target = cfg["target_lang"]
    code = lang_code(target)
    if code != "other" and detect_lang(text) == code:
        return cfg["fallback_lang"]
    return target


MODES = ("translate", "learn", "summary")


def build_system(cfg: dict, text: str, mode: str) -> tuple[str, float]:
    """返回 (system prompt, temperature)。mode: translate 翻译 / learn 学习讲解 / summary 总结。"""
    if mode == "translate":
        target = pick_target(cfg, text)
        return (
            f"你是翻译引擎。把用户给出的文字翻译成{target}。"
            "原文可能是不完整的片段（如语法词组、半句话），照实翻译，不要补全、猜测或解释。"
            "保留原有换行、格式、代码、专有名词和数字。只输出译文，不要加引号。"
        ), 1.0
    if mode == "summary":
        target = cfg["target_lang"]  # 总结永远用用户自己的阅读语言写，原文是外语也直接读懂
        return (
            f"你是严谨的编辑。用{target}总结用户给出的文字（原文语言不限）。严格按下面的 Markdown 结构输出：\n"
            "## 一句话总结\n用一句话概括全文最核心的信息。\n"
            "## 要点\n3～6 条，每条以「- 」开头、各占一行，按重要性排序；保留关键的数字、人名、机构名和结论。\n"
            "只依据原文，不添加原文没有的信息、评论或推测；原文很短时只写「一句话总结」，省略「要点」。"
        ), 0.3

    native, fb = cfg["target_lang"], cfg["fallback_lang"]
    if lang_code(native) != "other" and detect_lang(text) == lang_code(native):
        task = f"用户的原文是{native}。先把它译成最自然地道的{fb}，然后围绕你给出的{fb}译文进行学习讲解。"
    else:
        task = f"用户的原文是外语。先把它译成{native}，然后围绕原文本身（原文语言）进行学习讲解。"
    level = cfg["learn_level"]
    return (
        f"你是资深语言老师，帮{native}母语的学习者讲解外语句子。{task}\n"
        f"学习者水平：{level}。低于这个水平的词汇和语法（基础助词、常见词、简单活用等）一律不讲，"
        "只讲对该水平有学习价值的内容，宁缺毋滥。\n"
        "原文可能是不完整的片段（如语法词组），照实讲解，不要擅自补全。\n"
        "【注音规则】不要给整句注音。只给「重点词汇」注音：紧跟在词后面用括号标注，"
        "日语写成 漢字（かな），韩语标罗马音，中文标拼音；简单、常见、学习者已会的词不注音；其他语言无需注音。\n"
        f"讲解一律使用{native}，严格按下面的 Markdown 结构输出，简洁，不要寒暄；各节中的条目一律以「- 」开头、各占一行，不要嵌套列表；例句不要加粗：\n"
        "## 译文\n自然流畅的译文，一两句话。\n"
        "## 断句\n教学习者把句子切开。先单独写一行：把原句按意群（日语按文节：名词+助词、动词+助动词等）"
        "用「／」切开，只输出切分后的整句，不加任何其他文字；"
        "然后逐段列出，每段一行：**该段原文**：成分/作用（主语、宾语、状语、原因从句、定语等）与含义，"
        "并说明它修饰或连接到哪一段。最多 12 段，切到合理粒度即可，不要切得太碎。\n"
        "## 变形解析\n凡句中出现活用/变形的动词、形容词（含后接助动词的连接），每个一行：**句中的形式**（辞书形）："
        "逐步写出变形过程，如 決める → 被动形 決められる → た形 決められた，并说明每一步的作用和接续规则。"
        "最多 8 条，按出现顺序；ます/です 这类一眼能懂的基础形式可省略；该语言没有活用变形则省略整节。\n"
        "## 重点词汇\n最多 6 条，每条一行：**词（读音）**：词性与含义；用法或搭配一句话。\n"
        "## 语法点\n最多 4 条，只列真正值得学的，每条一行：**形式**（接续）：含义；语气、使用场合"
        "（口语/书面/敬语等）；如能判断可标注难度（如 JLPT N2）。\n"
        "## 地道例句\n2～3 条，围绕上面的语法点，自然、实用、不要太长；每条只占一行，"
        "写成：例句（仅重点词汇后注音，每句最多注 1～2 个生词，其余汉字词不要注）— 译文。\n"
        "## 小提示\n易混淆的近似表达或常见错误，最多 2 条；没有就省略整节。"
    ), 0.7


def stream_translate(cfg: dict, text: str, usage_out: dict | None = None, mode: str | None = None):
    """逐块 yield 译文（学习模式下为讲解）；结束后把 token 用量写入 usage_out。"""
    # 本地模型（本机 / 内网地址）可以不填 key，但 SDK 要求非空；本地大模型首字可能较慢，所以超时放宽
    client = OpenAI(api_key=cfg["api_key"] or "none", base_url=cfg["base_url"], timeout=120)
    mode = mode or ("learn" if cfg["learn_mode"] else "translate")
    system, temperature = build_system(cfg, text, mode)
    kwargs = dict(
        model=cfg["model"],
        messages=[{"role": "system", "content": system}, {"role": "user", "content": text}],
        temperature=temperature,
        stream=True,
    )
    if is_deepseek(cfg):  # 只有 DeepSeek 确认支持；其他服务（如 o1 系列）可能不认 max_tokens
        kwargs["max_tokens"] = {"translate": 8192, "learn": 4096, "summary": 2048}[mode]
    # Qwen3 等本地模型默认先"思考"再回答，翻译白白慢十几倍（实测 2.6s → 0.2s）；通过模板参数关掉
    extra = {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}} if is_local(cfg) and cfg["disable_thinking"] else {}
    usage_opt = {"stream_options": {"include_usage": True}}
    stream = None
    # 个别服务器不认 extra_body / stream_options：逐级去掉重试（代价：拿不到 token 用量）
    for opts in ({**extra, **usage_opt}, usage_opt, {}):
        try:
            stream = client.chat.completions.create(**opts, **kwargs)
            break
        except BadRequestError:
            if not opts:
                raise

    def pieces():
        for chunk in stream:
            if usage_out is not None and getattr(chunk, "usage", None):
                usage_out.update(chunk.usage.model_dump())
            if usage_out is not None and chunk.choices and chunk.choices[0].finish_reason:
                usage_out["finish_reason"] = chunk.choices[0].finish_reason  # "length" = 译文撞到输出上限被截断
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    started = False
    for piece in strip_think(pieces()):
        if not started:  # 部分模型的回答以空行开头
            piece = piece.lstrip()
            if not piece:
                continue
            started = True
        yield piece


def strip_think(chunks):
    """去掉流式输出里内联的 <think>…</think> 思考过程（Qwen3、DeepSeek-R1 蒸馏等本地模型常见）。"""
    open_tag, close_tag = "<think>", "</think>"
    buf, inside = "", False
    for piece in chunks:
        buf += piece
        out = ""
        while True:
            if inside:
                i = buf.find(close_tag)
                if i == -1:
                    buf = buf[-(len(close_tag) - 1):]  # 思考内容丢弃，只留可能是半个结束标签的尾巴
                    break
                buf, inside = buf[i + len(close_tag):].lstrip("\n"), False
            else:
                i = buf.find(open_tag)
                if i == -1:
                    keep = next((k for k in range(min(len(open_tag) - 1, len(buf)), 0, -1) if open_tag.startswith(buf[-k:])), 0)
                    out += buf[: len(buf) - keep]
                    buf = buf[len(buf) - keep:]
                    break
                out += buf[:i]
                buf, inside = buf[i + len(open_tag):], True
        if out:
            yield out
    if not inside and buf:
        yield buf
