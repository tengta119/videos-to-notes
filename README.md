# VideoBook Agent

提供一段 YouTube 或 Bilibili 的视频链接，AI 助手（如 Claude Code / Antigravity）将负责整理内容逻辑，并将视频中带有演示操作的时间锚点直接转为内嵌视频卡片，最终自动全产出生成高质量的技术图文电子书。

## 环境准备

1. `Python >= 3.10`
2. 克隆本仓库到本地环境
3. 安装 Python 依赖:
   ```bash
   pip install -r requirements.txt
   ```

## 🚀 如何使用？(用户视角)

**全自动托管！你唯一要做的就是把视频链接发给 AI。**

1. 将当前的工作目录导入或者在此唤醒你的 AI 助手。
2. 给它下达诸如像这样的一句话口语化自然语言指令：
   > “请接管帮我把这个视频做成电子书：`https://www.bilibili.com/video/BVxxx/`，如果是海外视频，必要的话去使用我的系统代理或者指定带有大会员权限浏览器的 cookie。”
3. 到这步为止你就可以泡杯咖啡休息了。助手内部会自动流转管道。
4. 完事后，它将甩在你的聊天面板里这主要两样东西：
   - 包含着精细化Markdown技术指南文章的文件：**`output/<视频ID>/book.md`**
   - 甚至不用你手动敲起服务，它会贴心地帮你开启本地端口服务然后告诉你：**你现在可以去 `http://localhost:8080/book.html` 边看边互动啦！**

---

## ⚙️ 内部 Pipeline 运作原理 (Agent 侧)

当你向助手发放链接任务时，本工具箱实质为其底层配置了一套六步组合流水线。
**详细流程、各步的校验判据与失败处置，一律以 `instructions.md` 为准**；本节只作导览。

1. **抓取 字幕 (Scraping)**: 调用脚本 `python src/dump_transcript.py <url>` 剥离得到原始口语字幕 JSON。
   若平台侧根本没有字幕（作者未上传 CC、B 站 AI 字幕尚未生成），自动兜底 `python src/asr_transcript.py <video_id>`：
   用 yt-dlp 只拉音频轨，再交给本地 faster-whisper（large-v3；AMD 显卡自动改走 whisper.cpp/ROCm）转写，产出**与平台字幕完全同构**的 `transcript.json`，后续各步零改动。
2. **订正 字幕 (Correcting)**: `python src/make_corrected.py <video_id>` 产出修正版字幕对照稿，再由 AI 做一轮行级订正——**先修错词、再写书**，避免错词带进正文。
3. **重写 编排 (Stitching)**: AI 利用大模型能力将修正后的字幕提取要义改写为 Markdown，并在关键讲解处插入 `![描述](SCREENSHOT:00:15:30)` 时间戳指令占位；排版契约见 `prompts/stitcher_system.md`。
4. **截帧 插图 (Capturing)**：在已登录浏览器中直接截取平台播放器画面（画质直接来自平台，不下载任何媒体文件）。优先级：Codex 桌面环境首选 ChatGPT 浏览器扩展；否则用 `python src/capture_frames.py` 的专用截帧配置（`.capture-profile/`，**默认 Edge**，一次性登录、长期复用登录态；可设 `VIDEOBOOK_BROWSER=chrome` 切换）；全部失败才兜底 `python src/extract_frames.py`。
5. **渲染 网页 (Rendering)**: 调用 `python src/post_process.py <url> <md>` 把所有占位的锚点改造成 YouTube/B站原生轻量级 `iframe` 代码，并启动本地 HTTP 服务器预览。
6. **发布 成品 (Publishing)**: `python src/publish.py <video_id>` 把成品提交到 `pages` 分支——详见 `DEPLOY.md`。


## ⚠️ 常见踩坑指南

1. **为什么 Youtube 无法获取字幕或者在内嵌的 iframe 卡片上显示 "视频配置错误(153)" 之类的错误？**
   这并非脚本代码问题，而是网络审查与封锁。如果你打算处理海外视频，你必须：
   - **终端走代理**：底层基于第三方库爬取时，才能去拿去它的字幕和源信息。
   - **浏览器走全局代理**：如果你生成的页面上有 YouTube 内嵌 iframe 请求，其源来自于你本台机器发去的直连请求。如果没有挂梯打开这篇 HTML 电子书，依旧将会是一片黑块裂图。

2. **为什么最后偏偏多加一步挂本地 HTTP Server 服务（`python -m http.server`）而不是直接用系统双击本地资源打开 .html 文件？**
   由于跨域安全以及 Cookie 隐私保护协议问题，内嵌在线带有交互控制器的播放组件如果是在没有后端协议的本地静态环境（浏览器左上角地址栏为 `file:///...`），视频源将会强制拒载报错加载失败。所以必须通过本地 HTTP 服务解决该隐患缺陷。

3. **专享和会员加密资源抓取受限？**
   对于大会员等登录拦截权限视频，推荐先在项目专用 Edge 配置中登录，再使用 `--cookies-from-profile .capture-profile` 导出 Cookie；如需切换 Chrome，可设置 `VIDEOBOOK_BROWSER=chrome`。

