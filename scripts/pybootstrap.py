# -*- coding: utf-8 -*-
"""解释器自举：让脚本总能跑在「装了依赖的 python」上。

本模块被两个 skill 各存一份**完全相同**的副本
（`meeting-slide-pdf-pipeline`、`video-to-feishu-minutes`），
因为每个 skill 要能独立安装、独立运行。改动请两边同步。

问题
----
这两套流水线都需要第三方包（PDF 线：numpy / pymupdf / PIL / requests /
imageio-ffmpeg / rapidocr-onnxruntime；视频线：requests），
而环境里 PATH 上的 `python` 常常一个都没装 —— WorkBuddy 托管的裸解释器
（`binaries/python/versions/<ver>/python.exe`）就是空的，别的 agent 宿主
（DSH / Claude Code …）自带的 python 也一样，且目录结构各不相同。
直接 `python scripts/pipeline.py` 只会 `ModuleNotFoundError: numpy`。

反过来，把某个绝对的 venv 路径写死在文档里也不可移植：换机器就失效。

做法
----
1. 当前解释器能满足依赖 -> 什么都不做（只用 find_spec 查，不真导入，开销极低）。
2. 不满足 -> 按候选顺序探测，找到第一个满足的就**用它的 python 重新执行本脚本**。
3. 都找不到 -> 打印诊断与一行 `pip install` 命令，退出码 3（不抛 traceback）。

候选顺序（先到先用）
--------------------
MPDF_PY 环境变量 -> 当前解释器 -> WorkBuddy 托管 venv -> 工作区 .venv/venv ->
WorkBuddy 托管裸解释器 -> PATH 上的 python / python3 / py

环境变量
--------
MPDF_PY / V2F_PY / PDF_PIPELINE_PY   指定解释器，优先于一切自动探测
MPDF_NO_REEXEC=1  禁用自举（只诊断，不换解释器）
MPDF_QUIET=1      不打印「已切换解释器」提示

用法
----
    # 脚本内：必须放在任何第三方 import 之前
    import pybootstrap
    pybootstrap.ensure()

    # 命令行诊断 / 手动指定
    python scripts/pybootstrap.py --check                # 当前解释器、缺什么、建议用哪个
    python scripts/pybootstrap.py --show                 # 只输出建议的解释器路径
    python scripts/pybootstrap.py --refresh              # 清缓存后重新探测
    python scripts/pybootstrap.py --exec scripts/pipeline.py --all
"""
import json
import os
import subprocess
import sys
import tempfile
import time

# 跑主线所需的全部第三方包（缺一个就跑不动对应步骤）
DEPS_PIPELINE = ("numpy", "pymupdf", "PIL", "requests",
                 "imageio_ffmpeg", "rapidocr_onnxruntime")
# 只要生成 PDF 的最低集合
DEPS_PDF = ("pymupdf", "PIL")
# video-to-feishu-minutes：只要 requests（其余走 lark-cli 子进程）
DEPS_VIDEO = ("requests",)

# 本模块在两个 skill 里各留一份副本（各自独立可安装），内容保持一致。
# 环境变量两种命名都认，便于同一台机器上两个 skill 共用设置。
_ENV_KEYS = ("MPDF_PY", "V2F_PY", "PDF_PIPELINE_PY")

_CACHE_TTL = 7 * 24 * 3600          # 缓存 7 天
_CACHE = os.path.join(tempfile.gettempdir(), "mpdf_pybootstrap.json")


