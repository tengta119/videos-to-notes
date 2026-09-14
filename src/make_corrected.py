"""由 transcript.json 生成 AI 修正版字幕对照稿 transcript.corrected.txt。

原则（用户约定）：逐段保留讲师原始字词与顺序，不合并、不改写为书面语；
仅做两类修改——(1) ASR 错词替换（MAP）；(2) 口癖清理（纯语气词整段删除、
句尾语气词剥离、单字口吃叠词折叠）。MAP 可按视频扩充。

用法: python make_corrected.py <video_id> [<video_id> ...] | --all
"""
import argparse
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ASR 错词 -> 正确词（按长度降序应用，避免子串误伤）
MAP = {
    "深圳市软件工程": "生成式软件工程",
    "英拉SEMBLY": "内联汇编",
    "英外SAMBLY": "内联汇编",
    "inline sembly": "内联汇编",
    "英line sembly": "内联汇编",
    "chain of salt": "chain of thought",
    "chal thought": "chain of thought",
    "chef s": "chain of thought",
    "chap out": "chain of thought",
    "chp out": "chain of thought",
    "CHAPSP": "chain of thought",
    "test time skilling": "test-time scaling",
    "试time skilling": "test-time scaling",
    "cloud opo4.5": "Claude Opus 4.5",
    "MANUEL伯纳姆": "Manuel Blum",
    "hugin face": "Hugging Face",
    "open street map": "OpenStreetMap",
    "home brew": "Homebrew",
    "exterminate js": "xterm.js",
    "xterm js": "xterm.js",
    "deep sv4flash": "DeepSeek",
    "deep sick with the flash": "DeepSeek",
    "deep pick": "DeepSeek",
    "deep chick": "DeepSeek",
    "D4C": "DeepSeek",
    "DIVSK": "DeepSeek",
    "KIMIK3": "Kimi K3",
    "GPT5.6": "GPT-5.6",
    "GBT5.6": "GPT-5.6",
    "GPP5.6": "GPT-5.6",
    "GP5.6": "GPT-5.6",
    "GPT56": "GPT-5.6",
    "cheat gp d": "ChatGPT",
    "CHEGBT": "ChatGPT",
    "拆GBT": "ChatGPT",
    "拆GPT": "ChatGPT",
    "拆GPA": "ChatGPT",
    "terry machine": "Turing machine",
    "church tcs": "Church-Turing 论题",
    "habalton pass": "哈密顿路径",
    "ham alton": "哈密顿",
    "three reset": "3-SAT",
    "justin time": "just-in-time",
    "include pass": "include path",
    "yo mode": "YOLO mode",
    "low list": "allowlist",
    "AI slap": "AI slop",
    "passer": "parser",
    "sober": "solver",
    "agents点MD": "agents.md",
    "AGENTS点MD": "agents.md",
    "agent4点MD": "agents.md",
    "agency md": "agents.md",
    "H4点MD": "agents.md",
    "卢卡": "LUCA",
    "杠I": "-I",
    "在ID里": "在 IDE 里",
    "ID里面": "IDE 里面",
    "ID的": "IDE 的",
    "GBT": "ChatGPT",
    "威尔法尔": "verifier",
    "威尔法": "verifier",
    "VERIFILE": "verifier",
    "VERIFI": "verifier",
    "WIFI": "verifier",
    "linux": "Linux",
    # ── BV1kybV6DE47 软件仓库管理（本地 large-v3 ASR 稿）──
    "GP16": "GPT-6",
    "GP6": "GPT-6",
    "GP5.6": "GPT-5.6",
    "GP5": "GPT-5",
    "义父楼": "逸夫楼",
    "光山玩具": "光栅玩具",
    "光山尺": "光栅尺",
    "巨深赛道": "具身赛道",
    "操系统": "操作系统",
    "外部": "外包",
    "webcoding": "vibe coding",
    "web coding": "vibe coding",
    "webcode": "vibe code",
    "Vichy Studio": "Visual Studio",
    "hello.ce": "hello.c",
    "hello.ca": "hello.c",
    "git积交": "git 提交",
    "Basic Practice": "best practice",
    "confessional commits": "Conventional Commits",
    "chat gpg": "ChatGPT",
    "fixed井三": "fixes #3",
    "井三": "#3",
    ".ds-store": ".DS_Store",
    ".dstor": ".DS_Store",
    "DS Store": ".DS_Store",
    "command and fund": "command not found",
    "GH或者GLab": "gh 或者 glab",
    "GLab": "glab",
    "一个go说": "一个 goal 说",
    "汉诺坦": "汉诺塔",
    "双机": "双击",
    "灵光一线": "灵光一现",
    "巨声无比": "巨大无比",
    "bysect": "bisect",
    "by set": "bisect",
    "Community History": "commit history",
    "computer object": "commit object",
    "committed object": "commit object",
    "committed message": "commit message",
    "CommitMessage": "commit message",
    "hide是指向": "HEAD 是指向",
    "ZXY的循环": "X、Y、Z 的循环",
    "就可以出科技": "就可以出效果",
    "都是AIS了": "都是 AI slop 了",
    "一个ATI": "一个 API",
    "cs6ea": "cs61a",
    "learn git branching.js.org": "learngitbranching.js.org",
    "Bitkeeper": "BitKeeper",
    "ProGit": "Pro Git",
    "Git Unity": "git init",
    "git unity": "git init",
    "off by onein strlencheck": "off-by-one in strlen check",
    "state of art": "state of the art",
    "Virtual Implement就": "Virtual Implementation 就",
    "NEO VM": "Neovim",
    "MPM": "npm",
    "cherrypick": "cherry-pick",
    "Implantation": "Implementation",
    "Intent and Spat": "Intent 和 Spec、",
    "Information的空间": "Implementation 的空间",
    "Redmi": "README",
    "Readme": "README",
    "AI Stop": "AI Slop",
    "deep-seek": "DeepSeek",
    "全大学会": "全大写会",
    "前移默化": "潜移默化",
    "不定的变好": "不停地变好",
    "省上要的": "省 token 的",
    "快招": "快照",
    "测试用力": "测试用例",
    "报打好": "包打好",
    "或者流打": "或者流水线",
    "CICD": "CI/CD",
    "precommitted hook": "pre-commit hook",
    "interment": "int main(void)",
    "intimate void": "int main(void)",
    "监控号": "尖括号",
    "叫hours": "叫 ours",
    "可以reveal": "可以 revert",
    "agency.md": "AGENTS.md",
    "agent.cmd": "AGENTS.md",
    "考案神": "convention",
    "超系统课": "计算机系统课",
    # ── BV1ygKa67ExD 让 Agent 不再等待：基于 RocketMQ 的异步协作架构实战（平台 AI 字幕稿）──
    # 产品/术语
    "net topic": "LiteTopic",
    "NETTOPIC卡": "LiteTopic 名",
    "NETTOPIC": "LiteTopic",
    "NETOPIC": "LiteTopic",
    "NETB的": "LiteTopic 的",
    "NETB": "LiteTopic",
    "net的队列": "LiteTopic 的队列",
    "NETB,": "LiteTopic,",
    "light oppick": "LiteTopic",
    "light it topic": "LiteTopic",
    "light topic": "LiteTopic",
    "light tpc": "LiteTopic",
    "light toy": "LiteTopic",
    "topic克": "topic",
    "ROCKEMQ": "RocketMQ",
    "ROCKEMK": "RocketMQ",
    "ROKEMK": "RocketMQ",
    "ROKEMQ": "RocketMQ",
    "ROGUEQ": "RocketMQ",
    "ROGUQ": "RocketMQ",
    "rockemk": "RocketMQ",
    "BROKMQ": "RocketMQ",
    "LOCMQ": "RocketMQ",
    "rock rock": "RocketMQ",
    "rock mek": "RocketMQ",
    "ROGUMQ": "RocketMQ",
    "rocket q": "RocketMQ",
    "百联网关": "百炼网关",
    "百利": "百炼",
    "open cloud": "OpenClaw",
    "open cl连接层": "OpenClaw 连接层",
    "Open cl": "OpenClaw",
    "open clg": "OpenClaw",
    "open cor": "OpenClaw",
    "open cou": "OpenClaw",
    "open call": "OpenClaw",
    "open CD": "OpenClaw",
    "两个cloud": "两个 OpenClaw",
    "high cloud": "HiClaw",
    "海克劳": "HiClaw",
    "HK2": "HiClaw",
    "matrix": "Matrix",
    "mini I/O": "MinIO",
    "瓦斯比P": "WhatsApp",
    "THTTP": "HTTP",
    "或者AHTTP": "或者 HTTP",
    "和NTS": "和 NATS",
    "AIA型层": "AI Agent 层",
    "AIAGINK": "AI Agent",
    "A镜特": "Agent",
    "A镜头": "Agent",
    "A型的": "Agent 的",
    "A镜": "Agent",
    "A英": "Agent",
    "长任务多A映射": "长任务、多 Agent 协作",
    "多a net": "多 Agent",
    "a to a": "A2A",
    "它MCV": "MCP",
    # 会话/队列语义（平台字幕把 session 识别成绘画/三线/筛选等）
    "绘画网格": "会话网关",
    "绘画": "会话",
    "三线": "session",
    "个筛选进来": "个会话进来",
    "筛选新的筛选": "会话新的会话",
    "这个筛选": "这个会话",
    "筛选里面": "会话里面",
    "筛选有数据": "会话有数据",
    "节是状态": "状态",
    "集品师": "集中式",
    "多音节": "多节点",
    "单音节": "单节点",
    "插线": "session",
    "就三星logo": "session log",
    "transport的眼睛": "transport 的演进",
    # 机制术语
    "一幅画": "异步化",
    "易住抑制阻塞": "队头阻塞",
    "对头阻塞": "队头阻塞",
    "对头": "队头",
    "传统的币啊": "传统的比啊",
    "像SOB": "像 SLB",
    "种double": "种 dubbo",
    "马斯达尼克斯": "马刺打尼克斯",
    "少量TPC": "少量 Topic",
    "TP狗": "Topic",
    "TOPIK": "Topic",
    "TOBY和top1": "Topic0 和 Topic1",
    "一个control": "一个 consumer",
    "赛事": "会话",
    "SaaS spend": "suspend",
    "SaaS Spend": "suspend",
    "saas spend": "suspend",
    "sars spend": "suspend",
    "sars pen": "suspend",
    "SAAS寸": "suspend",
    "SaaS寸": "suspend",
    "subs喷": "suspend",
    "SaaS喷": "suspend",
    "read set": "ready set",
    "radio set": "ready set",
    "塞特有数据": "set 有数据",
    "回血": "回写",
    "回结": "回写",
    "驱虫": "去重",
    "市场可能是分钟": "时长可能是分钟",
    "来缩建": "来构建",
    "是在编码器": "是被编排好的",
    "从宣写的代码": "程序员写的代码",
    "用力场景": "用例场景",
    "road map": "Roadmap",
    "方方式": "方式",
    "这个眼镜": "这个演进",
    "不是中泰": "不是终态",
    "顺序交易": "顺序消息",
    "release这样": "Redis 这样",
    "那个QS是不同": "那个 QPS 是不同",
    "大冒险": "大模型",
    # ── BV1k3JA6cEAt 【零到全栈】4.2 Vite、npm与前端构建（本地 ASR 稿）──
    # 课程/项目/人名
    "领导全栈": "零到全栈",
    "领导全站": "零到全栈",
    "优宇锡": "尤雨溪",
    "Lil2Tech": "zero2tech",
    "CD Zero to Tag": "cd zero2tech",
    "Zero to Tag": "zero2tech",
    "Zero2Tag": "zero2tech",
    # 口误字/同音词
    "见值对": "键值对",
    "建制队": "键值对",
    "欠套": "嵌套",
    "dole号": "逗号",
    "秒急": "秒级",
    "结偶": "解耦",
    "制好了": "治好了",
    "Varion": "version",
    "Vern": "version",
    "Jason": "JSON",
    "scraps": "scripts",
    "remi": "README",
    "REME": "README",
    "dist.access": "dist/assets",
    # 命令行/包名
    "init-y": "init -y",
    "node-v": "node -v",
    "npm-v": "npm -v",
    "install-d": "install -D",
    "bannery": ".bin",
    "banary": ".bin",
    "benary": ".bin",
    "node modules": "node_modules",
    "NodeModules": "node_modules",
    "package.lock.json": "package-lock.json",
    "EnemyJS": "animejs",
    "enemy.js": "anime.js",
    "animate.js": "anime.js",
    "enemy": "anime",
    "Enemy": "anime",
    "ngx": "nginx",
    "NGX": "nginx",
    "control加shift加c": "Ctrl+Shift+C",
    "control加c": "Ctrl+C",
    "command加s": "Command+S",
    # ── BV1FX7F6UEWq 【零到全栈】4.3 React、前端开发规则（本地 ASR 稿）──
    # 课程/项目/文件
    "瑞爱": "React",
    "REAN": "React",
    "Viu": "Vue",
    "viu": "Vue",
    "resultcard": "ResultCard",
    "vanillavanilla": "vanilla。vanilla",
    # 注意：MAP 按 key 长度降序应用，且是「先全量替换再输出」——原文写的是
    # TaxLab，所以 "TextLab." 这类 key 匹配不到，必须同时给出 TaxLab 变体。
    "TaxLab.": "textlab.",
    "taxlab.": "textlab.",
    # 保护性空映射：4.2 段里的 "Animate"->"anime" 会把 AnimatedCardGrid 吃成
    # animedCardGrid；key 更长者先应用，所以在这里挡一道。
    "AnimatedCardGrid": "AnimatedCardGrid",
    "vatconfig.js": "vite.config.js",
    "improcardredocard": "InputCard、ResultCard",
    "InputCardResultCard": "InputCard、ResultCard",
    "ViteJS然后PluginReact": "@vitejs/plugin-react",
    "Poweringyourfavoriteframeworks": "Powering your favorite frameworks",
    # "Vite" + "Config.js" 被切成两段，拼回来才是 Vite.config.js
    "Config.js": ".config.js",
    "TextLab.": "textlab.",
    "TextLabJSX": "textlab.jsx",
    # 命名规则（据视频画面核对）：组件 PascalCase（TextLabPage.jsx），
    # HTML 用 kebab-case（text-lab.html），入口文件全小写（textlab.jsx / result.jsx）。
    "textlabpage": "TextLabPage",
    "TaxLabPage": "TextLabPage",
    "TagsLabPage": "TextLabPage",
    "textlab.html": "text-lab.html",
    "TaxLab.html": "text-lab.html",
    "TextLab.html": "text-lab.html",
    "taxlab.html": "text-lab.html",
    "techslab.html": "text-lab.html",
    "techslab": "text-lab.html",
    "TagsLab": "text-lab.html",
    "TaxLab.jsx": "textlab.jsx",
    "TextLab.jsx": "textlab.jsx",
    "TaxLab": "TextLab",
    "taxlab": "TextLab",
    # 第一个入口文件叫 result.jsx（不是 ResultCard.jsx——那是组件）
    "redout.jsx": "result.jsx",
    "readout.jsx": "result.jsx",
    "pagehiding": "PageHeading",
    "PageHiding": "PageHeading",
    "improcard": "InputCard",
    "redocard": "ResultCard",
    "REDOCARD": "ResultCard",
    "resultcar": "ResultCard",
    "result.car": "ResultCard",
    "readallcar": "ResultCard",
    "readrcard": "ResultCard",
    "readercar": "ResultCard",
    "redout": "ResultCard",
    "readout": "ResultCard",
    "homepage.jsx": "HomePage.jsx",
    "ViteConfig": "vite.config.js",
    "vatconfig": "vite.config.js",
    "man.jsx": "main.jsx",
    "withoutroot": "result-root",
    "toTagDemos": "zero2tech-demos",
    "zerotag": "zero2tech",
    "demo的remix": "demo 的 README",
    # 命令行
    "npmcreatevit": "npm create vite",
    "npmcreatebit": "npm create vite",
    "npminstall": "npm install",
    "NPMInstall": "npm install",
    "NPMinstall": "npm install",
    "npmrun": "npm run",
    "nodemodules": "node_modules",
    "cmd+s": "Ctrl+S",
    "怎么加C复制": "怎么 Ctrl+C 复制",
    "怎么加B": "怎么 Ctrl+V",
    "NPMTree": "npm trends",
    # 术语/组件库
    "安特迪赞": "Ant Design",
    "yeslint": "ESLint",
    "reactbeats": "React Bits",
    "出类旁通": "触类旁通",
    "悲词疾笔": "非此即彼",
    "两参": "两掺",
    "附用": "复用",
    "服用": "复用",
    "加盟录": "家目录",
    "尤与锡": "尤雨溪",
    # 幻听/片尾（本地 ASR 噪声，整段删除）
    "Isurewanttopass": "",
    "优优独播剧场——YoYoTelevisionSeriesExclusive": "",
}
_MAP_ITEMS = sorted(MAP.items(), key=lambda kv: -len(kv[0]))

