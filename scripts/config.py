# -*- coding: utf-8 -*-
"""统一配置加载。所有脚本共享，避免把工作区路径 / token 写死在代码里。

配置来源优先级：环境变量 > 工作区 config.json > 脚本同目录 config.json

常用环境变量：
  MSP_BASE        工作区根目录（必填，或写到 config.json）
  MSP_JWT         视频 CDN 的 JWT（也可放 jwt_file 指向的文件）
  MSP_TMP_ROOT    大体积中间产物目录（视频/抽帧），默认 <base>/_tmp
"""
import os
import json
import re

DEFAULT = {
    # ---- 路径 ----
    "base": "",                    # 工作区根目录
    "tmp_root": "",                # 视频/抽帧落盘目录（建议放到空闲盘，默认 <base>/_tmp）
    "tasks_file": "_work/tasks.json",
    "progress_file": "_work/progress.json",
    "final_report": "_work/final_report.json",
    "fillsync_log": "_work/fillsync_log.json",
    "pdf_dir": "pdf",              # 段 PDF（<base>/pdf/<场>/）
    "final_dir": "pdf_final",      # 按演讲行合并后的成品（<base>/pdf_final/<子表>/）
    # ---- 飞书 ----
    "lark_cli": "",                # lark-cli.cmd 绝对路径
    "spreadsheet": "",             # 表格 token
    "sheets": {},                  # {子表名: sheet_id}
    "pdf_header": "PDF文件",       # 目标列表头名（列定位依据）
    "folder_token": "",            # 云空间文件夹 token
    "file_url_prefix": "https://your-tenant.feishu.cn/file/",
    # ---- 视频源 ----
    "jwt": "",
    "jwt_file": "",
    "video_url_tpl": "https://video.example.com/session/{mid}/video.mp4",
    # ---- 版式（1920x1080 会议录播）----
    "slide_box": [14, 155, 1444, 959],      # 幻灯片区域
    "speaker_box": [1395, 465, 1910, 645],  # 讲者名条区域（切段依据）
    # ---- 下载 ----
    # 并发分片数（CDN 回 Accept-Ranges 时生效）。实测某支持 HTTP Range 的 CDN 上单连接
    # 只有 0.58 MB/s，8 并发约 3 MB/s：2 GB 视频 60 分钟 -> 12 分钟。
    # 源不支持 Range 时 fastdl 自动放弃，回退单连接断点续传。
    "download_jobs": 8,
    # ---- 抽帧与判据 ----
    "fps": "1",                    # 抽帧密度；关键帧模式(-skip_frame nokey)会漏页，勿用
    "switch_min_diff": 12,         # 讲者条切换灰度差阈值
    "page_min_edge": 0.045,        # 保留页的最小强边缘占比
    "dedup_lost_ratio": 0.12,      # 内容被替换的比例阈值（判定换页）
    "discard_single_page_sec": 300,# 单页持续超过该秒数 -> 讨论/待机，丢弃
    "anchor_score": 0.62,          # 归属锚点门槛（COI 页可放宽到 coi_anchor_score）
    "coi_anchor_score": 0.45,
    "fallback_score": 0.35,        # 无锚点时，为未覆盖行补锚点的最低分
    # ---- 磁盘与清理（零弹窗相关，见 diskguard.py 文件头）----
    "purge": True,                 # 是否允许清理中间产物（False = 一切删除动作关闭）
    "min_free_gb": 13.0,           # 系统盘可用下限：Windows 低磁盘告警线约 10 GB，必须留在其上
    "sys_disk": "C:\\",            # 配额检查盯的盘（下载虽然落 tmp_root，系统盘也不能跌破）
    "video_est_gb": 2.5,           # 单场视频预估体积，下载前据此做配额检查
    "purge_min_interval_h": 6,     # 自动清理最小间隔（小时），避免低空间时反复触发
    "keep_deliverables": ["pdf", "pdf_final"],  # 永不清删的目录
}


def _load_json(p):
    """容错读取 JSON：`utf-8-sig` 会自动跳过文件开头的 BOM（Windows 记事本 /
    PowerShell `Set-Content -Encoding UTF8` 等工具常见），无 BOM 时与 `utf-8` 完全等价。
    写入路径一律保持无 BOM 的 `utf-8`，因此对其它 agent / 平台完全通用。"""
    with open(p, encoding="utf-8-sig") as f:
        return json.load(f)


def _read(p):
    try:
        return _load_json(p)
    except Exception:
        return None


def load(base=None):
    cfg = dict(DEFAULT)
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(here, "config.json"),
              os.path.join(os.path.dirname(here), "config.json")):
        d = _read(p)
        if d:
            cfg.update(d)
    base = base or os.environ.get("MSP_BASE") or cfg.get("base") or os.getcwd()
    cfg["base"] = base
    d = _read(os.path.join(base, "config.json"))
    if d:
        cfg.update(d)
    # 环境变量覆盖
    if os.environ.get("MSP_BASE"):
        cfg["base"] = os.environ["MSP_BASE"]
    if os.environ.get("MSP_TMP_ROOT"):
        cfg["tmp_root"] = os.environ["MSP_TMP_ROOT"]
    if os.environ.get("MSP_JWT"):
        cfg["jwt"] = os.environ["MSP_JWT"]
    if not cfg["tmp_root"]:
        cfg["tmp_root"] = os.path.join(base, "_tmp")
    if not cfg["jwt"] and cfg.get("jwt_file"):
        fp = cfg["jwt_file"]
        if not os.path.isabs(fp):
            fp = os.path.join(base, fp)
        try:
            cfg["jwt"] = re.search(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+",
                                   open(fp, encoding="utf-8").read()).group(0)
        except Exception:
            pass
    for k in ("tasks_file", "progress_file", "final_report", "fillsync_log",
              "pdf_dir", "final_dir"):
        v = cfg.get(k) or ""
        cfg[k + "_abs"] = v if os.path.isabs(v) else os.path.join(base, v)
    return cfg


def tasks(cfg):
    return _load_json(cfg["tasks_file_abs"])


def progress(cfg):
    p = cfg["progress_file_abs"]
    return _load_json(p) if os.path.exists(p) else {}


def save_progress(cfg, prog):
    p = cfg["progress_file_abs"]
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(prog, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def key_of(task):
    """进度文件的键：session 名前 60 字符（与行号解耦，用户增删行不丢状态）"""
    return str(task.get("session", ""))[:60]


def free_gb(path="C:\\"):
    import shutil
    try:
        return shutil.disk_usage(path).free / 1024 ** 3
    except Exception:
        return -1.0
