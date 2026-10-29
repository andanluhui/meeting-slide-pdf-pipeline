<h1 align="center">Video to Slides PDF · 视频转幻灯片 PDF</h1>

<p align="center"><strong>不用对着视频一页页截图：自动找到幻灯片、去掉重复画面、整理成 PDF。</strong></p>
<p align="center">适用于课程录屏、学术讲座、会议回放；访问登录后的视频需要另外授权 AI 使用对应的网站会话。</p>

<p align="center"><a href="README.en.md">English version</a> | <strong>中文版</strong></p>

<p align="center"><img src="assets/readme-showcase.jpg" alt="左侧是一段带讲者画面的测试视频，右侧是提取后的三张幻灯片" width="100%"></p>
<p align="center"><sub>以上是用合成测试视频生成的真实处理结果，不包含任何会议或个人资料。</sub></p>

## 它可以帮你做什么？

| 你手里有什么 | 它可以做什么 |
| --- | --- |
| 电脑里的一段讲座、课程或录屏视频 | 提取幻灯片，去掉大部分重复画面，生成 PDF |
| 很长的会议视频 | 按你提供的时间点拆成多份 PDF，并提供缩略图供检查 |
| 登录后才能看的网页视频 | 在你授权的 AI 浏览器环境中登录并播放后，识别真实视频来源，再下载和整理 |
| 一个会议网站里的许多场视频 | **如果视频地址有固定规律，可用一个已确认的地址加上场次编号，批量下载并生成 PDF** |
| 已经做好的 PDF | 按需上传飞书；也能沿用原来的会议表格回填流程 |

> **重要：** 不是任何网页都能下载。网站可能限制下载，或者每场视频采用不同的链接。Skill 不会绕过登录或版权保护。正式归档前仍要检查是否漏页。

## 重要：AI 需要单独获得网站访问权限

**你已经在自己的浏览器登录，不代表 AI 已经登录。** 无论使用哪一种 AI 工具，只有它实际操作的浏览器或下载程序获得授权后，才能访问受保护的视频。

- **AI 能打开浏览器：** 先让 AI 打开网站；需要登录时，由你在 **AI 正在使用的浏览器** 中完成登录和验证，然后再让它查找视频。
- **使用本地运行的 Skill：** 程序可以打开一个独立浏览器窗口，你需要在这个窗口重新登录。不会自动读取你平时 Chrome 或 Edge 的登录状态。
- **AI 无法打开网页：** 可以提供已经获准下载的本地视频；或在可信任的本地设备导出网络记录，由本地工具分析。网络记录可能包含登录信息，切勿上传到公开聊天或 GitHub。

即使 AI 能访问网页，也不代表所有网站允许下载。批量下载前先确认视频清单，上传云端前另行征得同意。

**还有一点：** 即使 AI 自带的浏览器已经登录，Skill 在电脑上另行启动的浏览器也不一定能使用同一登录状态。要确认**实际负责下载的程序**已经获得网站授权。

## 最容易理解的使用方式：直接告诉你的 AI

如果你已经把这个 Skill 安装到支持运行本地脚本的 AI 工具（例如配置好的 Codex），可以像这样说：

> **整理一个本地视频：**“把这个视频里的 PPT 整理成 PDF，去掉重复页，保留原视频，并给我一份缩略图方便检查。”

> **处理需要登录的网站：** “请用你能操作的浏览器打开这个视频网站。需要登录时，告诉我在那个浏览器中完成登录。确认你能观看视频后，先列出发现的视频让我核对，再下载整理成 PDF。”

> **批量整理多场视频：** “请先在你能访问、我已授权的浏览器中播放一场视频，检查其他场次是否沿用相同地址规律。只处理我提供的场次清单，下载前让我确认。”

这样你不需要自己弄懂下面提到的代码或技术名词。**登录网站以及授权处理内容仍由你完成，AI 负责执行具体步骤。** 只有 Agent 具有运行程序和访问相关文件的能力时，才可以自动完成整套工作。

## 📦 如何分享和安装？

- **分享仓库：** 把本仓库链接发给对方：`https://github.com/andanluhui/meeting-slide-pdf-pipeline`。如果仓库仍是私有的，需要先向仓库维护者申请 GitHub 访问权限。
- **使用 Codex 等支持 Skills 的 AI：** 可以把仓库链接交给它，让它按照 `SKILL.md` 安装。部分客户端也支持 `npx skills add andanluhui/meeting-slide-pdf-pipeline`，但需要已经安装对应工具。
- **手动安装：** 下载仓库 ZIP，解压，把包含 `SKILL.md` 的文件夹放到相应 AI 工具的 Skills 目录；第一次运行还需要安装视频处理依赖。

**注意：** 仅发送 GitHub 链接，不代表普通 ChatGPT 网页聊天就能自动安装 Skill、访问网站或运行电脑上的程序；具体取决于使用者选择的 AI 工具。

## 输出结果长什么样？

