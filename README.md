# meeting-slide-pdf-pipeline v1.1 — Video to Slides PDF

把**本地视频或直接视频 URL** 提取为幻灯片 PDF，自动去重、保守去黑边、生成缩略图核查页。默认无需会议议程、飞书、JWT 或云端账号。需要时还可手动按章节拆分 PDF、上传至飞书，或使用原有会议批处理/议程自动归属与表格回填功能。

> **当前状态：v1.1 通用基础版。** 自动识别使用启发式算法，演讲者小窗、动态背景、渐变和一秒内闪过的页面可能造成误检或漏检；重要资料必须检查缩略图总览。当前没有提供任何视频网页的登录/DRM 绕过功能。

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
| `lecture-slides-contact.jpg` | 缩略图总览，检查漏页、重复页或摄像头画面 |
| `lecture-slides.json` | 输入来源类型、帧数、页数、页起始时间、裁剪范围、章节结果 |

结果默认写入**运行命令时的当前文件夹**，不会修改或删除原始视频。临时解码帧在正常运行结束或失败时清理；`--keep-work` 可以保留以便排错。已有输出默认不覆盖，须明确指定 `--overwrite`。

## 复杂布局和章节

录播中讲者占右侧、幻灯片在左侧时，可指定幻灯片区域（x,y,width,height），四个 0–1 数值为画面比例：

```bash
python scripts/video_to_pdf.py "./lecture.mp4" --crop 0,0,0.78,1 --fps 2
```

不指定 `--crop` 时只会保守识别黑边，不会自动识别所有画中画、侧栏或动态布局；请检查输出的缩略图总览，并在必要时用 `--crop` 重跑。适当提高 `--fps` 能改善短暂页面采样，但会增加计算量。

按已知时间点拆分章节（不需要云端议程表）：

```bash
python scripts/video_to_pdf.py "./conference.mp4" --chapters examples/chapters.example.json
```

`examples/chapters.example.json` 是通用示例，可复制修改。每项包含 `start`（从视频开始算起的秒）与 `title`。生成总 PDF 之外，还生成 `*-slides-chapters/` 目录中的单章节 PDF；没有有效页面的章节会在报告中标记为 `empty`。

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

测试覆盖本地视频、HTTP 直链、黑边、浅色/深色背景、章节拆分、拒绝覆盖已有文件、上传 dry-run。完整真实会议视频、不同播放器网站和真实飞书上传仍需在有访问权限的实际环境中验证。

## 安全与隐私

读取 [SECURITY.md](SECURITY.md)。`config.json`、JWT、飞书工作表 ID、个人路径、源视频及输出成果均应存放在私有工作区，不提交至 GitHub。公开发布需额外确认代码产权及视频/会议内容的分享授权。
