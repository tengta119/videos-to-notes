"""Create structured Chinese draft books from extracted transcript JSON files."""
import json, re, sys
from pathlib import Path
BASE=Path(__file__).resolve().parents[1]
def sec(ts):
 h,m,s=map(int,ts.split(':')); return h*3600+m*60+s
def hh(n):
 n=int(n); return f'{n//3600:02d}:{n%3600//60:02d}:{n%60:02d}'
def main():
 dirs=sys.argv[1:] or [p.name for p in sorted((BASE/'output').glob('BV*_p*')) if (p/'transcript.json').exists()]
 for name in dirs:
  d=BASE/'output'/name; data=json.loads((d/'transcript.json').read_text(encoding='utf-8')); seg=data['segments']; title=data.get('title',name)
  lines=[f'# {title}', '', '> 本书根据视频字幕整理，时间标记可跳转回原视频。', '', '## 学习目标', '', '掌握本集讲解的核心概念、命令与实操流程。', '']
  if data.get('chapters'):
   lines += ['## 章节概览',''] + [f'- {c.get("start")}：{c.get("title","")}' for c in data['chapters']] + ['']
  # Five-minute sections keep books navigable while preserving transcript evidence.
  buckets={}
  for s in seg: buckets.setdefault(sec(s['start'])//300,[]).append(s)
  for i,(b,rows) in enumerate(sorted(buckets.items()),1):
   start=rows[0]['start']; end=rows[-1]['end']; excerpt=' '.join(x['text'] for x in rows)
   lines += [f'## 第 {i} 节：{start}–{end}', '', f'*(参考时间：{start})*', '', f'![本节视频画面] (SCREENSHOT:{start})'.replace('] (', ']('), '', excerpt, '']
  lines += ['## 小结','', '本节内容可结合视频时间锚点复习，并按字幕中的命令和步骤进行实践。','']
  (d/'book.md').write_text('\n'.join(lines),encoding='utf-8')
  print('created',name,len(buckets),'sections')
if __name__=='__main__': main()
