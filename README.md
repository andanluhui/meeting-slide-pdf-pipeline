# meeting-slide-pdf-pipeline v1.3 — Video to Slides PDF

把**本地视频或直接视频 URL** 提取为幻灯片 PDF，自动去重、保守去黑边、生成缩略图核查页。默认无需会议议程、飞书、JWT 或云端账号。需要时还可手动按章节拆分 PDF、上传至飞书，或使用原有会议批处理/议程自动归属与表格回填功能。

> **当前状态：v1.3 授权网页来源适配版。** 新增谨慎的讲者侧栏自动识别、原帧率场景切换补帧、裁剪预览、逐页 QA 标记和章节候选建议。启发式判断仍可能误检或漏检；重要资料必须检查预览及缩略图。当前新增 HAR 与正常浏览器登录后的媒体请求捕获功能，但不绕过站点认证、DRM 或授权限制。

## 登录后的网页视频：EADV 等会议平台（v1.3）

如果视频必须先登录网站，在开发者工具 Network 中才能看到 `video.mp4` 请求，可以使用**授权网络请求适配器**。不需要把登录密码或 JWT 填进 Skill，也无需修改现有 PDF 引擎。

**方式 1：从浏览器开发者工具导出的 HAR 发现视频（最接近 EADV 历史流程）**：先正常登录、播放视频，在开发者工具 Network 导出本地 HAR；首次只检查域名，不下载：

```bash
python scripts/authenticated_video.py har --har "/private/work/network.har" --dry-run
python scripts/authenticated_video.py har --har "/private/work/network.har" \
  --allow-host media.example.org --out "/private/work/video-jobs"
```

**方式 2：通过独立浏览器正常登录并捕获请求**：只需安装一次可选依赖，然后在启动的浏览器里登录、打开视频页面并点击播放：

```bash
python -m pip install -r scripts/requirements-browser.txt
python -m playwright install chromium
python scripts/authenticated_video.py browser --url "https://portal.example.org/videos" \
  --allow-host media.example.org --out "/private/work/video-jobs"
```

适配器会检查播放请求、去重、下载媒体，再调用通用 PDF 引擎。`--download-only` 只下载；`--pages-file` 可批量访问已有授权页面；`--dry-run` 只检查候选资源。实际视频网站的媒体域名需根据网络请求确认，以上域名均为占位示例。页面里没有观察到 MP4 等直接视频资源时，不会假称下载成功。HLS/DASH/DRM 尚未支持。

**敏感信息提醒：** HAR 可能包含 Cookie、Authorization 和签名 URL；Playwright 专用浏览器资料目录会在本机保存登录状态。两者都必须放在私有工作区，不能分享或提交 GitHub。输出报告不包含原始链接/凭证。完整操作及限制见 [授权网页视频获取指南](references/authenticated-sources.md)。

## 最快使用方式（无需飞书）

需要 **Python 3.10+**。克隆/下载本仓库，在仓库根目录打开命令行：

```bash
python -m pip install -r scripts/requirements-core.txt
python scripts/video_to_pdf.py "/path/to/lecture.mp4"
```

Windows 示例：

```powershell
py -m pip install -r scripts/requirements-core.txt
py scripts/video_to_pdf.py "D:\\Videos\\lecture.mp4" --output "D:\\Videos\\lecture-slides.pdf"
```

可访问的直接视频 URL：

```bash
python scripts/video_to_pdf.py "https://example.org/lecture.mp4" --output "./lecture.pdf"
```

**直接链接**指访问后返回实际视频字节的 HTTP(S) URL，并非一个含播放器的网页。若需要登录、访问受保护资源或播放流媒体，先在有授权的环境中取得本地视频，或后续安装来源适配器。

### 输出结果

| 产物 | 作用 |
|---|---|
| `lecture-slides.pdf` | 按时间顺序保留的候选幻灯片 |
| `lecture-slides-contact.jpg`（必要时附 `-002.jpg` 等） | 全部候选页面的缩略图，检查漏页和重复页；不截断长视频 |
| `lecture-slides-layout.jpg` | 原画面红框标出选中的幻灯片裁剪范围 |
| `lecture-slides.json` | 输入、帧数、布局判断、逐页复核提示、页面时间、章节结果与风险提醒 |

结果默认写入**运行命令时的当前文件夹**，不会修改或删除原始视频。临时解码帧在正常运行结束或失败时清理；`--keep-work` 可以保留以便排错。已有输出默认不覆盖，须明确指定 `--overwrite`。

## 复杂布局和章节

录播中讲者占右侧、幻灯片在左侧时，可指定幻灯片区域（x,y,width,height），四个 0–1 数值为画面比例：

