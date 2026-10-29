# -*- coding: utf-8 -*-
"""磁盘守卫（零弹窗保证）——可独立运行，也被 pipeline / cleanup / monitor 调用。

==============================================================================
为什么需要这个文件：历史上「C 盘检测清理」反复弹窗，三个真实来源
==============================================================================
A. Windows 低磁盘告警弹窗（系统级，不是脚本弹的）
   卷 ≥10 GB 时告警线 = min(10% × 容量, 10 GB)；150 GB 盘约 10 GB。
   空闲跌破后系统每隔一段时间弹「空间不足」，越清理越弹（清理本身也读写磁盘）。
   → 对策：**预防式配额**。落盘前先算预算，预计跌破 min_free_gb 就拒绝并报告，
     根本不让空间走到告警线附近。

B. 批量删除保护弹窗（安全中心 / 沙箱）
   连续删除大量文件会弹「确认删除」；Python 的 shutil.rmtree、循环 os.remove
   同样会被拦，被拦后如果继续重试，就会一轮一轮地弹。
   → 对策：**熔断**。一旦检测到删除被拦截，立刻写 blocked.flag 并中止，
     后续所有自动清理直接跳过（见 cleanup.py），直到人工处置。

C. 提权 / 沙箱授权弹窗
   schtasks / reg / sc / wmic / net / wsl / wslconfig 等命令一执行就走提权流程。
   → 对策：本 skill 全部脚本都不调用这些命令；磁盘信息只用 Python 的
     shutil.disk_usage；计划任务注册交给 PowerShell 的 Register-ScheduledTask
     cmdlet（示例在 references/workflow.md，不自动执行）。

本文件只做三件事，全部无窗口、无对话框、无网络：
  1. status()            只读报告磁盘与中间产物占用
  2. quota_ok(need_gb)   预防式配额判定（给下载/落盘前调用）
  3. blocked()/mark_blocked()/unblock()   删除拦截熔断标志
"""

import os
import sys
import json
import time
import shutil
import argparse
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C

# 连续失败多少次判定为「删除被拦截」并熔断
BLOCK_THRESHOLD = 3
FLAG_NAME = "_work/purge_blocked.flag"


# ---------------------------------------------------------------- 只读状态
def disks(cfg=None, paths=None):
    """返回 [(path, total_gb, used_gb, free_gb)]，只读，绝不修改任何东西。"""
    cfg = cfg or C.load()
    ps = paths or [cfg.get("sys_disk", "C:\\")]
    tr = cfg.get("tmp_root")
    if tr:
        drv = os.path.splitdrive(os.path.abspath(tr))[0] + "\\"
        if drv and drv not in ps:
            ps = ps + [drv]
    out = []
    for p in ps:
        try:
            u = shutil.disk_usage(p)
            out.append((p, u.total / 2 ** 30, u.used / 2 ** 30, u.free / 2 ** 30))
        except Exception:
            out.append((p, -1, -1, -1))
    return out


