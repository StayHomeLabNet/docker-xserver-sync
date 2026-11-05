param(
    [switch]$BuildInstaller
)

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
$installerPath = Join-Path $distDirectory "Docker-XServer-Sync-Setup-v0.1.4.exe"

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
        --product-version="0.1.4" `
        --file-version="0.1.4" `
        --output-filename="Docker XServer Sync.exe" `
        --enable-plugin=pyside6 `
        --include-package=dxsync `
        --include-package=keyring `
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

    if ($BuildInstaller) {
        $isccCandidates = @(
            (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6/ISCC.exe"),
            (Join-Path $env:ProgramFiles "Inno Setup 6/ISCC.exe")
        )
        $iscc = $isccCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
        if (-not $iscc) {
            throw "Inno Setup 6 compiler was not found."
        }
        if (Test-Path -LiteralPath $installerPath) {
            Remove-Item -LiteralPath $installerPath -Force
        }

        $env:DXSYNC_APP_SOURCE = $appDirectory
        $env:DXSYNC_OUTPUT_DIR = $distDirectory
        & $iscc scripts/windows-installer.iss
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
            throw "Windows installer compilation failed."
        }

        $installTestDirectory = Join-Path $buildRoot "installed"
        $installResult = Start-Process `
            -FilePath $installerPath `
            -ArgumentList @(
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/DIR=`"$installTestDirectory`""
            ) `
            -Wait `
            -PassThru
        if ($installResult.ExitCode -ne 0) {
            throw "Silent installer test failed with exit code $($installResult.ExitCode)."
        }

        $installedExecutable = Join-Path $installTestDirectory "Docker XServer Sync.exe"
        if (-not (Test-Path -LiteralPath $installedExecutable -PathType Leaf)) {
            throw "Installed executable was not found: $installedExecutable"
        }
        $env:DXSYNC_STATE_DIR = Join-Path $buildRoot "installed-state"
        $installedSmokeTest = Start-Process `
            -FilePath $installedExecutable `
            -ArgumentList "--smoke-test" `
            -Wait `
            -PassThru
        if ($installedSmokeTest.ExitCode -ne 0) {
            throw "Installed application smoke test failed with exit code $($installedSmokeTest.ExitCode)."
        }

        $uninstaller = Join-Path $installTestDirectory "unins000.exe"
        if (-not (Test-Path -LiteralPath $uninstaller -PathType Leaf)) {
            throw "Uninstaller was not found: $uninstaller"
        }
        $uninstallResult = Start-Process `
            -FilePath $uninstaller `
            -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART") `
            -Wait `
            -PassThru
        if ($uninstallResult.ExitCode -ne 0) {
            throw "Silent uninstall test failed with exit code $($uninstallResult.ExitCode)."
        }

        $installer = Get-Item -LiteralPath $installerPath
        $installerHash = Get-FileHash -LiteralPath $installerPath -Algorithm SHA256
        Write-Output $installer.FullName
        Write-Output ("SHA256 " + $installerHash.Hash.ToLowerInvariant())
    }
}
finally {
    Remove-Item Env:UV_PROJECT_ENVIRONMENT -ErrorAction SilentlyContinue
    Remove-Item Env:DXSYNC_STATE_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:DXSYNC_APP_SOURCE -ErrorAction SilentlyContinue
    Remove-Item Env:DXSYNC_OUTPUT_DIR -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $buildRoot) {
        Remove-Item -LiteralPath $buildRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
