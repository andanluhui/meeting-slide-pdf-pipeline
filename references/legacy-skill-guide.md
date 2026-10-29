# 既有会议批量处理模式（保留旧版指引）

此文档对应 v1.0 的专用会议流程。通用视频入口请先阅读根目录 `SKILL.md`。


# 会议录播 → 单场演讲幻灯片 PDF → 表格回填

## 目的

把一场会议录播（可能连续包含十几场演讲）自动切成按演讲归类的幻灯片 PDF，
上传云文档后把链接写回表格中该演讲所在的行，并保证：内容完整不漏页、
归属不错行、重复运行不产生重复劳动。

## 何时使用

- 用户给出会议视频（平台直链 / CDN / 本地文件）+ 日程表，要求按演讲出 PDF
- 要求「把 PDF 链接填回表格对应行」「按演讲主题 + 讲者命名」
- 需要排除黑屏、投票互动页、主席过渡、纯问答片段
- 这类流水线跑起来之后的运维需求：清理已下载视频、定期自检进度与质量

## 快速开始

1. 复制 `scripts/config.example.json` 到工作区根目录 `config.json`，
   填写 `base` / `lark_cli` / `spreadsheet` / `sheets` / `folder_token` / `jwt`。
   大体积中间产物目录 `tmp_root` 必须指向空闲盘，不要放系统盘。
2. 建立底账：`python scripts/make_tasks.py --sheet <子表> --kw <关键词>`，
   然后补齐每场的 `manage_id`（视频 ID）。
3. 先跑一场采样：`python scripts/pipeline.py <场次key>`，
   看 `analysis_sheet.png` 与段 PDF，确认版式坐标与切页密度合适。
4. 批量：`python scripts/pipeline.py --all`
5. 合并成品：`python scripts/build_final.py`
6. 回填：`python scripts/fillsync.py --dry` 先看计划，再去掉 `--dry` 执行
7. 验收：`python scripts/selftest.py`，再抽样打开几份成品

**每个环节都能单独跑**（契约与组合示例见 `references/standalone.md`）：
download / frames / segment / assign 四步用 `--steps` 指定，
`build_final`、`fillsync` 可细到 `--sheet` / `--session` / `--rows`，
`selftest` 可只跑 `--section disk|progress|assign|reconcile`。
改一处逻辑只重跑一处，不要全量重来。

**解释器不用管**：`pipeline.py` / `build_final.py` 启动时会**自动切换到装了依赖的解释器**
（`pybootstrap.py`），所以直接 `python scripts/pipeline.py --all` 就行。
裸 python、系统 python、别的 agent 宿主自带的 python 都能起得来 —— 缺依赖时它自己换，
全都找不到就打印一行 `pip install` 命令并给出退出码 3（**不再是 ImportError 回溯**）。

```bash
python scripts/pybootstrap.py --check        # 诊断：当前解释器 / 缺什么 / 建议用哪个
set MPDF_PY=<python.exe 完整路径>             # 强制指定解释器
python -m venv .venv && .venv\Scripts\pip install -r scripts/requirements.txt   # 全新环境
```

> 依赖清单固定在 `scripts/requirements.txt`（numpy / pymupdf / pillow / requests /
> imageio-ffmpeg / rapidocr-onnxruntime）。**不要再往文档里写死解释器绝对路径** ——
> 换机器或换 agent 宿主目录结构就不同，写死必然失效。

## 硬约束（违反会付出真实代价）

1. **目标列一律按第 1 行表头名定位**（`colmap.pdf_col()`），
   绝不硬编码 `U`/`N`/`Q` 等列字母 —— 用户增删列后字母会整体漂移。
2. **写入必须幂等**：写入前用 `cell_value()` 精确读单元格，已有同名链接就跳过；
   同一份 PDF 按文件签名复用已上传 URL，绝不重复上传。
3. **覆盖前先出清单**：目标单元格有用户手工内容或合并单元格时，只报告不动手。
4. **抽帧用 `fps=1`**，不用关键帧模式；页去重的基准 mask 不累积；
   不丢弃单页段（只有单页且持续 > 5 分钟才判为讨论 / 待机）。
5. **成品无条件重建**，不要「已存在就跳过」；归属逻辑改动先用 `--rematch` 验证。
6. **大文件落空闲盘，系统盘空闲守住 13 GB 以上**（低空间会触发系统反复弹窗）。
7. **巡检走后台**：用计划任务 + `pythonw` 跑 `monitor_silent.py`，
   不要在会话里反复轮询进度与磁盘。
