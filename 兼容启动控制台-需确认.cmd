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
echo WARNING: This mode DISABLES the Qt renderer sandbox for this process tree.
echo Use only the trusted bundled local UI. Do not load remote web content.
choice /C YN /N /M "Continue with reduced isolation? [Y/N] "
if errorlevel 2 exit /b 1
set QTWEBENGINE_DISABLE_SANDBOX=1
set QTWEBENGINE_CHROMIUM_FLAGS=
set "PATH=%~dp0runtime\node;%~dp0runtime\python;%SystemRoot%\System32;%SystemRoot%"
"%~dp0runtime\python\python.exe" -X utf8 -B "%~dp0desktop\app.py" --check
if errorlevel 1 goto :fail
start "" "%~dp0runtime\python\pythonw.exe" -X utf8 -B "%~dp0desktop\app.py"
exit /b 0
:fail
echo Portable desktop startup failed. No packages were installed or services restarted.
pause
exit /b 1