# ------------------------------------------------------------------ 探测
def missing(mods, exe=None):
    """当前（或指定）解释器缺哪些模块。用 find_spec，不真导入，快且安全。"""
    if exe is None:
        import importlib.util
        out = []
        for m in mods:
            try:
                if importlib.util.find_spec(m) is None:
                    out.append(m)
            except (ImportError, ValueError):
                out.append(m)
        return out
    code = ("import importlib.util as u, json, sys\n"
            "print(json.dumps([m for m in %r if not u.find_spec(m)]))\n" % (list(mods),))
    try:
        r = subprocess.run([exe, "-c", code], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=40)
    except (OSError, subprocess.SubprocessError):
        return None                      # 解释器不可用
    if r.returncode != 0:
        return None
    try:
        return json.loads((r.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def _home():
    return os.path.expanduser("~")


def _glob_dirs(pattern):
    import glob
    return sorted(glob.glob(pattern), reverse=True)     # 版本号新的在前


def candidates():
    """按优先级列出候选解释器（只做存在性过滤，不在这里探测）。"""
    win = os.name == "nt"
    exe_name = "python.exe" if win else "python3"
    out = []

    for key in _ENV_KEYS:
        v = (os.environ.get(key) or "").strip()
        if v:
            out.append(v)

    out.append(sys.executable)

    # WorkBuddy 托管 venv：envs/default 优先，其余按最新优先
    base = os.path.join(_home(), ".workbuddy", "binaries", "python")
    out.append(os.path.join(base, "envs", "default", "Scripts", exe_name))
    for d in _glob_dirs(os.path.join(base, "envs", "*")):
        out.append(os.path.join(d, "Scripts", exe_name))

    # 工作区自建 venv（从当前目录往上找 3 层）
    here = os.getcwd()
    for _ in range(3):
        for name in (".venv", "venv", "env"):
            out.append(os.path.join(here, name, "Scripts", exe_name))
            out.append(os.path.join(here, name, "bin", "python3"))   # POSIX 布局
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent

    # WorkBuddy 托管裸解释器（用户可能往里 pip install 过）
    for d in _glob_dirs(os.path.join(base, "versions", "*")):
        out.append(os.path.join(d, exe_name))

    # PATH
    import shutil
    for name in ("python", "python3", "py"):
        p = shutil.which(name)
        if p:
            out.append(p)

    # 去重 + 只留真实存在的文件
    seen, res = set(), []
    for p in out:
        try:
            key = os.path.normcase(os.path.realpath(p))
        except OSError:
            continue
        if key in seen or not os.path.isfile(p):
            continue
        seen.add(key)
        res.append(p)
    return res


# ------------------------------------------------------------------ 缓存
def _cache_read(mods):
    try:
        with open(_CACHE, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("mods") == list(mods) and time.time() - d.get("ts", 0) < _CACHE_TTL:
            if os.path.isfile(d.get("exe", "")):
                return d["exe"]
    except (OSError, ValueError):
        pass
    return None


def _cache_write(exe, mods):
    try:
        with open(_CACHE, "w", encoding="utf-8") as f:
            json.dump({"exe": exe, "mods": list(mods), "ts": time.time()}, f)
    except OSError:
        pass


def clear_cache():
    try:
        os.remove(_CACHE)
    except OSError:
        pass


# ------------------------------------------------------------------ 解析
def resolve(required=DEPS_PIPELINE, use_cache=True, refresh=False, verbose=False):
    """返回报告 dict：
    {ok, current, current_missing, chosen, source, probed, candidates}
    ok=True 表示「当前解释器就够用」（chosen == current）。
    """
    mods = tuple(required)
    cur_missing = missing(mods)
    rep = {"current": sys.executable, "current_missing": cur_missing,
           "chosen": None, "source": None, "probed": {}, "candidates": []}
    if cur_missing == []:
        rep.update(ok=True, chosen=sys.executable, source="current")
        return rep
    rep["ok"] = False

    if refresh:
        clear_cache()

    if use_cache:
        c = _cache_read(mods)
        if c and missing(mods, c) == []:
            rep.update(chosen=c, source="cache")
            return rep

    for p in candidates():
        ms = missing(mods, p)
        if ms is None:
            continue
        rep["probed"][p] = ms
        if verbose:
            print("  探测 %s -> 缺 %s" % (p, ms or "无"), file=sys.stderr)
        if ms == []:
            rep.update(chosen=p, source="probe")
            _cache_write(p, mods)
            return rep
    return rep


def _pythonw(exe):
    """Windows：换成同目录的 pythonw.exe（无控制台窗口）——只在调用方本来就用
    pythonw 启动时才换，避免把交互式运行变成无输出。"""
    if os.name != "nt":
        return exe
    if os.path.basename(sys.executable).lower() != "pythonw.exe":
        return exe
    cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return cand if os.path.isfile(cand) else exe


def _hint(required):
    deps = " ".join(
        {"PIL": "pillow", "pymupdf": "pymupdf", "imageio_ffmpeg": "imageio-ffmpeg",
         "rapidocr_onnxruntime": "rapidocr-onnxruntime"}.get(m, m) for m in required)
    return (
        "\n[pybootstrap] 找不到装有依赖的 python（需要：%s）。\n"
        "  三条路任选（推荐第 1 条）：\n"
        "  1) 安装到工作区虚拟环境：\n"
        "       python -m venv .venv\n"
        "       .venv\\Scripts\\pip install -r scripts/requirements.txt\n"
        "     然后重跑命令即可（脚本会自动找到 .venv）。\n"
        "  2) 直接指定解释器：set MPDF_PY=<python.exe 完整路径>\n"
        "  3) 关掉自举、自己手工装：set MPDF_NO_REEXEC=1\n" % deps)


def ensure(required=DEPS_PIPELINE, argv=None):
    """脚本入口调用：当前解释器可用则原样返回；否则换解释器重新执行本脚本。

    返回 True 表示「当前解释器可用，继续跑」；不会正常返回「需要切换」这种情况
    （切换时会 exec/退出）。
    """
    mods = tuple(required)
    cur = missing(mods)
    if cur == []:
        return True

    if os.environ.get("MPDF_NO_REEXEC") == "1":
        print("[pybootstrap] 当前解释器缺 %s，已设 MPDF_NO_REEXEC=1 不切换。"
              % ",".join(cur), file=sys.stderr)
        return True

    rep = resolve(mods)
    exe = rep.get("chosen")

    if not exe or os.path.normcase(exe) == os.path.normcase(sys.executable):
        print(_hint(mods), file=sys.stderr)
        sys.exit(3)

    exe = _pythonw(exe)
    if os.environ.get("MPDF_QUIET") != "1":
        print("[pybootstrap] 当前解释器缺 %s -> 改用 %s 重新执行"
              % (",".join(cur), exe), file=sys.stderr)

    env = dict(os.environ)
    env["MPDF_NO_REEXEC"] = "1"          # 防循环：只切换一次
    env.setdefault("PYTHONIOENCODING", "utf-8")
    args = [exe] + (list(argv) if argv is not None else sys.argv)
    try:
        sys.exit(subprocess.call(args, env=env))
    except OSError as e:
        print("[pybootstrap] 切换失败（%s）。" % e, file=sys.stderr)
        print(_hint(mods), file=sys.stderr)
        sys.exit(3)


# ------------------------------------------------------------------ CLI
def _cli():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0

    if args[0] == "--exec":
        if len(args) < 2:
            print("用法：python scripts/pybootstrap.py --exec <脚本> [参数...]")
            return 2
        rep = resolve()
        exe = rep.get("chosen")
        if not exe:
            print(_hint(DEPS_PIPELINE), file=sys.stderr)
            return 3
        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        return subprocess.call([exe] + args[1:], env=env)

    refresh = "--refresh" in args
    if refresh:
        clear_cache()

    rep = resolve(refresh=refresh, verbose="--verbose" in args)
    if "--show" in args:
        print(rep.get("chosen") or "")
        return 0 if rep.get("chosen") else 3

    # --check（默认）
    print("当前解释器 : %s" % rep["current"])
    print("          缺 : %s" % (", ".join(rep["current_missing"]) or "（无，可直接跑）"))
    if rep.get("ok"):
        print("结论       : 当前解释器可用，无需切换 ✓")
        return 0
    if rep.get("chosen"):
        print("建议使用   : %s  （来源：%s）" % (rep["chosen"], rep.get("source")))
        print("结论       : 脚本启动时会自动切到这个解释器（无需手工指定）✓")
        return 0
    print(_hint(DEPS_PIPELINE))
    print("结论       : 未找到可用解释器 ✗")
    return 3


if __name__ == "__main__":
    sys.exit(_cli())
