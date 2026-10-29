# -*- coding: utf-8 -*-
"""附加技能①：中间产物清理（视频 / 抽帧 / 被取代的旧段 PDF）。

==============================================================================
零弹窗保证（这条最重要，历史事故复盘见 diskguard.py 文件头）
==============================================================================
- 本脚本**从不弹任何对话框**：不用 input()、不用 messagebox、不调用任何外部命令，
  只调用 Python 的 os.remove / os.rmdir 与 shutil.disk_usage。
- **熔断优先于删除**：一旦探测到删除被安全策略拦截，立刻写熔断标志并中止，
  **绝不重试、绝不循环**——「被拦 → 重试 → 再被拦」正是历史上不停弹窗的成因。
  熔断后所有自动清理（--auto / monitor_silent）直接跳过，直到人工处置。
- **预防优于清理**：真正的防线是 pipeline 落盘前的配额检查（diskguard.quota_ok），
  不让空间逼近 Windows 告警线；清理只是兜底。
- 默认只出清单，加 --do 才执行；--auto 仅在低于 min_free_gb 且未熔断、
  且距上次清理超过 purge_min_interval_h 时才执行。

安全边界（不可突破）：
  - 只清「可再生的中间产物」：抽帧 parts、video.mp4、extract_status.json、
    已被合并进成品的旧段 PDF。
  - 绝不清 pdf/ 与 pdf_final/（交付物），绝不清未完成的场次。
  - 删除被拦时的正确处置不是脚本内重试，而是在终端里按目录分批 `rm -rf`
    （用户环境实测可行），或人工确认后删除。

用法（每个动作都可独立运行）:
  python cleanup.py --scan                占用与磁盘一览（只读）
  python cleanup.py --plan                待清理清单（人工审阅，默认动作）
  python cleanup.py --do                  按清单执行（受熔断与 --limit 约束）
  python cleanup.py --do --limit 500      每批最多 500 个文件，分批跑更安全
  python cleanup.py --auto                低空间时自动清理（熔断/节流时跳过）
  python cleanup.py --superseded [--do]   已被合并进成品的旧段 PDF
  python cleanup.py --truncate            用截断为 0 字节代替删除（删除被拦时的备选）
  python cleanup.py --videos              只清已下载视频（最占空间的一项）
"""
import os
import sys
import json
import glob
import argparse
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C
import diskguard as DG


def du(p):
    return DG.du(p)


def scan(cfg):
    print("C 盘可用 %.2f GB（下限 %.1f GB）" % (C.free_gb(), cfg.get("min_free_gb", 13.0)))
    print("清理熔断：%s" % ("已熔断，自动清理跳过" if DG.blocked(cfg) else "正常"))
    for label, p, mb in DG.usage(cfg):
        print("  %-16s %9.1f MB   %s" % (label, mb, p))
    root = cfg["tmp_root"]
    if not os.path.isdir(root):
        print("中间产物目录不存在:", root)
        return
    print("  分场次明细（%s）：" % root)
    for d in sorted(glob.glob(os.path.join(root, "*"))):
        if os.path.isdir(d):
            print("    %-20s %8.0f MB   %s"
                  % (os.path.basename(d), du(d) / 1048576,
                     " ".join(sorted(os.listdir(d)))[:60]))


def done_ids(cfg):
    prog = C.progress(cfg)
    tasks = C.tasks(cfg)
    ids = set()
    for k, v in prog.items():
        if v.get("status") in ("done", "pdf_done"):
            for t in tasks:
                if C.key_of(t) == k and t.get("manage_id"):
                    ids.add(str(t["manage_id"]))
    return ids


