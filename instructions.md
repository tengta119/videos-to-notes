# VideoBook Agent 工作流指令

> 本文件是 AI 助手（Codex / Antigravity / Claude Code）的操作手册。
> 当用户发来一个视频链接时，请严格按照以下步骤执行。
>
> **每步都有"静默失败"的可能**——命令退出码 0、产物文件也存在，但内容是坏的。
> 所以每步末尾都有一个「校验门」：**判据不通过就不得进入下一步**。

## 触发条件

用户发来一条包含 YouTube (`youtube.com`, `youtu.be`) 或 B站 (`bilibili.com`) 视频链接的消息，
并表示希望将其生成为电子书/教程/指南。

## 总览：步骤与校验门

只列命令和判据。失败动作见对应章节。

| 步 | 命令 | 判据 | 失败 |
|---|---|---|---|
| 1 | `python src/dump_transcript.py "<VIDEO_URL>"` | 退出码 0，且覆盖率 ≥50% | →§1.3 |
| 1b | `python src/asr_transcript.py <video_id> --restart` | `grep -c '重转未改善'` 为 0 | →§1b.4 |
| 2 | `make_corrected.py` → AI 订正 → `apply_corrections.py` | `apply_corrections.py --dry-run` 无未匹配 | →§2.3 |
| 2b | 写 `book.md` | 标识符与画面一致（截帧后回看核对） | →§2.5 |
| 3 | `python src/capture_frames.py <video_id> "<VIDEO_URL>"` | `book.md` 里每个 `SCREENSHOT:` 时间戳都有对应 png | →§3.5 |
| 4 | `post_process.py` + `python -m http.server 8080` | `grep -c 'SCREENSHOT:' book.md` 为 0 | →§4.1 |
| 5 | （告知用户） | 预览 URL 可访问 | — |
| 6 | `python src/publish.py <video_id>` 后 `git push mine pages` | 输出为三种正常消息之一、无 traceback | →`DEPLOY.md` |

## 贯穿全程的硬约束

- **工作目录**始终为仓库根（本文件所在目录），所有相对路径（如 `output/<video_id>/...`）均相对于它解析。
- **Python 解释器**：一律 `python`；装包用 `python -m pip`（不要裸 `pip`）。
  本仓库用 uv 管理，请用 `.venv\Scripts\python.exe` 或 `uv run python`。
- **沙箱/提权**：启动 Chrome / 读取浏览器 cookie 库的命令必须**沙箱外**执行——
  `dump_transcript.py`（B 站）、`capture_frames.py`、`capture_frames.py --setup-profile`；
  `post_process.py`、`http.server` 沙箱内即可。在 Codex 中对应 require_escalated 审批。
- **cookies 安全**：自动导出的 cookies 写入系统临时目录、用完即删；
  不要在仓库里手放 `cookies.txt`（已被 .gitignore 忽略，但仍应避免）。
- **外部工具**：所有 yt-dlp 调用一律通过 `sys.executable -m yt_dlp` 走，不要依赖 PATH 上的 yt-dlp。
- **可重跑性**：流水线各步幂等。若 `output/<video_id>` 被意外清理：重跑第一步恢复字幕；
  只要 `book.tagged.md` 还在，重跑第三步即可恢复截图（占位符清单读自 tagged 稿）。
  **唯一例外是 §2.3 的 AI 行级订正**，见该节说明。
- **凡标识符（文件名、组件名、元素 id、命令、变量名）一律以画面为准。**
  ASR 稿里的标识符只是**线索，不是事实**——同音/近音错写在讲代码的视频里几乎必然发生。
  核对方法见 §2.5。

## 第一步：提取字幕

### 1.1 抓取平台字幕

```bash
python src/dump_transcript.py "<VIDEO_URL>"
```

