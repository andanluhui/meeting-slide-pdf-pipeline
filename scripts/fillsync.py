# -*- coding: utf-8 -*-
"""上传成品 PDF 到云空间，并把链接回填到表格对应行（幂等）。

三条铁律（血泪教训）：
  1. 目标列一律按表头名动态定位（colmap），绝不硬编码列字母。
  2. 必须幂等：写入前用 cell_value 精确读单元格；已有同名链接就跳过。
     同一份 PDF 绝不重复上传 —— 按文件签名（大小+mtime）复用 fillsync_log 里的 URL。
     重跑脚本必须能安全重入。
  3. 覆盖前先出清单：目标单元格若有用户手工内容/合并单元格，只报告不动手。

可独立运行，且支持细到「单子表 / 单行」：
  python fillsync.py                       按 final_report.json 上传并回填
  python fillsync.py --dry                 只列计划，不上传不写入
  python fillsync.py --sheet 1003          只处理该子表
  python fillsync.py --rows 121,122        只处理这些行（配合 --sheet）
  python fillsync.py --fill-only           只回填不上传：复用 fillsync_log 里已有的 URL
                                           （上传失败但链接已生成时用它补写单元格）
"""
import os
import sys
import json
import subprocess
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C
from colmap import Sheets, link_cells


def sig_of(path):
    """本地文件签名：大小 + 修改时间（内容变了签名就变，必须重新上传）"""
    try:
        st = os.stat(path)
        return "%d-%d" % (st.st_size, int(st.st_mtime))
    except OSError:
        return ""


def upload(cfg, path):
    r = subprocess.run([cfg["lark_cli"], "drive", "+upload", "--file", path,
                        "--name", os.path.basename(path),
                        "--folder-token", cfg["folder_token"], "--format", "json"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="ignore", timeout=900)
    try:
        d = json.loads(r.stdout)
    except Exception:
        return None
    return d["data"]["file_token"] if d.get("ok") else None


def load_prev(cfg):
    p = cfg["fillsync_log_abs"]
    if not os.path.exists(p):
        return {}
    try:
        return {(e["sheet"], e["row"]): e
                for e in json.load(open(p, encoding="utf-8"))}
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--sheet", default=None, help="只处理该子表")
    ap.add_argument("--rows", default=None, help="只处理这些行，逗号分隔（配合 --sheet）")
    ap.add_argument("--fill-only", action="store_true",
                    help="只回填，不上传；复用 fillsync_log 中已有的 URL")
    ap.add_argument("--base", default=None)
    a = ap.parse_args()

    cfg = C.load(a.base)
    sh = Sheets(cfg)
    rep = json.load(open(cfg["final_report_abs"], encoding="utf-8"))
    prev = load_prev(cfg)

    only_rows = None
    if a.rows:
        only_rows = {int(x) for x in a.rows.split(",") if x.strip()}
    if a.sheet:
        rep = [e for e in rep if e[0] == a.sheet]
    if only_rows is not None:
        rep = [e for e in rep if e[1] in only_rows]
    print("本次处理 %d 行%s" % (len(rep), "（--fill-only，不上传）" if a.fill_only else ""))

    target_col, coltext = {}, {}
    for sheet, sid in cfg["sheets"].items():
        c = sh.pdf_col(sid) or sh.ensure_pdf_col(sid)
        target_col[sheet] = c
        coltext[sheet] = sh.col_texts(sid, c)
        print("目标列: %-10s -> %s" % (sheet, c))

    log, skipped, blocked = [], 0, []
    for sheet, row, fname, nseg, raw, npages in rep:
        sid = cfg["sheets"][sheet]
        col = target_col[sheet]
        path = os.path.join(cfg["final_dir_abs"], sheet, fname)
        if not os.path.exists(path):
            print("跳过（文件缺失）", path)
            continue
        cur = sh.cell_value(sid, col, row)
        # 单元格已有用户手工内容且不是我们的链接 -> 只报告，等人工决定
        if cur and fname not in cur and "feishu.cn/file/" not in cur:
            blocked.append((sheet, col, row, cur[:60]))
            print("[!] 单元格已有非链接内容，跳过待人工确认: %s%d -> %s" % (col, row, cur[:60]))
            continue
        e = prev.get((sheet, row), {})
        if fname in cur and e.get("sig") == sig_of(path) and e.get("url"):
            skipped += 1
            continue
        if a.dry:
            print("[dry] %s%d  %s  (%d 页)  计划写入" % (col, row, fname, npages))
            continue
        if e.get("url") and (a.fill_only or e.get("sig") == sig_of(path)):
            url = e["url"]                      # 内容未变 -> 复用已有云文档链接
        elif a.fill_only:
            print("跳过（--fill-only 且日志中没有该文件的 URL）", fname)
            continue
        else:
            tok = upload(cfg, path)
            if not tok:
                print("上传失败", fname)
                continue
            url = cfg["file_url_prefix"] + tok
        ok = sh.set_cell(sid, col, row, link_cells([(fname, url)]))
        good = bool(ok.get("ok"))
        print("[%s] %s%-4d %2d 段 %3d 页 -> %s" % (sheet, col, row, nseg, npages, good))
        sys.stdout.flush()
        log.append({"sheet": sheet, "row": row, "file": fname, "pages": npages,
                    "url": url, "sig": sig_of(path), "ok": good})

    if blocked:
        print("\n待人工确认 %d 行（未写入）：" % len(blocked))
        for s, c, r, t in blocked:
            print("   %s %s%d  %s" % (s, c, r, t))
    if a.dry:
        print("\n(dry run，未上传未写入)")
        return

    hist = prev
    for e in log:
        hist[(e["sheet"], e["row"])] = e
    json.dump(list(hist.values()), open(cfg["fillsync_log_abs"], "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("完成：写入 %d 行，幂等跳过 %d 行，日志累计 %d 行" % (len(log), skipped, len(hist)))


if __name__ == "__main__":
    main()
