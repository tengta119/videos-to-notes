"""本地 ASR 兜底管线：当平台没有 CC / AI 字幕时，用 faster-whisper 转写音频。

产出的 transcript.json 与 scraper.get_transcript 完全同构
（video_url / title / video_id / duration / chapters / segments[{start,end,text}]），
因此后续排版、截帧、渲染各步无需任何改动。

用法:
  python src/asr_transcript.py <video_id|url> [选项]

常用选项:
  --model large-v3            whisper 模型（默认 large-v3）
  --device cuda               cuda / cpu（默认自动探测）
  --compute-type int8_float16 GPU 显存 <=4GB 时推荐
  --language zh               默认 zh
  --initial-prompt "..."      领域术语提示，抑制同音错词并让中文输出带标点
  --sample-start 60 --sample-dur 120   只试跑一段，用于评估质量与速度
  --no-words                  关闭词级时间戳（更快，分段更粗）

AMD 显卡（无需 CUDA，如 RX 9070 XT / RDNA4）:
  一次性安装:  python src/asr_transcript.py --install-amd
    下载 Lemonade 预编译的 whisper.cpp ROCm 构建（gfx120X，自带全部运行时 DLL，
    免装 ROCm）到 tools/whisper-cli/，并下载 GGML large-v3 模型到 tools/models/。
    RX 7000 系用 --amd-arch gfx110X。之后 --backend auto（默认）在检测不到 CUDA
    但能找到 whisper-cli + GGML 模型时自动走该后端；也可 --backend whispercpp 强制。
  强制指定:    --backend whispercpp --ggml-model large-v3（或 .bin 路径）
  分块粒度:    --chunk-sec 600（whisper-cli 按定长块转写，断点续跑以块为粒度）

特性:
  - 音频已存在则复用（output/<id>/audio.m4a），否则用 yt-dlp + .capture-profile
    登录态下载（cookies 写在 output/<id>/_cookies.txt，output/ 已被 gitignore）。
  - 断点续跑：进度实时落盘 output/<id>/_asr_progress.jsonl，重跑自动从末尾续接。
"""
import argparse
import io
import json
import re
import os
import subprocess
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))

from config import get_video_dir  # noqa: E402

DEFAULT_MODEL = "large-v3"
SR = 16000


def setup_cuda_dlls():
    """让 ctranslate2 找到 pip 版 CUDA 运行库（nvidia-cublas-cu12 / nvidia-cudnn-cu12）。

    Windows 下这些 DLL 装在 site-packages/nvidia/*/bin，不在系统 PATH 上，
    直接 import 会报 "Library cublas64_12.dll is not found"。必须在
    import ctranslate2 之前调用。
    """
    import glob
    import site

    cands = []
    for base in list(getattr(site, "getsitepackages", lambda: [])()) + [
        os.path.join(os.path.dirname(os.path.dirname(sys.executable)), "Lib", "site-packages")
    ]:
        cands += glob.glob(os.path.join(base, "nvidia", "*", "bin"))
    added = []
    for d in dict.fromkeys(cands):
        if not os.path.isdir(d):
            continue
        try:
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(d)
        except OSError:
            pass
        added.append(d)
    if added:
        os.environ["PATH"] = os.pathsep.join(added) + os.pathsep + os.environ.get("PATH", "")
        print(f">> 已注册 {len(added)} 个 CUDA 运行库目录")
    return added


# --------------------------------------------------------------------------- #
# AMD GPU backend: whisper.cpp (ROCm / gfx120X prebuilt, no CUDA required)
# --------------------------------------------------------------------------- #
TOOLS_DIR = os.path.join(BASE, "tools")
CLI_DIR = os.path.join(TOOLS_DIR, "whisper-cli")
MODELS_DIR = os.path.join(TOOLS_DIR, "models")

