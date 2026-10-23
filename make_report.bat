@echo off
chcp 65001 >nul
rem ---------------------------------------------------------------
rem  双击入口：依次选择 recipe、log，生成 HTML 报告
rem  解释器查找顺序：环境变量 CVD_PY  >  项目内 .venv  >  PATH 里的 python
rem ---------------------------------------------------------------
set "PY=%CVD_PY%"
if not defined PY if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY set "PY=python"

"%PY%" -X utf8 "%~dp0make_report.py" %*
echo.
pause