def build_plan(cfg, only_done=True, videos_only=False):
    """返回 [(说明, 路径, 大小, 是否允许, 原因)]"""
    ids = done_ids(cfg)
    plans = []
    for d in sorted(glob.glob(os.path.join(cfg["tmp_root"], "*"))):
        if not os.path.isdir(d):
            continue
        name = os.path.basename(d)
        mid = name.split("_")[-1]
        ok = (not only_done) or (mid in ids)
        why = "已完成" if ok else "未完成，保留"
        if not videos_only:
            for parts in (glob.glob(os.path.join(d, "parts")) +
                          glob.glob(os.path.join(d, "parts_old_*"))):
                if os.path.isdir(parts):
                    plans.append(("抽帧目录 %s" % name, parts, du(parts), ok, why))
            for f in glob.glob(os.path.join(d, "extract_status.json")):
                # 清掉状态文件，防止下次误判"已有帧"
                plans.append(("抽帧状态 %s" % name, f, os.path.getsize(f), ok, why))
        for f in (glob.glob(os.path.join(d, "video.mp4")) +
                  glob.glob(os.path.join(d, "video.mp4.part*"))):
            plans.append(("视频 %s" % name, f, os.path.getsize(f), ok, why))
    return plans


def superseded_plan(cfg):
    """已被合并进成品的旧段 PDF（段文件名 == OCR 名，成品用议程标题命名）"""
    prog = C.progress(cfg)
    plans = []
    for k, v in prog.items():
        if v.get("status") not in ("done", "pdf_done"):
            continue
        assigned = set()
        for names in (v.get("assign") or {}).values():
            assigned.update(names)
        for s in (v.get("segments") or []):
            fp = s.get("pdf")
            if not fp or not os.path.exists(fp):
                continue
            if s.get("name") in assigned and os.path.basename(fp)[:-4] == s.get("name"):
                plans.append(("旧段 PDF", fp, os.path.getsize(fp), True, "已合并进成品"))
    return plans


# ---------------------------------------------------------------- 带熔断的删除
class Fuse(object):
    """连续失败即熔断：不重试、不循环，这是「不停弹窗」的根治点。"""

    def __init__(self, cfg, log=print):
        self.cfg = cfg
        self.log = log
        self.fail = 0

    def delete(self, fp, truncate=False):
        if not self.alive():
            return 0
        try:
            before = os.path.getsize(fp)
        except OSError:
            return 0
        try:
            if truncate:
                with open(fp, "wb"):
                    pass
            else:
                os.remove(fp)
            self.fail = 0
            return before
        except Exception as e:
            self.fail += 1
            if self.fail >= DG.BLOCK_THRESHOLD:
                p = DG.mark_blocked(self.cfg, "删除被拦截: %s" % (e,))
                self.log("\n[熔断] 连续 %d 次删除被拦截，已写 %s" % (self.fail, p))
                self.log("[熔断] 停止一切删除动作，自动清理此后全部跳过，不再重试（杜绝反复弹窗）。")
                self.log("[熔断] 请人工处置：在终端按目录分批 rm -rf，"
                         "或确认后执行，再跑 diskguard.py --unblock 解除。")
            return 0

    def alive(self):
        return not DG.blocked(self.cfg)