- 脚本自动检测平台，抓取字幕并保存为 `output/<video_id>/transcript.json`。
- **B 站登录态自动获取**：B 站视频在专用配置 `.capture-profile/` 存在登录态时，脚本会自动导出
  cookies（系统临时目录、用完即删，不会落入仓库）。默认启动无头 Edge；
  可用 `VIDEOBOOK_BROWSER=chrome` 切换 Chrome。
- 若提示专用配置无登录态：请用户先运行 `python src/capture_frames.py --setup-profile` 登录一次
  （扫码即可），再重试。
- 可选参数：`--cookies-from-profile <dir>`（指定配置导出）、`--cookies-file <path>`
  （Netscape cookies 文件）、`--cookies-from <browser>`（旧方式；
  **Windows 主 Chrome 因新版 App-Bound 加密通常不可用，勿默认使用**）。
- **transcript.json 结构（数据字典）**：
  `video_url / title / video_id / duration / chapters / segments`。
  `duration` 是总时长秒数，`chapters` 是平台官方章节切分（**可能为空数组**）。
  第二步必须利用这两个字段。
- **海外视频终端需走代理**；YouTube 链接且终端无代理时，字幕抓取会失败。

### 1.2 判别失败类型

两类失败的处置**完全不同**，必须先分清：

| 类型 | 症状 | 处置 |
|---|---|---|
| **登录态 / 代理问题** | 覆盖率过低、超时、地区限制 | 按提示修复后**重跑本步** |
| **平台确实没有字幕** | `--list-subs` 只有 `danmaku`；播放器接口 `subtitle.subtitles` 为 `[]` | **不要停止流程**，转入 §1b 本地 ASR 兜底 |

平台确实没有字幕在新上传视频上很常见（B 站 AI 字幕尚未生成）。
如果失败且无法兜底，请告知用户可能的原因（无字幕、需要代理、需要登录等），并停止流程。

### 1.3 校验门

- **判据**：退出码 0，且脚本打印的"字幕覆盖率"（末段结束时间 / 视频时长）**≥50%**。
  覆盖率低于 50% 时脚本以退出码 3 结束并打印修复提示——此时**不得进入第二步**，
  先按提示解决（通常是登录态问题）。

## 第一步兜底（1b）：本地 ASR 转写（平台无任何字幕时）

```bash
python src/asr_transcript.py <video_id>
```

脚本自行完成：只下载音频轨（`output/<id>/audio.m4a`，**不下载视频流**）→ faster-whisper
`large-v3`（或 whisper.cpp）转写 → 按标点/时长切成接近平台字幕粒度的短段 →
写出 `transcript.json`（字段与 `scraper.get_transcript` **完全同构**：
`video_url / title / video_id / duration / chapters / segments`）与 `transcript.txt`。
**正因为同构，第二步之后的所有步骤无需任何改动。**

> **ASR 兜底稿同样是"原始字幕"**：它在后续步骤中与平台字幕**完全等价**——同样要建术语表、
> 容忍同音错词、产出 `transcript.corrected.txt`。本地 large-v3 的错词率通常低于平台 AI 字幕，
> 但仍需人工订正专有名词。

### 1b.1 术语提示文件：写成一行纯 ASCII 术语表

在 `output/<id>/_asr_prompt.txt` 写术语提示。**AMD/whisper.cpp 后端下，正确写法是
一行纯 ASCII 术语列表，10 个左右**：

```
React React React JSX Vite npm install useState useEffect props state
```

- **绝对不要写中文主题句**。实测在本机 whisper.cpp(ROCm) 后端上，中文为主的 prompt
  （哪怕里面重复写了 React）**完全压不住英文术语错词**：React 仍被识别成
  `Re-add` / `read` / `real`，Vite 变成 `Vit`。同一段音频换成上面这行 ASCII 术语表，
  React / Vite 全部正确。
- **宁短勿长**。术语堆到 25 个左右时，同一段的**中文正文会掉内容**（实测 231 字 → 203 字）。
  术语表只放本讲最高频、最易错的几个。
