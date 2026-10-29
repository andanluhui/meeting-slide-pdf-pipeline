# 飞书（lark-cli）用法速查

所有脚本通过 `config.lark_cli` 指向的 `lark-cli.cmd` 调用，输出为 JSON。

## 读

```bash
# 整列纯文本（快，用于幂等判断；单元格内换行会导致行错位）
lark-cli sheets +csv-get --spreadsheet-token <ss> --sheet-id <sid> \
    --range A1:A600 --include-row-prefix=false --max-chars 4000000

# 精确读单个单元格（判断"是否已有内容"必须用这个）
lark-cli sheets +cells-get --spreadsheet-token <ss> --sheet-id <sid> --range N121
```

## 写

cells 内容需要先写成 JSON 文件，再用 `@文件路径` 传入：

```json
[[{"rich_text": [{"type": "link", "text": "文件名.pdf", "link": "https://xxx.feishu.cn/file/<token>"}],
   "cell_styles": {"word_wrap": "auto-wrap", "vertical_alignment": "top"}}]]
```

```bash
lark-cli sheets +cells-set --spreadsheet-token <ss> --sheet-id <sid> \
    --range N121 --cells @cells.json --format json

# 取消合并（合并单元格写不进内容）
lark-cli sheets +cells-unmerge --spreadsheet-token <ss> --sheet-id <sid> --range N272:N275

# 清空区域（保留格式与列）
lark-cli sheets +cells-clear --spreadsheet-token <ss> --sheet-id <sid> --range U1:U400 --yes
```

## 云盘

```bash
lark-cli drive +upload --file 本地.pdf --name 显示名.pdf \
    --folder-token <folder> --format json
# 返回 data.file_token，拼成 https://<tenant>.feishu.cn/file/<token>
```

## 关键约束

- 表格单元格**不支持附件**，只能用 `rich_text` 超链接指向云文档。
- 写入前必须读一次目标单元格做幂等判断，否则会重复上传 + 重复写入。
- 列位置一律按第 1 行表头名解析（见 `colmap.py`），1 小时缓存；
  表格结构变动后用 `--refresh` 或清 `_work/colmap_cache.json`。
- 合并单元格必须先 unmerge；覆盖用户手工内容前要先出清单确认。
