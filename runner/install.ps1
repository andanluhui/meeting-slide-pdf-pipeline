<#
  meeting-slide-pdf-pipeline -- 一键安装器（可选组件）

  作用：把这个 skill 文件夹装进检测到的主机 skill 目录，并（可选）为 DSH 创建一个
        「低思考档」profile `msp-run`，再生成一个双击启动器 —— 以后跑流水线不用手动切档。

  用法：
    .\install.ps1                 # 自动检测 DSH / WorkBuddy 并安装；DSH 存在时顺带建 msp-run
    .\install.ps1 -Target dsh     # 只装 DSH
    .\install.ps1 -NoProfile      # 只复制 skill，不建 profile
    .\install.ps1 -DesktopShortcut
    .\install.ps1 -DryRun         # 只打印将要做什么
    .\install.ps1 -Uninstall      # 只删掉 msp-run profile 与启动器（不动 skill 本身）

  说明：skill 本体对任何 agent 通用；`-NoProfile` 时本脚本只是"复制文件夹"。
#>
param(
    [ValidateSet('auto', 'dsh', 'workbuddy', 'both')][string]$Target = 'auto',
    [switch]$NoProfile,
    [switch]$DesktopShortcut,
    [switch]$Uninstall,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'

$src = Split-Path $PSScriptRoot -Parent          # skill 根目录
$skillName = Split-Path $src -Leaf
$dshHome = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE '.dsh' }
$wbHome = Join-Path $env:USERPROFILE '.workbuddy'
$profName = 'msp-run'
$profDir = Join-Path $dshHome "profiles\$profName"
$launcher = Join-Path $PSScriptRoot 'launch-msp-run.bat'

function Say($m) { Write-Host $m }
function Act([string]$desc, [scriptblock]$body) {
    if ($DryRun) { Say "  [dry] $desc" } else { Say "  $desc"; & $body }
}

function Find-DshCli {
    $c = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\DeepSeek Harness\resources\runtime\cli\bin\dsh.cmd'),
        (Join-Path $env:PROGRAMFILES 'DeepSeek Harness\resources\runtime\cli\bin\dsh.cmd')
    )
    $cmd = Get-Command dsh -ErrorAction SilentlyContinue
    if ($cmd) { $c += $cmd.Source }
    foreach ($x in $c) { if ($x -and (Test-Path $x)) { return $x } }
    return $null
}

# ---------------------------------------------------------------- 卸载
if ($Uninstall) {
    Say "== 卸载 $profName（只删 profile 与启动器，skill 文件夹保留）=="
    if (Test-Path $profDir) { Act "删除 profile: $profDir" { Remove-Item $profDir -Recurse -Force } } else { Say "  profile 不存在: $profDir" }
    if (Test-Path $launcher) { Act "删除启动器: $launcher" { Remove-Item $launcher -Force } } else { Say "  启动器不存在" }
    Say "== 完成 =="
    return
}

# ---------------------------------------------------------------- 主机检测
$targets = @()
$dshSkills = Join-Path $dshHome 'skills'
$wbSkills = Join-Path $wbHome 'skills'
switch ($Target) {
    'dsh' { $targets += $dshSkills }
    'workbuddy' { $targets += $wbSkills }
    'both' { $targets += @($dshSkills, $wbSkills) }
    default {
        if (Test-Path $dshHome) { $targets += $dshSkills }
        if (Test-Path $wbHome) { $targets += $wbSkills }
        if (-not $targets) { throw "没检测到 DSH($dshHome) 或 WorkBuddy($wbHome)；请用 -Target 指定。" }
    }
}

