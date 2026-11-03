$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not $IsWindows) {
    throw "This build script must run on Windows."
}

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$buildRoot = Join-Path $env:TEMP ("dxsync-windows-build-" + [guid]::NewGuid().ToString("N"))
$buildVenv = Join-Path $buildRoot "venv"
$distDirectory = Join-Path $repoRoot "dist"
$zipPath = Join-Path $distDirectory "Docker XServer Sync-Windows-x64.zip"

New-Item -ItemType Directory -Path $buildRoot | Out-Null

try {
    $env:UV_PROJECT_ENVIRONMENT = $buildVenv
    uv sync --python 3.12 --extra dev --extra gui --extra ssh --extra build

    $python = Join-Path $buildVenv "Scripts/python.exe"
    & $python -m pytest

    & $python -m nuitka `
        --standalone `
        --windows-console-mode=disable `
        --product-name="Docker XServer Sync" `
        --product-version="0.1.0" `
        --file-version="0.1.0" `
        --output-filename="Docker XServer Sync.exe" `
        --enable-plugin=pyside6 `
        --include-package=dxsync `
        --assume-yes-for-downloads `
        --output-dir="$buildRoot" `
        --remove-output `
        scripts/app_main.py

    $generatedDirectory = Join-Path $buildRoot "app_main.dist"
    if (-not (Test-Path -LiteralPath $generatedDirectory -PathType Container)) {
        throw "Build completed but the standalone application directory was not found: $generatedDirectory"
    }

    $appDirectory = Join-Path $buildRoot "Docker XServer Sync"
    Move-Item -LiteralPath $generatedDirectory -Destination $appDirectory
    $executable = Join-Path $appDirectory "Docker XServer Sync.exe"
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw "Build completed but the executable was not found: $executable"
    }

    $env:DXSYNC_STATE_DIR = Join-Path $buildRoot "state"
    $smokeTest = Start-Process `
        -FilePath $executable `
        -ArgumentList "--smoke-test" `
        -Wait `
        -PassThru
    if ($smokeTest.ExitCode -ne 0) {
        throw "Compiled application smoke test failed with exit code $($smokeTest.ExitCode)."
    }

    New-Item -ItemType Directory -Force -Path $distDirectory | Out-Null
    if (Test-Path -LiteralPath $zipPath) {
        Remove-Item -LiteralPath $zipPath -Force
    }
    Compress-Archive -LiteralPath $appDirectory -DestinationPath $zipPath -CompressionLevel Optimal

    $archive = Get-Item -LiteralPath $zipPath
    if ($archive.Length -le 0) {
        throw "The Windows distribution ZIP is empty."
    }
    $hash = Get-FileHash -LiteralPath $zipPath -Algorithm SHA256
    Write-Output $archive.FullName
    Write-Output ("SHA256 " + $hash.Hash.ToLowerInvariant())
}
finally {
    Remove-Item Env:UV_PROJECT_ENVIRONMENT -ErrorAction SilentlyContinue
    Remove-Item Env:DXSYNC_STATE_DIR -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $buildRoot) {
        Remove-Item -LiteralPath $buildRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
