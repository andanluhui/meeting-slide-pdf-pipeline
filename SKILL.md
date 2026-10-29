---
name: meeting-slide-pdf-pipeline
description: 从本地视频文件、可访问的 HTTP(S) 视频直链或经正常登录授权的网页视频请求提取幻灯片/关键画面，智能去重、去黑边并生成 PDF 与核查缩略图；可根据章节时间点拆分多个 PDF。可选把 PDF 上传飞书，或沿用基于会议议程表的自动分段及回填流程。支持从浏览器导出 HAR、用户登录后的网络请求中批量发现媒体并下载（不绕过登录/DRM），再进行 PDF 整理。适用于视频转幻灯片 PDF、录屏课件整理、讲座抽帧、会议视频切片、PDF 归档和云文档上传等请求；若无上传授权，默认只处理本地文件。
---

# Video / lecture → slide PDF (v1.3)

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

内部产物：`*-slides.pdf`、`*-slides-contact.jpg`（超过 120 页可有后续联系表）、`*-slides-layout.jpg`（裁剪预览）、`*-slides.json`（候选页、时间、布局证据与质量复核标记）。默认交付 PDF，其余文件供 Agent 自检使用。
若使用 `--chapters`，额外生成 `*-slides-chapters/` 目录。通用模式不读取或写入飞书。
默认只清理自己临时生成的解码帧，绝不删除源视频，`--keep-work` 可保留中间产物。
输出存在时中止；只有用户明确要求替换才允许 `--overwrite`。

### 通用模式边界

- 只接受本地视频与**用户有权限访问的直接视频文件 URL**。播放器网页、DRM、需登录下载的来源不能保证直接使用，也不应绕过认证或访问控制。
- 默认固定采样 2 fps，另做原视频帧率的场景切换补帧，以减少短暂幻灯片漏检；可通过 `--no-scene-boost` 关闭二次扫描。该策略依然**不能保证零漏页**；重点复核短暂页面、后半段和文字小改动。
- `--crop` 省略时采用保守 `--layout auto`，只在强证据情况下裁掉持续动态的讲者侧栏；其它情况保留完整画面，`--layout blackbars|full` 可关闭高级自动裁剪。纯人物采访或复杂画中画仍可能产生较多候选页。
- `--chapters` 使用权威 JSON 时间点。可选 `--auto-chapters` 只能基于明显的视觉重置生成**低置信度章节建议**，不能凭空识别讲者、主题或议程，结果必须复核。
- 处理视频和上传 PDF 是不同授权：若用户只要求生成 PDF，不要把源视频或 PDF 传到第三方服务。

## Website access and separate agent authorization (mandatory)

- **A user being signed in on their own device/browser does NOT mean the agent is signed in.** Never assume access to the user's Chrome/Edge cookies or to their existing browser window. A GitHub Skill installation does not grant account access.
- First determine whether the executing agent actually has a supported interactive browser and can run the needed local video processing commands. When it does not, do **not** pretend to have visited or downloaded website content. Offer the local-file path or a private, local HAR workflow instead.
- When an authorized website requires sign-in, have the agent open the site in a browser **it can actually access**, and ask the user to perform the normal login/MFA in that exact browser. Never request the user's password, cookie, JWT, or one-time code in chat.
- This Skill's `authenticated_video.py browser` mode launches a **separate Playwright browser profile**. Even if the agent has its own cloud/desktop browser session, its cookies do not automatically transfer to this separate local browser or downloader. The user may have to authorize the browser that the script actually launched. If the agent cannot share the authorized session with the downloader, report the limitation and use an authorized local file instead.
- HAR mode is a fallback for a trusted **local** agent: the user exports the network trace after logging into their *own* browser, and the local script reads it from the local private filesystem. A HAR may contain active session credentials and must not be uploaded to public chat, shared with colleagues, or committed to GitHub. A remote/cloud agent cannot read the user's local HAR unless a separate, deliberately authorized transfer is supported and appropriate.
- Before accessing additional session URLs by a pattern, require an explicit user-provided authorized session list, preview the candidates, and obtain approval before bulk downloading. Playback access is not a blanket license to download; honor the site's restrictions. Uploading to any external service requires separate authorization.