8. **清理绝不重试**：删除被安全策略拦截时立即熔断并停止（`diskguard`），
   「被拦 → 重试 → 再被拦」正是历史上不停弹窗的成因。
   所有脚本都不调用 `schtasks` / `reg` / `sc` / `wmic` / `wsl` 等会弹授权框的命令。

完整流程、判据阈值与命令细节见 `references/workflow.md`；
全部踩坑与成因见 `references/pitfalls.md`（改动脚本前必读）；
飞书读写命令见 `references/feishu.md`。

## 附加技能（均可独立运行）

| 能力 | 命令 | 说明 |
|---|---|---|
| 磁盘守卫 | `diskguard.py` / `--need-gb N` / `--unblock` | 只读检测、落盘前配额判定、清理熔断标志 |
| 中间产物清理 | `cleanup.py --scan` / `--plan` / `--do --limit 500` | 只清可再生的抽帧目录与已下载视频；默认只出清单 |
| 低空间自动清理 | `cleanup.py --auto` | 低于 `min_free_gb` 才执行，且过熔断 + 节流两道闸 |
| 被取代的旧段 PDF | `cleanup.py --superseded --do` | 已合并进成品的段 PDF |
| 只清视频 | `cleanup.py --videos --do` | 最占空间的一项 |
| 定期自检 | `selftest.py [--section disk/progress/assign/reconcile]` | 只读；支持只跑单项 |
| 静默守护 | `monitor_silent.py [--foreground]` | 配计划任务每 30 分钟；写日志、停滞告警、低空间自动清理 |

**弹窗治理（用户明确要求过「巡检别打扰我」）**：弹窗有三个真实来源，
都已在本 skill 里堵死 ——
① Windows 低磁盘告警 → 靠**下载前配额检查**预防，不靠事后清理；
② 批量删除保护 → 靠**熔断**（连续失败即停、写 flag，此后一律不重试）；
③ 提权授权框 → 全程不调用 `schtasks` / `reg` / `sc` / `wmic` / `wsl`。
详见 `references/pitfalls.md` 的「弹窗专项」。

安全边界：清理永不触碰 `pdf/` 与 `pdf_final/`（交付物），
永不删除未完成场次的中间产物，删除用逐文件方式，且被拦即停。

## 脚本索引

| 脚本 | 独立运行入口 |
|---|---|
| `pybootstrap.py` | 解释器自举与诊断：`--check` / `--show` / `--exec`；被 pipeline / build_final 自动调用 |
| `config.py` | 配置加载（工作区 config.json / 环境变量），被所有脚本复用 |
| `fastdl.py` | 多连接分片下载（CDN 支持 Range 时 8 并发 0.58→3 MB/s）；被 download 自动调用，可单跑 |
| `make_tasks.py` | `--sheet <子表> --kw <关键词>` 生成 tasks.json 底账骨架 |
| `pipeline.py` | `--steps download,frames,segment,assign`；`<key>` 单场，`--all` 批量，`--rematch` 只重算归属 |
| `build_final.py` | `[子表]` / `--session <key>` / `--rows a,b` |
| `fillsync.py` | `--dry` / `--sheet` / `--rows` / `--fill-only` |
| `diskguard.py` | 磁盘守卫（附加技能⓪）：状态、配额判定、熔断 |
| `cleanup.py` | 中间产物清理（附加技能①）：`--scan/--plan/--do/--auto/--videos/--superseded` |
| `selftest.py` | 只读自检（附加技能②）：`--section` 只跑单项 |
| `monitor_silent.py` | 静默后台守护（附加技能③）：计划任务 + pythonw |
| `colmap.py` | 按表头名定位列 + 单元格读写（所有写表操作都走它） |

## 归属存疑时的复核捷径

段 PDF 的文件名里带讲者署名，是比 OCR 标题更可靠的证据。
出现「某行页数畸高、后面几行 0 段」时，按署名把段拆回对应行即可，
详见 `references/pitfalls.md` 第 12 条。

## 可选：一键运行器 `runner/`

`runner/` 是**非必需**的自动化包装，删掉不影响本 skill 任何功能：

- `runner/run.ps1`：一条命令跑完 抽帧→切段→归属→成品→自检→回填计划，终端只回摘要，
  完整日志落 `<base>\_work\runner-*.log`，失败才回尾部日志（跑流水线时几乎不消耗模型 token）。
- `runner/install.ps1`：一键装进 DSH / WorkBuddy 的 skill 目录，并可创建 DSH 低思考档
  profile `msp-run`（skill 的 frontmatter 没有 model/effort 字段，档位只能在 profile 层设置）。

用法与卸载见 `runner/README.md`。
