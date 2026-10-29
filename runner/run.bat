@echo off
chcp 65001 >nul
rem 双击运行：把工作区路径和场次 key 作为参数传进来，例如
rem   run.bat -Base D:\work\msp -Key --all
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
echo.
pause
