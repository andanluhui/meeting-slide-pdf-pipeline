# runner/ —— 可选的一键运行器

这个目录**不是 skill 的必需部分**。没有它，`SKILL.md` 的流程照样在任何 agent 里跑。
它的作用是：把重复劳动固定成两条命令，并让"跑流水线"这件事尽量不消耗模型 token。

## 一键安装

```powershell
cd <本 skill 目录>\runner
.\install.ps1                 # 自动检测 DSH / WorkBuddy 并安装
.\install.ps1 -DryRun         # 先看它会做什么，不动任何文件
.\install.ps1 -Target dsh     # 只装 DSH；-NoProfile 则只复制文件夹
.\install.ps1 -Uninstall      # 只删 msp-run profile 与启动器，skill 文件夹保留
```

装完会做两件事：

1. 把整个 skill 文件夹复制进检测到的主机 skill 目录：
   - DSH → `%DSH_HOME%\skills\`（默认 `~\.dsh\skills\`）
   - WorkBuddy → `~\.workbuddy\skills\`
2. 若存在 DSH：复制一份 `desktop`(或 `web`) profile 为 **`msp-run`**，只把
   `reasoningEffort` 改成 `low`，并生成 `runner\launch-msp-run.bat`。
   以后双击它启动的就是"低思考档"会话 —— 跑流水线不用再手动切档。

> 为什么放这里而不是写进 `SKILL.md`：skill 的 frontmatter 没有 model/effort 字段，
> skill 本身**无法**改变会话档位。所以档位放在 profile 层，skill 保持纯净、跨 agent 通用。

## 一键跑

```powershell
# 全流程（抽帧→切段→归属→成品→自检→回填计划），终端只回摘要
.\run.ps1 -Base D:\work\msp -Key --all

# 单场 + 只重算指定步骤
.\run.ps1 -Base D:\work\msp -Key <manage_id> -Steps segment,assign

# 真正写入表格（默认只出 --dry 计划）
.\run.ps1 -Base D:\work\msp -Apply
```

| 参数 | 说明 |
|---|---|
| `-Base` | 工作区（含 `config.json`）；缺 `scripts/` 会自动从 skill 复制 |
| `-Key` | `manage_id` / session 名片段 / `--all` |
| `-Steps` | 透传给 `pipeline.py --steps`（`download,frames,segment,assign` 任选） |
| `-Apply` | `fillsync` 真正写入；不带时只跑 `--dry` |
| `-NoBuild` | 跳过 `build_final` |
| `-Tail` | 失败时回多少行日志（默认 15） |

- 完整日志：`<Base>\_work\runner-*.log`（成功时不进终端）
- 退出码：0 成功；非 0 = 第一个失败步骤的退出码
- 双击运行用 `run.bat`（结束会 `pause`）

## 两个脚本的编码注意
`install.ps1` / `run.ps1` 是 **UTF-8 带 BOM** 保存的：Windows PowerShell 5.1 读取无 BOM 的
UTF-8 脚本会按本地代码页解码，中文会乱码并导致语法错误。若你编辑过这两个脚本，请保持 BOM。

## 改了 `run.ps1` 的话请留意（两个实测踩过的坑）

1. **启动子进程不要用 `Start-Process`**。Windows PowerShell 5.1 组装子进程环境块时
   对变量名大小写不敏感，而本机同时存在 `HTTP_PROXY` 与 `http_proxy` 这类变量时，
   会直接抛 `已添加项。字典中的关键字:「HTTP_PROXY」` 并**拒绝启动任何子进程**。
   现在用 `& $py @args 2>&1 | Out-File -Encoding UTF8`，同样能拿 `$LASTEXITCODE` 与完整日志。
2. **原生命令往 stderr 写日志会触雷**。脚本顶部是 `$ErrorActionPreference='Stop'`，
   在这种模式下 python 的一行 stderr（比如 pybootstrap 的「已切换解释器」提示）
   会被当成**终止错误**炸掉整个脚本 —— 所以 `Invoke-Step` 里临时把它改成 `Continue`，
   用完立刻还原。
3. 中文乱码：子进程输出是 UTF-8 字节，脚本顶部设 `[Console]::OutputEncoding = UTF8`。
`launch-msp-run.bat` 由 `install.ps1` **在本机生成**（内含本机 dsh.cmd 路径），
换机器后重新跑一次 `install.ps1` 即可。

## 换机器 / 换 agent

- **只用 skill 本体**：把 skill 文件夹放进目标 agent 的 skills 目录即可，`runner/` 可删。
- **只想用运行器**：把整个文件夹拷过去，跑 `install.ps1 -NoProfile`，然后直接用 `run.ps1`
  （它自己会找可用的 Python；也可用 `MPDF_PY` 指定）。
- 低档 profile 是 DSH 专有概念，其它主机忽略这一步即可。
