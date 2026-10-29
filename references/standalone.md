# 各部分独立运行契约

主线被拆成 6 个可单独执行的环节，每环节有明确的「输入 / 输出 / 重跑代价 / 前置检查」。
任何一个环节都能脱离其他环节单独跑，这是刻意设计的——改一处逻辑只重跑一处，
不要动辄全量重来（历史上「改一次逻辑就重跑全部」浪费了大量时间与算力）。

```
make_tasks → [download] → [frames] → [segment] → [assign] → build_final → fillsync
                                                      ↑
                                              selftest（只读，随时可跑）
```

## 1. make_tasks.py — 建底账（无前置）

- 输入：飞书表格 + 关键词
- 输出：`_work/tasks.json`
- 重跑代价：低。但会覆盖已有 tasks.json，改过 `manage_id` / `talks` 的话先备份。

```bash
python scripts/make_tasks.py --sheet 1001 --kw acne,rosacea
```

## 2. download — 只下载视频

- 输入：`tasks.json` 里的 `manage_id` + `jwt`
- 输出：`<tmp_root>/S_<mid>/video.mp4`
- 前置检查：**磁盘配额**（`diskguard.quota_ok`）。预计写入后系统盘低于 `min_free_gb`
  就直接拒绝下载并说明原因——这是防止 Windows 低磁盘告警反复弹窗的第一道闸门。
- 重跑代价：网络带宽。已存在且 >10 MB 时直接复用缓存。

```bash
python scripts/pipeline.py --all --steps download
python scripts/pipeline.py 6742 --steps download     # 单场
```

## 3. frames — 只抽帧

- 输入：`video.mp4`
- 输出：`<tmp_root>/S_<mid>/parts/part_01/f_*.jpg` + `showinfo.log`
  + `extract_status.json`
- 重跑代价：高（一场 1–3 小时视频约十几分钟）。**改抽帧密度才需要重跑这一步**。
- 抽帧不全（覆盖 < 时长 -15s）时内部会自动重下一次视频。

```bash
python scripts/pipeline.py 6742 --steps frames
```

## 4. segment — 切段 + 切页 + 段 PDF（不改归属）

- 输入：抽帧产物
- 输出：`progress[key].segments`、`pdf/S_<mid>/*.pdf`、`<work>/analysis_sheet.png`
- 重跑代价：中（几分钟，含 OCR）。**调切段/切页阈值后只重跑这一步**。
- 只想重算段而保留旧帧：先 `--steps frames` 之外的都不动；或配合 `--no-frame`。

```bash
python scripts/pipeline.py 6742 --steps segment
python scripts/pipeline.py --all --steps segment     # 全部场次重切段
```

## 5. assign — 只重算归属（秒级，最常单独跑）

- 输入：`progress[key].segments` + `tasks.json` 里的 `talks`
- 输出：`progress[key].assign`
- 重跑代价：**极低（几秒）**。改了匹配逻辑、或发现「吞段」错分后，只跑这一步。
- 不抽帧、不重建 PDF、不碰 `segments`。

```bash
python scripts/pipeline.py --rematch                 # 全部场次
python scripts/pipeline.py 6742 --steps assign       # 单场
```

## 6. build_final.py — 合并成品（可到单行）

- 输入：`progress.json` + `pdf/S_<mid>/*.pdf`
- 输出：`pdf_final/<子表>/<行号>_<标题> - <讲者>.pdf` + `_work/final_report.json`
- 重跑代价：低。**必须无条件重建**（成品存在就跳过会整行丢失）。
- 局部重做时会把新结果合并进已有 `final_report.json`，不会把全量结果冲掉。

```bash
python scripts/build_final.py                        # 全量
python scripts/build_final.py 1003                   # 单子表
python scripts/build_final.py --session 6742         # 单场
python scripts/build_final.py 1003 --rows 121,122    # 单行/多行
```

## 7. fillsync.py — 上传 + 回填（可到单行）

- 输入：`final_report.json` + `pdf_final/`
- 输出：云文档 URL + 表格单元格写入 + `_work/fillsync_log.json`
- 幂等：签名未变复用 URL；单元格已有同名链接则跳过 —— 可以放心反复跑。
- `--fill-only`：只回填不上传（上传已成功但写表失败时用）。

```bash
python scripts/fillsync.py --dry                     # 先看计划
python scripts/fillsync.py --sheet 1003
python scripts/fillsync.py --sheet 1003 --rows 121,122
python scripts/fillsync.py --fill-only               # 只补写单元格
```

## 8. selftest.py — 只读自检（随时可跑，分检查项）

```bash
python scripts/selftest.py --section disk        # 磁盘与清理熔断
python scripts/selftest.py --section progress    # 进度/失败场次
python scripts/selftest.py --section assign      # 0 段行 / 吞段 / 页数过少 / 未匹配段
python scripts/selftest.py --section reconcile   # 成品 vs 表格链接对账
python scripts/selftest.py --no-sheet            # 离线，不读表格
```

## 9. diskguard.py — 磁盘守卫（独立、只读）

```bash
python scripts/diskguard.py                      # 状态一览
python scripts/diskguard.py --json
python scripts/diskguard.py --need-gb 2.5        # 询问再写 2.5 GB 是否安全（返回码 0/1）
python scripts/diskguard.py --unblock            # 人工处置后解除清理熔断
```

被 pipeline 的 download 步骤内部调用，也可以在任何落盘动作前单独问一次。

## 10. cleanup.py — 中间产物清理（独立）

```bash
python scripts/cleanup.py --scan                 # 只读占用一览
python scripts/cleanup.py --plan                # 待清理清单（默认动作，人工审阅）
python scripts/cleanup.py --do --limit 500      # 分批执行
python scripts/cleanup.py --videos --do         # 只清已下载视频
python scripts/cleanup.py --superseded --do     # 只清被取代的旧段 PDF
python scripts/cleanup.py --auto                # 低空间自动清理（受熔断与节流约束）
```

## 11. monitor_silent.py — 静默守护（独立）

由计划任务 + `pythonw` 拉起，也可手动跑一次看日志：
`python scripts/monitor_silent.py --foreground`

## 组合示例：发现某场归属错了，只修这一场

```bash
python scripts/pipeline.py 6742 --steps assign   # 秒级重算归属
python scripts/build_final.py --session 6742     # 只重建这一场成品
python scripts/fillsync.py --dry                 # 先看影响范围
python scripts/fillsync.py                       # 回填（幂等，已填的会跳过）
```

全程不重新下载、不重新抽帧、不重新 OCR。
