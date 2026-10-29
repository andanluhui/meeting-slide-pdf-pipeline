---
name: meeting-slide-pdf-pipeline
description: 从本地视频文件或可访问的 HTTP(S) 视频直链提取幻灯片/关键画面，智能去重、去黑边并生成 PDF 与核查缩略图；可根据章节时间点拆分多个 PDF。可选把 PDF 上传飞书，或沿用基于会议议程表的自动分段及回填流程。适用于视频转幻灯片 PDF、录屏课件整理、讲座抽帧、会议视频切片、PDF 归档和云文档上传等请求；若无上传授权，默认只处理本地文件。
---

# Video / lecture → slide PDF (v1.1)

## 默认使用：通用、离线优先

**当用户只给出本地视频文件或可访问的 HTTP(S) 视频直链、要求提取 PPT/幻灯片并生成 PDF 时，优先使用 `scripts/video_to_pdf.py`。**
不要求飞书表格、JWT、会议 `manage_id`、议程表或固定的直播画面尺寸。
不要为了执行通用任务而运行旧版 `pipeline.py --all`，也不要自行调用上传脚本。

```bash
# 安装一次通用依赖（Python 3.10+）
python -m pip install -r scripts/requirements-core.txt

# 本地视频 -> 本地 PDF（缺省写到当前目录，默认不覆盖）
python scripts/video_to_pdf.py "./lecture.mp4"

# 用户授权的直接视频 URL（不是播放器网页）
python scripts/video_to_pdf.py "https://example.org/lecture.mp4" --output "./lecture.pdf"

# 录播画面包含讲者小窗时，按需手工裁剪到幻灯片区域：x,y,width,height，0-1 的比例值
python scripts/video_to_pdf.py "./conference.mp4" --crop 0,0,0.78,1 --output "./slides.pdf"

# 用户明确给出章节起始时间时，额外生成分章节 PDF
python scripts/video_to_pdf.py "./conference.mp4" --chapters examples/chapters.example.json
```

输出：`*-slides.pdf`、`*-slides-contact.jpg`（缩略图总览）、`*-slides.json`（页数、起始时间、画面裁剪信息）。
若使用 `--chapters`，额外生成 `*-slides-chapters/` 目录。通用模式不读取或写入飞书。
默认只清理自己临时生成的解码帧，绝不删除源视频，`--keep-work` 可保留中间产物。
输出存在时中止；只有用户明确要求替换才允许 `--overwrite`。

### 通用模式边界

- 只接受本地视频与**用户有权限访问的直接视频文件 URL**。播放器网页、DRM、需登录下载的来源不能保证直接使用，也不应绕过认证或访问控制。
- 抽帧缺省 1 fps，启发式识别“背景相同但内容替换”与渐显；**不是无漏页保证**。用缩略图总览核查，复杂布局/讲者小窗可指定 `--crop`；用户要求高完整性时提高 `--fps` 并人工复核。
- `--crop` 省略时仅保守识别持续黑边；不会自动准确识别复杂画中画幻灯片边框。任意视频（比如纯人物采访）不是幻灯片视频，可能产生较多候选页。
- `--chapters` 需用户提供 JSON 时间点；通用模式不凭空识别讲者身份，也不自动猜测议程。
- 处理视频和上传 PDF 是不同授权：若用户只要求生成 PDF，不要把源视频或 PDF 传到第三方服务。

## 仅在用户明确要求上传时：独立飞书上传

通用模式可以先输出 PDF，再选择性上传到飞书云空间；不需要表格和议程：

```bash
python scripts/upload_pdf.py "./lecture-slides.pdf" --dry-run
# 需要事先安装并登录 lark-cli，配置 LARK_FOLDER_TOKEN / LARK_FILE_URL_PREFIX
python scripts/upload_pdf.py "./lecture-slides.pdf"
```

该脚本用内容 SHA-256 对同一文件+目标文件夹做幂等上传记录，链接在本地 `.pdf_uploads.json` 中缓存，不写表格；`--force` 才重新上传。
不得把飞书 Token、私有视频链接、上传日志、会议文件提交到 GitHub。

## 原有高级功能：会议议程匹配 + 批处理 + 自动回填

原来的 `pipeline.py` / `make_tasks.py` / `build_final.py` / `fillsync.py` / `selftest.py` / `cleanup.py` / `monitor_silent.py` 均保留。
**只有在用户明确要求“按讲者或议程归属、批量处理、上传并回填表格”时才启用。**
旧版完整步骤和硬约束见 [references/legacy-skill-guide.md](references/legacy-skill-guide.md)，分步契约见 [references/standalone.md](references/standalone.md)，踩坑见 [references/pitfalls.md](references/pitfalls.md)。

其中必须遵守：按照第一行**表头**定位 PDF 列；写入前精确检查单元格；已有人工内容不覆盖；重复 PDF 不重复上传；先执行 `fillsync.py --dry`；清理仅限可再生中间产物，禁止清理成品。该模式需本地私密 `config.json` 和已授权的飞书环境。

## 调试、验收与分享

```bash
python -m unittest discover -s tests -v
python scripts/video_to_pdf.py --help
python scripts/upload_pdf.py --help
```

- 使用细节与跨平台安装：`README.md`；安全要求：`SECURITY.md`。
- 复杂布局下执行结果不能仅凭自动页数认定完整，需查看联系表 `*-contact.jpg`。
- 不要将实际视频、表格、密钥、输出 PDF 或本机启动器打包进 Skill。