# Lemonade SDK 预构建仓库：每个 release 附带各后端 zip，ROCm/Vulkan 运行时 DLL
# 已打包进 zip，无需另装 ROCm。gfx120X 覆盖 RX 9070 XT（gfx1201）。
WHISPERCPP_REPO = "lemonade-sdk/whisper.cpp-rocm"
WHISPERCPP_TAG = os.environ.get("VIDEOBOOK_WHISPERCPP_TAG", "v1.8.4")
# GGML 模型体积较大，从 HuggingFace ggerganov/whisper.cpp 直下。
# 国内网络可设 VIDEOBOOK_GGML_BASE_URL=https://hf-mirror.com/ggerganov/whisper.cpp/resolve/main
GGML_BASE_URL = os.environ.get(
    "VIDEOBOOK_GGML_BASE_URL",
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main")

TS_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
                   r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")


def _find_cli(cli_arg):
    """定位 whisper-cli 可执行文件：显式参数 > tools/whisper-cli > PATH。"""
    cands = []
    if cli_arg:
        cands.append(cli_arg)
    for name in ("whisper-cli.exe", "whisper-cli"):
        cands.append(os.path.join(CLI_DIR, name))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    # 最后尝试 PATH
    from shutil import which
    return which("whisper-cli")


def _find_ggml(model_spec):
    """把 large-v3 / 相对名 / 绝对路径解析成实际 .bin 路径；未下载则返回 None。"""
    if model_spec and os.path.isfile(model_spec):
        return model_spec
    fname = model_spec if (model_spec or "").endswith(".bin") \
        else f"ggml-{model_spec or 'large-v3'}.bin"
    # 优先 tools/models，其次 whisper-cli 同目录，再次仓库根
    for base in (MODELS_DIR, CLI_DIR, BASE):
        for cand in (fname, os.path.basename(fname)):
            p = os.path.join(base, cand)
            if os.path.isfile(p):
                return p
    return None


def _download(url, dest):
    import urllib.request
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        print(f">> 已存在，跳过: {dest}")
        return dest
    print(f">> 下载 {url}\n   -> {dest}")
    tmp = dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "videobook-asr"})
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            blk = r.read(1 << 20)
            if not blk:
                break
            f.write(blk)
            got += len(blk)
            if total:
                print(f"\r   {got / 1e6:.0f}/{total / 1e6:.0f} MB "
                      f"({got / total:.0%})", end="", flush=True)
    print()
    if total and got != total:
        # 连接被中途掐断时 urllib 可能静默 EOF，不留校验就会落一个"看起来完整"的残档
        os.remove(tmp)
        raise SystemExit(f">> 下载不完整（{got}/{total} 字节），已删除残档。"
                         f"请重跑本命令；网络不稳时挂代理，HF 不可达可设 "
                         f"VIDEOBOOK_GGML_BASE_URL 指向 hf-mirror。")
    os.replace(tmp, dest)
    return dest


def _extract_zip_member(zip_path, exe_name, out_dir):
    """从 zip 里解出某个可执行文件及其同级 DLL（保留目录结构）。"""
    import zipfile
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        # 找到含目标 exe 的顶层目录前缀，整棵子树解出（exe 依赖同目录 DLL）
        target = next((n for n in names
                       if os.path.basename(n).lower() == exe_name.lower()), None)
        if not target:
            raise SystemExit(f"zip 内未找到 {exe_name}，含: "
                             + ", ".join(os.path.basename(n) for n in names[:20]))
        top = target.split("/")[0] if "/" in target else ""
        for n in names:
            if top and not (n == top or n.startswith(top + "/")):
                continue
            if n.endswith("/"):
                continue
            dst = os.path.join(out_dir, os.path.relpath(n, top).replace("/", os.sep))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with z.open(n) as src, open(dst, "wb") as out:
                out.write(src.read())


def install_amd(arch, model_name, cli_only=False):
    """下载 whisper.cpp ROCm 预构建 + GGML 模型到 tools/。一次性操作。"""
    asset = f"whisper-{WHISPERCPP_TAG}-windows-rocm-{arch}.zip"
    url = f"https://github.com/{WHISPERCPP_REPO}/releases/download/{WHISPERCPP_TAG}/{asset}"
    os.makedirs(CLI_DIR, exist_ok=True)
    zpath = os.path.join(TOOLS_DIR, asset)
    print(f">> 下载 whisper.cpp ROCm ({arch}) 预构建包 ...")
    _download(url, zpath)
    print(">> 解包到 tools/whisper-cli/ ...")
    _extract_zip_member(zpath, "whisper-cli.exe", CLI_DIR)
    print(f">> 完成：whisper-cli 已就位（{CLI_DIR}）")
    if cli_only:
        return
    fname = f"ggml-{model_name}.bin"
    print(f">> 下载 GGML 模型 {fname}（约 3GB，首次较慢，之后复用）...")
    _download(f"{GGML_BASE_URL}/{fname}", os.path.join(MODELS_DIR, fname))
    print(">> AMD 后端安装完成，之后直接 `python src/asr_transcript.py <id>` 即自动使用。")


