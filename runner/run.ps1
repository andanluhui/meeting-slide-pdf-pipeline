<#
  meeting-slide-pdf-pipeline -- 一键运行器（可选组件，任何 agent 都可以不用它）

  作用：把「抽帧 → 切段 → 归属 → 成品 → 自检 → 回填计划」串成一条命令，
        终端只回摘要；每步完整日志落 <Base>\_work\runner-*.log。
        只有失败时才回尾部日志 —— 这样跑流水线时模型几乎不需要读上下文。

  用法：
    .\run.ps1 -Base D:\work\msp -Key <manage_id 或 session 片段>          # 跑单场
    .\run.ps1 -Base D:\work\msp -Key --all                                # 跑底账里全部场次
    .\run.ps1 -Base D:\work\msp -Key <mid> -Steps segment,assign          # 只重算某几步
    .\run.ps1 -Base D:\work\msp -Apply                                    # 回填（真正写入表格；默认只出计划）
  退出码：0 全部成功；非 0 = 第一个失败步骤的退出码（终端已回该步尾部日志）
#>
param(
    [string]$Base = (Get-Location).Path,
    [string]$Key = '--all',
    [string]$Steps = '',
    [switch]$Apply,          # 默认 fillsync 只跑 --dry
    [switch]$NoBuild,        # 跳过 build_final
    [int]$Tail = 15
)
$ErrorActionPreference = 'Stop'
# 子进程（python）输出的是 UTF-8 字节：不改控制台解码，日志与终端里的中文会乱码
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

function Say($msg) { Write-Host $msg }

if (-not (Test-Path $Base)) { throw "工作区不存在: $Base" }
$Base = (Resolve-Path $Base).Path
$skillRoot = Split-Path $PSScriptRoot -Parent

if (-not (Test-Path (Join-Path $Base 'config.json'))) {
    throw "缺少 $Base\config.json —— 请先复制 $skillRoot\scripts\config.example.json 并填写"
}

# 工作区没有 scripts/ 时自动从 skill 目录补一份（skill 文档要求 scripts 与工作区同级可用）
$ws = Join-Path $Base 'scripts'
if (-not (Test-Path $ws)) {
    Say "[init] 工作区缺 scripts/，已从 skill 目录复制"
    Copy-Item (Join-Path $skillRoot 'scripts') -Destination $Base -Recurse -Force
}

function Resolve-Python {
    $c = @()
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $c += $cmd.Source }
    $c += (Get-ChildItem "$env:USERPROFILE\.dsh\dsh-runtimes\*\dependencies\python\python.exe" -ErrorAction SilentlyContinue | ForEach-Object FullName)
    $c += "$env:USERPROFILE\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
    $c += (Get-ChildItem "$env:USERPROFILE\.workbuddy\binaries\python\versions\*\python.exe" -ErrorAction SilentlyContinue | ForEach-Object FullName)
    # 逐个实测：跳过微软商店存根(WindowsApps\python.exe，运行即失败)与损坏的解释器
    foreach ($x in $c) {
        if (-not $x) { continue }
        if ($x -like '*\WindowsApps\*') { continue }
        if (-not (Test-Path $x)) { continue }
        $v = $null
        try { $v = & $x -c "import sys;print(sys.version_info[0])" 2>$null } catch { $v = $null }
        if ($LASTEXITCODE -eq 0 -and "$v".Trim() -match '^\d+$') { return $x }
    }
    throw "找不到可用 python。请装 Python 3.9+，或用 MPDF_PY 指定已装依赖的解释器。"
}
$script:Py = if ($env:MPDF_PY -and (Test-Path $env:MPDF_PY)) { $env:MPDF_PY } else { Resolve-Python }
$env:MSP_BASE = $Base
$env:PYTHONIOENCODING = 'utf-8'

$logDir = Join-Path $Base '_work'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Invoke-Step([string]$Name, [string[]]$StepArgs, [string]$WorkDir) {
    $out = Join-Path $logDir "runner-$Name.log"
    # ⚠ 不要用 Start-Process：Windows PowerShell 5.1 在环境里同时存在
    # HTTP_PROXY / http_proxy（大小写不同的同名变量）时，Start-Process 组装
    # 子进程环境块会直接抛「已添加项。字典中的关键字:「HTTP_PROXY」」并拒绝启动，
    # 实测 100% 失败。改用调用运算符 + `*>` 重定向，同样能拿退出码与完整日志。
    Push-Location $WorkDir
    $prevEap = $ErrorActionPreference
    # 原生命令往 stderr 写一行日志，在 Stop 模式下会被当成终止错误直接炸掉整个脚本
    $ErrorActionPreference = 'Continue'
    try {
        & $script:Py @StepArgs 2>&1 | Out-File -FilePath $out -Encoding UTF8
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEap
        Pop-Location
    }
    if ($code -ne 0) {
        Say "[$Name] 失败 exit=$code  日志: $out"
        Get-Content $out -Tail $Tail -Encoding UTF8 -ErrorAction SilentlyContinue
        exit $code
    }
    return $out
}
function Show-Match($file, [string]$Pattern, [int]$First = 0, [int]$Last = 0, [string]$Prefix = '  ') {
    $m = Get-Content $file -Encoding UTF8 -ErrorAction SilentlyContinue | Select-String -Pattern $Pattern
    if (-not $m) { return }
    if ($First -gt 0) { $m = $m | Select-Object -First $First }
    if ($Last -gt 0) { $m = $m | Select-Object -Last $Last }
    $m | ForEach-Object { Say ($Prefix + $_.Line.Trim()) }
}

Say "== 运行器 ==  base=$Base  key=$Key"

# 1) 主线：下载(可跳过)/抽帧/切段/归属
$pArgs = @((Join-Path $ws 'pipeline.py'), $Key)
if ($Steps) { $pArgs += @('--steps', $Steps) }
$o = Invoke-Step 'pipeline' $pArgs $Base
Show-Match $o '切换点|帧数 |锚点 |归属完成'

# 2) 成品
if (-not $NoBuild) {
    $bArgs = @((Join-Path $ws 'build_final.py'))
    if ($Key -and $Key -ne '--all') { $bArgs += @('--session', $Key) }
    $o = Invoke-Step 'build_final' $bArgs $Base
    Show-Match $o '完成 \d+ 行' -Last 1
}

# 3) 只读自检（只回前几行结论）
$o = Invoke-Step 'selftest' @((Join-Path $ws 'selftest.py')) $Base
Get-Content $o -Encoding UTF8 -ErrorAction SilentlyContinue | Select-Object -First 8 | ForEach-Object { Say ("  " + $_.Trim()) }

# 4) 回填：默认只出计划
if (Test-Path (Join-Path $logDir 'final_report.json')) {
    $fArgs = @((Join-Path $ws 'fillsync.py'))
    if (-not $Apply) { $fArgs += '--dry' }
    $o = Invoke-Step 'fillsync' $fArgs $Base
    Show-Match $o '目标列|\[dry\]|待人工确认|完成：|跳过'
    if (-not $Apply) { Say "  (仅计划；确认无误后加 -Apply 真正写入)" }
}
Say "== 完成 =="
