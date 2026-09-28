# ============================================================
# 抖音火花助手 —— Windows 后端启动脚本
#
# 做四件事：找到真的能用的 Python -> 准备 Windows 结构的 .venv
#        -> 装依赖 -> 启动 backend.py。
#
# 为什么不能像以前那样「判断 .venv 目录存在就跳过创建」：
#   仓库里出现过从 Linux 拷过来的 .venv（里面是 bin/python3，
#   没有 Scripts\python.exe）。只看目录存在就会跳过创建，
#   接着去调 .venv\Scripts\python.exe，然后报一堆看不懂的错。
#   所以下面一律以「Scripts\python.exe 是否真的存在」为准。
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

# ---------- 1. 找一个「真的能执行」的 Python ----------
# 顺序：py -3（官方启动器，能挑到最新的 3.x），再退到 python。
# 注意 Windows 上 `python` 很可能只是 Microsoft Store 的应用执行别名：
# Get-Command 查得到它，但它一执行就退出（本机实测退出码 9009），
# 所以必须真的跑一次并检查退出码，光看命令存在是不算数的。
$candidates = @(
    @{ Exe = 'py';     Prefix = @('-3'); Label = 'py -3' },
    @{ Exe = 'python'; Prefix = @();     Label = 'python' }
)

$pythonExe = $null
$pythonPrefix = @()
$pythonVersion = $null

foreach ($candidate in $candidates) {
    if (-not (Get-Command $candidate.Exe -ErrorAction SilentlyContinue)) { continue }

    $probeArgs = @($candidate.Prefix) + @('-c', 'import sys; print("%d.%d" % sys.version_info[:2])')
    $LASTEXITCODE = 0
    $output = & $candidate.Exe @probeArgs 2>$null
    $probeCode = $LASTEXITCODE

    if ($probeCode -ne 0 -or -not $output) {
        Write-Warn "跳过 '$($candidate.Label)'：命令存在但执行失败（退出码 $probeCode）。这通常是 Microsoft Store 的 Python 占位别名，不是真的解释器。"
        continue
    }

    $version = $null
    foreach ($line in $output) {
        $text = "$line".Trim()
        if ($text -match '^(\d+)\.(\d+)') {
            $version = [version]('{0}.{1}' -f $matches[1], $matches[2])
            break
        }
    }
    if (-not $version) {
        Write-Warn "跳过 '$($candidate.Label)'：拿不到版本号（输出：$($output -join ' ')）。"
        continue
    }
    if ($version -lt [version]'3.10') {
        Write-Warn "跳过 '$($candidate.Label)'：版本 $version 太旧，本项目需要 Python 3.10 及以上。"
        continue
    }

    $pythonExe = $candidate.Exe
    $pythonPrefix = @($candidate.Prefix)
    $pythonVersion = $version
    break
}

if (-not $pythonExe) {
    Write-Err "没有找到可用的 Python（需要 3.10 及以上），已停止。"
    Write-Host ""
    Write-Host "  如果本机只装了 Microsoft Store 的 Python，运行 python 会跳出应用商店，"
    Write-Host "  这种占位别名不能用来跑本项目。请按下面步骤安装真正的 Python："
    Write-Host "    1. 打开 https://www.python.org/downloads/windows/ 下载 Python 3.10+ 安装包；"
    Write-Host "    2. 安装第一步务必勾选 “Add python.exe to PATH”（默认是不勾的，漏了就还是找不到）；"
    Write-Host "    3. 装完后关掉当前终端、重新开一个，再运行本脚本。"
    Write-Host ""
    Write-Host "  装好后可以用这条命令确认：py -3 -c ""import sys; print(sys.version)"""
    exit 1
}

Write-Step "使用 Python $pythonVersion（$pythonExe $($pythonPrefix -join ' ')）"

# ---------- 2. 准备虚拟环境 ----------
# 判据是 Scripts\python.exe，不是 .venv 目录本身（见文件头说明）。
$venvDir = Join-Path $PSScriptRoot '.venv'
$venvPython = Join-Path $venvDir 'Scripts\python.exe'

if (Test-Path -LiteralPath $venvDir) {
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Write-Err ".venv 目录存在，但里面没有 Scripts\python.exe —— 这不是 Windows 的虚拟环境，无法使用。"
        Write-Host ""
        Write-Host "  常见原因：这个 .venv 是在 Linux/macOS 上创建后整个拷过来的。"
        Write-Host "  Linux 的 venv 里是 bin/python3 和 lib/python3.x/site-packages，"
        Write-Host "  没有 Scripts\ 目录、也没有 python.exe，Windows 上完全跑不起来。"
        Write-Host ""
        Write-Host "  处理办法（二选一）："
        Write-Host "    A. 删掉后重跑本脚本（脚本会自动重建）："
        Write-Host "         Remove-Item -Recurse -Force .venv"
        Write-Host "    B. 手动重建："
        Write-Host "         py -3 -m venv .venv        # 没有 py 就用：python -m venv .venv"
        exit 1
    }
    Write-Step "复用已有的虚拟环境 .venv"
} else {
    Write-Step "正在创建虚拟环境 .venv ..."
    & $pythonExe @pythonPrefix -m venv $venvDir
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Write-Err "虚拟环境创建失败：没有生成 $venvPython"
        exit 1
    }
}

# ---------- 3. 安装依赖 ----------
$requirements = Join-Path $PSScriptRoot 'requirements.txt'
if (-not (Test-Path -LiteralPath $requirements)) {
    Write-Err "找不到 requirements.txt（期望位置：$requirements）"
    exit 1
}

Write-Step "正在安装依赖（首次会比较慢，需要能访问 PyPI）..."
& $venvPython -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) {
    Write-Err "依赖安装失败（退出码 $LASTEXITCODE）。请检查网络或代理后重试。"
    exit 1
}

# ---------- 4. 启动 ----------
if (-not $env:PORT) { $env:PORT = '9844' }

Write-Step "正在启动后端：http://127.0.0.1:$($env:PORT)（面板登录后请到设置页确认连接正常）"
Write-Step "首次运行会弹出 Chrome 窗口，扫码 / 短信验证请在那个窗口里完成；按 Ctrl+C 可退出。"

& $venvPython (Join-Path $PSScriptRoot 'backend.py')
exit $LASTEXITCODE