- prompt 有 token 上限（约 224），超长会被**静默截断**（保留尾部），脚本看不到截断警告。
- **PowerShell 下不要用命令行传含中文引号的长 prompt**（弯引号会被当成字符串定界符），一律走文件。
- 也可用 `--initial-prompt` / `--initial-prompt-file` 显式指定，缺省时脚本自动读
  `output/<id>/_asr_prompt.txt`。
- 注：文档旧版说术语提示"能显著压制同音错词，并让中文输出自带标点"——那是
  **faster-whisper（NVIDIA）路径**的结论，在本机 whisper.cpp 后端上未复现，勿照搬。

### 1b.2 先试跑，再全量

```bash
python src/asr_transcript.py <video_id> --sample-start 900 --sample-dur 180
```

**试跑的坑**：`--sample-start` 会被**回退到 `--chunk-sec` 边界**（whisper.cpp 后端默认 600s），
所以上面这条实际转写的是 600→1080s，不是 900→1080s。试跑的目的是确认术语表写法是否有效，
**跑完立刻决定，不要反复做参数 A/B**（本次实测在这一步浪费了约 25 分钟）。

试跑后跑全量**务必带 `--restart`**，否则会漏掉试跑区间之前的内容：

```bash
python src/asr_transcript.py <video_id> --restart
```

- **断点续跑**：进度实时写入 `output/<id>/_asr_progress.jsonl`，中断后重跑自动从末尾续接；
  `--restart` 强制从头。
- whisper.cpp 后端按 `--chunk-sec`（默认 600s）定长块转写，**续跑以块为粒度**
  （残块整块重跑，不产生重复段落）。

### 1b.3 算力与耗时

**NVIDIA**：有卡自动用 CUDA，显存 ≤4GB 用 `--compute-type int8_float16`；无卡自动退 CPU int8。
Windows 报 `Library cublas64_12.dll is not found` 时装 `nvidia-cublas-cu12 nvidia-cudnn-cu12`，
脚本已自动注册这些 DLL 目录。

**AMD 显卡（如 RX 9070 XT / RDNA4）**：faster-whisper 不支持 AMD，脚本自动切换到
whisper.cpp (ROCm) 后端。一次性安装：

```bash
python src/asr_transcript.py --install-amd
```

下载 Lemonade 预构建的 gfx120X 版 whisper-cli（**运行时 DLL 全部打包、免装 ROCm**）
及 GGML large-v3 模型到 `tools/`，均已 gitignore；RX 7000 系用 `--amd-arch gfx110X`。
此后 `--backend auto` 在无 CUDA 环境下自动发现并使用；也可 `--backend whispercpp` 强制。
**默认关闭 flash-attn** 以规避 RDNA4 驱动 bug（`--flash-attn` 可强制开启）。

**耗时预期**（两套数据并列，按你的卡看）：

| 后端 | 实测速度 | 100 分钟课程 |
|---|---|---|
| faster-whisper / RTX 3050 (4GB) | — | 约 35 分钟 |
| whisper.cpp (ROCm) / RX 9070 XT | 13–17x 实时 | 约 6–8 分钟 |

转写期间可并行准备第三步的截帧环境与术语表。

**其余参数速查**（`python src/asr_transcript.py --help` 可查全部）：

| 参数 | 说明 |
|---|---|
| `--model` | whisper 模型，默认 `large-v3` |
| `--device` | `cuda` / `cpu`，默认自动探测 |
| `--language` | 默认 `zh` |
| `--beam-size` | 默认 5 |
| `--no-words` | 关闭词级时间戳（更快，但分段更粗） |
| `--threads` | whisper.cpp 的 CPU 线程数 |
| `--cli` / `--ggml-model` | 显式指定 whisper-cli 与 GGML 模型路径（默认自动发现 `tools/`） |
| `--max-context` | whisper.cpp `-mc`：携带的上文 token 数。调大更连贯，但**更易诱发重复循环** |
| `--no-loop-guard` | 关闭重复循环检测（默认开启，见 §1b.4） |
| `--no-flash-attn` / `--flash-attn` | 显式关/开 flash-attn（默认关） |

