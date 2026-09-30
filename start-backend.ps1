# ============================================================
# 抖音火花助手 —— Windows 后端启动脚本
#
# 做四件事：准备好能用的 .venv -> 装依赖 -> 启动 backend.py。
#
# 关于找 Python，这里有两条踩过的坑：
#   1. 不能「判断 .venv 目录存在就跳过创建」：仓库里出现过从 Linux
#      拷过来的 .venv（里面是 bin/python3，没有 Scripts\python.exe）。
#      只看目录存在就会跳过创建，接着去调 .venv\Scripts\python.exe，
#      然后报一堆看不懂的错。所以一律以 Scripts\python.exe 是否真的
#      存在、是否真的能跑起来为准。
#   2. Windows 上 `python` 很可能只是 Microsoft Store 的应用执行别名：
#      Get-Command 查得到它，但它一执行就退出（本机实测退出码 9009），
#      而且 `py` 启动器也未必存在。所以判断「能不能用」必须真的跑一次
#      并检查退出码，光看命令存在是不算数的；同时也得把 uv 装的、以及
#      官方安装包默认位置里的解释器都找一遍。
#      能复用现成的 .venv 时，根本不需要去找基础解释器。
# ============================================================

$ErrorActionPreference = "Stop"

# 本脚本大量使用「试一下这个解释器能不能跑」的探测，原生程序返回非 0 是正常的
# 探测结果，不应该被当成致命异常抛出来（PowerShell 7.3+ 才有这个开关）。
if (Test-Path variable:PSNativeCommandUseErrorActionPreference) {
    $PSNativeCommandUseErrorActionPreference = $false
}

# Windows PowerShell 5.1 的控制台默认按 GBK 解释输出，中文提示会变成乱码 ——
# 而这些提示恰恰是写给「环境已经坏了」的用户看的，乱码等于没提示。
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

function Write-Step { param([string]$Message) Write-Host "[spark-web] $Message" -ForegroundColor Cyan }
function Write-Warn { param([string]$Message) Write-Host "[spark-web] $Message" -ForegroundColor Yellow }
function Write-Err  { param([string]$Message) Write-Host "[spark-web] $Message" -ForegroundColor Red }

# 相对路径全部以脚本所在目录为准，这样从任何目录调用都不会找错文件。
Set-Location -LiteralPath $PSScriptRoot

# ---------- 0. 探测某个解释器能不能用，返回版本号或 $null ----------
function Get-PythonVersion {
    param([string]$Exe, [string[]]$Prefix = @())

    if (-not (Test-Path -LiteralPath $Exe)) {
        if (-not (Get-Command $Exe -ErrorAction SilentlyContinue)) { return $null }
    }

    # 探针代码里绝对不要出现内嵌双引号：Windows PowerShell 5.1 在拼原生命令行时
    # 会把参数里的 " 转义坏掉，python -c 'print("%d.%d" ...)' 到 python 手里就变成
    # 语法错误（本机实测报 File "<string>", line 1）。只用单引号拼字符串就没这事。
    $probeArgs = @($Prefix) + @('-c', 'import sys; print(str(sys.version_info.major)+chr(46)+str(sys.version_info.minor))')

    # 5.1 下 $ErrorActionPreference='Stop' 会把原生命令写 stderr 当成致命错误，
    # 而这里探测的解释器本来就是坏的居多（会往 stderr 吐东西），所以临时放宽。
    $probeErrorAction = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $Exe @probeArgs 2>$null
        $probeCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $probeErrorAction
    }
    if ($probeCode -ne 0 -or -not $output) { return $null }

    foreach ($line in $output) {
        if ("$line".Trim() -match '^(\d+)\.(\d+)') {
            return [version]('{0}.{1}' -f $matches[1], $matches[2])
        }
    }
    return $null
}

# ---------- 1. 现成的 .venv 能直接用就先用它 ----------
$venvDir = Join-Path $PSScriptRoot '.venv'
$venvPython = Join-Path $venvDir 'Scripts\python.exe'

$venvReady = $false
if (Test-Path -LiteralPath $venvPython) {
    $venvVersion = Get-PythonVersion -Exe $venvPython
    if ($venvVersion -and $venvVersion -ge [version]'3.10') {
        Write-Step "复用已有的虚拟环境 .venv（Python $venvVersion）"
        $venvReady = $true
    } else {
        Write-Warn "已有的 .venv 跑不起来或版本低于 3.10（探测结果：$venvVersion），下面会重建。"
    }
} elseif (Test-Path -LiteralPath $venvDir) {
    Write-Warn ".venv 目录存在，但里面没有 Scripts\python.exe —— 这不是 Windows 的虚拟环境（常见于从 Linux 拷过来），下面会重建。"
}

