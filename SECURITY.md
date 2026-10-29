# Security and privacy

- 本 Skill 默认只在本地处理视频。读取本地视频、下载用户有权访问的 HTTP(S) 直链与上传到云端是**三种不同操作**；上传必须由用户明确提出。
- 不尝试绕过站点登录、视频平台访问控制或 DRM。网页播放器不是直接视频 URL；不支持时改用授权取得的本地视频。
- 不在日志中输出含访问令牌的完整视频 URL；用户自行检查视频文件名（可能含识别信息）。
- 不提交 `config.json`、`_jwt.txt`、`.env`、`.pdf_uploads.json`、飞书表格/云空间 Token、个人账号信息及含凭证的调试日志。
- `runner/launch-msp-run.bat` 在本机生成，可能包含个人绝对路径；不进入版本控制。
- 输出 PDF、预览图、报告和临时帧属于用户内容；勿擅自公开、上传、同步或在代码仓库中提交。
- 生成 PDF 的本地脚本默认不删除输入源、不覆盖现有输出；`--overwrite` 和 `--keep-work` 由用户选择。
- Git 的忽略规则对已跟踪文件无效。若凭证意外发布，应立即撤销/轮换凭证并处理 Git 历史。
- 视频版权、会议内容与幻灯片可能受到访问授权与分发限制；用户需保证有权处理并分享。

## Authenticated source adapter (v1.3)

- HAR exports can include Cookie, Authorization, signed media URLs, and browsing history. Keep HAR files private and never attach/share a raw HAR as proof of a download.
- Browser-assisted mode stores browser-managed login state in a dedicated local persistent profile, which may include login cookies. It is not equivalent to RAM-only storage; erase the profile if no longer needed.
- The downloader keeps request headers in memory and saves only sanitized media IDs, hostnames, and statuses in `media-report.json`. Do not print raw URLs or HAR contents in errors.
- Use explicit `--allow-host` before downloading. Cross-host/port redirects do not inherit captured cookies or Authorization tokens. No access-control/DRM bypass.
- Credentials accidentally exposed in a public repo or shared HAR must be revoked and rotated.

## Authorized pattern-based batch downloads (v1.3.1)

- Start only from a real media request observed while the user played an authorized video. Do not guess video file suffixes, generate unlisted session IDs, or scan a website for private videos.
- Require explicit approval of the video host and a user-provided list of sessions that the user is authorized to access. If credentials are bound to individual sessions, a 401/403 response stops the batch; user must log in again or capture requests normally.
- Treat HAR, private session lists, signed URLs, cookies and browser profiles as sensitive local files. Do not commit or share them.

## Separate browser authorization (all website sources)

- A website login belongs to the browser/session in which it happened. Neither an AI agent nor this Skill silently inherits the user's existing Chrome/Edge login or a cloud browser's session.
- The user must explicitly authorize the browser and execution environment that will capture or download media. When using the Playwright browser mode, sign in inside its new browser window.
- Never request, paste, or transmit account passwords, MFA codes, cookies or JWTs through a public chat. HAR/network exports can contain live credentials; limit their use to a trusted local runtime and never attach them to the README or a public issue.
- Confirm website permissions and the intended session list before batch downloads; separately confirm any upload to Feishu or another cloud provider.
