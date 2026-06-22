@echo off
echo ============================================
echo   NOVA Launcher - Build
echo ============================================

pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    pip install pyinstaller
)

if exist "dist\NOVA Launcher" rmdir /s /q "dist\NOVA Launcher"
if exist "build" rmdir /s /q "build"

echo Building...
pyinstaller nova_launcher.spec --clean --noconfirm

if errorlevel 1 (
    echo Build failed. See log above.
    pause
    exit /b 1
)

echo Archiving...
powershell Compress-Archive -Path "dist\NOVA Launcher" -DestinationPath "dist\NOVA_Launcher_build.zip" -Force

echo.
echo ============================================
echo   Done!
echo   Folder: dist\NOVA Launcher\
echo   Archive: dist\NOVA_Launcher_build.zip
echo ============================================
echo.
echo   Share the zip with friends.
echo   They unzip and run "NOVA Launcher.exe"
echo.
echo   Requires: Windows 10/11 with WebView2
echo   If missing: https://go.microsoft.com/fwlink/p/?LinkId=2124703
echo.
pause
