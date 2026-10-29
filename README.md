# 会议录播转幻灯片 PDF（meeting-slide-pdf-pipeline）

本 Skill 用于将一场或多场学术会议录播视频按演讲拆分为独立幻灯片 PDF，依据日程表归属对应讲者，并将 PDF 上传云文档、回填表格。支持分步重跑、幂等回填、自检、磁盘清理和静默巡检。

## 文件结构

- `SKILL.md`：操作流程、约束和脚本索引
- `scripts/`：视频下载、抽帧、切段、归属、生成 PDF、飞书回填、自检与清理的脚本
- `references/`：工作流、运行经验、飞书命令说明
- `runner/`：Windows 可选安装器和一键运行器
- `agents/`、`assets/`：Skill 元数据与图标

## 首次使用

1. 下载本仓库并将 `meeting-slide-pdf-pipeline` 目录放入你的 Skills 目录。
2. 将 `scripts/config.example.json` 复制到**本地工作区根目录**并重命名为 `config.json`。
3. 在本地 `config.json` 中填写 `base`、`lark_cli`、`spreadsheet`、`sheets`、`folder_token`、`file_url_prefix`、`video_url_tpl` 等字段。
4. 将会议视频访问凭证通过环境变量 `MSP_JWT` 或本地 `jwt_file` 提供，**不要把真实 JWT 写进 Git 仓库**。
5. 参考 `SKILL.md` 快速开始，以单场采样和 `fillsync.py --dry` 先确认效果。

依赖见 `scripts/requirements.txt`；可运行 `python scripts/pybootstrap.py --check` 诊断解释器。

## 安全与隐私

仓库中的域名、文件夹标识和表格 ID 均为通用示例，并不提供任何真实会议视频或飞书内容的访问权限。真实视频链接、会议数据、工作区配置、JWT、日志和生成的 PDF 应留在本地或适当授权的环境中。

`.gitignore` 已排除常见的密钥、运行数据和自动生成的本机启动器，但它**不能替代提交前的安全检查**。例如，使用 `git add -A` 后应执行 `git diff --cached --name-only` 并逐项核实。

`runner/launch-msp-run.bat` 是 `runner/install.ps1` 在本机自动生成的文件，故不包含在仓库中。

## 更新说明

本公开分发准备版去除了特定用户的本地绝对路径、实际飞书租户域名、工作表 ID 和会议视频 CDN 路径，并将默认配置改为占位符。处理逻辑未改变；重新部署时须填写各自工作环境的配置。
