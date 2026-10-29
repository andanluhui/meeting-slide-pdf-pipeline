# -*- coding: utf-8 -*-
"""附加技能②：定期自检（只读，绝不修改表格与文件）。

检查项：
  1. 进度分布：每场状态、失败原因、未完成清单
  2. 归属异常：0 段行 / 疑似「吞段」行（某行页数远超同场中位数，且同场存在 0 段行）
  3. 未匹配段（pending segments）
  4. 成品对账：pdf_final 成品 与 表格已填链接 的行差集
  5. 页数健检：成品页数过少（<3）的行
  6. 磁盘：C 盘可用空间 vs 阈值
  7. 与上一版快照对比页数变化（--snapshot 时写入快照）

用法:
  python selftest.py                       输出自检报告（全项）
  python selftest.py --json                机器可读输出
  python selftest.py --snapshot            顺带把当前页数存为快照，供下次对比
  python selftest.py --section disk        只看磁盘与清理熔断
  python selftest.py --section assign      只看归属异常（0 段行 / 吞段 / 页数过少 / 未匹配段）
  python selftest.py --section progress    只看进度与失败场次
  python selftest.py --section reconcile   只做成品 vs 表格链接对账
"""
import os
import re
import sys
import json
import glob
import argparse
import datetime
import statistics

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C
from colmap import Sheets


def build(cfg):
    tasks = C.tasks(cfg)
    prog = C.progress(cfg)
    rep = []
    by_task = {}
    for t in tasks:
        by_task[C.key_of(t)] = t
    rows = []   # (sheet, session, row, pages, nseg)
    QA_RE = re.compile(r"discussion|q\s*&\s*a|q\s*and\s*a|panel|问答|讨论|"
                       r"introduction by chairs|^introduction$", re.I)
    issues = {"zero_rows": [], "swallow": [], "pending": [], "thin": [],
              "failed": [], "no_video": [], "expected_empty": []}

    for k, st in prog.items():
        t = by_task.get(k, {})
        sheet = st.get("sheet") or t.get("sheet") or "?"
        sess = t.get("session", k)
        if st.get("status") == "failed":
            issues["failed"].append((sheet, sess, st.get("why", "")))
            continue
        if st.get("status") == "no_video":
            issues["no_video"].append((sheet, sess))
            continue
        segs = [s for s in st.get("segments", []) if s.get("pdf")]
        assign = st.get("assign") or {}
        used = set()
        for names in assign.values():
            used.update(names)
        if t.get("kind") == "talk-rows":
            # session-row 整场合一份 PDF，assign 本就为空，不算未匹配
            for s in segs:
                if s["name"] not in used:
                    issues["pending"].append((sheet, sess, s["name"][:50], s.get("pages", 0)))
        if t.get("kind") == "talk-rows":
            talks = {int(tk["row"]): tk for tk in (t.get("talks") or [])}
            pages = {}
            for r, names in assign.items():
                n = sum(s.get("pages", 0) for s in segs if s["name"] in names)
                pages[int(r)] = n
            for r, tk in talks.items():
                rows.append((sheet, sess, r, pages.get(r, 0), len(assign.get(str(r), []))))
                if pages.get(r, 0) == 0:
                    it = (sheet, sess, r, tk.get("title_en", "")[:50], tk.get("speaker", ""))
                    # 讨论/问答/主席开场行本来就没有幻灯片，属预期空值
                    if QA_RE.search(str(tk.get("title_en", "")) + str(tk.get("title_cn", ""))):
                        issues["expected_empty"].append(it)
                    else:
                        issues["zero_rows"].append(it)
                elif pages.get(r, 0) < 3:
                    issues["thin"].append((sheet, sess, r, pages[r]))
            vals = [v for v in pages.values() if v > 0]
            if len(vals) >= 3:
                med = statistics.median(vals)
                if med > 0:
                    # 同场存在 0 段行，且本行页数远超中位数 -> 疑似吞并
                    zero_here = [z[2] for z in issues["zero_rows"] if z[1] == sess]
                    if zero_here:
                        for r, n in sorted(pages.items(), key=lambda x: -x[1]):
                            if n >= max(med * 4, med + 30):
                                issues["swallow"].append((sheet, sess, r, n, round(med, 1)))
    return rows, issues, prog


