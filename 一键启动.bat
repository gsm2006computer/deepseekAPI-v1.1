@echo off
chcp 65001 >nul

set "WORK_DIR=F:\deepseek项目"
set "PYTHON_EXE=C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe"
set "HTML_FILE=F:\deepseek项目\deepseek网页版.html"

rem 1. 清理之前残留的 python.exe / pythonw.exe 进程
taskkill /F /IM python.exe >nul 2>&1
taskkill /F /IM pythonw.exe >nul 2>&1

rem 2. 稍微等一下让端口释放
ping -n 2 127.0.0.1 >nul

rem 3. 通过 PowerShell 隐式启动新的 server.py（不弹黑框），并记录日志方便排错
powershell -WindowStyle Hidden -Command "Start-Process -FilePath '%PYTHON_EXE%' -ArgumentList 'server.py' -WorkingDirectory '%WORK_DIR%' -WindowStyle Hidden -RedirectStandardOutput '%WORK_DIR%\server.out.log' -RedirectStandardError '%WORK_DIR%\server.err.log'"

rem 4. 打开前台网页
start "" "%HTML_FILE%"

exit