def du(path):
    t = 0
    if not os.path.exists(path):
        return 0
    if os.path.isfile(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0
    for r, _, fs in os.walk(path):
        for f in fs:
            try:
                t += os.path.getsize(os.path.join(r, f))
            except OSError:
                pass
    return t


def usage(cfg=None):
    """中间产物 / 交付物占用（MB），只读。"""
    cfg = cfg or C.load()
    rows = []
    for label, p in (("中间产物 tmp_root", cfg.get("tmp_root")),
                     ("段 PDF pdf/", cfg.get("pdf_dir_abs")),
                     ("成品 pdf_final/", cfg.get("final_dir_abs"))):
        if p:
            rows.append((label, p, du(p) / 1048576))
    return rows


def status(cfg=None):
    cfg = cfg or C.load()
    need = float(cfg.get("min_free_gb", 13.0))
    d = disks(cfg)
    sysfree = d[0][3]
    return {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "min_free_gb": need,
        "sys_disk_free_gb": round(sysfree, 2),
        "low": 0 < sysfree < need,
        "disks": [{"path": p, "total_gb": round(t, 1), "free_gb": round(f, 2)} for p, t, u, f in d],
        "usage_mb": [{"label": a, "path": b, "mb": round(c, 1)} for a, b, c in usage(cfg)],
        "purge_blocked": blocked(cfg),
    }


# ---------------------------------------------------------------- 预防式配额
def quota_ok(cfg, need_gb, disk=None):
    """落盘前询问：再写 need_gb 后，系统盘是否仍高于下限？

    返回 (ok, free_now, free_after, need)。ok=False 时**不要下载**，
    先报告用户 / 先清理已完成场次的中间产物。
    """
    disk = disk or cfg.get("sys_disk", "C:\\")
    free = C.free_gb(disk)
    after = free - float(need_gb)
    need = float(cfg.get("min_free_gb", 13.0))
    return (after >= need, free, after, need)


def guard(cfg, need_gb, what="", log=print):
    """给调用方用：不足时打印可读说明并返回 False。"""
    ok, free, after, need = quota_ok(cfg, need_gb)
    if ok:
        return True
    log("[diskguard] 拒绝%s：需要 %.2f GB，系统盘现有 %.2f GB，" % (what or "落盘", need_gb, free))
    log("[diskguard] 写入后仅剩 %.2f GB < 下限 %.1f GB（Windows 低磁盘告警线约 10 GB）。" % (after, need))
    log("[diskguard] 请先跑 cleanup.py --plan 清理已完成场次的中间产物，或把 tmp_root 指向空闲盘。")
    return False


# ---------------------------------------------------------------- 熔断
def _flag_path(cfg):
    return os.path.join(cfg["base"], "_work", "purge_blocked.flag")


def blocked(cfg=None):
    cfg = cfg or C.load()
    return os.path.exists(_flag_path(cfg))


def mark_blocked(cfg, reason=""):
    """删除被拦截 -> 熔断。此后 --auto / monitor 的自动清理全部跳过，不再重试。"""
    p = _flag_path(cfg)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write("%s\n%s\n" % (datetime.datetime.now().isoformat(timespec="seconds"), reason))
    return p


def unblock(cfg=None):
    cfg = cfg or C.load()
    p = _flag_path(cfg)
    if os.path.exists(p):
        os.remove(p)
        return True
    return False


# ---------------------------------------------------------------- 节流
def _throttle_path(cfg):
    return os.path.join(cfg["base"], "_work", "_last_purge")


def throttled(cfg, hours=None):
    """自动清理的最小间隔（小时）。避免低空间时每轮都触发清理，反复弹窗。"""
    cfg = cfg or C.load()
    hours = float(hours if hours is not None else cfg.get("purge_min_interval_h", 6))
    p = _throttle_path(cfg)
    if not os.path.exists(p):
        return False, 0.0
    try:
        last = float(open(p).read().strip() or 0)
    except Exception:
        return False, 0.0
    ago = (time.time() - last) / 3600.0
    return ago < hours, ago


def touch_purge(cfg):
    p = _throttle_path(cfg)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(str(time.time()))


# ---------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser(description="磁盘守卫：只读检测 + 配额判定 + 清理熔断")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--need-gb", type=float, default=None,
                    help="询问再写 N GB 是否安全；不安全则返回码 1")
    ap.add_argument("--unblock", action="store_true", help="人工处置后解除清理熔断")
    ap.add_argument("--base", default=None)
    a = ap.parse_args()

    cfg = C.load(a.base)

    if a.unblock:
        print("已解除清理熔断" if unblock(cfg) else "当前没有熔断标志")
        return

    if a.need_gb is not None:
        ok, free, after, need = quota_ok(cfg, a.need_gb)
        print("需要 %.2f GB | 现有 %.2f GB | 写入后 %.2f GB | 下限 %.1f GB -> %s"
              % (a.need_gb, free, after, need, "允许" if ok else "拒绝"))
        sys.exit(0 if ok else 1)

    st = status(cfg)
    if a.json:
        print(json.dumps(st, ensure_ascii=False, indent=1))
        return
    print("磁盘守卫状态  %s" % st["ts"])
    for d in st["disks"]:
        mark = "  [低]" if d["free_gb"] < st["min_free_gb"] else ""
        print("  %-6s 总 %6.1f GB   可用 %6.2f GB%s" % (d["path"], d["total_gb"], d["free_gb"], mark))
    print("  下限 min_free_gb = %.1f GB（Windows 低磁盘告警线约 10 GB，必须留在其上）" % st["min_free_gb"])
    print("占用：")
    for u in st["usage_mb"]:
        print("  %-16s %9.1f MB   %s" % (u["label"], u["mb"], u["path"]))
    print("清理熔断：%s" % ("已熔断（自动清理全部跳过，人工处置后 diskguard.py --unblock）"
                            if st["purge_blocked"] else "正常"))


if __name__ == "__main__":
    main()