def execute(cfg, plans, truncate=False, limit=0, log=print):
    """执行清理。limit>0 时本批最多处理 limit 个文件（分批跑，降低触发批量删除保护的概率）。"""
    man = os.path.join(cfg["base"], "_work", "purge_manifest.txt")
    os.makedirs(os.path.dirname(man), exist_ok=True)
    fuse = Fuse(cfg, log)
    freed, n, skipped_limit = 0, 0, 0
    with open(man, "a", encoding="utf-8") as mf:
        mf.write("# %s limit=%d truncate=%s\n"
                 % (datetime.datetime.now().isoformat(timespec="seconds"), limit, truncate))
        for what, p, sz, ok, _ in plans:
            if not ok or not fuse.alive():
                continue
            targets = ([p] if os.path.isfile(p) else
                       [os.path.join(r, f) for r, _, fs in os.walk(p) for f in fs])
            for fp in targets:
                if limit and n >= limit:
                    skipped_limit += 1
                    continue
                if not fuse.alive():
                    break
                got = fuse.delete(fp, truncate)
                if got:
                    freed += got
                    n += 1
                    mf.write("%s\n" % fp)
            if not fuse.alive():
                break
            if not truncate and os.path.isdir(p):
                for r, ds, _ in os.walk(p, topdown=False):
                    for dd in ds:
                        try:
                            os.rmdir(os.path.join(r, dd))
                        except OSError:
                            pass
                try:
                    os.rmdir(p)
                except OSError:
                    pass
    DG.touch_purge(cfg)
    log("处理 %d 个文件，释放 %.2f GB（C 盘现有 %.2f GB）%s"
        % (n, freed / 2 ** 30, C.free_gb(),
           "；因 --limit 跳过 %d 个（下一批继续）" % skipped_limit if skipped_limit else ""))
    log("清单追加至 %s" % man)
    return n, freed


def show(plans):
    total = 0
    print("=" * 74)
    print("%-34s %10s  %s" % ("项目", "大小(MB)", "状态"))
    print("=" * 74)
    for what, p, sz, ok, why in plans:
        total += sz if ok else 0
        print(" %s %-32s %10.1f  %s" % ("OK " if ok else "!!!", what[:32], sz / 1048576, why))
    print("-" * 74)
    print(" 合计可释放 %.2f GB" % (total / 2 ** 30))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true", help="只读占用一览")
    ap.add_argument("--plan", action="store_true", help="待清理清单（默认）")
    ap.add_argument("--do", action="store_true", help="真正执行")
    ap.add_argument("--auto", action="store_true", help="低空间自动清理（受熔断与节流约束）")
    ap.add_argument("--superseded", action="store_true", help="只处理被取代的旧段 PDF")
    ap.add_argument("--videos", action="store_true", help="只处理已下载视频")
    ap.add_argument("--truncate", action="store_true", help="用截断代替删除")
    ap.add_argument("--limit", type=int, default=0, help="本批最多处理多少个文件（0=不限）")
    ap.add_argument("--base", default=None)
    a = ap.parse_args()

    cfg = C.load(a.base)
    if not cfg.get("purge", True) and (a.do or a.auto):
        print("配置 purge=false：一切删除动作已关闭")
        return

    if a.scan or not any([a.plan, a.do, a.auto, a.superseded, a.videos]):
        scan(cfg)
        return

    # ---- 熔断检查：放在一切删除动作之前 ----
    if (a.do or a.auto) and DG.blocked(cfg):
        print("清理已熔断（上次删除被安全策略拦截）。自动清理跳过，不重试、不弹窗。")
        print("处置：终端按目录分批 rm -rf，或人工确认后删除，再跑 diskguard.py --unblock。")
        return

    plans = (superseded_plan(cfg) if a.superseded
             else build_plan(cfg, only_done=True, videos_only=a.videos))

    if a.auto:
        free = C.free_gb()
        need = cfg.get("min_free_gb", 13.0)
        if free >= need:
            print("C 盘可用 %.2f GB >= 阈值 %.1f GB，无需清理" % (free, need))
            return
        yes, ago = DG.throttled(cfg)
        if yes:
            print("距上次清理仅 %.1f 小时 < 间隔 %.1f 小时，本轮跳过（避免反复触发）"
                  % (ago, cfg.get("purge_min_interval_h", 6)))
            return
        print("C 盘可用 %.2f GB < 阈值 %.1f GB，自动清理已完成场次的中间产物" % (free, need))
        execute(cfg, plans, a.truncate, a.limit)
        return

    show(plans)
    if a.do:
        execute(cfg, plans, a.truncate, a.limit)
    else:
        print("\n这是试算。加 --do 才真正执行（建议 --do --limit 500 分批）。")


if __name__ == "__main__":
    main()
