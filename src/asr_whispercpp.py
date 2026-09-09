"""AMD/跨厂商 GPU ASR backend using whisper.cpp (Vulkan).

Requires a locally built whisper-cli and a GGML model.  The output schema is
compatible with asr_transcript.py so the rest of VideoBook needs no changes.
"""
import argparse, json, os, re, subprocess, sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
TS = re.compile(r"\[(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})\]\s*(.*)")

def sec(s):
    h,m,x=s.split(":"); return int(h)*3600+int(m)*60+float(x)
def hh(v):
    v=max(0,float(v)); i=int(round(v)); return f"{i//3600:02d}:{i%3600//60:02d}:{i%60:02d}"

def main():
    ap=argparse.ArgumentParser(description="whisper.cpp Vulkan ASR for AMD GPUs")
    ap.add_argument("video_id"); ap.add_argument("--cli", default=os.environ.get("WHISPER_CPP_CLI","whisper-cli"))
    ap.add_argument("--model", required=True, help="GGML/GGUF Whisper model path")
    ap.add_argument("--threads", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--language", default="zh"); ap.add_argument("--prompt-file")
    args=ap.parse_args(); out=BASE/"output"/args.video_id; out.mkdir(parents=True,exist_ok=True)
    audio=next((out/f"audio{e}" for e in (".m4a",".webm",".opus",".mp3",".wav") if (out/f"audio{e}").exists()),None)
    if not audio: raise SystemExit(f"audio not found in {out}; run dump_transcript.py or asr_transcript.py first")
    prefix=out/"_whispercpp"; txt=prefix.with_suffix(".txt")
    cmd=[args.cli,"-m",str(Path(args.model).resolve()),"-f",str(audio),"-l",args.language,"-t",str(args.threads),"-otxt","-of",str(prefix),"--print-progress"]
    if args.prompt_file: cmd += ["--prompt",Path(args.prompt_file).read_text(encoding="utf-8")]
    print(">> running whisper.cpp (Vulkan backend is selected by the binary)")
    subprocess.run(cmd,check=True,cwd=BASE)
    rows=[]
    for line in txt.read_text(encoding="utf-8",errors="replace").splitlines():
        m=TS.search(line.strip())
        if m: rows.append({"start":hh(sec(m.group(1))),"end":hh(sec(m.group(2))),"text":m.group(3).strip()})
    if not rows: raise SystemExit(f"No timestamped segments parsed from {txt}; run whisper-cli -h and enable timestamp text output")
    info={}
    p=out/"_info.json"
    if p.exists():
        try: info=json.loads(p.read_text(encoding="utf-8"))
        except Exception: pass
    result={"video_url":info.get("webpage_url", ""),"title":info.get("title",args.video_id),"video_id":args.video_id,"duration":float(info.get("duration") or 0),"chapters":[],"segments":rows,"asr":{"backend":"whisper.cpp","device":"vulkan"}}
    (out/"transcript.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    (out/"transcript.txt").write_text("\n".join(f"[{r['start'][3:]}] {r['text']}" for r in rows)+"\n",encoding="utf-8")
    print(f"✅ transcript.json written: {out} ({len(rows)} segments)")
if __name__ == "__main__": main()