# ---------- 2. 需要时，找一个「真的能执行」的 Python ----------
if (-not $venvReady) {
    $candidates = New-Object System.Collections.ArrayList
    [void]$candidates.Add(@{ Exe = 'py';      Prefix = @('-3'); Label = 'py -3' })
    [void]$candidates.Add(@{ Exe = 'python';  Prefix = @();     Label = 'python' })
    [void]$candidates.Add(@{ Exe = 'python3'; Prefix = @();     Label = 'python3' })

    # uv 自己管着一套解释器，装在 %APPDATA%\uv\python\<名字>\python.exe。
    $uvRoot = Join-Path $env:APPDATA 'uv\python'
    if (Test-Path -LiteralPath $uvRoot) {
        Get-ChildItem -LiteralPath $uvRoot -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object {
                $exe = Join-Path $_.FullName 'python.exe'
                if (Test-Path -LiteralPath $exe) {
                    [void]$candidates.Add(@{ Exe = $exe; Prefix = @(); Label = "uv:$($_.Name)" })
                }
            }
    }

    # 官方安装包的默认位置（用户级安装在前，全局在后）。
    foreach ($pattern in @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python3*\python.exe'),
        'C:\Python3*\python.exe',
        'C:\Program Files\Python3*\python.exe'
    )) {
        Get-Item -Path $pattern -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending |
            ForEach-Object {
                [void]$candidates.Add(@{ Exe = $_.FullName; Prefix = @(); Label = $_.FullName })
            }
    }

    $baseExe = $null
    $basePrefix = @()
    $baseVersion = $null

    foreach ($candidate in $candidates) {
        # 先确认这个候选到底存不存在：绝对路径看文件，命令名看 PATH。
        # 不存在就静默跳过 —— 对根本没装的 py 报「执行失败」是误导。
        $candidateExists = if ($candidate.Exe -match '[\\/]') {
            Test-Path -LiteralPath $candidate.Exe
        } else {
            [bool](Get-Command $candidate.Exe -ErrorAction SilentlyContinue)
        }
        if (-not $candidateExists) { continue }

        $version = Get-PythonVersion -Exe $candidate.Exe -Prefix $candidate.Prefix

        if (-not $version) {
            Write-Warn "跳过 '$($candidate.Label)'：命令在，但执行不起来。这通常是 Microsoft Store 的 Python 占位别名，不是真的解释器。"
            continue
        }
        if ($version -lt [version]'3.10') {
            Write-Warn "跳过 '$($candidate.Label)'：版本 $version 太旧，本项目需要 Python 3.10 及以上。"
            continue
        }

        $baseExe = $candidate.Exe
        $basePrefix = @($candidate.Prefix)
        $baseVersion = $version
        break
    }

    if (-not $baseExe) {
        Write-Err "没有找到可用的 Python（需要 3.10 及以上），已停止。"
        Write-Host ""
        Write-Host "  如果本机只装了 Microsoft Store 的 Python，运行 python 会跳出应用商店，"
        Write-Host "  这种占位别名不能用来跑本项目。装一个真正的 Python，任选一种："
        Write-Host "    A. uv（推荐，装完就能被本脚本自动找到）"
        Write-Host "         winget install --id=astral-sh.uv -e"
        Write-Host "         uv python install 3.12"
        Write-Host "    B. 官方安装包 https://www.python.org/downloads/windows/"
        Write-Host "         安装第一步务必勾选 “Add python.exe to PATH”（默认不勾，漏了就还是找不到）"
        Write-Host ""
        Write-Host "  装完后关掉当前终端、重新开一个，再运行本脚本。"
        exit 1
    }

    Write-Step "使用 Python $baseVersion（$baseExe $($basePrefix -join ' ')）"

    # ---------- 3. 建虚拟环境 ----------
    if (Test-Path -LiteralPath $venvDir) {
        Write-Step "删除无法使用的 .venv ..."
        Remove-Item -Recurse -Force -LiteralPath $venvDir
    }
    Write-Step "正在创建虚拟环境 .venv ..."
    & $baseExe @basePrefix -m venv $venvDir
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Write-Err "虚拟环境创建失败：没有生成 $venvPython"
        exit 1
    }
}

# ---------- 4. 安装依赖 ----------
$requirements = Join-Path $PSScriptRoot 'requirements.txt'
if (-not (Test-Path -LiteralPath $requirements)) {
    Write-Err "找不到 requirements.txt（期望位置：$requirements）"
    exit 1
}

Write-Step "正在安装依赖（首次会比较慢，需要能访问 PyPI）..."

# uv 创建的虚拟环境默认不带 pip，直接调 python -m pip 会报 "No module named pip"。
# ensurepip 是 CPython 自带的、离线可用的引导器，用它把 pip 补上就恢复正常了。
$installErrorAction = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    & $venvPython -m pip --version 2>$null | Out-Null
    $pipCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $installErrorAction
}

if ($pipCode -ne 0) {
    Write-Warn "这个虚拟环境里没有 pip，正在用 ensurepip 补装（离线，不需要网络）..."
    & $venvPython -m ensurepip --upgrade
    if ($LASTEXITCODE -ne 0) {
        Write-Err "补装 pip 失败（退出码 $LASTEXITCODE）。可以改用 uv 手动安装依赖："
        Write-Host "    uv pip install --python `"$venvPython`" -r `"$requirements`""
        exit 1
    }
}

& $venvPython -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) {
    Write-Err "依赖安装失败（退出码 $LASTEXITCODE）。请检查网络或代理后重试。"
    exit 1
}

# ---------- 5. 启动 ----------
if (-not $env:PORT) { $env:PORT = '9844' }

Write-Step "正在启动后端：http://127.0.0.1:$($env:PORT)（面板登录后请到设置页确认连接正常）"
Write-Step "首次运行会弹出 Chrome 窗口，扫码 / 短信验证请在那个窗口里完成；按 Ctrl+C 可退出。"

& $venvPython (Join-Path $PSScriptRoot 'backend.py')
exit $LASTEXITCODE