## 授权网页视频：EADV 等需登录的视频网站（v1.3）

仅在用户已对实际运行的浏览器或本地下载环境完成授权后，才使用 `scripts/authenticated_video.py`，不要把登录密码放进命令或 Git。

```bash
# 先在浏览器中正常登录并播放视频，然后导出本地网络 HAR：
python scripts/authenticated_video.py har --har "/private/work/network.har" --dry-run
python scripts/authenticated_video.py har --har "/private/work/network.har" --allow-host media.example.org --out "/private/work/video-jobs"

# 可选：使用专用浏览器会话，通过正常页面登录、播放并捕获网络资源
python -m pip install -r scripts/requirements-browser.txt
python -m playwright install chromium
python scripts/authenticated_video.py browser --url "https://portal.example.org/videos" --allow-host media.example.org --out "/private/work/video-jobs"
```

媒体域名由用户确认并明确允许；HAR 和浏览器 profile 可能包含登录数据，**绝不上传或共享**。脚本只记录脱敏任务结果。遇 401/403 需由用户重新登录并播放，不绕过 DRM 或站点授权。HLS/DASH 目前仅标记不支持。详细步骤见 [references/authenticated-sources.md](references/authenticated-sources.md)。

## Reuse a verified address pattern for authorized sessions (v1.3.1)

用户在 EADV 一类网站中，已登录播放一场视频，且希望根据明确提供的其他场次编号批量下载时，优先检查视频 URL 是否具有相同的路径结构。**不要随意把播放器网页地址后缀改成 .mp4**；必须基于在 HAR 中已观察到的真实媒体请求。

若 `HAR` 中有一个可确认的直接视频文件（例如 `/session/100/video.mp4`），且用户提供具有访问权限的 `session_ids.txt`，可运行：

```bash
# 先预览，不访问视频服务器
python scripts/batch_by_pattern.py --har "network.har" --ids-file "session_ids.txt" --allow-host media.example.org --dry-run
# 用户确认来源和编号后运行，并自动交给原 PDF 处理脚本
python scripts/batch_by_pattern.py --har "network.har" --ids-file "session_ids.txt" --allow-host media.example.org --out "video-jobs"
```

自动按已观察到的 `session/ID` 等路径段推断待替换的编号；若 HAR 中有多个来源，需明确指定 `--example-id`。只能使用用户提供的编号，**禁止枚举、猜测或扫描其他场次**。网站若为每场视频分别签发链接，替换 ID 可能失败；401/403 时停止，不突破网站权限限制，也不访问 DRM 视频。HAR 内可能有登录凭证，绝不共享、上传或记录在日志中。详见 `references/authenticated-sources.md`。

对一般用户仅介绍可完成的任务、需要的授权和确认步骤；不主动讲解网页媒体地址规律、HAR、JWT、Playwright 等内部操作。仅在用户明确追问或必须手动操作时提供有关细节。

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
- **结果自检由执行的 Agent 负责，不默认交给用户。** 生成 PDF 后，Agent 应检查视频是否成功处理、PDF 是否可打开且页数合理；自行核查 `*-layout.jpg` 的裁剪范围、全部 `*-contact*.jpg` 的重复/遗漏迹象，以及 `*-slides.json` 的 `quality.alerts` / `quality.pages[].review_flags`。发现风险时，结合原视频对应时间段复核，能通过调整裁剪、采样密度或去重参数改善的，应自行重跑并复查。质量标记只是检查线索，不能保证零漏页。
- **交付默认只提供用户要求的 PDF（或一组 PDF）**，不要要求用户逐项检查缩略图、裁剪图和处理记录，也不要默认把这些调试文件作为成果介绍。只有用户明确要求或存在确实无法消除的重要问题时，才提供相关核查资料或请求必要协助；如无法完成充分复核，必须明确说明实际限制，不得声称已保证无漏页。
- 质量限制及参数见 [references/quality-v12.md](references/quality-v12.md)。
- 不要将实际视频、表格、密钥、输出 PDF 或本机启动器打包进 Skill。
