@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo [1/3] 生成图标...
python make_icon.py || goto :error

echo [2/3] 打包 exe...
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "npm源测速" --icon app.ico --add-data "app.ico;." npm_mirror_tool.py || goto :error

echo [3/3] 完成
echo.
echo 输出文件：dist\npm源测速.exe
pause
exit /b 0

:error
echo.
echo 构建失败，请检查上方错误信息（需要 Python 3.9+ 与 PyInstaller）。
pause
exit /b 1
