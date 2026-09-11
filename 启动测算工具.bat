@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

if not exist "venv\Scripts\streamlit.exe" (
    echo [提示] 尚未安装依赖，请先双击 install.bat 完成首次安装。
    echo.
    pause
    exit /b 1
)

echo ========================================
echo   工商业储充场站测算工具
echo   启动中... 浏览器将自动打开
echo   关闭本窗口即可停止服务
echo ========================================
echo.

start "" "http://localhost:8501"
"venv\Scripts\streamlit.exe" run app.py --server.headless true
