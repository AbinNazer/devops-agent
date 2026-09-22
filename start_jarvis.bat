@echo off
setlocal
title JARVIS DevOps Agent

rem Start PostgreSQL as WSL root so no sudo password prompt appears.
wsl.exe -u root -- service postgresql start
if errorlevel 1 goto failed

rem Launch the application as the normal WSL user.
wsl.exe -- bash -lc "cd ~/ai/devops-agent && source venv/bin/activate && exec uvicorn app.api.app:app --host 0.0.0.0 --port 8001"
if errorlevel 1 goto failed
goto end

:failed
echo.
echo JARVIS could not start. Check the error above.
pause

:end
endlocal
