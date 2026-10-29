# -*- coding: utf-8 -*-
"""多连接分片下载：CDN 支持 `Accept-Ranges: bytes` 时把大视频下快数倍。

为什么需要它（某会议视频 CDN 的大文件下载性能测试）：
  - 单连接：**0.58 MB/s** -> 约 60 分钟
  - 4 并发：总吞吐约 1.7 MB/s
  - 8 并发：总吞吐约 3 MB/s  -> 约 12 分钟
  同一 URL、同一时刻，只是一条连接跑不满带宽。所以「源不支持 Range」这个
  早先的判断是错的 —— 探测到 `Accept-Ranges: bytes` 就该并发。
  实测 2 GB 视频：60 分钟 -> 7 分钟。

实现要点：
  - 先 HEAD 探测大小与 Range 支持；不支持、或文件 < 64 MB -> 返回 None，
    调用方（pipeline.download）原样回退单连接逻辑，行为不变。
  - 下载写到 `<dst>.dl`（**不是 dst 本身**）：这样中途失败也不会让
    「文件存在且 >10 MB 就复用」的判断误以为视频已完整。
  - 分片直接 seek 写入 `.dl` 的对应偏移，不额外做一次合并拷贝（省 2 GB IO）。
  - 每片完成写一个 `<dst>.dl.dNNNNN` 标记：重跑时只补没下完的片（断点续传）。
    分片方案（总长|片长|片数）落在 `<dst>.dl.plan` 里，换了片长自动重来，
    避免旧标记与新分片错位。
  - prealloc=True 预分配整段（省碎片，PDF 线用）；False 边下边长（NTFS 稀疏），
    磁盘余量看得见地涨，适合**多路并发下载的视频线**（3 路 × 2 GB 预分配会
    瞬时吃掉 6 GB，把空闲一路压到 Windows 告警线以下）。
  - 全部成功才 `os.replace('.dl', dst)`，然后清理标记。

CLI：
    python fastdl.py --url <mp4> --jwt-file _jwt.txt --out <path> [--jobs 8]
    python fastdl.py --base <工作区> --mid <session-id> --out <path> [--jobs 8]
"""
import os
import sys
import time
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 解释器自举：本模块要用 requests，而宿主 python 常常没有。
# 直接 `python fastdl.py` 当命令行用时尤其如此 —— 缺依赖会自动换成装了它的
# 解释器重跑（详见 pybootstrap.py）。被 pipeline 调用时依赖已满足，零开销。
try:
    import pybootstrap
    pybootstrap.ensure(("requests",))
except Exception:
    pass

MIN_PARALLEL = 64 * 1024 * 1024      # 小于这个就别并发了，起线程的开销不值


def _session():
    import requests
    s = requests.Session()
    s.trust_env = False              # 忽略环境里的代理设置，避免走错出口
    return s


