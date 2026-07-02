@echo off
setlocal enabledelayedexpansion

:: --- KONFIGURACE ---
set "WORKDIR=C:\Repositories\ai-stack"
set "LOGDIR=C:\Repositories\ai-stack\logs"
set "DISTRO=Ubuntu"
set "LOGFILE=%LOGDIR%\ai_log_today.txt"

:: --- 1. PŘÍPRAVA ---
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
echo [%date% %time%] === Startuji AI Stack (Absolutni cesta) === >> "%LOGFILE%"

:: --- 2. START DOCKER DÉMONA (Absolutní cesta) ---
echo [%time%] Startuji Docker... >> "%LOGFILE%"
:: Používáme přímou cestu k binárce service
wsl -d %DISTRO% -u root -e /usr/sbin/service docker start >> "%LOGFILE%" 2>&1

:: --- 3. ČEKÁNÍ A KONTROLA SOCKETU ---
echo [%time%] Cekam na Docker socket... >> "%LOGFILE%"
:loop
wsl -d %DISTRO% -e test -S /var/run/docker.sock
if %errorlevel% neq 0 (
    timeout /t 2 >nul
    goto :loop
)

:: --- 4. START KONTEJNERŮ ---
echo [%time%] Spoustim kontejnery... >> "%LOGFILE%"
wsl -d %DISTRO% -u root -e bash -c "cd /mnt/c/Repositories/ai-stack && docker compose up -d" >> "%LOGFILE%" 2>&1

if %errorlevel% neq 0 (
    echo [CHYBA] Docker compose selhal. >> "%LOGFILE%"
    exit /b 1
)

:: --- 5. SÍŤ ---
for /f "tokens=1" %%i in ('wsl -d %DISTRO% -e hostname -I') do set "WSL_IP=%%i"
if defined WSL_IP (
    netsh interface portproxy reset >nul
    netsh interface portproxy add v4tov4 listenport=9090 listenaddress=0.0.0.0 connectport=9090 connectaddress=!WSL_IP! >> "%LOGFILE%" 2>&1
)

echo [%time%] === HOTOVO === >> "%LOGFILE%"
exit /b 0