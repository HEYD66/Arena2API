@echo off
setlocal
cd /d "%~dp0"
set PYTHONHOME=
set PYTHONPATH=
set PYTHONNOUSERSITE=1
set PYTHONDONTWRITEBYTECODE=1
set PYTHONIOENCODING=utf-8
set QT_API=pyside6
set QT_PLUGIN_PATH=
set QT_QPA_PLATFORM_PLUGIN_PATH=
set QTWEBENGINE_DISABLE_SANDBOX=
set QTWEBENGINE_CHROMIUM_FLAGS=
set "PATH=%~dp0runtime\node;%~dp0runtime\python;%SystemRoot%\System32;%SystemRoot%"
if "%~1"=="" goto :usage
if "%~2"=="" goto :usage
"%~dp0runtime\node\node.exe" "%~dp0desktop\local_cdp_bridge.cjs" --instance "%~1" --cdp "%~2"
exit /b %errorlevel%
:usage
echo Usage: this-script.cmd "YOUR_INSTANCE_NAME" CDP_PORT
echo Prefer Add Bridge in the desktop console. No target is guessed.
exit /b 1
