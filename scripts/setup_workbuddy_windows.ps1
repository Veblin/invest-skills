# setup_workbuddy_windows.ps1 — Windows 上重建仓库技能链接（方案 B 落地）
#
# 背景：仓库用 git 符号链接发布技能入口（14 条）。Windows 上 core.symlinks=false
# （默认）会把链接物化为文本文件；即使开开发者模式，clone 静默降级也难排查。
# 本脚本用 NTFS junction（目录，无需管理员/开发者模式）+ 硬链接（文件，同卷无权限）
# 重建全部 14 条入口，幂等可重跑。macOS 零影响（脚本只在 Windows 上跑）。
#
# 用法（clone 后一次性）：
#   git config core.symlinks true        # 可选：避免 git 再物化（需开发者模式）
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#   .\scripts\setup_workbuddy_windows.ps1
#
# 验收（T1-T5 见 host-docs/v0.2.6/Windows_workbuddy_symlink兼容方案_20260812.md §7）：
#   cmd /c dir .workbuddy\skills          # 应显示 <JUNCTION>
#   Get-Content .workbuddy\skills\invest-a-stock\SKILL.md -TotalCount 5
#   Get-Content .claude\commands\invest-a-stock.md -TotalCount 5

$ErrorActionPreference = "Stop"

# 仓库根目录 = 本脚本上级目录
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

# 目录入口：junction（Target 相对仓库根；Junction 目标参数用绝对路径最稳）
$DirLinks = @(
    @{ Name = ".workbuddy\skills\invest-a-stock";    Target = "skills\invest-a-stock" },
    @{ Name = ".workbuddy\skills\invest-a-etf";      Target = "skills\invest-a-etf" },
    @{ Name = ".workbuddy\skills\invest-a-journal";  Target = "skills\invest-a-journal" },
    @{ Name = ".workbuddy\skills\invest-a-pulse";    Target = "skills\invest-a-pulse" },
    @{ Name = ".workbuddy\skills\invest-a-gap-scan"; Target = "skills\invest-a-gap-scan" },
    @{ Name = ".claude\skills\invest-a-stock";    Target = "skills\invest-a-stock" },
    @{ Name = ".claude\skills\invest-a-etf";      Target = "skills\invest-a-etf" },
    @{ Name = ".claude\skills\invest-a-journal";  Target = "skills\invest-a-journal" },
    @{ Name = ".claude\skills\invest-a-gap-scan"; Target = "skills\invest-a-gap-scan" }
)

# 文件入口：硬链接（junction 不支持文件；硬链接同卷免权限，编辑 SKILL.md 两端同步）
$FileLinks = @(
    @{ Name = ".claude\commands\invest-a-stock.md";    Target = "skills\invest-a-stock\SKILL.md" },
    @{ Name = ".claude\commands\invest-a-etf.md";      Target = "skills\invest-a-etf\SKILL.md" },
    @{ Name = ".claude\commands\invest-a-journal.md";  Target = "skills\invest-a-journal\SKILL.md" },
    @{ Name = ".claude\commands\invest-a-pulse.md";    Target = "skills\invest-a-pulse\SKILL.md" },
    @{ Name = ".claude\commands\invest-a-gap-scan.md"; Target = "skills\invest-a-gap-scan\SKILL.md" }
)

Write-Host "== invest:a-stock 技能链接重建（junction + hardlink，幂等）=="

foreach ($l in $DirLinks) {
    $linkPath = Join-Path $RepoRoot $l.Name
    $targetPath = Join-Path $RepoRoot $l.Target

    if (-not (Test-Path -LiteralPath $targetPath -PathType Container)) {
        Write-Warning "跳过 $($l.Name)：目标目录不存在 $($l.Target)"
        continue
    }

    # 已是 junction 则跳过（幂等）；文本文件/真实目录/损坏链接一律先清
    $item = Get-Item -LiteralPath $linkPath -Force -ErrorAction SilentlyContinue
    if ($null -ne $item -and $item.LinkType -eq "Junction") {
        Write-Host "OK(skip): $($l.Name) 已是 <JUNCTION>"
        continue
    }
    if ($null -ne $item) {
        Remove-Item -LiteralPath $linkPath -Force -Recurse
    }
    New-Item -ItemType Junction -Path $linkPath -Target $targetPath | Out-Null
    Write-Host "OK: $($l.Name) -> $($l.Target)"
}

foreach ($l in $FileLinks) {
    $linkPath = Join-Path $RepoRoot $l.Name
    $targetPath = Join-Path $RepoRoot $l.Target

    if (-not (Test-Path -LiteralPath $targetPath -PathType Leaf)) {
        Write-Warning "跳过 $($l.Name)：目标文件不存在 $($l.Target)"
        continue
    }

    # 幂等：已是硬链接（链接数 > 1 且内容与目标一致）则跳过
    $item = Get-Item -LiteralPath $linkPath -Force -ErrorAction SilentlyContinue
    if ($null -ne $item -and $item.LinkType -eq "HardLink") {
        Write-Host "OK(skip): $($l.Name) 已是硬链接"
        continue
    }
    if ($null -ne $item) {
        Remove-Item -LiteralPath $linkPath -Force
    }
    try {
        New-Item -ItemType HardLink -Path $linkPath -Target $targetPath | Out-Null
        Write-Host "OK: $($l.Name) -hardlink-> $($l.Target)"
    }
    catch {
        # FAT/exFAT 等无硬链接支持的文件系统 → 拷贝降级（SKILL.md 修改后需重跑本脚本）
        Copy-Item -LiteralPath $targetPath -Destination $linkPath
        Write-Warning "OK(copy): $($l.Name) 硬链接不可用，已拷贝（修改 SKILL.md 后需重跑）"
    }
}

Write-Host ""
Write-Host "完成。验证：cmd /c dir .workbuddy\skills   # 应显示 <JUNCTION>"