def probe(url, jwt, timeout=60):
    """返回 (总字节数, 是否支持 Range)。失败返回 (0, False)。"""
    try:
        h = _session().head(url, params={"jwt": jwt},
                            headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
        size = int(h.headers.get("content-length") or 0)
        ok = (h.headers.get("accept-ranges") or "").lower().startswith("bytes")
        return size, bool(ok and size)
    except Exception:
        return 0, False


def download_parallel(url, jwt, dst, jobs=8, chunk_mb=48, log=None, quiet=False,
                      guard=None, prealloc=True):
    """并发下载。返回写入字节数；若不适合并发（不支持 Range / 小文件 / 全失败）返回 None。

    guard：可选的零参回调，周期性询问"现在还能继续写吗"（磁盘告警线）。
    返回 False 就立刻停手并返回 None（已下的分片保留，供下次续传）；
    调用方随后可回退单连接（那里有更细的磁盘暂停逻辑）。
    prealloc：见模块 docstring。
    """
    # ⚠ 默认 log 必须 flush：重定向到文件时 stdout 是块缓冲，
    # 不 flush 的进度行要等进程结束才落盘 —— 事后看日志像是「卡死」。
    log = log or (lambda s: print(s, flush=True))
    total, ranges_ok = probe(url, jwt)
    if not ranges_ok or total < MIN_PARALLEL:
        return None

    tmp = dst + ".dl"
    d = os.path.dirname(os.path.abspath(dst))
    if d:
        os.makedirs(d, exist_ok=True)

    step = max(1, int(chunk_mb)) * 1024 * 1024
    parts = []
    a = 0
    while a < total:
        b = min(total, a + step) - 1
        parts.append((a, b))
        a = b + 1

    plan = "%d|%d|%d" % (total, step, len(parts))
    plan_f = tmp + ".plan"
    old = ""
    if os.path.exists(plan_f):
        try:
            old = open(plan_f, encoding="utf-8").read().strip()
        except OSError:
            old = ""
    fresh = (not os.path.exists(tmp)) or (old != plan)
    if fresh:
        with open(tmp, "wb") as f:
            if prealloc:
                f.truncate(total)        # 预分配，避免边下边扩
        for i in range(len(parts)):
            try:
                os.remove("%s.d%05d" % (tmp, i))
            except OSError:
                pass
        try:
            open(plan_f, "w", encoding="utf-8").write(plan)
        except OSError:
            pass

    jobs = max(1, min(int(jobs), len(parts)))
    prog = {}                     # 片号 -> 已落盘字节（重试时被覆盖，不会重复计数）
    lock = threading.Lock()
    errs = []
    abort = threading.Event()     # guard 判定不安全时置位，worker 尽快收手
    t0 = time.time()

    def worker(i, a, b):
        want = b - a + 1
        df = "%s.d%05d" % (tmp, i)
        if os.path.exists(df):
            with lock:
                prog[i] = want
            return
        last = None
        for attempt in range(4):
            try:
                headers = {"User-Agent": "Mozilla/5.0",
                           "Range": "bytes=%d-%d" % (a, b)}
                with lock:
                    prog[i] = 0
                with open(tmp, "r+b") as f:
                    f.seek(a)
                    got = 0
                    with _session().get(url, params={"jwt": jwt}, headers=headers,
                                        stream=True, timeout=180) as r:
                        if r.status_code not in (200, 206):
                            raise RuntimeError("HTTP %s" % r.status_code)
                        for c in r.iter_content(1 << 20):
                            if not c:
                                continue
                            if abort.is_set():
                                raise RuntimeError("guard 叫停（磁盘）")
                            f.write(c)
                            got += len(c)
                            with lock:
                                prog[i] = got
                if got != want:
                    raise RuntimeError("片长不符 %d/%d" % (got, want))
                last = None
                break
            except Exception as e:                      # 网络抖动就退避重试
                last = e
                if abort.is_set():
                    break
                time.sleep(1.5 * (attempt + 1))
        if abort.is_set():
            errs.append("guard 叫停")
            return
        if last is not None:
            errs.append("片 %d [%d-%d]: %s" % (i, a, b, last))
            return
        open(df, "w").close()                # 完成标记（供续传判断）

    stop = threading.Event()

    def reporter():
        while not stop.wait(15):
            with lock:
                got = sum(prog.values())
            el = max(0.1, time.time() - t0)
            if not quiet:
                log("    下载 %.1f%%  %.0f/%.0f MB  %.2f MB/s" %
                    (100.0 * got / total, got / 1048576.0,
                     total / 1048576.0, got / 1048576.0 / el))
            if guard is not None:
                try:
                    if not guard():
                        log("  磁盘已到安全线以下，停止并发下载（保留 %s 供续传）" % tmp)
                        abort.set()
                        return
                except Exception:
                    pass

    th = threading.Thread(target=reporter, daemon=True)
    th.start()
    try:
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            list(ex.map(lambda p: worker(p[0], p[1], p[2]),
                        [(i, a, b) for i, (a, b) in enumerate(parts)]))
    finally:
        stop.set()

    if errs:
        log("  并发下载未完成（保留 %s 供续传）: %s" % (tmp, errs[0]))
        return None

    if os.path.getsize(tmp) != total:
        log("  并发下载结果大小不符，放弃并回退单连接（%s）" % tmp)
        return None

    os.replace(tmp, dst)
    for i in range(len(parts)):
        try:
            os.remove("%s.d%05d" % (tmp, i))
        except OSError:
            pass
    try:
        os.remove(plan_f)
    except OSError:
        pass
    el = max(0.1, time.time() - t0)
    log("  视频完成 %.0f MB（%d 并发，%.2f MB/s，用时 %.1f 分钟）" %
        (total / 1048576.0, jobs, total / 1048576.0 / el, el / 60))
    return total


def _from_base(base, mid=None):
    """从工作区 config.json 取 (url 模板, jwt)；兼容两条线的字段名。"""
    import json
    import re
    p = os.path.join(base, "config.json")
    j = json.load(open(p, encoding="utf-8-sig"))
    tpl = j.get("video_url_tpl") or j.get("cdn")
    jwt = j.get("jwt")
    if not jwt:
        jf = j.get("jwt_file", "_jwt.txt")
        if not os.path.isabs(jf):
            jf = os.path.join(base, jf)
        if os.path.exists(jf):
            m = re.search(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+",
                          open(jf, encoding="utf-8", errors="ignore").read())
            jwt = m.group(0) if m else None
    url = None
    if tpl:
        if mid:
            # 三条线的模板占位符写法不一：`{}` / `{mid}` / `{sid}`，逐个试。
            for kw in ({"mid": mid}, {"sid": mid}, None):
                try:
                    url = tpl.format(**kw) if kw else tpl.format(mid)
                    break
                except (KeyError, IndexError, AttributeError):
                    continue
        else:
            url = tpl
    return url, jwt


def main():
    ap = argparse.ArgumentParser(description="多连接分片下载器（不支持 Range 时自动放弃）")
    ap.add_argument("--url", default=None, help="直链（不含 jwt 参数）")
    ap.add_argument("--base", default=None, help="工作区：用它的 config.json 拼 URL")
    ap.add_argument("--mid", default=None, help="manage_id / session id（配合 --base）")
    ap.add_argument("--jwt", default=None)
    ap.add_argument("--jwt-file", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--chunk-mb", type=int, default=48)
    ap.add_argument("--no-prealloc", action="store_true", help="边下边长（省瞬时占用）")
    a = ap.parse_args()

    if a.base:
        url, jwt = _from_base(a.base, a.mid)
    else:
        url, jwt = a.url, a.jwt
        if not jwt and a.jwt_file:
            import re
            jwt = re.search(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+",
                            open(a.jwt_file, encoding="utf-8").read()).group(0)
    if not url or not jwt:
        print("需要 --url/--base 与 jwt（--jwt / --jwt-file）")
        return 2

    total, ranges_ok = probe(url, jwt)
    print("探测: %.1f MB, Accept-Ranges=%s" % (total / 1048576.0, ranges_ok))
    if not ranges_ok:
        print("源不支持并发下载，退出（用 pipeline 走单连接回退）")
        return 1
    n = download_parallel(url, jwt, a.out, jobs=a.jobs, chunk_mb=a.chunk_mb,
                          prealloc=not a.no_prealloc)
    print("结果:", n)
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(main())