### 1b.4 校验门

转写完成后脚本会执行覆盖率自检（低于 50% 说明没跑完，重跑续写）。
**但覆盖率自检有已知盲区**：它只校验"末段结束时间 / 总时长"，即时间轴是否推进到底，
**查不出内容被幻觉吃掉**。

whisper.cpp 在个别块上会陷入**重复循环幻觉**——把同一句话刷满整块（实测有 8.5 分钟
真实内容被这样替换掉），而时间戳照常前进，覆盖率仍显示 100%。脚本已内置检测与自愈，
所以**必须同时看 stdout**：

- `⚠ 疑似重复循环（重复度 N），用 -mc 0 重转本块` —— **正常**，已自动重转并替换。
- `⚠ 重转未改善（N），保留原结果，请人工核对本块` —— **判据失败**，该块必须人工核对。
  用 `--sample-start` 定位到那一块重转，或直接听音频确认。

```
grep -c '重转未改善' <(python src/asr_transcript.py <video_id> --restart 2>&1)   # 期望 0
```

## 第二步：先订正，再写书

**顺序很重要**：先把 ASR 错词在源头修掉，再动笔写书。否则错词会全带进 `book.md`，
事后再补就得改两遍。

### 2.1 读字幕，建术语表

1. 阅读 `output/<video_id>/transcript.json`（修正后为 `transcript.corrected.txt`，见 2.4）。
2. 阅读 `prompts/stitcher_system.md`——**它是 book.md 的排版契约唯一真源**
   （含 Mermaid 强制要求、`*(参考时间: MM:SS)*` 锚点、`SCREENSHOT:` 占位格式、章节层级）。
3. 动笔前先结合标题与 `chapters` 建一份**本讲术语表**（注意：这与 §1b.1 的
   `_asr_prompt.txt` **不是一回事**——那个是喂给 Whisper 的提示，这个是给你自己改写时统一用的）。

**容忍 ASR 噪声**：字幕含同音错词（如"深圳市软件工程"→生成式、"威尔法/WIFI"→verifier、
"KIMIK3"→Kimi K3、"chain of salt"→chain of thought、"舔狗/做题家"等口语梗保留原意）。
改写时统一规范化。

### 2.2 生成修正版字幕稿

```bash
python src/make_corrected.py <video_id>      # 或 --all
```

生成 `output/<video_id>/transcript.corrected.txt`——与 `transcript.txt` 同格式
（`[MM:SS] 原文`、逐段不合并），**保留讲师原始字词与顺序**，仅做两类修改：

1. **ASR 错词替换**（脚本内 MAP，按视频扩充）；
2. **口癖清理**（纯语气词段删除、句尾语气词剥离、单字口吃折叠）。

**不改写为书面语、不概括。**

#### MAP 扩充的强约束（必须遵守，不是"注意事项"）

`MAP` 是**全局共用的**、按 key 长度降序应用的**子串替换**。本次实战踩中三个坑，规则如下：

1. **加任何短的英文/中文 key 之前，先扫一遍全部讲次**，确认不会误伤：
   ```bash
   grep -ohiE '.{12}\b<候选词>\b.{12}' output/*/transcript.txt
   ```
   全部命中都出自本讲才可加。
2. **短英文 key 一律走带词边界的 `REGEX`，不要放 `MAP`。**
   空映射（`"AnimatedCardGrid": "AnimatedCardGrid"` 这种自我映射）**挡不住**子串替换——
   它替换后文本没变，后面的短 key 照样命中，`AnimatedCardGrid` 被 4.2 段的
   `"Animate"→"anime"` 吃成 `animedCardGrid`（已修）。
