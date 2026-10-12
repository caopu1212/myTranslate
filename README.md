# 划词翻译（myTranslate）

Windows 上的划词翻译小工具：**选中文字，连按两次 Ctrl+C**，鼠标旁就弹出译文浮窗。
用任意 OpenAI 兼容的大模型接口翻译（DeepSeek、OpenAI、通义千问，或你自己部署的本地模型），
并带有给日语学习者用的「学习模式」。

## 功能

- **划词翻译**：选中文字 → 双击 Ctrl+C（也可改成 `Ctrl+Alt+T` 这类组合键）→ 流式显示译文，不抢你的剪贴板内容。
- **学习模式**（设置里勾选，或点浮窗上的「讲解」）：
  - 译文
  - **断句**：把长句按意群用「／」切开，逐段说明成分和修饰关系
  - **变形解析**：动词、形容词一步步怎么变形（如 `決める → 被动形 決められる → た形 決められた`）
  - 重点词汇（只给重点词注音）、语法点（含接续、语感、JLPT 难度）、地道例句
  - 可设置学习者水平，低于该水平的内容不讲
- **总结**：浮窗上点「总结」，给出一句话总结和要点，用你的目标语言写。
- **语言可选**：「翻译成」和「原文已是目标语言时翻译成」都能在设置里选。语种由程序按字符判断，不依赖模型猜。
- **多模型服务**：预设 DeepSeek / OpenAI / 通义千问 / 本地 LM Studio / 本地 Ollama / 自定义；
  每个服务各自保存地址、Key、模型，切换不会互相覆盖。本机或内网地址的模型可以不填 Key。
- **本地模型优化**：自动关闭 Qwen3 等模型的"思考模式"（翻译更快），并过滤 `<think>` 内容。
- **用量与余额**：浮窗显示本次消耗；DeepSeek 可在设置里查余额，并统计今日/本月用量。
- 托盘常驻、开机自启、启动提示、防止重复启动、浮窗可拖动/固定/复制。

## 下载（开箱即用）

到 [Releases](https://github.com/caopu1212/myTranslate/releases/latest) 下载 `myTranslate-win64.zip`，
**解压整个文件夹**后双击 `划词翻译.exe`，不需要安装 Python。然后单击托盘里的「译」图标，在设置里填 API Key 即可。

> exe 没有数字签名，首次运行 Windows 可能提示"已保护你的电脑"，选「更多信息 → 仍要运行」。
> 不放心的话可以按下面的方式从源码运行或自行打包。

## 快速开始

### 方式一：从源码运行

需要 Windows 10/11 和 Python 3.10+。

```bash
pip install -r requirements.txt
python main.py
```

第一次运行后，单击右下角托盘里的蓝色「译」图标打开**设置**，选择模型服务并填入 API Key，保存即生效。
之后选中文字，快速按两次 Ctrl+C 即可。

> `run.bat` 用 `pythonw` 启动，不显示命令行窗口。

### 方式二：打包成 exe

```bash
pip install pyinstaller
python -m PyInstaller --noconfirm --windowed --name 划词翻译 ^
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets ^
  --exclude-module PySide6.QtPdf --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick main.py
```

产物在 `dist\划词翻译\`，是一个文件夹（不是单文件），整个文件夹拷走即可，对方电脑不需要装 Python。
exe 没有数字签名，首次运行 Windows 可能提示"已保护你的电脑"，选「更多信息 → 仍要运行」。

## 配置

在设置窗口里改即可。配置文件位置：

| 运行方式 | 配置文件 |
|---|---|
| 源码运行 | 项目目录下的 `config.json` |
| 打包的 exe | `%APPDATA%\划词翻译\config.json` |

`config.example.json` 是最小示例。API Key 以明文保存在本机配置文件里，该文件已在 `.gitignore` 中，不会被提交。

常用项：

| 键 | 说明 | 默认 |
|---|---|---|
| `provider` / `profiles` | 当前模型服务 / 各服务各自的 `base_url`、`api_key`、`model` | DeepSeek |
| `target_lang` | 翻译成 | 简体中文 |
| `fallback_lang` | 原文已是目标语言时翻译成 | English |
| `learn_mode` / `learn_level` | 学习模式 / 学习者水平 | 关 / 中级（N3～N2） |
| `hotkey` | 触发方式；`ctrl+c+c` 即双击 Ctrl+C | `ctrl+c+c` |
| `max_chars` | 单次最多翻译字数，超出部分截掉并在浮窗提示 | 10000 |
| `disable_thinking` | 本地模型关闭思考模式 | 开 |

## 使用本地模型

1. 设置里「模型服务」选「本地 LM Studio」或「本地 Ollama」。
2. 把「服务地址」改成实际地址。模型跑在另一台电脑上时**不能填 `localhost`**，要填那台电脑的 IP，如 `http://192.168.1.10:1234/v1`。
3. 点「获取列表」选择模型（或直接手写模型名），API Key 可留空。

## 费用

按 DeepSeek 的 token 价格估算（单价可在配置里改，默认值未经联网核对，请以官网为准）：
普通翻译一次约几分之一分钱；学习模式输出更长，约 0.004 元/次。其他服务只显示 token 数。

## 已知限制

- 仅支持 Windows。
- 终端里 Ctrl+C 是中断而不是复制，所以终端内的选中文字取不到。
- 双击 Ctrl+C 模式下，如果没选中文字，会翻译剪贴板里上次复制的内容。
- 只有 DeepSeek 支持余额查询；费用估算也只针对 DeepSeek。
- 译文本身受模型输出上限限制，超长文本建议分段翻译。

## 项目结构

| 文件 | 作用 |
|---|---|
| `main.py` | 入口：托盘、线程调度、单实例、启动提示 |
| `capture.py` | 双击 Ctrl+C 键盘钩子 / 全局热键，读取选中文字 |
| `llm.py` | 配置与多服务 profile、提示词、流式调用、语种判断、用量统计 |
| `popup.py` | 译文浮窗 |
| `render.py` | 学习模式/总结的 Markdown → 排版整齐的富文本 |
| `settings.py` | 设置窗口、开机自启 |
| `PLAN.md` | 开发计划、决策日志和变更记录 |
