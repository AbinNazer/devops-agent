@echo off
setlocal EnableExtensions
set "WSL_DESKTOP=\\wsl.localhost\Ubuntu-24.04\home\abin\ai\devops-agent\desktop"
set "LOCAL_DESKTOP=%LOCALAPPDATA%\JARVIS-Desktop"
where wsl.exe >nul 2>&1 || (echo WSL is required.& exit /b 1)
where npm.cmd >nul 2>&1 || (echo Node.js and npm are required.& exit /b 1)
robocopy "%WSL_DESKTOP%" "%LOCAL_DESKTOP%" /E /XD node_modules dist >nul
if errorlevel 8 (echo Failed to copy the desktop frontend.& exit /b 1)
if not exist "%LOCAL_DESKTOP%\node_modules\.bin\vite.cmd" (pushd "%LOCAL_DESKTOP%" && call npm.cmd install && popd)
start "JARVIS Backend" cmd /d /k "cd /d %SystemRoot% && wsl.exe -d Ubuntu-24.04 --cd /home/abin/ai/devops-agent -- /home/abin/ai/devops-agent/venv/bin/uvicorn app.api.app:app --host 127.0.0.1 --port 8001 --reload"
start "JARVIS Frontend" cmd /d /k "cd /d %LOCAL_DESKTOP% && npm.cmd run dev"
echo Backend: http://127.0.0.1:8001/api/health
echo Frontend: http://localhost:5174
endlocal