3. **改名的"新值"也要给 key。** 原文写的是 `TaxLab.`，所以 `"TextLab.": "textlab."`
   这个 key **永远匹配不到**——必须先加 `"TaxLab.": "textlab."` 才有意义。同理凡是
   "A→B→C" 的两跳改名，两段的原值都要列。
4. **拼接产物会二次命中，要用更长更精确的 key 覆盖**：
   `resultcar` 把 `resultcard` 变成 `ResultCardd`；
   `ViteConfig` 把 `ViteConfig.js` 变成 `vite.config.js.js`。
5. **改完 MAP 必须对已有书跑一遍 `diff` 回归**，确认没改坏别的讲次：
   ```bash
   cp output/<某本已发布的书>/transcript.corrected.txt tmp/before.txt
   python src/make_corrected.py <该 video_id>
   diff tmp/before.txt output/<该 video_id>/transcript.corrected.txt
   ```
6. **`make_corrected.py` 会无条件覆盖 `transcript.corrected.txt`**，且**不读**
   `_ai_fixes.json`。所以重跑它会**丢掉下节的 AI 订正**——重跑后必须重新做 §2.3。
   动它之前先备份。

### 2.3 AI 行级订正

由 AI 通读 `transcript.corrected.txt`，做一轮**行级**订正，产出 old→new 清单
（可委派子代理），写到 `output/<video_id>/_ai_fixes.json`，然后应用：

```bash
python src/apply_corrections.py <video_id>            # 应用
python src/apply_corrections.py <video_id> --dry-run  # 只报告不落盘
```

清单格式（`line` 为 1-based 行号，`old` 必须与文件**逐字完全一致**）：

```json
[{"line": 22, "old": "该行完整原文（含 [MM:SS] 前缀）", "new": "该行完整新文本"}]
```

**订正原则——两道方向相反的护栏，别搞混**：

- **必修**：明显的识别错字词（如"车轱话"→"车轱辘话"）、专有名词错拼、被截断的半个词。
- **禁止**：改写为书面语、概括、调整语序、合并或拆分行、改动行数或时间戳。
- **禁止**：折叠讲师**有意为之的强调式重复**——"工作工作工作工作"、"点点点点点点点了十层"
  这类要原样保留。（注意区分：若是同一句话在同一区域机械重复几十次，那是 §1b.4 说的
  ASR 幻觉残留，由转写阶段处理，不在这一轮改。）
- **拿不准就不改**。宁可漏改，不可过改。

> ⚠ **这一步只能跑一次**。没有脚本读 `_ai_fixes.json`，重跑 `make_corrected.py` 会覆盖订正结果。
> 因此"流水线各步幂等"这条**不适用于 2.3**——重跑 2.2 之后必须重新执行 2.3。

### 2.4 用修正稿写 book.md

1. 阅读 `output/<video_id>/transcript.corrected.txt`（**已修正干净的版本**，优先用它，
   而不是原始的 `transcript.json`）。
2. 按 `prompts/stitcher_system.md` 的契约把字幕重构为结构化 Markdown 技术指南。
3. 写入 `output/<video_id>/book.md`。

**关键要求**（排版契约的细则以 `prompts/stitcher_system.md` 为准，此处只列必须记住的）：

- 口语化内容转为书面化技术语言；原始语言非中文时翻译为中文。
- 分章节组织，使用 Markdown 标题。
- 在关键界面/操作步骤处插入截图占位符：`![场景描述](SCREENSHOT:HH:MM:SS)`。
- **Mermaid**：按 stitcher 要求绘制流程/架构图（**无条件要求**，见该文件的判据）。
- **官方章节仅供参考**：`chapters` 字段往往较粗糙，只用于辅助定位内容与选择截图时间戳；
  顶层分章按内容自身逻辑组织，**不要求**与官方章节一一对应；
  `SCREENSHOT:` 时间戳落在其所属内容的时间区间内即可。