def reconcile(cfg, sheets_api, rows):
    """成品 vs 表格链接 对账"""
    miss, extra = [], []
    rep_f = cfg["final_report_abs"]
    if not os.path.exists(rep_f):
        return None, None
    rep = json.load(open(rep_f, encoding="utf-8"))
    for sheet, row, fname, nseg, raw, npages in rep:
        sid = cfg["sheets"].get(sheet)
        if not sid:
            continue
        col = sheets_api.pdf_col(sid)
        if not col:
            miss.append((sheet, row, "表内无 PDF 列"))
            continue
        v = sheets_api.cell_value(sid, col, row)
        if fname not in v:
            miss.append((sheet, row, fname))
    # 表里有链接但无对应成品
    have = {(s, r) for s, r, *_ in rep}
    for sheet, sid in cfg["sheets"].items():
        col = sheets_api.pdf_col(sid)
        if not col:
            continue
        for r, txt in sheets_api.col_texts(sid, col).items():
            if "feishu.cn/file/" in txt and (sheet, r) not in have:
                extra.append((sheet, r))
    return miss, extra


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--snapshot", action="store_true")
    ap.add_argument("--no-sheet", action="store_true", help="跳过飞书对账（离线自检）")
    ap.add_argument("--section", default=None,
                    help="只跑指定检查项，逗号分隔：disk,progress,assign,reconcile,snapshot")
    ap.add_argument("--base", default=None)
    a = ap.parse_args()

    SECTIONS = ("disk", "progress", "assign", "reconcile", "snapshot")
    want = set(SECTIONS)
    if a.section:
        want = {s.strip() for s in a.section.split(",") if s.strip()}
        bad = [s for s in want if s not in SECTIONS]
        if bad:
            print("未知检查项:", bad, "可选:", SECTIONS)
            sys.exit(1)

    cfg = C.load(a.base)
    rows, issues, prog = build(cfg)
    free = C.free_gb()
    need = cfg.get("min_free_gb", 13.0)

    out = []
    out.append("# 自检报告  %s%s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                                     "（仅 %s）" % ",".join(sorted(want)) if a.section else ""))
    out.append("")
    if "disk" in want:
        import diskguard as DG
        out.append("- C 盘可用 **%.2f GB**（阈值 %.1f GB）%s"
                   % (free, need, "⚠ 低于阈值" if 0 < free < need else "OK"))
        out.append("- 清理熔断：%s" % ("已熔断，自动清理跳过（diskguard.py --unblock 解除）"
                                       if DG.blocked(cfg) else "正常"))
    if "progress" in want:
        out.append("- 进度：%d 场，已完成 %d，失败 %d，无视频 %d"
                   % (len(prog),
                      sum(1 for v in prog.values() if v.get("status") in ("done", "pdf_done")),
                      len(issues["failed"]), len(issues["no_video"])))
        out.append("- 演讲行 %d 行，成品总页数 %d" % (len(rows), sum(r[3] for r in rows)))

    def sec(title, items, fmt):
        out.append("")
        out.append("## %s（%d）" % (title, len(items)))
        if not items:
            out.append("- 无")
            return
        for it in items[:60]:
            out.append("- " + fmt(it))
        if len(items) > 60:
            out.append("- …其余 %d 项省略" % (len(items) - 60))

    if "progress" in want:
        sec("失败场次", issues["failed"], lambda x: "%s %s：%s" % x)
        sec("无视频场次", issues["no_video"], lambda x: "%s %s" % x)
    if "assign" in want:
        sec("0 段行（需关注：议程行没拿到任何幻灯片）", issues["zero_rows"],
            lambda x: "%s r%d  %s（%s）" % (x[0], x[2], x[3], x[4]))
        sec("0 段行（预期空：讨论/问答/主席开场）", issues["expected_empty"],
            lambda x: "%s r%d  %s" % (x[0], x[2], x[3]))
        sec("疑似吞段行（页数远超同场中位数）", issues["swallow"],
            lambda x: "%s %s r%d：%d 页，同场中位 %.1f 页" % (x[0], x[1][:40], x[2], x[3], x[4]))
        sec("页数过少（<3）", issues["thin"], lambda x: "%s r%d -> %d 页" % (x[0], x[2], x[3]))
        sec("未匹配段", issues["pending"],
            lambda x: "%s %s  %s（%d 页）" % (x[0], x[1][:40], x[2], x[3]))

    if "reconcile" in want and not a.no_sheet:
        try:
            sh = Sheets(cfg)
            miss, extra = reconcile(cfg, sh, rows)
            if miss is not None:
                sec("成品有但表格缺链接", miss, lambda x: "%s r%d  %s" % (x[0], x[1], x[2]))
                sec("表格有链接但无成品（可能是上一版遗留）", extra, lambda x: "%s r%d" % x)
        except Exception as e:
            out.append("")
            out.append("## 对账")
            out.append("- 跳过（读表失败：%r）" % (e,))

    # 页数快照对比
    snap_f = os.path.join(cfg["base"], "_work", "pages_snapshot.json")
    cur = {"%s|%d" % (r[0], r[2]): r[3] for r in rows}
    if "snapshot" in want and os.path.exists(snap_f):
        try:
            old = json.load(open(snap_f, encoding="utf-8"))
            inc = [(k, old[k], cur[k]) for k in cur if k in old and cur[k] > old[k]]
            dec = [(k, old[k], cur[k]) for k in cur if k in old and cur[k] < old[k]]
            out.append("")
            out.append("## 与上次快照对比")
            out.append("- 增加 %d 行，减少 %d 行，持平 %d 行"
                       % (len(inc), len(dec),
                          len([k for k in cur if k in old and cur[k] == old[k]])))
            for k, o, n in sorted(dec, key=lambda x: x[1] - x[2])[:15]:
                out.append("  - %s：%d -> %d 页" % (k, o, n))
        except Exception:
            pass
    if a.snapshot:
        os.makedirs(os.path.dirname(snap_f), exist_ok=True)
        json.dump(cur, open(snap_f, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        out.append("")
        out.append("（已写入页数快照 %s）" % snap_f)

    text = "\n".join(out)
    if a.json:
        print(json.dumps({"free_gb": round(free, 2), "rows": len(rows),
                          "issues": {k: len(v) for k, v in issues.items()}},
                         ensure_ascii=False, indent=1))
    else:
        print(text)
    rp = os.path.join(cfg["base"], "_work", "selftest_report.md")
    os.makedirs(os.path.dirname(rp), exist_ok=True)
    open(rp, "w", encoding="utf-8").write(text)
    print("\n报告已写入", rp)


if __name__ == "__main__":
    main()