# 英文错词但需词边界保护（防止 Inter→Intel 误伤 Internet、beat 误伤 heartbeat 等）；
# ASCII 前后不接字母即算边界，中文紧邻时可正常命中。
REGEX = [
    (re.compile(r"(?<![A-Za-z])(?:vit|vita)(?![A-Za-z])", re.I), "Vite"),
    (re.compile(r"(?<![A-Za-z])beat(?![A-Za-z])", re.I), "Vite"),
    (re.compile(r"(?<![A-Za-z])inter(?![A-Za-z])", re.I), "Intel"),
    (re.compile(r"(?<![A-Za-z])scribe(?![A-Za-z])", re.I), "scripts"),
    (re.compile(r"(?<![A-Za-z])nodejs(?![A-Za-z.])", re.I), "Node.js"),
    # 原来在 MAP 里写的是 "Animate"->"anime"，但 str.replace 是子串替换，
    # 会把 AnimatedCardGrid 吃成 animedCardGrid。改成带词边界的正则。
    (re.compile(r"(?<![A-Za-z])animate(?![A-Za-z])", re.I), "anime"),
    # 4.3 讲：React 被识别成 read / real / rei / re-i 等独立 ASCII 词，Vue 被识别成 view。
    # 已核对全部讲次的字幕，这些独立词只出现在本讲，不会误伤其他视频的合法用法；
    # 词边界保护 README（后接 M）、already/thread（前接字母）等。
    (re.compile(r"(?<![A-Za-z])(?:re-?(?:add|i)|readd|read|rea)(?![A-Za-z])", re.I), "React"),
    (re.compile(r"(?<![A-Za-z])real(?![A-Za-z-])", re.I), "React"),
    (re.compile(r"(?<![A-Za-z])view(?![A-Za-z])", re.I), "Vue"),
    # 专有名词统一大小写（ASR 大小写不稳）。react 后接 "-" 时不匹配，
    # 以免把 npm 包名 react-dom / react-router 改坏。
    # 前面是 "-" 时不动：@vitejs/plugin-react、react-dom 这类包名要保留小写
    (re.compile(r"(?<![A-Za-z-])react(?![A-Za-z-])", re.I), "React"),
    (re.compile(r"(?<![A-Za-z])vue(?![A-Za-z])", re.I), "Vue"),
    # 后面跟 "." / "-" 时不动：vite.config.js、vite-preview 这类是文件名/包名
    (re.compile(r"(?<![A-Za-z])vite(?![A-Za-z.-])", re.I), "Vite"),
]

