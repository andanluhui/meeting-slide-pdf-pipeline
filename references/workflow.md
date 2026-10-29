# 完整工作流（会议录播 → 单场演讲幻灯片 PDF → 回填表格）

## 0. 前置依赖

Python 包：`numpy` `pillow` `pymupdf` `requests` `rapidocr-onnxruntime` `imageio-ffmpeg`
（ffmpeg 二进制由 imageio_ffmpeg 提供，无需单独安装）
外部命令：`lark-cli`（飞书表格/云盘读写，Node 包，路径写进 config）
运行环境：Windows / macOS 均可；Windows 下注意删除保护与磁盘告警（见 pitfalls）。

配置：复制 `scripts/config.example.json` 为 `<工作区>/config.json` 并填写
base / lark_cli / spreadsheet / sheets / folder_token / jwt。

## 1. 建立底账（tasks.json）

一场任务 = 一个视频 + 若干表格行。两种形态：

- `talk-rows`：一场录播里连续多场演讲，每行 = 一场演讲（学术子表，主流形态）
- `session-row`：整场一个 PDF（企业专场 / 网页回放补充）

```json
{
  "sheet": "1001",
  "session": "Acne: clinical variants and treatment recommendations",
  "rows": [121, 122, 123, 124, 125],
  "kind": "talk-rows",
  "manage_id": 6742,
  "priority": 1,
  "talks": [
    {"row": 121, "title_en": "Acne vulgaris in adults",
     "title_cn": "成人寻常痤疮", "speaker": "Lally Mekokishvili"}
  ]
}
```

生成骨架：`python scripts/make_tasks.py --sheet 1001 --kw acne,rosacea`
之后必须人工/平台检索补齐 `manage_id`（没有视频 ID 的场次会被标记为 no_video）。

## 2. 下载 + 抽帧

```
python scripts/pipeline.py --all          # 按 priority 顺序跑全部未完成场次
python scripts/pipeline.py 6742           # 单场
python scripts/pipeline.py --all --steps download    # 只下载，不抽帧
python scripts/pipeline.py 6742 --steps frames       # 只抽帧（用已有视频）
```

四步解耦，可任意组合：`--steps download,frames,segment,assign`。
下载前会做**磁盘配额检查**（`diskguard`）：预计写入后系统盘低于 `min_free_gb`
就拒绝下载，避免触发 Windows 低磁盘告警反复弹窗。

- 视频落 `tmp_root`（务必指向空闲盘，不要放系统盘）。
- 抽帧 **必须用 `fps=1`**，不要用 `-skip_frame nokey`：只取关键帧会漏掉非关键帧处的换页，
  实测同一视频 47 个实际画面只抽出 21 页。
- 抽帧后若 `last_pts < duration - 15s`，说明下载不完整，脚本会自动重下一次。

## 3. 切段与切页

- **切段**：检测右侧「讲者名条」区域的灰度突变（单帧突变 + 前后平静），
  得到演讲切换时间点 `S`。阈值 `switch_min_diff`。
- **切页**：逐帧比较幻灯片区二值 mask，丢失比例 > `dedup_lost_ratio` 判为新页。
  **基准 mask 绝不累积**（见 pitfalls）。
- **保留页**：强边缘占比 ≥ `page_min_edge` 直接保留；低边缘页用 OCR 词数兜底
  （章节分隔页有字 → 保留；待机屏没字 → 剔除）。
- **剔除**：投票/互动页（右下 teal 按钮 + OCR 关键词）、单页持续 > 5 分钟（讨论/待机）。

## 4. 生成段 PDF

每段一个 PDF（页 = 该段保留下来的幻灯片），文件名 `OCR标题 - 讲者.pdf`。
同时输出 `analysis_sheet.png` 缩略图总览，供人工抽检。

## 5. 归属：段 → 议程行

`assign_talks()` 的策略顺序：