- **截图时间戳选择**：优先章节边界、新幻灯片出现时刻、演示画面时刻；
  结合前后字幕语义定位，格式 `HH:MM:SS`。

### 2.5 校验门：标识符以画面为准

`book.md` 里出现的**文件名、组件名、元素 id、命令、变量名**，凡是从 ASR 稿拿的，
都必须回看画面核对（IDE 标题栏、文件树、终端回显）。

本次实战三个实例——全部只有回看画面才发现：

| ASR 稿给的 | 画面里实际是 |
|---|---|
| `TextLab.html` | `text-lab.html`（HTML 用 kebab-case） |
| `root` | `result-root`（挂载点元素 id） |
| `redout.jsx` | `result.jsx`（入口文件全小写） |

**判据**：§3 截帧完成后，抽查 2–3 张能看见文件树/终端/编辑器标签栏的截图，
逐条比对 `book.md` 里的标识符。发现不一致**必须改 book.md 并重跑 §4**。
（这也是把核对放在 §3 之后的原因——在截图产生之前，你没有可核对的依据。）

## 第三步：截帧并将截图占位符替换为图片

画面必须**直接来自平台播放器、使用已登录账号的高画质**；
**本步任何方式都不得下载视频/音频文件**。按以下优先级执行，**上一级全部失败才进入下一级**。

> 参数速查：`--method {auto,dedicated}` 选截帧方式；`--materialize-only` 只把已有图替换进
> `book.md` 不重新截；`--profile-dir <path>` 换配置目录。

### 3.1 优先级 0（桌面环境首选）：Chrome 扩展（@Chrome）

仅当运行在 Codex/ChatGPT 桌面应用内、且 ChatGPT 浏览器扩展已安装并连接时使用
（在 设置 > Computer Use 中确认）：

- 通过扩展控制用户真实、已登录的 Chrome：逐个 `SCREENSHOT:` 时间戳
  （取自 `book.md` 或 `book.tagged.md`），在 Chrome 中打开对应平台播放器页面，
  等待视频画面渲染后对该标签页截图，保存为 `output/<video_id>/images/shot_HH_MM_SS.png`。
  - B 站播放器地址：`https://player.bilibili.com/player.html?bvid=<video_id>&t=<秒>&autoplay=1&high_quality=1&danmaku=0`
  - YouTube 播放器地址：`https://www.youtube.com/embed/<video_id>?start=<秒>&autoplay=1&high_quality=1`
- 特点：使用登录态画质、后台运行、不接管用户屏幕。
- 全部时间戳截完后执行物化：`python src/capture_frames.py <video_id> --materialize-only`。
- 扩展未连接、截图失败、或运行环境不是 Codex/ChatGPT 桌面应用时，进入优先级 1。

### 3.2 优先级 1：专用截帧配置（v2 管线，**默认使用 Edge**）

Chrome 136+ 的安全策略禁止对**默认**用户数据目录做任何远程调试，因此脚本改用专用数据目录
（默认仓库根目录下 `.capture-profile/`）：不与主 Chrome 冲突，也不受调试禁令限制。

**一次性初始化（登录一次，长期复用）：**

```bash
python src/capture_frames.py --setup-profile
```

- 脚本会用专用配置启动一个 **Edge** 窗口：在其中登录你需要的平台（B 站 / YouTube），
  然后关闭窗口。Cookies 持久化在 `.capture-profile/`，此后截帧自动携带登录态画质。
  需要使用 Chrome 时先设置 `VIDEOBOOK_BROWSER=chrome`。

**日常截帧：**

```bash
python src/capture_frames.py <video_id> "<VIDEO_URL>"
```

v2 管线自动完成以下事情，Agent 无需也不应手工干预：

- **无头运行**：默认 headless 不弹窗打扰用户，失败自动回退有头模式；
- **权益探针**：抓前查询该账号/该视频的顶档原生分辨率，viewport 按 1:1 设置
  （不放大、不糊；换账号/换视频自动适配）；
