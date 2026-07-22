@echo off
setlocal EnableExtensions
rem OpenSquawk Bridge - Windows launcher (self-updating).
rem
rem A single downloadable file. On first run it installs `uv` (Astral's static
rem Python manager), fetches the latest source from GitHub, builds an isolated
rem environment, and starts the app. Every launch checks GitHub and updates
rem itself, so you always run the latest version. Nothing is signed.
rem
rem bootstrap.py is embedded (base64) after the marker at the bottom and is
rem written out on each launch; the launcher itself carries no app code.

set "APP=OpenSquawk Bridge"
set "DATA=%LOCALAPPDATA%\%APP%"
set "BIN=%DATA%\bin"
set "UV=%BIN%\uv.exe"
set "BOOT=%DATA%\bootstrap.py"

if not exist "%DATA%" mkdir "%DATA%" >nul 2>&1
if not exist "%BIN%" mkdir "%BIN%" >nul 2>&1

if not exist "%UV%" (
    echo Setting up %APP% - first launch may take a minute...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$env:UV_INSTALL_DIR='%BIN%'; $env:UV_NO_MODIFY_PATH='1'; $env:INSTALLER_NO_MODIFY_PATH='1'; try { irm https://astral.sh/uv/install.ps1 | iex } catch { exit 1 }"
)

if not exist "%UV%" (
    echo Setup failed. Check your internet connection and try again.
    pause
    exit /b 1
)

rem Extract the embedded base64 bootstrap.py (everything after the marker line).
rem (the marker also appears in this command, so take the LAST match = the data).
powershell -NoProfile -ExecutionPolicy Bypass -Command "$l=Get-Content -LiteralPath '%~f0'; $i=($l | Select-String -SimpleMatch '@@BOOTSTRAP_B64@@' | Select-Object -Last 1).LineNumber; $b=($l[$i..($l.Count-1)]) -join ''; [IO.File]::WriteAllBytes('%BOOT%',[Convert]::FromBase64String($b.Trim()))"

set "UV_INSTALL_DIR=%BIN%"
"%UV%" run --python 3.12 --no-project "%BOOT%"

endlocal
exit /b 0

@@BOOTSTRAP_B64@@
@BASE64@