# 整段即为口癖 -> 删除该段
FILLER_ONLY = {"呃", "嗯", "啊", "哦", "啧", "哎", "哎呀", "哈哈", "哈哈哈", "嘿嘿",
               "对", "对吧", "对啊", "好啊", "好", "好的", "然后", "任何", "额",
               "anyway", "Anyway", "ANYWAY", "呃呃", "嗯嗯", "啊哈", "呜", "喂", "yes", "no"}

# 句尾口癖 -> 剥离
TAIL_FILLER = re.compile(r"(?:对吧|对不对|是吧|是不是|嘛|呀|哦|呃|嗯|哈哈|哈|啊|呢)+$")

# 单字口吃叠词折叠：我我我->我 等
STUTTER = re.compile(r"(我|你|他|它|就|是|有|去|来|啊|呃|嗯|对|但|那|这|也|都|还|又|再|很|太|会|要|想|说|看|做|搞|弄|写|读|问|答|学|教|玩|用|给|把|被|让|使|等)\1+")


def correct(text: str):
    for old, new in _MAP_ITEMS:
        if old in text:
            text = text.replace(old, new)
    for pat, rep in REGEX:
        text = pat.sub(rep, text)
    text = text.replace("呃", "").replace("嗯", "")
    text = STUTTER.sub(r"\1", text)
    text = TAIL_FILLER.sub("", text).strip()
    return text


