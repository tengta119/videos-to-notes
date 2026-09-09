from pathlib import Path
import json, html
base=Path(__file__).resolve().parents[1]; out=base/'output'
items=[]
for d in sorted(out.glob('BV*_p*')):
 p=d/'transcript.json'
 if p.exists() and (d/'book.html').exists():
  try: title=json.loads(p.read_text(encoding='utf-8')).get('title',d.name)
  except Exception: title=d.name
  items.append(f'<li><a href="{d.name}/book.html">{html.escape(title)}</a></li>')
doc='<!doctype html><meta charset="utf-8"><title>VideoBook 书架</title><body style="font:16px system-ui;max-width:900px;margin:40px auto"><h1>Docker 容器课程电子书</h1><ol>'+''.join(items)+'</ol></body>'
(out/'index.html').write_text(doc,encoding='utf-8'); print('created',out/'index.html')