- **流锁定**：路由拦截 playurl 响应，强制首帧即拉顶档最高码率流
  （根除"自动档从 360P 起播、暂停冻结升档"造成的糊图）；
- **纯净帧**：visibility CSS 隐藏全部播放器 UI（顶栏/控制栏/引流条/暂停推荐层），
  仅对 `<video>` 元素截图，无黑边；嵌入播放器优先，失败自动降级主站观看页；
- **精确帧**：`seek(目标秒) → 等 seeked → pause` 后截图，时间戳精确且为静止帧；
- **QA 自检**：截图文件过小判为黑帧，自动偏移重试；**只截缺失帧**，全部存在时直接跳过；
- **增量友好**：占位符清单始终读自 `book.tagged.md`（若存在），因此物化后重跑也能正确补帧。

本步启动 Chrome，**需沙箱外执行**。若截图变回未登录态（Cookie 过期），
重新执行 `--setup-profile` 登录一次。

### 3.3 兜底：下载视频源 + ffmpeg 抽帧

仅当上述所有浏览器方式全部失败（`capture_frames.py` 以非零码退出并提示
`All browser capture methods failed`），或环境无浏览器/无界面时：

```bash
python src/extract_frames.py <video_id> "<VIDEO_URL>"
```

- 若 `output/<video_id>/video_source.mp4` 不存在，脚本会自动用 yt-dlp 下载未登录可用的
  最高 avc1 档；**注意这是未登录画质，仅作兜底**。需要更高清晰度时，先自行下载
  （如带登录 cookies）同名文件，脚本会跳过下载。
- 兜底产生的 `video_source.mp4` 属于大媒体文件：流程中途可保留，
  流程结束时按第五步询问用户是否删除。

### 3.4 明确排除的方式（不要使用）

- 内置浏览器（@Browser）：独立配置文件，默认没有平台登录态，不满足高画质要求。
- Playwright 自带的干净 Chromium：无登录态。
- 对主 Chrome 默认数据目录的任何远程调试（CDP 端口 / Playwright 管道）：
  Chrome 136+ 安全策略禁止，**永不生效**。
- win32 屏幕截取：接管用户屏幕，已从脚本中移除。
- `--cookies-from chrome` 读取主 Chrome：Windows 上 Chrome 新版 App-Bound 加密使 yt-dlp
  无法解密，不要默认使用。

### 3.5 校验门

- `images/` 下**每个** `SCREENSHOT:` 时间戳都有对应的 `shot_HH_MM_SS.png`
  （**不是**"占位符没了"就算过——占位符可能被替换成了指向缺失/旧图的链接）。
- `book.md` 中不再有 `SCREENSHOT:` 占位符。
- **Agent QA 习惯**：抽查 1–2 张图（一张幻灯片帧、一张演示帧）确认清晰无遮挡。
  若整体发糊，通常是账号档位问题——请用户在专用配置里登录大会员账号后重跑本命令
  （管线会自动按新档位原生分辨率重截）。
- 第四步生成的 HTML 中，截图卡片内置点击放大（lightbox）：点击图片查看大图，
  点击空白处或按 Esc 关闭。

## 第四步：转换 HTML + 启动预览

纯本地步骤，沙箱内即可。两条命令是配套的（端口 8080 与第五步给用户的 URL 对应）：

```bash
python src/post_process.py "<VIDEO_URL>" output/<video_id>/book.md
python -m http.server 8080 --directory output/<video_id>
```

在生成或修改 HTML 时，请确保文本颜色与背景颜色的**对比度符合 WCAG AA 标准**
（对比度至少 4.5:1）。

### 4.1 校验门

- **判据**：`grep -c 'SCREENSHOT:' output/<video_id>/book.md` 为 **0**。
- 脚本会打印替换了截图占位符的数量；为 0 说明没有占位符可替换（检查 §2.4 是否漏插）。

## 第五步：告知用户

将以下信息回复给用户：

