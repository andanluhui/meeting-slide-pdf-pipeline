# -*- coding: utf-8 -*-
"""飞书表格列定位与单元格读写。

铁律：一律按第 1 行「表头名称」定位目标列，禁止硬编码列字母（A/U/N/Q/L…）。
原因：用户在表格里增删列后字母会整体漂移，硬编码会把数据写进错误的空列，
并在那里重建表头 —— 结果是重复回填、浪费调用配额、污染表格。

行为约定：
  pdf_col(sid)        找到返回列字母，找不到返回 None（绝不擅自建列）
  ensure_pdf_col(sid) 找不到时在最后一列之后追加一列并写表头（调用方显式调用才允许）

命令行：
  python colmap.py            # 打印各子表 PDF 列位置
  python colmap.py --refresh  # 忽略缓存重新扫描
"""
import os
import sys
import json
import subprocess
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C


class Sheets:
    def __init__(self, cfg=None):
        self.cfg = cfg or C.load()
        self.lark = self.cfg["lark_cli"]
        self.ss = self.cfg["spreadsheet"]
        self.header_name = self.cfg.get("pdf_header", "PDF文件")
        self.cache_f = os.path.join(self.cfg["base"], "_work", "colmap_cache.json")
        self.ttl = 3600

    # ---------- 底层 ----------
    def api(self, args, timeout=120, stdin_data=None):
        kw = {}
        if stdin_data is not None:
            kw["input"] = stdin_data
        r = subprocess.run([self.lark] + args, capture_output=True, text=True,
                           encoding="utf-8", errors="ignore", timeout=timeout, **kw)
        for s in (r.stdout, r.stderr):
            try:
                d = json.loads(s)
                if isinstance(d, dict):
                    return d
            except Exception:
                continue
        return {"ok": False, "raw": (r.stdout or r.stderr or "")[:600]}

    @staticmethod
    def col_letter(idx):
        s, idx = "", idx + 1
        while idx:
            idx, rem = divmod(idx - 1, 26)
            s = chr(65 + rem) + s
        return s

    # ---------- 表头 ----------
    def scan_header(self, sid, row=1):
        rng = "A%d:BB%d" % (row, row)
        d = self.api(["sheets", "+csv-get", "--spreadsheet-token", self.ss,
                      "--sheet-id", sid, "--range", rng, "--include-row-prefix=false"])
        if not d.get("ok"):
            raise RuntimeError("读取表头失败 %s: %s" % (sid, d.get("error", d)))
        line = d["data"].get("annotated_csv", "")
        if line.startswith(" "):
            line = line[1:]
        hdr = {}
        for i, cell in enumerate(line.split(",")):
            name = cell.strip()
            if name and name not in hdr:
                hdr[name] = self.col_letter(i)
        return hdr

    def header_map(self, sid, refresh=False):
        try:
            cache = json.load(open(self.cache_f, encoding="utf-8"))
        except Exception:
            cache = {}
        ent = cache.get(sid)
        if not refresh and ent and time.time() - ent.get("t", 0) < self.ttl:
            return ent["map"]
        m = self.scan_header(sid)
        cache[sid] = {"t": time.time(), "map": m}
        try:
            os.makedirs(os.path.dirname(self.cache_f), exist_ok=True)
            json.dump(cache, open(self.cache_f, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
        except Exception:
            pass
        return m

    def pdf_col(self, sid, refresh=False):
        return self.header_map(sid, refresh=refresh).get(self.header_name)

    def ensure_pdf_col(self, sid, refresh=False):
        m = self.header_map(sid, refresh=refresh)
        col = m.get(self.header_name)
        if col:
            return col
        last = max((self._idx(v) for v in m.values()), default=-1)
        new_col = self.col_letter(last + 1)
        cells = [[{"rich_text": [{"type": "text", "text": self.header_name}]}]]
        ok = self.set_cell(sid, new_col, 1, cells)
        if not ok.get("ok"):
            raise RuntimeError("新建 PDF 列失败: %s" % ok.get("error", ok))
        print("[colmap] %s: 新建列 %s（已写表头「%s」）" % (sid, new_col, self.header_name))
        self.header_map(sid, refresh=True)
        return new_col

    @staticmethod
    def _idx(letter):
        n = 0
        for ch in letter:
            n = n * 26 + (ord(ch) - 64)
        return n - 1

    # ---------- 读写 ----------
    def col_texts(self, sid, col, nrows=600):
        """整列纯文本 -> {行号: 文本}。用于回填前的幂等判断（不解析链接）"""
        d = self.api(["sheets", "+csv-get", "--spreadsheet-token", self.ss,
                      "--sheet-id", sid, "--range", "%s1:%s%d" % (col, col, nrows),
                      "--include-row-prefix=false", "--max-chars", "4000000"], timeout=180)
        if not d.get("ok"):
            return {}
        return {i: ln for i, ln in enumerate(
            d["data"].get("annotated_csv", "").splitlines(), start=1) if ln.strip()}

    def cell_value(self, sid, col, row):
        """精确读取单个单元格（CSV 会因单元格内换行错位，判断"是否已有内容"必须用这个）"""
        d = self.api(["sheets", "+cells-get", "--spreadsheet-token", self.ss,
                      "--sheet-id", sid, "--range", "%s%d" % (col, row)], timeout=120)
        try:
            return d["data"]["ranges"][0]["cells"][0][0].get("value") or ""
        except Exception:
            return ""

    def set_cell(self, sid, col, row, cells):
        """写单元格。

        cells 一律走 stdin（`--cells -`），绝不用 `@文件路径`：
        lark-cli 对 `--cells @path` 有路径白名单（只允许 cwd / 账户临时目录 /
        ~/files），工作区一旦在别的盘（如 E:\\work）就会被拒
        （outside the built-in allowlist），而 stdin 无此限制。
        """
        payload = json.dumps(cells, ensure_ascii=False)
        return self.api(["sheets", "+cells-set", "--spreadsheet-token", self.ss,
                         "--sheet-id", sid, "--range", "%s%d" % (col, row),
                         "--cells", "-", "--format", "json"],
                        timeout=180, stdin_data=payload)

    def clear_range(self, sid, rng):
        return self.api(["sheets", "+cells-clear", "--spreadsheet-token", self.ss,
                         "--sheet-id", sid, "--range", rng, "--yes", "--format", "json"],
                        timeout=180)

    def unmerge(self, sid, rng):
        """取消合并单元格：合并区域写不进内容，必须先取消合并"""
        return self.api(["sheets", "+cells-unmerge", "--spreadsheet-token", self.ss,
                         "--sheet-id", sid, "--range", rng, "--format", "json"], timeout=180)


def link_cells(items, wrap=True):
    """构造 rich_text 超链接单元格。items: [(显示文本, 链接), ...]

    注意：飞书表格单元格不支持真正的附件，只能用 rich_text link 指向云文档。
    """
    rich = [{"type": "link", "text": k, "link": v} for k, v in items]
    return [[{"rich_text": rich,
              "cell_styles": {"word_wrap": "auto-wrap" if wrap else "clip",
                              "vertical_alignment": "top"}}]]


if __name__ == "__main__":
    sh = Sheets()
    refresh = "--refresh" in sys.argv
    for name, sid in sh.cfg["sheets"].items():
        hdr = sh.header_map(sid, refresh=refresh)
        print("%-10s %-8s PDF列=%-4s 总列数=%d" % (name, sid, hdr.get(sh.header_name), len(hdr)))
