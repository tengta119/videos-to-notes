"""应用 AI 行级订正清单到 transcript.corrected.txt。

清单由 AI 通读订正稿后产出，落在 output/<video_id>/_ai_fixes.json：

    [{"line": 22, "old": "该行完整原文（含 [MM:SS] 前缀）", "new": "该行完整新文本"}]

`old` 必须与文件逐字完全一致（含时间戳前缀），否则该条会被判为"未匹配"。

⚠ 本步**只能跑一次**：没有脚本读 _ai_fixes.json，重跑 make_corrected.py 会覆盖订正结果，
   所以重跑订正稿之后必须重新执行本脚本。

用法:
  python src/apply_corrections.py <video_id> [...]   # 应用
  python src/apply_corrections.py <video_id> --dry-run   # 只报告不落盘

退出码: 0 = 全部应用成功；1 = 存在未匹配条目（清单与订正稿不同步，需人工核对）。
"""
import argparse
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def apply_one(vid: str, dry_run: bool) -> bool:
    """返回 True 表示全部匹配成功。"""
    out_dir = os.path.join(BASE, "output", vid)
    fixes_path = os.path.join(out_dir, "_ai_fixes.json")
    target = os.path.join(out_dir, "transcript.corrected.txt")

    if not os.path.exists(fixes_path):
        print(f"{vid}: 未找到 {fixes_path}，跳过")
        return True
    if not os.path.exists(target):
        print(f"{vid}: 未找到 {target}（先跑 make_corrected.py）")
        return False

    with open(target, encoding="utf-8") as f:
        text = f.read()
    with open(fixes_path, encoding="utf-8") as f:
        fixes = json.load(f)

    applied, missing, multi = 0, [], 0
    for item in fixes:
        old, new = item["old"], item["new"]
        n = text.count(old)
        if n == 0:
            missing.append(item["line"])
            continue
        if n > 1:
            multi += 1
            print(f"  ⚠ 第 {item['line']} 行的 old 在文中出现 {n} 次，只替换首处")
        text = text.replace(old, new, 1)
        applied += 1

    status = "dry-run，未落盘" if dry_run else "已写入"
    print(f"{vid}: 应用 {applied}/{len(fixes)} 条（{status}），重复出现 {multi} 条")
    if missing:
        print(f"  ❌ 未匹配 {len(missing)} 条（行号 {missing}）——"
              f"清单与订正稿不同步；若刚重跑过 make_corrected.py，请重新产出清单")

    if not dry_run and applied:
        with open(target, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    return not missing


def main():
    ap = argparse.ArgumentParser(description="应用 AI 行级订正清单到 transcript.corrected.txt")
    ap.add_argument("video_id", nargs="+")
    ap.add_argument("--dry-run", action="store_true", help="只报告不落盘")
    args = ap.parse_args()

    ok = all([apply_one(vid, args.dry_run) for vid in args.video_id])
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
