@echo off
REM wxauto 手动安装辅助脚本（Windows版）
REM 使用方法：
REM 1. 手动从 https://github.com/cluo/wxauto 下载ZIP
REM 2. 将ZIP文件放到此脚本同目录下
REM 3. 运行此脚本：start_wechat_sync.bat

echo 🚀 开始安装wxauto（手动模式）...

REM 查找wxauto ZIP文件
set ZIP_FILE=
for %%f in (wxauto*.zip) do set ZIP_FILE=%%f

if "%ZIP_FILE%"=="" (
    echo ❌ 未找到wxauto ZIP文件
    echo    请先手动下载：https://github.com/cluo/wxauto
    echo    然后将ZIP文件放到此脚本同目录下
    pause
    exit /b 1
)

echo ✅ 找到ZIP文件: %ZIP_FILE%

REM 解压
echo 📦 解压中...
powershell -Command "Expand-Archive -Path '%ZIP_FILE%' -DestinationPath 'wxauto_temp' -Force"

if %ERRORLEVEL% NEQ 0 (
    echo ❌ 解压失败
    pause
    exit /b 1
)

echo ✅ 解压完成

REM 查找解压后的目录
set WXAUTO_DIR=
for /d %%d in (wxauto_temp\wxauto-*) do set WXAUTO_DIR=%%d

if "%WXAUTO_DIR%"=="" (
    echo ❌ 未找到wxauto目录
    pause
    exit /b 1
)

echo ✅ 找到wxauto目录: %WXAUTO_DIR%

REM 安装
echo 📥 安装中...
C:\Users\SGM-AXELD\.workbuddy\binaries\python\versions\3.13.12\python.exe -m pip install "%WXAUTO_DIR%"

if %ERRORLEVEL% NEQ 0 (
    echo ❌ 安装失败
    pause
    exit /b 1
)

echo ✅ wxauto安装成功

REM 清理
echo 🧹 清理临时文件...
rmdir /s /q wxauto_temp

echo 🎉 wxauto手动安装完成！
echo    可以运行 V13_2_WeChatRealtimeSync.py 测试

pause