4. **在 AI 沙箱（如 Codex）里运行为何报"拒绝访问"？哪些命令需要沙箱外执行？**
   本流水线的截帧与字幕抓取需要启动 Chrome / Playwright、读取浏览器 cookie 库，属于沙箱外权限。托管给 AI 助手时，以下命令应申请沙箱外执行（Codex 中即批准 require_escalated）：
   - `python src/capture_frames.py <video_id> <url>`（启动无头浏览器截帧，**默认 Edge**）
   - `python src/capture_frames.py --setup-profile`（弹出 Edge 供一次性登录）
   - `python src/dump_transcript.py <url>`（yt-dlp 网络请求；B 站需登录态时会自动从 `.capture-profile` 导出 cookies，期间启动无头 Edge）
   纯本地步骤（`post_process.py`、`python -m http.server`）在沙箱内即可运行。

5. **视频没有任何字幕怎么办？**
   不少新上传的 B 站视频既没有 CC 字幕、AI 字幕也还没生成（`--list-subs` 只有 `danmaku`）。此时走本地 ASR 兜底：
   ```bash
   python src/asr_transcript.py <video_id> [--sample-start 900 --sample-dur 180]
   ```
   - 依赖 `faster-whisper`；有 NVIDIA 显卡时自动用 CUDA（显存 ≤4GB 建议 `--compute-type int8_float16`），无卡则 CPU int8；**AMD 显卡自动改走 whisper.cpp (ROCm)**，详见下节。
   - Windows 上若报 `Library cublas64_12.dll is not found`，装 `nvidia-cublas-cu12 nvidia-cudnn-cu12` 即可，脚本会自动注册 DLL 目录。
   - 进度实时落盘 `_asr_progress.jsonl`，中断后重跑自动续写；`--restart` 从头。
   - **`output/<video_id>/_asr_prompt.txt` 写一行纯 ASCII 术语表**（10 个左右），例如
     `React React React JSX Vite npm install useState useEffect props state`。
     注意：**不要写中文主题句**——在 whisper.cpp 后端上实测压不住英文术语错词；术语堆多了还会掉中文内容。
     详见 `instructions.md` §1b.1。
   - **转写完必须看 stdout**：whisper.cpp 会偶发重复循环幻觉（整块内容被同一句话刷掉，**覆盖率仍显示 100%**），
     脚本会自动检测并用 `-mc 0` 重转；若出现"重转未改善"则必须人工核对那一块。
   - 耗时：100 分钟课程在 RTX 3050 (4GB) 上约 35 分钟；RX 9070 XT (ROCm) 实测 13–17x 实时。会产生约 90MB 的 `audio.m4a`，流程结束时询问是否删除。

6. **自动导出的 cookies 会泄露吗？**
   不会落在仓库里：`dump_transcript.py` / `login_utils.py` 导出的 cookies 写入系统临时目录、用完即删；`cookies.txt` 等模式已加入 `.gitignore`。若需更高清晰度（大会员档位），在 `--setup-profile` 窗口登录大会员账号即可，截帧管线会自动按顶档原生分辨率截取。

---

## 📚 成品在哪里看？

- **在线阅读（GitHub Pages）**：落地页列出全部电子书，点击标题即可阅读（含截图放大、Mermaid 交互）。
  需在仓库 Settings → Pages 一次性选择分支 `pages` + `/ (root)`。具体站点地址与首次启用步骤见 `DEPLOY.md`。
- **分支布局**：`main` = 工具代码；`pages` = 成品（独立分支，目录名 = 视频标题，如 `【零到全栈】4.3-React,前端开发规则`）。
- **发布方式**：`python src/publish.py <video_id>` 或 `python src/publish.py --all`，然后 `git push mine pages`
  （`mine` 是你自己的仓库；上游工具仓库是 `origin`，不要推）。发布 `book.html / book.md / images/` 与（若存在）
  `transcript.corrected.txt`（AI 修正版字幕对照稿，落地页卡片附"字幕对照"链接）；
  原始字幕、transcript.json 等中间物不进公开仓库；`output/` 本地工作区不受任何 git 操作影响。

---

## AMD 显卡本地 ASR

`faster-whisper` 的 GPU 路径面向 NVIDIA CUDA，不支持 AMD。脚本会在检测不到 CUDA 时
**自动切换到 whisper.cpp (ROCm) 后端**，无需手编。一次性安装：

```bash
python src/asr_transcript.py --install-amd
```

它下载 Lemonade 预构建的 gfx120X 版 whisper-cli（**运行时 DLL 全部打包、免装 ROCm**）
及 GGML large-v3 模型到 `tools/`（均已 gitignore）。RX 7000 系用 `--amd-arch gfx110X`。
此后 `--backend auto` 会自动发现并使用；也可 `--backend whispercpp` 强制。
该后端按 `--chunk-sec`（默认 600s）定长块转写，断点续跑以块为粒度；
**默认关闭 flash-attn** 以规避 RDNA4 驱动 bug。

需要先用 `dump_transcript.py` 或 `asr_transcript.py` 下载音频。生成的 `transcript.json`
与平台字幕完全同构，可直接交给现有订正、写书、截图和 HTML 流程。

> 历史脚本 `src/asr_whispercpp.py`（需自行 `cmake -DGGML_VULKAN=1` 编译）已被上述
> `--install-amd` 取代，**不要再用**。