def write_wav(path, audio_f32, sr=SR):
    """float32 mono -> 16-bit PCM WAV（whisper-cli 无 ffmpeg 时只吃这个格式）。"""
    import wave
    import numpy as np
    pcm = (np.clip(audio_f32, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


class _W:
    """轻量 word，兼容 rechunk 对 words 的 .word/.start/.end 访问。"""
    __slots__ = ("word", "start", "end")
    def __init__(self, word, start, end):
        self.word, self.start, self.end = word, start, end


class _WSeg:
    """轻量 segment，兼容 rechunk(seg, words) 调用。"""
    __slots__ = ("start", "end", "text", "words")
    def __init__(self, start, end, text, words=None):
        self.start, self.end, self.text, self.words = start, end, text, words


def _parse_srt_entries(srt_path):
    """解析 whisper-cli -osrt 输出为 [(start, end, text)]（时间相对该块 0 起点）。"""
    entries = []
    cur = None
    for raw in io.open(srt_path, encoding="utf-8-sig", errors="replace").read().splitlines():
        line = raw.strip()
        m = TS_RE.search(line)
        if m:
            if cur and "".join(cur["text"]).strip():
                entries.append((cur["start"], cur["end"], " ".join(cur["text"]).strip()))
            h1, m1, s1, ms1, h2, m2, s2, ms2 = m.groups()
            cur = {"start": int(h1) * 3600 + int(m1) * 60 + int(s1) + int(ms1) / 1000,
                   "end": int(h2) * 3600 + int(m2) * 60 + int(s2) + int(ms2) / 1000,
                   "text": []}
            tail = line[m.end():].strip()
            if tail:
                cur["text"].append(tail)
        elif line and cur is not None and not line.isdigit():
            cur["text"].append(line)
    if cur and "".join(cur["text"]).strip():
        entries.append((cur["start"], cur["end"], " ".join(cur["text"]).strip()))
    return entries


_REP_WIN = 12
# 正常语音的 _repeat_score 通常是 1~2；陷入重复循环时可达数十。
_REP_THRESHOLD = 5


def _repeat_score(entries):
    """重复度指标：块内任意 12 字窗口出现的最大次数。

    whisper 在个别音频上会陷入「同一句话刷满整块」的重复循环幻觉（时间戳照常
    前进，故覆盖率自检查不出来），把整块真实内容吃掉。这是检测该故障最灵敏的
    信号，比关键字匹配更通用。
    """
    text = "".join(t for _, _, t in entries)
    if len(text) < _REP_WIN * 2:
        return 0
    counts = {}
    for i in range(0, len(text) - _REP_WIN, 4):
        w = text[i:i + _REP_WIN]
        counts[w] = counts.get(w, 0) + 1
    return max(counts.values())


def run_whisper_chunk(cli, ggml, wav_path, args, prompt, mc=None):
    """对单个 WAV 块跑一次 whisper-cli，返回块内时间（相对 0）的 SRT 条目。

    词级模式（默认）加 -ml 1：whisper.cpp 让每个 token 成为独立条目，即官方
    词级时间戳用法；由 transcribe_whispercpp 重组后再走 rechunk，粒度与
    faster-whisper 路径一致。--no-words 时条目即正常段落。

    mc: 覆盖 -mc（携带的上文 token 数）。None 表示用 whisper.cpp 默认值；
    传 0 可切断跨窗口上下文，用来破除重复循环。
    """
    import tempfile
    prefix = os.path.join(tempfile.gettempdir(),
                          f"vb_wcp_{os.getpid()}_{int(time.time())}")
    cmd = [cli, "-m", ggml, "-f", wav_path, "-l", args.language,
           "-t", str(args.threads or (os.cpu_count() or 4)),
           "--no-flash-attn" if args.no_flash_attn else "--flash-attn",
           "-osrt", "-of", prefix, "-pp"]
    if mc is not None:
        cmd += ["-mc", str(mc)]
    if not args.no_words:
        cmd += ["-ml", "1"]
    if prompt:
        cmd += ["--prompt", prompt]
    if args.beam_size and args.beam_size > 1:
        cmd += ["-bs", str(args.beam_size)]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
        return _parse_srt_entries(prefix + ".srt")
    finally:
        for ext in (".srt", ".vtt", ".txt"):
            p = prefix + ext
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


def transcribe_whispercpp(wav, args, lo, hi, prompt, prog_fh):
    """用 whisper.cpp 分块转写 [lo,hi)。断点续跑以 --chunk-sec 为粒度。

    调用方已把 lo 对齐到块边界并裁掉残块进度，故此处从 lo 顺序向前处理。
    """
    cli = _find_cli(args.cli)
    ggml_spec = args.ggml_model or args.model
    ggml = _find_ggml(ggml_spec)
    if not cli:
        raise SystemExit("未找到 whisper-cli。先运行：python src/asr_transcript.py --install-amd")
    if not ggml:
        raise SystemExit(f"未找到 GGML 模型（{ggml_spec}）。先运行：--install-amd")
    print(f">> AMD 后端: {cli}\n            模型: {ggml}")

    chunk = max(30.0, float(args.chunk_sec))
    t_start = time.time()
    n_total = 0
    pos = lo
    while pos < hi - 0.5:
        end = min(pos + chunk, hi)
        seg_dur = end - pos
        clip = wav[int(pos * SR):int(end * SR)]
        tmp_wav = os.path.join(os.environ.get("TEMP", "/tmp"),
                               f"vb_chunk_{int(pos)}.wav")
        write_wav(tmp_wav, clip)
        try:
            print(f">> [whisper.cpp] 转写块 [{hhmmss(pos)} -> {hhmmss(end)}]"
                  f" ({seg_dur / 60:.1f} 分钟) ...", flush=True)
            tb = time.time()
            entries = run_whisper_chunk(cli, ggml, tmp_wav, args, prompt,
                                        args.max_context)
            if args.loop_guard:
                # -mc 与是否触发循环并不单调（实测 96 正常、128 循环、160 正常），
                # 所以没法靠调参一劳永逸，只能事后检测本块、命中则用 -mc 0 重转。
                bad = _repeat_score(entries)
                if bad >= _REP_THRESHOLD:
                    print(f"   ⚠ 疑似重复循环（重复度 {bad}），用 -mc 0 重转本块 ...",
                          flush=True)
                    retry = run_whisper_chunk(cli, ggml, tmp_wav, args, prompt, 0)
                    good = _repeat_score(retry)
                    if good < bad:
                        entries = retry
                        print(f"   已替换为重转结果（重复度 {bad} -> {good}）", flush=True)
                    else:
                        print(f"   ⚠ 重转未改善（{good}），保留原结果，请人工核对本块",
                              flush=True)
            if args.no_words:
                segs = [_WSeg(st, en, tx) for st, en, tx in entries]
            else:
                # 逐 token 条目按停顿间隙重组为 segment，词时间戳交给 rechunk 细切，
                # 输出粒度与 faster-whisper 路径一致。间隙阈值 0.7s：句间停顿通常
                # 大于此值，且句末标点处 rechunk 仍会二次切分，容错足够。
                segs = []
                buf_words, buf_start = [], None
                for st, en, tx in entries:
                    if buf_words and st - buf_words[-1].end > 0.7:
                        segs.append(_flush_words(buf_words, buf_start))
                        buf_words, buf_start = [], None
                    if buf_start is None:
                        buf_start = st
                    buf_words.append(_W(tx, st, en))
                if buf_words:
                    segs.append(_flush_words(buf_words, buf_start))
            for s in segs:
                for c in rechunk(s, s.words):
                    c["text"] = fix_punct(c["text"])
                    rec = {"start": round(c["start"] + pos, 3),
                           "end": round(c["end"] + pos, 3), "text": c["text"]}
                    prog_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    n_total += 1
            prog_fh.flush()
            el = time.time() - tb
            print(f"   块完成：{len(segs)} 段，用时 {el:.0f}s "
                  f"（{seg_dur / max(el, 1e-6):.1f}x 实时）")
        finally:
            if os.path.exists(tmp_wav):
                os.remove(tmp_wav)
        pos = end
    print(f">> whisper.cpp 本轮共写 {n_total} 段，用时 {(time.time() - t_start) / 60:.1f} 分钟")
    return n_total


def _flush_words(words, start):
    """把逐词条目折叠成一个 _WSeg（带块内时间戳）。

    whisper.cpp 的英文 token 自带前导空格、中文 token 不带，故含 CJK 时直接
    拼接、纯英文时用空格连接 stripped token，都能还原自然书写。
    """
    if any(any("一" <= ch <= "鿿" for ch in w.word) for w in words):
        text = "".join(w.word for w in words).strip()
    else:
        text = re.sub(r"\s+", " ", " ".join(w.word.strip() for w in words)).strip()
    return _WSeg(start, words[-1].end, text, words)



def hhmmss(sec: float) -> str:
    sec = max(0, int(round(sec)))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def _norm_text(t: str) -> str:
    return re.sub(r"[\s。，、？！：；,.?!:;\"]", "", t)


def dedupe_rows(rows):
    """折叠进度文件里的重复段（机器伪影，非讲师重复）。

    试跑后未 --restart、或断点续跑整块重转时，同一区间会被写两遍，进度里
    出现大量"同 start 同 text"段与"合并段+逐字段"变体。三步折叠：
    1) 相邻 (start, text) 完全相同 -> 只留一条；
    2) 同 start 组内某段归一化文本被另一段包含 -> 留最长；
    3) 某段归一化文本 == 紧随其后 2~8 段文本的拼接 -> 合并伪影，删。
    """
    rows.sort(key=lambda r: (r["start"], r["end"]))
    # 同 start 且归一化文本相同即视为重复（两轮转写对同一词可能大小写不同）
    def _key(r):
        return (r["start"], _norm_text(r["text"]).casefold())
    out = []
    for r in rows:
        if out and _key(out[-1]) == _key(r):
            continue
        out.append(r)
    flat, i = [], 0
    while i < len(out):
        j = i
        while j + 1 < len(out) and out[j + 1]["start"] == out[i]["start"]:
            j += 1
        g = out[i:j + 1]
        if len(g) > 1:
            norm = [_norm_text(r["text"]).casefold() for r in g]
            g = [r for a, r in enumerate(g)
                 if not any(b != a and norm[a] and norm[a] in norm[b]
                            for b in range(len(g)))] or g
        flat.extend(g)
        i = j + 1
    norm = [_norm_text(r["text"]).casefold() for r in flat]
    drop = set()
    for i in range(len(flat)):
        acc = ""
        for k in range(i + 1, min(i + 9, len(flat))):
            acc += norm[k]
            if acc == norm[i]:
                drop.add(i)
                break
            if len(acc) > len(norm[i]):
                break
    return [r for i, r in enumerate(flat) if i not in drop]


def norm_id(arg: str) -> str:
    """从 BVxxxx 或完整 URL 中取出视频 id，并保留 B 站分 P。"""
    import re
    m = re.search(r"(BV[0-9A-Za-z]{10})", arg)
    if m:
        vid = m.group(1)
        # 一个 BV 号可包含多个分 P；为每个分 P 建立独立工作目录，
        # 避免合集转写/截图/电子书互相覆盖。
        pm = re.search(r"[?&]p=(\d+)", arg)
        return f"{vid}_p{int(pm.group(1))}" if pm else vid
    m = re.search(r"(?:v=|youtu\.be/)([\w-]{11})", arg)
    if m:
        return m.group(1)
    return arg.strip().rstrip("/")


def video_url_of(vid: str) -> str:
    if vid.startswith("BV") or vid.startswith("av"):
        m = re.match(r"(.+)_p(\d+)$", vid)
        if m:
            return f"https://www.bilibili.com/video/{m.group(1)}?p={m.group(2)}"
        return f"https://www.bilibili.com/video/{vid}"
    return f"https://www.youtube.com/watch?v={vid}"


def ensure_cookies(out_dir: str) -> str:
    from login_utils import export_cookies, has_login_cookie
    path = os.path.join(out_dir, "_cookies.txt")
    try:
        export_cookies(out_path=os.path.abspath(path))
        if has_login_cookie(path):
            print(">> 已导出 .capture-profile 登录态 cookies")
            return path
    except Exception as e:  # noqa: BLE001
        print(f">> cookies 导出失败（继续匿名下载）: {e}")
    return None


def ensure_audio(out_dir: str, url: str) -> str:
    for ext in (".m4a", ".webm", ".opus", ".mp3", ".wav", ".mp4"):
        p = os.path.join(out_dir, "audio" + ext)
        if os.path.exists(p) and os.path.getsize(p) > 1024:
            print(f">> 复用已有音频: {p}")
            return p
    ck = ensure_cookies(out_dir)
    tpl = os.path.join(out_dir, "audio.%(ext)s")
    cmd = [sys.executable, "-m", "yt_dlp", "-f", "bestaudio", "--no-playlist", "-o", tpl]
    if ck:
        cmd += ["--cookies", ck]
    cmd.append(url)
    print(">> 下载音频（仅音频，不下载视频流）...")
    subprocess.run(cmd, check=True)
    for ext in (".m4a", ".webm", ".opus", ".mp3", ".wav"):
        p = os.path.join(out_dir, "audio" + ext)
        if os.path.exists(p):
            return p
    raise SystemExit("音频下载失败")


def fetch_meta(out_dir: str, url: str) -> dict:
    p = os.path.join(out_dir, "_info.json")
    if not os.path.exists(p):
        ck = ensure_cookies(out_dir)
        cmd = [sys.executable, "-m", "yt_dlp", "-j", "--no-playlist"]
        if ck:
            cmd += ["--cookies", ck]
        cmd.append(url)
        with open(p, "w", encoding="utf-8") as f:
            subprocess.run(cmd, stdout=f, check=True)
    d = json.load(open(p, encoding="utf-8"))
    chapters = []
    for c in d.get("chapters") or []:
        chapters.append({
            "start": hhmmss(c.get("start_time") or 0),
            "end": hhmmss(c.get("end_time") or 0),
            "title": (c.get("title") or "").strip(),
        })
    return {"title": d.get("title") or "", "duration": float(d.get("duration") or 0),
            "chapters": chapters}


_CJK = "\\u4e00-\\u9fff\\u3400-\\u4dbf\\u3000-\\u303f\\uff00-\\uffef"
_HALF2FULL = {",": "\uff0c", ";": "\uff1b", ":": "\uff1a", "?": "\uff1f", "!": "\uff01"}


def fix_punct(text: str) -> str:
    """把中文语境下的半角标点转为全角；英文术语、文件名、版本号内部标点保持原样。

    Whisper 输出中文时习惯用半角逗号，直接进电子书观感较差。判定规则是
    该标点至少有一侧紧邻 CJK 字符（句号还要求右侧不是字母数字，
    以免把 hello.c / v1.2 之类改坏）。
    """
    cjk = re.compile("[" + _CJK + "]")
    out = []
    for i, ch in enumerate(text):
        repl = _HALF2FULL.get(ch)
        if ch == ".":
            nxt = text[i + 1] if i + 1 < len(text) else ""
            prev = text[i - 1] if i else ""
            if cjk.match(prev) and not (nxt.isalnum()):
                repl = "\u3002"
        if repl:
            prev = text[i - 1] if i else ""
            nxt = text[i + 1] if i + 1 < len(text) else ""
            if cjk.match(prev) or cjk.match(nxt):
                out.append(repl)
                continue
        out.append(ch)
    return "".join(out)


def rechunk(seg, words, max_sec=8.0, max_chars=60):
    """把一个 whisper 长段按标点 / 时长 / 字数切成接近平台字幕粒度的小段。"""
    text = (seg.text or "").strip()
    if not words or len(text) <= max_chars:
        return [{"start": seg.start, "end": seg.end, "text": text}]

    out, buf, buf_start, last_end = [], [], None, seg.start
    for w in words:
        wt = (w.word or "").strip()
        if not wt:
            continue
        if buf_start is None:
            buf_start = w.start
        buf.append(wt)
        cur = "".join(buf)
        last_end = w.end
        hard = (w.end - buf_start) >= max_sec or len(cur) >= max_chars
        soft = cur[-1] in "。！？；，、,.!?;:："
        if hard or (soft and len(cur) >= max_chars * 0.55):
            out.append({"start": buf_start, "end": last_end, "text": cur.strip()})
            buf, buf_start = [], None
    if buf:
        out.append({"start": buf_start, "end": last_end, "text": "".join(buf).strip()})
    return [c for c in out if c["text"]]


def main():
    ap = argparse.ArgumentParser(description="faster-whisper / whisper.cpp 本地转写兜底")
    ap.add_argument("video", nargs="?", help="video_id 或视频 URL（--install-amd 时可省略）")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--device", default="auto")
    ap.add_argument("--compute-type", default=None)
    ap.add_argument("--language", default="zh")
    ap.add_argument("--initial-prompt", default=None)
    ap.add_argument("--initial-prompt-file", default=None,
                    help="领域术语提示文件；缺省时自动读取 output/<id>/_asr_prompt.txt")
    ap.add_argument("--beam-size", type=int, default=5)
    ap.add_argument("--sample-start", type=float, default=None)
    ap.add_argument("--sample-dur", type=float, default=None)
    ap.add_argument("--no-words", action="store_true")
    ap.add_argument("--restart", action="store_true", help="忽略已有进度，从头转写")
    # ---- 后端选择（AMD GPU）----
    ap.add_argument("--backend", choices=["auto", "faster-whisper", "whispercpp"], default="auto",
                    help="auto：有 CUDA 走 faster-whisper，否则探测 whisper.cpp（AMD）")
    ap.add_argument("--cli", default=None, help="whisper-cli 可执行文件路径（默认自动发现 tools/whisper-cli）")
    ap.add_argument("--ggml-model", default=None,
                    help="GGML 模型名（如 large-v3 / large-v3-turbo）或 .bin 路径；默认同 --model")
    ap.add_argument("--threads", type=int, default=None, help="whisper.cpp CPU 线程数")
    ap.add_argument("--chunk-sec", type=float, default=600,
                    help="whisper.cpp 分块转写粒度（秒），也是断点续跑粒度")
    ap.add_argument("--max-context", type=int, default=-1,
                    help="whisper.cpp -mc：携带的上文 token 数（-1 用其默认值）。"
                         "调大更连贯，但也更容易诱发重复循环幻觉")
    ap.add_argument("--no-loop-guard", dest="loop_guard", action="store_false",
                    default=True,
                    help="关闭重复循环检测（默认开启：命中后自动用 -mc 0 重转该块）")
    ap.add_argument("--no-flash-attn", dest="no_flash_attn", action="store_true", default=True,
                    help="whisper.cpp 关闭 flash-attn（默认关：RDNA4 Vulkan 驱动有崩溃 bug）")
    ap.add_argument("--flash-attn", dest="no_flash_attn", action="store_false",
                    help="whisper.cpp 强制启用 flash-attn")
    # ---- 安装 AMD 后端 ----
    ap.add_argument("--install-amd", action="store_true",
                    help="下载 whisper.cpp ROCm 预构建 + GGML 模型到 tools/ 后退出")
    ap.add_argument("--amd-arch", default="gfx120X",
                    help="ROCm 目标架构：gfx120X(RX9000/RDNA4) / gfx110X(RX7000/RDNA3)")
    args = ap.parse_args()

    if args.install_amd:
        install_amd(args.amd_arch, args.ggml_model or args.model)
        return
    if not args.video:
        ap.error("需要 video_id 或视频 URL（或使用 --install-amd）")

    vid = norm_id(args.video)
    url = video_url_of(vid)
    out_dir = get_video_dir(vid)
    os.makedirs(out_dir, exist_ok=True)

    prompt = args.initial_prompt
    if not prompt:
        pf = args.initial_prompt_file or os.path.join(out_dir, "_asr_prompt.txt")
        if os.path.exists(pf):
            prompt = io.open(pf, encoding="utf-8-sig").read().strip()
            print(f">> 使用领域术语提示: {pf}（{len(prompt)} 字）")

    audio = ensure_audio(out_dir, url)
    meta = fetch_meta(out_dir, url)
    duration = meta["duration"]
    print(f">> 标题: {meta['title']}  时长: {duration:.0f}s ({hhmmss(duration)})")
    print(f">> 官方章节: {len(meta['chapters'])} 个")

    setup_cuda_dlls()
    from faster_whisper import decode_audio  # 两种后端共用音频解码

    # ---- 后端选择：auto = 有 CUDA 用 faster-whisper，否则探测 whisper.cpp（AMD）----
    import ctranslate2
    backend = args.backend
    has_cuda = ctranslate2.get_cuda_device_count() > 0
    if backend == "auto":
        if has_cuda:
            backend = "faster-whisper"
        elif _find_cli(args.cli) and _find_ggml(args.ggml_model or args.model):
            backend = "whispercpp"
        else:
            backend = "faster-whisper"  # 退 CPU int8
            print(">> 未检测到 CUDA，也未发现 whisper-cli/GGML 模型，退回 faster-whisper CPU。")
            print(">> AMD 显卡请一次性执行: python src/asr_transcript.py --install-amd")
    device = args.device if backend == "faster-whisper" else "amd-gpu(rocm)"

    wav = decode_audio(audio, sampling_rate=SR)
    total = len(wav) / SR
    print(f">> 音频解码完成，实际时长 {total:.0f}s")

    prog = os.path.join(out_dir, "_asr_progress.jsonl")
    resume_at = 0.0
    rows = []
    if args.sample_start is not None or args.restart:
        if os.path.exists(prog):
            os.remove(prog)
    elif os.path.exists(prog):
        with open(prog, encoding="utf-8") as f:
            rows = [json.loads(x) for x in f if x.strip()]
        if rows:
            resume_at = float(rows[-1]["end"])
            print(f">> 检测到进度，从 {hhmmss(resume_at)} 续跑（已有 {len(rows)} 段）")

    lo = args.sample_start if args.sample_start is not None else resume_at
    hi = (lo + args.sample_dur) if (args.sample_start is not None and args.sample_dur) else duration

    if backend == "whispercpp":
        # whisper.cpp 以 --chunk-sec 定长块为续跑粒度：残块（进度落在块中间）整块
        # 重跑，故先把 lo 回退到块边界并裁掉该块之后已写的行，避免重复段落。
        bnd = int(lo // args.chunk_sec) * args.chunk_sec
        if lo > bnd:
            keep = [r for r in rows if float(r["start"]) < bnd]
            with open(prog, "w", encoding="utf-8") as f2:
                for r in keep:
                    f2.write(json.dumps(r, ensure_ascii=False) + "\n")
            lo = float(bnd)
            print(f">> 进度回退到块边界 {hhmmss(lo)} 重跑残块")

    fh = open(prog, "a", encoding="utf-8")
    try:
        if backend == "whispercpp":
            print(f">> 转写区间 {hhmmss(lo)} -> {hhmmss(min(hi, total))}"
                  f"（{(min(hi, total) - lo) / 60:.1f} 分钟音频）")
            transcribe_whispercpp(wav, args, lo, hi, prompt, fh)
        else:
            from faster_whisper import WhisperModel
            if device == "auto":
                device = "cuda" if has_cuda else "cpu"
            ctype = args.compute_type or ("int8_float16" if device == "cuda" else "int8")
            print(f">> 加载模型 {args.model} @ {device}/{ctype} ...")
            t0 = time.time()
            model = WhisperModel(args.model, device=device, compute_type=ctype)
            print(f">> 模型就绪，用时 {time.time() - t0:.1f}s")

            clip = wav[int(lo * SR):int(hi * SR)]
            print(f">> 转写区间 {hhmmss(lo)} -> {hhmmss(min(hi, total))}"
                  f"（{(min(hi, total) - lo) / 60:.1f} 分钟音频）")
            kw = dict(language=args.language, beam_size=args.beam_size, vad_filter=True,
                      vad_parameters={"min_silence_duration_ms": 400},
                      condition_on_previous_text=False,
                      word_timestamps=not args.no_words)
            if prompt:
                kw["initial_prompt"] = prompt
            t1 = time.time()
            segments, info = model.transcribe(clip, **kw)
            n = 0
            for seg in segments:
                for c in rechunk(seg, seg.words if not args.no_words else None):
                    c["text"] = fix_punct(c["text"])
                    rec = {"start": round(c["start"] + lo, 3), "end": round(c["end"] + lo, 3),
                           "text": c["text"]}
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    n += 1
                fh.flush()
                if n and n % 40 == 0:
                    el = time.time() - t1
                    done = seg.end
                    rate = el / max(done, 1e-6)
                    eta = rate * ((hi - lo) - done)
                    print(f"   [{hhmmss(seg.end + lo)}] {n} 段  "
                          f"{rate:.2f}x 耗时  ETA {eta / 60:.1f} 分钟", flush=True)
            print(f">> 本轮转写完成: {n} 段，用时 {(time.time() - t1) / 60:.1f} 分钟")
    finally:
        fh.close()

    if args.sample_start is not None:
        print(">> 试跑模式：不写 transcript.json。抽样结果：")
        with open(prog, encoding="utf-8") as f:
            for line in list(f)[:12]:
                r = json.loads(line)
                print(f"   [{hhmmss(r['start'])}] {r['text']}")
        return

    rows = dedupe_rows([json.loads(x) for x in open(prog, encoding="utf-8") if x.strip()])
    segs = [{"start": hhmmss(r["start"]), "end": hhmmss(r["end"]), "text": r["text"]}
            for r in rows if r["text"]]

    asr_meta = {"model": (args.ggml_model or args.model) if backend == "whispercpp"
                          else args.model,
                "device": device, "language": args.language,
                "backend": "whisper.cpp(ROCm)" if backend == "whispercpp" else "faster-whisper"}
    if backend != "whispercpp":
        asr_meta["compute_type"] = ctype
    result = {"video_url": url, "title": meta["title"], "video_id": vid,
              "duration": duration or total, "chapters": meta["chapters"],
              "segments": segs, "asr": asr_meta}

    with open(os.path.join(out_dir, "transcript.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "transcript.txt"), "w", encoding="utf-8") as f:
        for s in segs:
            mm = int(s["start"].split(":")[0]) * 60 + int(s["start"].split(":")[1])
            f.write(f"[{mm:02d}:{s['start'].split(':')[2]}] {s['text']}\n")

    cov = rows[-1]["end"] / (duration or total) if rows else 0
    print(f"\n✅ transcript.json 已写入 {out_dir}（{len(segs)} 段）")
    print(f">> 字幕覆盖率自检: {cov:.1%}")
    if cov < 0.5:
        print("❌ 覆盖率过低，重跑本命令可自动续写剩余部分。")
        sys.exit(3)


if __name__ == "__main__":
    main()
