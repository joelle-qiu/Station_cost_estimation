@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

echo ========================================
echo   工商业储充场站测算工具 - 首次安装
echo ========================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python。
    echo.
    echo 请先安装 Python 3.11：
    echo   1. 打开 https://www.python.org/downloads/
    echo   2. 下载并安装 Python 3.11
    echo   3. 安装时务必勾选 "Add python.exe to PATH"
    echo   4. 安装完成后重新双击本文件
    echo.
    pause
    exit /b 1
)

python --version
echo.

if exist "venv\Scripts\python.exe" (
    echo 检测到已有虚拟环境，将重新安装/更新依赖...
) else (
    echo 正在创建虚拟环境 venv ...
    python -m venv venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败。
        pause
        exit /b 1
    )
)

echo.
echo 正在安装依赖（首次约 2-5 分钟，请保持网络畅通）...
"venv\Scripts\python.exe" -m pip install --upgrade pip
"venv\Scripts\pip.exe" install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
    echo.
    echo 使用清华镜像失败，尝试默认源重试...
    "venv\Scripts\pip.exe" install -r requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败，请检查网络后重试。
        pause
        exit /b 1
    )
)

echo.
echo ========================================
echo   安装完成！
echo   请双击「启动测算工具.bat」开始使用
echo ========================================
echo.
pause