```bash
python scripts/video_to_pdf.py "./lecture.mp4" --crop 0,0,0.78,1 --fps 2
```

不指定 `--crop` 时，默认 `--layout auto`：对多帧有明显分界线的讲者侧栏尝试自动裁剪；其他场景保持完整画面或仅裁黑边。不能保证识别所有画中画、侧栏或动态布局。请检查 `*-layout.jpg` 及缩略图，并在必要时用 `--crop` 重跑。默认固定采样 2 fps，并做额外 FFmpeg 场景切换补帧以找回短暂出现的页面；`--no-scene-boost` 可关闭第二次扫描，`--fps 5` 可提高固定采样密度，均不能保证零漏页。

按已知时间点拆分章节（不需要云端议程表）：

```bash
python scripts/video_to_pdf.py "./conference.mp4" --chapters examples/chapters.example.json
```

`examples/chapters.example.json` 是通用示例，可复制修改。每项包含 `start`（从视频开始算起的秒）与 `title`。生成总 PDF 之外，还生成 `*-slides-chapters/` 目录中的单章节 PDF；没有有效页面的章节会在报告中标记为 `empty`。

## 质量检查与自动章节建议（v1.2）

```bash
# 保留完整原画面，不做自动裁剪
python scripts/video_to_pdf.py "lecture.mp4" --layout full
# 不同的短页采样策略（更耗计算资源）
python scripts/video_to_pdf.py "lecture.mp4" --fps 5 --scene-threshold 0.08
# 基于明显的标题/版式切换提出低置信度章节建议并尝试分段
python scripts/video_to_pdf.py "conference.mp4" --auto-chapters
```

检查 `*-slides.json` 中的 `quality.alerts` 和 `quality.pages[].review_flags`；短页、低细节页、较长的无换页区间会提示人工检查。`*-layout.jpg` 用红框展示实际裁剪区域。对于超过 120 个候选页面的录播，自动生成后续 `*-contact-002.jpg` 等联系表以避免遗漏后半部分。

`--auto-chapters` 只是**视觉启发式建议**，不能识别真实演讲者或主题。如果缺少足够的视觉线索，可能只输出一个章节；不要直接把这种分段当作准确议程使用。有权威议程和时间点时，优先使用 `--chapters`。

完整边界、说明与复核清单见 [v1.2 质量复核指南](references/quality-v12.md)。

## 可选：把 PDF 上传飞书，不回填表格

默认完全在本地运行。只有明确需要分享或归档时再上传。需要单独安装并登录 `lark-cli`，然后在**本机环境变量**（不要放进 Git 仓库）中设置 `LARK_FOLDER_TOKEN` 和 `LARK_FILE_URL_PREFIX`（例如 `https://your-tenant.feishu.cn/file/`）。

```bash
python scripts/upload_pdf.py "./lecture-slides.pdf" --dry-run
python scripts/upload_pdf.py "./lecture-slides.pdf"
```

可用 `--lark-cli` 指定工具的绝对路径；同内容、同目的文件夹默认复用上传记录。链接缓存 `.pdf_uploads.json` 放在本机，不包含在仓库。上传脚本**不修改表格**。

## 已有会议批处理与回填（保留旧功能）

需要依据议程匹配讲者、自动切段、批量上传和飞书表格回填时，沿用旧的专用流程。先安装完整依赖，再准备工作区 `config.json` 和已授权的飞书工具，见 [旧版完整指南](references/legacy-skill-guide.md)。

```bash
python -m pip install -r scripts/requirements.txt
python scripts/pipeline.py --all
python scripts/build_final.py
python scripts/fillsync.py --dry
```

旧流程默认对特定会议版式要求较高；不要把它当成通用入口。

## 测试与兼容性

当前通用入口使用 Python 3.10+、PyMuPDF、Pillow、NumPy、Requests 与 FFmpeg（`imageio-ffmpeg` 自带）。适用于 Windows/macOS/Linux 的常规 Python 运行环境；实际目标机器仍需检查依赖和可访问视频的权限。

```bash
python -m unittest discover -s tests -v
python scripts/video_to_pdf.py --help
python scripts/upload_pdf.py --help
```

测试覆盖本地视频、HTTP 直链、黑边、侧栏/全屏布局判断、短暂幻灯片场景补帧、多张缩略图、章节拆分、拒绝覆盖已有文件、上传 dry-run。完整真实会议视频、不同播放器网站和真实飞书上传仍需在有访问权限的实际环境中验证。

## 安全与隐私

读取 [SECURITY.md](SECURITY.md)。`config.json`、JWT、飞书工作表 ID、个人路径、源视频及输出成果均应存放在私有工作区，不提交至 GitHub。公开发布需额外确认代码产权及视频/会议内容的分享授权。