1. 为每段算「标题相似度」与「讲者相似度」，**标题优先于讲者**
2. 高分锚点：分数 ≥ 0.62，或（是利益冲突/披露页 且 ≥ 0.45）
3. 锚点按时间排序，剔除与议程顺序矛盾的乱序锚点
4. 其余段按播放时间归入「其之前最近的演讲起点」
5. 兜底：未覆盖行独立重算所有段分数，取最高者补锚点（否则整行丢失）
6. `discussion / Q&A / panel` 行不回填

改了匹配逻辑后，用 `python scripts/pipeline.py --rematch` 只重算归属（几秒钟，不重抽帧）。

**人工复核的捷径**：段 PDF 文件名里带讲者署名，是最可靠的归属证据
（比 OCR 标题更稳）。归属存疑时按署名核对，见 pitfalls「吞段」。

## 6. 合并成品

```
python scripts/build_final.py                     # 全量
python scripts/build_final.py 1003                # 单子表
python scripts/build_final.py --session 6742      # 单场
python scripts/build_final.py 1003 --rows 121,122 # 单行/多行
```

局部重做时新结果会合并进已有 `final_report.json`，不会把全量结果冲掉。

按行合并该行所有段（按 t0 排序）→ 全局 pHash 去重 →
输出 `pdf_final/<子表>/<行号>_<标题> - <讲者>.pdf`，并写 `_work/final_report.json`。

成品必须**无条件重建**：按「文件已存在就跳过」会导致重跑后整行丢失。

## 7. 上传 + 回填

```
python scripts/fillsync.py --dry                  # 先看计划
python scripts/fillsync.py                        # 上传并回填
python scripts/fillsync.py --sheet 1003 --rows 121,122   # 只处理指定行
python scripts/fillsync.py --fill-only            # 只回填不上传（复用已有 URL）
```

- 上传走 `lark-cli drive +upload`，链接形如 `https://<tenant>.feishu.cn/file/<token>`
- 单元格用 `rich_text` 超链接（飞书表格不支持真正的附件）
- 幂等：按文件签名（大小+mtime）复用已上传 URL，单元格已有同名链接则跳过
- 目标单元格已有用户手工内容 → 只报告，不动手

## 8. 验收

- 抽样打开 2–3 份成品，确认首尾页完整、无投票页、无黑屏
- `python scripts/selftest.py` 看 0 段行 / 吞段行 / 页数异常 / 对账差异
- 向用户汇报：完成场次数、成品份数、回填行数、失败清单

## 附加技能

| 技能 | 命令 | 说明 |
|---|---|---|
| 磁盘守卫 | `diskguard.py` / `--need-gb N` / `--unblock` | 只读检测、落盘前配额判定、清理熔断标志 |
| 中间产物清理 | `cleanup.py --scan` / `--plan` / `--do --limit 500` / `--auto` | 只清可再生的 parts/video/旧段 PDF；`--auto` 过熔断 + 节流两道闸 |
| 定期自检 | `selftest.py [--section disk/progress/assign/reconcile]` | 只读；可只跑单项 |
| 静默守护 | `monitor_silent.py`（配计划任务 + pythonw） | 后台记日志、停滞告警、低空间自动清理；无窗口无弹窗 |

**弹窗治理**：清理被安全策略拦截时立即熔断（`_work/purge_blocked.flag`），
此后自动清理一律跳过、绝不重试；人工处置后 `diskguard.py --unblock` 解除。
低空间的第一道防线是下载前的配额检查，而不是事后清理。

守护注册示例（需管理员，人工确认后执行）：

```powershell
$A = New-ScheduledTaskAction -Execute "pythonw.exe" `
     -Argument "C:\...\skills\meeting-slide-pdf-pipeline\scripts\monitor_silent.py"
$T = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName "MeetingPipelineMonitor" -Action $A -Trigger $T -Force
```

## 任务编排建议

1. **先单场采样再批量**：先跑 1 场，把 `analysis_sheet.png` 与成品给用户抽检，
   确认版式坐标（slide_box / speaker_box）与切页密度合适后再批量。
2. 改逻辑后先 `--rematch`，确认归属合理再决定是否重抽帧。
3. 长任务用计划任务/后台进程，不要用会话内轮询。