Say "== 安装 skill: $skillName =="
Say "  源: $src"
$installedAny = $false
foreach ($root in $targets) {
    $dst = Join-Path $root $skillName
    if ((Test-Path $dst) -and ((Resolve-Path $dst).Path -eq (Resolve-Path $src).Path)) {
        Say "  跳过（源与目标同一目录）: $dst"
        continue
    }
    New-Item -ItemType Directory -Force -Path $root | Out-Null
    $existed = Test-Path $dst
    Act "$(if($existed){'更新'}else{'安装'}): $dst" { Copy-Item (Join-Path $src '*') -Destination $dst -Recurse -Force }
    $installedAny = $true
    # 装完校验文件数一致
    if (-not $DryRun) {
        $n1 = (Get-ChildItem -Recurse -File $src).Count
        $n2 = (Get-ChildItem -Recurse -File $dst).Count
        Say "    校验: 源 $n1 个文件 / 目标 $n2 个文件 $(if($n1 -eq $n2){'OK'}else{'不一致!'})"
    }
}

# ---------------------------------------------------------------- DSH 低思考档 profile
$dshCli = Find-DshCli
if (-not $NoProfile -and (Test-Path $dshHome)) {
    Say "== DSH 低思考档 profile: $profName =="
    $tpl = Join-Path $dshHome 'profiles\desktop'
    if (-not (Test-Path $tpl)) { $tpl = Join-Path $dshHome 'profiles\web' }
    if (-not (Test-Path $tpl)) { Say "  跳过：找不到可复制的模板 profile（desktop / web）" }
    else {
        if (Test-Path $profDir) { Say "  profile 已存在，跳过复制（保留你对 $profName 的自定义）" }
        else { Act "复制模板 $(Split-Path $tpl -Leaf) -> $profDir" { Copy-Item $tpl -Destination $profDir -Recurse -Force } }
        $patch = Join-Path $profDir 'cordis.patch.yml'
        if (-not $DryRun) {
            $txt = [System.IO.File]::ReadAllText($patch)
            $new = [regex]::Replace($txt, '(?m)^(\s*)reasoningEffort:\s*high\s*$', '${1}reasoningEffort: low')
            if ($new -ne $txt) {
                Say "    已将 reasoningEffort: high -> low（$patch）"
            } elseif ($txt -match '(?m)^\s*reasoningEffort:\s*low\s*$') {
                Say "    已是 low，无需改动"
            } else {
                $new = $txt.TrimEnd() + "`n- id: agent-default-model`n  config:`n    reasoningEffort: low`n"
                Say "    警告：模板里没找到 reasoningEffort，已追加覆盖项，请核对 $patch"
            }
            [System.IO.File]::WriteAllText($patch, $new, (New-Object System.Text.UTF8Encoding($false)))
        } else { Say "  [dry] 将把 reasoningEffort 改为 low" }

        if ($dshCli) {
            $bat = "@echo off`r`nchcp 65001 >nul`r`nrem 低思考档启动器（由 install.ps1 生成）`r`n`"$dshCli`" $profName %*`r`n"
            Act "生成启动器: $launcher" { [System.IO.File]::WriteAllText($launcher, $bat, (New-Object System.Text.UTF8Encoding($false))) }
            if ($DesktopShortcut) {
                $dsk = Join-Path ([Environment]::GetFolderPath('Desktop')) "DSH-$profName.bat"
                Act "复制到桌面: $dsk" { Copy-Item $launcher $dsk -Force }
            }
            # 验证 profile 能被正确组合（--dump-config 只打印配置，不启动应用）
            if (-not $DryRun) {
                $log = Join-Path $env:TEMP "msp-run-dumpconfig.log"
                $p = Start-Process -FilePath $dshCli -ArgumentList @($profName, '--dump-config') -RedirectStandardOutput $log -RedirectStandardError "$log.err" -NoNewWindow -Wait -PassThru
                $hit = (Select-String -Path $log -Pattern 'reasoningEffort' -Encoding UTF8 | Select-Object -First 1).Line
                Say "    校验: dsh $profName --dump-config exit=$($p.ExitCode)  生效档位 -> $(if($hit){$hit.Trim()}else{'未找到(见 '+$log+')'})"
            }
        } else {
            Say "  未找到 dsh.cmd，跳过启动器；可手动执行: dsh $profName"
        }
    }
}

Say "== 完成 =="
Say "跑流水线：   powershell -File `"$PSScriptRoot\run.ps1`" -Base 工作区路径"
if (-not $NoProfile -and $dshCli) { Say "低思考档启动： `"$launcher`"  （或 dsh $profName）" }