一次普通整理会得到：

- **幻灯片 PDF：** 按出现的顺序汇总页面。
- **缩略图总览：** 方便你快速查看是不是漏页或有重复页。
- **裁剪预览：** 看看是否把讲者窗口误当成幻灯片。
- **处理记录：** 标出哪些页面建议人工检查。

默认**保留原视频、不覆盖已有文件、不自动上传**，只有你明确要求时才会上传飞书。

## 如果网站没有“下载”按钮，怎么批量处理？

这是这个 Skill 专门保留和增强的一条路线：

1. 先确认 AI 能访问这个网页。若需要登录，应由用户在 AI 实际使用的浏览器中完成登录；用户自己浏览器已有的登录状态不会自动传给 AI。若 AI 无法浏览网页，则由用户在本地准备视频或安全的网络记录。
2. AI 或浏览器工具找到**这段视频真正的播放文件地址**。举个虚构的例子：`https://example.org/session/100/video.mp4`。
3. 如果另一场的地址只差场次编号，例如 `.../session/101/video.mp4`，就能依据**你提供的、确实有权限访问的场次清单**逐场下载。
4. 下载完自动交给原来的抽帧程序，生成各场次 PDF。

这里不是随便把网页地址末尾改成 `.mp4`：必须先找到一个**实际播放过、确认是视频文件的地址**。如果网站不使用同一种规则，Skill 就返回原来的“逐场捕获视频”办法；如果网站提示没有权限，也不会跳过权限检查继续尝试。

<details>
<summary><strong>展开：自己操作时需要的命令和参数（一般用户可以不看）</strong></summary>

### 准备环境

需要 Python 3.10 或更新版本。下载/克隆仓库后在仓库根目录执行：

```bash
python -m pip install -r scripts/requirements-core.txt
```

### 本地视频 → PDF

```bash
python scripts/video_to_pdf.py "lecture.mp4"
```

也可以给一个直接返回视频文件的 HTTP(S) 链接；播放器网页地址本身并不等于视频文件地址。

### 从已登录网站找出视频

先在浏览器登录并播放视频，使用开发者工具的 **Network（网络请求）** 导出 **HAR** 文件（浏览器保存的网络记录）。HAR 可能含登录信息，**只能保存在私有工作区，不要上传到 GitHub 或发给其他人**。

```bash
# 先看看找到的是什么网站，不下载
python scripts/authenticated_video.py har --har "network.har" --dry-run

# 确认后下载；域名只是假设示例，需替换为实际视频服务器
python scripts/authenticated_video.py har --har "network.har" --allow-host media.example.org --out "video-jobs"
```

不想导出 HAR 时，也可以使用浏览器模式（需要额外安装 Playwright）；参见 [已登录网页视频操作指南](references/authenticated-sources.md)。

### 按确认过的地址规律批量下载（新增）

将你有访问权限的场次编号保存在 `session_ids.txt`，每行一个。例如：

```text
100
101
102
```

在浏览器中**实际播放其中一个**，导出含视频请求的 HAR 文件，然后：

```bash
# 先检查是否识别到规律，不下载
python scripts/batch_by_pattern.py --har "network.har" --ids-file "session_ids.txt" --allow-host media.example.org --dry-run

# 确认后才下载并生成 PDF
python scripts/batch_by_pattern.py --har "network.har" --ids-file "session_ids.txt" --allow-host media.example.org --out "video-jobs"
```

如果同一个网络记录中有多个视频、分不清示例是哪一个，可补充 `--example-id 100`，明确用哪场作为参考。`--download-only` 可只下载不生成 PDF。最多默认处理 300 个由用户明确列出的场次；不自动猜测、遍历其他编号。

**注意：** 这个模式目前经过模拟网站验证，但未在真实 EADV 网站验证。网站如使用每场单独生成的授权链接，替换编号可能失败；出现 401/403（网站拒绝访问）时会停止，要求重新登录或逐场获取。支持普通直接视频文件，不支持破解 DRM 或直接下载 HLS/DASH 播放流。

### 其他功能

- 已知章节起点：`python scripts/video_to_pdf.py "conference.mp4" --chapters examples/chapters.example.json`
- 不裁剪原画面：`python scripts/video_to_pdf.py "lecture.mp4" --layout full`
- 上传飞书前预览：`python scripts/upload_pdf.py "lecture-slides.pdf" --dry-run`（需单独配置已授权的飞书工具）
- 运行测试：`python -m unittest discover -s tests -v`

</details>

## 更多说明（需要时再看）

- [登录后网页视频获取、批量下载及限制](references/authenticated-sources.md)
- [如何检查幻灯片有没有漏页](references/quality-v12.md)
- [原来的会议批量整理与飞书回填](references/legacy-skill-guide.md)
- [安全和隐私注意事项](SECURITY.md)
- [完整 Skill 工作说明](SKILL.md)

*目前仍是私有仓库。公开分享前请确认相关代码和内容拥有公开发布权限。*