def main():
    ap = argparse.ArgumentParser(description="生成 AI 修正版字幕对照稿")
    ap.add_argument("video_id", nargs="*")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    ids = args.video_id or [d for d in sorted(os.listdir(os.path.join(BASE, "output")))
                            if os.path.isfile(os.path.join(BASE, "output", d, "transcript.json"))]
    for vid in ids:
        src = os.path.join(BASE, "output", vid, "transcript.json")
        segs = json.load(open(src, encoding="utf-8"))["segments"]
        out_lines, dropped, fixed = [], 0, 0
        for seg in segs:
            raw = seg["text"].strip()
            if raw in FILLER_ONLY:
                dropped += 1
                continue
            txt = correct(raw)
            if txt != raw:
                fixed += 1
            if not txt:
                dropped += 1
                continue
            h, m, s = seg["start"].split(":")
            mm = int(h) * 60 + int(m)
            out_lines.append(f"[{mm:02d}:{s}] {txt}")
        dst = os.path.join(BASE, "output", vid, "transcript.corrected.txt")
        with open(dst, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(out_lines) + "\n")
        print(f"{vid}: {len(segs)} 段 -> {len(out_lines)} 行（修正 {fixed} 行，删除口癖段 {dropped}）")


if __name__ == "__main__":
    main()