1. ✅ 电子书 Markdown 文件位置：`output/<video_id>/book.md`
2. ✅ HTML 电子书预览地址：**http://localhost:8080/book.html**
3. 提醒用户：
   - 如果是 YouTube 视频，请确保浏览器可以访问 YouTube（需要代理）；
   - 如果是 B 站视频，可以直接访问；
   - 关闭预览服务器：在终端按 `Ctrl+C`。
4. 若流程中产生过大媒体文件（如 `output/<video_id>/video_source.mp4`、`audio.m4a`），
   询问用户是否需要删除，**得到确认后再删**。
5. 截图清晰度说明：截图为当前账号顶档的纯视频帧（非大会员通常为 720P）；
   如需更高清晰度，在专用配置窗口登录大会员账号后重跑
   `python src/capture_frames.py <video_id> <url>` 即可自动升级。

## 第六步（可选）：发布成品到 pages 分支

```bash
python src/publish.py <video_id>   # 或 python src/publish.py --all
git push mine pages
```

**发布流程（命令、Pages 一次性启用、代理排查）详见 `DEPLOY.md`**，此处不重复。要点：
目录名 = 视频标题；将 `book.html` / `book.md` / `images/` 以及（若存在）
`transcript.corrected.txt` 提交到独立分支 `pages`（远程名 `mine`）；
不触碰 `output/` 与 main 工作区；首次推送后需在 GitHub 仓库
Settings → Pages 一次性启用（分支 `pages`、目录 `/ (root)`），之后每次 push 自动部署。
纯本地 git 操作，沙箱内可跑；push 需网络。

**校验门**：`publish.py` 退出码 0，且输出为下列三种正常消息之一、**无 traceback**：

- `>> pages 分支已更新: <hash>`
- `>> <video_id>: 内容无变化，保持原发布`
- `>> 内容无变化，跳过提交`

后两种是**正常的幂等跳过**（内容确实没变），不是失败。

## 附：已知缺口

脚本层尚未补齐、但已知的问题（**目前靠上面的校验门人工兜住**）：

- `make_corrected.py` **零自检、无条件覆盖**输出，也不校验 `transcript.json` 是否存在的问题
  （位置参数分支会直接抛 `FileNotFoundError`）。改它之前务必先备份。
- **没有任何脚本校验 `book.md` 残留 `SCREENSHOT:`**，只能靠 §4.1 的 `grep`。
- 各脚本 `--help` 的中文说明在部分 shell 下输出为 GBK 乱码。
- `extract_frames.py` 没有 argparse，传 `--help` 会被当成 `video_id` 而报错。
- 以下 7 个脚本从未被文档提及，其中后四个是历史遗留或过时：
  `polish_books.py`、`scaffold_books.py`、`make_shelf.py`、`asr_whispercpp.py`、
  `agent.py`、`config.py`、`login_utils.py`。
  注意 `make_shelf.py` 的标题硬编码为 `Docker 容器课程电子书`，且 glob 漏掉不带 `_p` 后缀的
  BV 号——其功能已被 `publish.py` 的落地页取代，**不要用它**。

## 附：时间预算

正常跑完一本的实测耗时（参考值）：

| 步骤 | 耗时 |
|---|---|
| 1b 全量 ASR 转写（whisper.cpp/ROCm，56 min 视频） | 4–6 分钟 |
| 2 订正 + AI 行级订正 | 5–10 分钟（订正清单由子代理产出时可并行写书） |
| 3 截帧（37 张） | 约 3 分钟 |
| 4 渲染 HTML | 秒级 |
| 6 发布 + 推送 | 约 1 分钟 |

**排查类开销应有上限。** 一次实战中 51 分钟里约 25 分钟花在 whisper 参数 A/B 上——
其中大部分是可省的：术语表写法在 §1b.1 已给出结论，不要重新摸索；
重复循环由脚本自动处理，只需看 §1b.4 的告警。
