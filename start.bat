@echo off
rem Double-click to start the Stock Entry page. Close this window to stop the server.
cd /d "%~dp0"
if "%STOCK_ENTRY_PORT%"=="" set STOCK_ENTRY_PORT=8765
rem Open the browser 3 seconds from now, once the server below is listening.
start "" /min cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:%STOCK_ENTRY_PORT%"
".venv\Scripts\python.exe" run.py
pause
