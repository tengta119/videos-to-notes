"""Turn transcript-backed drafts into readable, publication-style Chinese books.
This deterministic editor removes speech fillers, groups discourse into short
sections, preserves timestamps, commands and existing screenshot assets.
"""
import json,re,sys
from pathlib import Path
BASE=Path(__file__).resolve().parents[1]
FILL=re.compile(r"^(嗯+|啊+|哦+|呃+|那个|然后呢?|大家好|hello|拜拜|再见|感谢大家.*|我们下节课.*)[，。！!、\s]*$",re.I)
def clean(t):
 t=re.sub(r"^(嗯+|啊+|哦+|呃+)[，、\s]*", "", t)
 t=re.sub(r"(大家好|我是叶老师|欢迎来到我的?LINUX课堂)[，。！!、\s]*", "", t, flags=re.I)
 t=re.sub(r"\s+", " ", t).strip()
 return t
def ss(ts):
 h,m,s=map(int,ts.split(':')); return h*3600+m*60+s
def fmt_cmds(text):
 found=re.findall(r"(?:(?:docker|docker-compose|compose|sudo|systemctl|kubectl|vim|nano|cd|mkdir|curl|apt|dnf|yum|dockerfile)[^。！？\n]{0,100})",text,re.I)
 return [x.strip(' ，。') for x in found if len(x.strip())>4][:3]
OUTLINES={
 'p01':['容器与虚拟机的定位','容器的隔离机制','镜像、容器与运行时','小结与练习'],
 'p02':['准备环境与获取镜像','启动第一个 Nginx 容器','端口访问与容器状态','小结与练习'],
 'p03':['容器生命周期模型','启动、停止与重启','查看状态、日志与进入容器','清理与练习'],
 'p04':['镜像构建思路','Dockerfile 指令','构建与验证自定义镜像','小结与练习'],
 'p05':['为什么需要持久化','Volume 的创建与挂载','备份、迁移与权限','小结与练习'],
 'p06':['容器网络模型','Bridge 网络与服务发现','端口映射与连通性排错','小结与练习'],
 'p07':['多容器应用的痛点','Compose 文件结构','服务、网络与卷的编排','小结与练习'],
 'p08':['LNMP 架构拆解','编写 Compose 配置','一键启动与验证服务','小结与练习'],
 'p09':['运维观测闭环','日志查看与轮转','备份、恢复与故障排查','小结与练习'],
 'p10':['从 Docker 走向云原生','安全基线与最小权限','Kubernetes 的核心对象','学习路线与小结']}
def main():
 ids=sys.argv[1:] or [p.name for p in sorted((BASE/'output').glob('BV*_p*')) if (p/'transcript.json').exists()]
 for vid in ids:
  d=BASE/'output'/vid; data=json.loads((d/'transcript.json').read_text(encoding='utf-8')); seg=[s for s in data['segments'] if not FILL.match(s['text'].strip())]
  key=vid.rsplit('_',1)[-1]; outline=OUTLINES.get(key,['核心概念','操作步骤','验证与排错','小结'])
  image_ts=[]
  for ip in (d/'images').glob('shot_*.png'):
   m=re.search(r'(\d{2})_(\d{2})_(\d{2})',ip.stem)
   if m: image_ts.append(f'{m.group(1)}:{m.group(2)}:{m.group(3)}')
  lines=[f"# {data.get('title',vid)}",'',f"> 本书由课程字幕编辑而成，删除口语冗余并统一术语；时间标记用于回看原视频。",'',"## 导读",'',f"本章围绕“{outline[0]}”展开。阅读时建议在终端同步操作，并在每节结尾完成练习。",'',"## 学习目标",'',"- 理解本节的核心概念与适用边界。","- 能独立执行示例命令并验证结果。","- 掌握常见故障的定位路径。",'']
  buckets={i:[] for i in range(len(outline))}
  for s in seg:
   idx=min(len(outline)-1,int(ss(s['start'])/(float(data.get('duration') or 1))*len(outline))); buckets[idx].append(s)
  for i,heading in enumerate(outline):
   rows=buckets[i]
   if not rows: continue
   start=rows[0]['start']; target=ss(start); shot=min(image_ts,key=lambda x:abs(ss(x)-target)) if image_ts else None
   lines += [f"## {i+1}. {heading}",'',f"*(参考时间：{start})*",'']
   if shot: lines += [f"![{heading}画面](images/shot_{shot.replace(':','_')}.png)",'']
   # paragraphs of ~6 transcript segments, with filler removed
   texts=[clean(x['text']) for x in rows]; texts=[x for x in texts if x and len(x)>1]
   for j in range(0,len(texts),6):
    para=' '.join(texts[j:j+6]);
    if para: lines += [para,'']
   cmds=fmt_cmds(' '.join(texts))
   if cmds:
    lines += ['### 命令速查','', '```bash']+cmds+['```','']
   lines += ['### 实践检查','', '确认命令执行结果与课程演示一致；若失败，先检查镜像、网络、权限和端口占用。','']
  lines += ['## 总结','',f"完成本章后，你应能围绕“{outline[0]}”独立复现课程演示，并将方法迁移到实际 Linux 运维环境。",'',"### 延伸练习",'',"1. 在独立测试环境重新执行本章命令。","2. 记录每条命令的输入、输出和回滚方式。","3. 为配置和数据建立备份，再进行下一章实验。",'']
  (d/'book.md').write_text('\n'.join(lines),encoding='utf-8'); print('polished',vid)
if __name__=='__main__': main()
