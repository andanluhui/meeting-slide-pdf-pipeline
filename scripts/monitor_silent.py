# -*- coding: utf-8 -*-
"""附加技能③：静默后台守护（无窗口、无弹窗、不经 Agent 会话）。

==============================================================================
用户明确要求：巡检类动作一律后台进行，不要弹窗打断工作
==============================================================================
本脚本的四条静默纪律：
  1. 只写日志文件，不 print（--foreground 除外）
  2. 拉起子脚本时优先用 pythonw.exe，并强制 CREATE_NO_WINDOW，
     避免任何控制台窗口闪现
  3. 自动清理前先过「熔断 + 节流」两道闸（diskguard），被拦即停、不重试、不循环
  4. 不调用 schtasks / reg / sc / wmic / net / wsl 等会触发提权弹窗的命令；
     计划任务注册请用 PowerShell 的 Register-ScheduledTask cmdlet（见 workflow.md）

运行方式：由 Windows 计划任务用 pythonw.exe 拉起（不产生控制台窗口）：
    pythonw.exe <skill>/scripts/monitor_silent.py
建议频率：每 30 分钟。

职责（只做四件事）：
  1. 把进度与磁盘空间写一行日志（便于事后追溯）
  2. 停滞检测：进度文件 N 分钟无变化 -> 记录一条告警
  3. 低空间：低于阈值 -> 调用 cleanup.py --auto（只清已完成场次的中间产物，受熔断/节流约束）
  4. 周期性跑 selftest.py，把结论摘要追加到日志

明确不做（安全边界）：
  - 不删除未确认的交付物；不动 pdf/ 与 pdf_final/
  - 不弹任何对话框、不申请提权、不杀进程、不重启别人的任务
"""
import os
import sys
import json
import time
import subprocess
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as C
import diskguard as DG

STALL_SEC = int(os.environ.get("MSP_STALL_SEC", "1800"))     # 30 分钟无变化 -> 停滞
SELFTEST_EVERY = int(os.environ.get("MSP_SELFTEST_EVERY", "12"))  # 每 12 轮跑一次自检

# 会触发提权/沙箱授权弹窗的命令，本 skill 全程禁用
FORBIDDEN = ("schtasks", "reg.exe", "sc.exe", "wmic", "net.exe", "net1", "wsl", "wslconfig")


def log(path, msg):
    line = "[%s] %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def quiet_python():
    """优先「装了依赖的解释器」对应的 pythonw.exe（无控制台），其次当前解释器的
    pythonw，最后退回当前解释器 + CREATE_NO_WINDOW。

    为什么要先问 pybootstrap：计划任务可能被注册成裸解释器（自身能跑，因为它只用
    标准库），但它拉起的 pipeline.py 需要 numpy/pymupdf —— 用错解释器会直接失败。
    """
    base = sys.executable
    try:
        import pybootstrap
        base = pybootstrap.resolve(pybootstrap.DEPS_PIPELINE).get("chosen") or base
    except Exception:
        pass
    here = os.path.dirname(base)
    pw = os.path.join(here, "pythonw.exe")
    if os.path.exists(pw):
        return pw
    pw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return pw if os.path.exists(pw) else sys.executable


def run_quiet(args):
    """静默执行子脚本：无窗口、无交互、超时保护。"""
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0                      # SW_HIDE
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return subprocess.run([quiet_python()] + args,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="ignore", timeout=1800,
                          startupinfo=si, creationflags=flags)


def main():
    fg = "--foreground" in sys.argv
    cfg = C.load()
    base = cfg["base"]
    logf = os.path.join(base, "_work", "monitor.log")
    os.makedirs(os.path.dirname(logf), exist_ok=True)

    def say(m):
        log(logf, m)
        if fg:
            print(m)

    tasks = []
    try:
        tasks = C.tasks(cfg)
    except Exception as e:
        say("读取任务清单失败: %r" % (e,))
    prog = C.progress(cfg)
    done = sum(1 for v in prog.values() if v.get("status") in ("done", "pdf_done"))
    fail = [k for k, v in prog.items() if v.get("status") == "failed"]

    free = C.free_gb()
    need = cfg.get("min_free_gb", 13.0)
    say("进度 %d/%d 场，失败 %d，C 盘可用 %.1f GB" % (done, len(tasks), len(fail), free))

    # 停滞检测
    try:
        age = time.time() - os.path.getmtime(cfg["progress_file_abs"])
        if done < len(tasks) and age > STALL_SEC:
            say("告警：进度文件 %.0f 分钟无变化，疑似停滞" % (age / 60))
    except OSError:
        pass

    # 低空间自动清理：先过熔断，再过节流，都不通过就直接跳过（绝不重试）
    if 0 < free < need:
        if DG.blocked(cfg):
            say("可用 %.1f GB < %.1f GB，但清理已熔断（上次删除被拦截）——跳过，不重试"
                % (free, need))
        else:
            yes, ago = DG.throttled(cfg)
            if yes:
                say("可用 %.1f GB < %.1f GB，距上次清理 %.1f 小时，节流跳过"
                    % (free, need, ago))
            else:
                say("可用 %.1f GB < %.1f GB，触发自动清理（只清已完成场次）" % (free, need))
                try:
                    r = run_quiet([os.path.join(HERE, "cleanup.py"), "--auto",
                                   "--limit", "800"])
                    say("cleanup: %s" % (r.stdout or r.stderr).strip().replace("\n", " | ")[:400])
                except Exception as e:
                    say("cleanup 失败: %r" % (e,))

    # 定期自检
    stamp_f = os.path.join(base, "_work", "_selftest_round")
    n = 0
    try:
        n = int(open(stamp_f).read().strip() or 0)
    except Exception:
        pass
    n += 1
    try:
        open(stamp_f, "w").write(str(n))
    except Exception:
        pass
    if n % SELFTEST_EVERY == 0:
        try:
            r = run_quiet([os.path.join(HERE, "selftest.py"), "--no-sheet"])
            head = [l for l in (r.stdout or "").splitlines() if l.startswith("- ")]
            say("自检: " + " ".join(head[:4]))
        except Exception as e:
            say("自检失败: %r" % (e,))


if __name__ == "__main__":
    main()
