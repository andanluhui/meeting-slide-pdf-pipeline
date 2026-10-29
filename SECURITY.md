# 安全注意事项

- 严禁提交 `config.json`、`_jwt.txt`、`.env`、飞书文档链接清单、个人账户资料或含访问凭证的日志。
- 真实的 `video_url_tpl`、`file_url_prefix`、`spreadsheet`、`sheets`、`folder_token` 必须在**本地或受控环境**配置。
- 运行器生成的 `runner/launch-msp-run.bat` 含本机路径，不应纳入版本控制。
- 会议音视频、幻灯片及输出 PDF 可能受版权、会议访问权限或合同限制；上传或分享前需获得相应授权。
- Git 忽略规则不会影响已被追踪的文件。若凭证已经被上传至公开仓库，应首先撤销或轮换凭证，再处理 Git 历史。
