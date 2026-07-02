@echo off
setlocal enabledelayedexpansion

:: --- KONFIGURACE ---
set "WORKDIR=C:\Repositories\ai-stack"
set "LOGDIR=C:\Repositories\ai-stack\logs"
set "DISTRO=Ubuntu"
set "LOGFILE=%LOGDIR%\ai_log_today.txt"

:: --- 1. PRIPRAVA ---
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
echo [%date% %time%] === Startuji AI Stack === >> "%LOGFILE%"

:: --- 2. START DOCKER DEMONA ---
echo [%time%] Startuji Docker... >> "%LOGFILE%"
wsl -d %DISTRO% -u root -e /usr/sbin/service docker start >> "%LOGFILE%" 2>&1

:: --- 3. CEKANI NA DOCKER SOCKET ---
echo [%time%] Cekam na Docker socket... >> "%LOGFILE%"
:docker_loop
wsl -d %DISTRO% -e test -S /var/run/docker.sock
if %errorlevel% neq 0 (
    timeout /t 2 >nul
    goto :docker_loop
)

:: --- 4. START OPENWEBUI COMPOSE ---
echo [%time%] Spoustim OpenWebUI compose... >> "%LOGFILE%"
wsl -d %DISTRO% -u root -e bash -lc "cd /mnt/c/Repositories/ai-stack && docker compose up -d" >> "%LOGFILE%" 2>&1

if %errorlevel% neq 0 (
    echo [CHYBA] Docker compose selhal. >> "%LOGFILE%"
    exit /b 1
)

:: --- 5. START CODEX SANDBOX / GATEWAY ---
echo [%time%] Spoustim Codex sandbox/gateway... >> "%LOGFILE%"
wsl -d %DISTRO% -u root -e bash -lc "/mnt/c/Repositories/ai-stack/codex/bin/start_codex_stack.sh" >> "%LOGFILE%" 2>&1

if %errorlevel% neq 0 (
    echo [CHYBA] Codex sandbox/gateway selhal. >> "%LOGFILE%"
    exit /b 1
)

:: --- 6. ZJISTENI WSL IP ---
echo [%time%] Nastavuji portproxy... >> "%LOGFILE%"
for /f "tokens=1" %%i in ('wsl -d %DISTRO% -e hostname -I') do set "WSL_IP=%%i"

if not defined WSL_IP (
    echo [CHYBA] Nepodarilo se zjistit WSL IP. >> "%LOGFILE%"
    exit /b 1
)

echo [%time%] WSL IP: !WSL_IP! >> "%LOGFILE%"

:: --- 7. PORTPROXY BEZ GLOBALNIHO RESETU ---
netsh interface portproxy delete v4tov4 listenport=9090 listenaddress=0.0.0.0 >nul 2>&1
netsh interface portproxy add v4tov4 listenport=9090 listenaddress=0.0.0.0 connectport=9090 connectaddress=!WSL_IP! >> "%LOGFILE%" 2>&1

netsh interface portproxy delete v4tov4 listenport=9101 listenaddress=0.0.0.0 >nul 2>&1
netsh interface portproxy add v4tov4 listenport=9101 listenaddress=0.0.0.0 connectport=9101 connectaddress=!WSL_IP! >> "%LOGFILE%" 2>&1

:: --- 8. FIREWALL PRAVIDLA PRO LAN ---
netsh advfirewall firewall show rule name="OpenWebUI 9090 LAN" >nul 2>&1
if %errorlevel% neq 0 (
    netsh advfirewall firewall add rule name="OpenWebUI 9090 LAN" dir=in action=allow protocol=TCP localport=9090 remoteip=192.168.0.0/24 >> "%LOGFILE%" 2>&1
)

netsh advfirewall firewall show rule name="Codex Gateway 9101 LAN" >nul 2>&1
if %errorlevel% neq 0 (
    netsh advfirewall firewall add rule name="Codex Gateway 9101 LAN" dir=in action=allow protocol=TCP localport=9101 remoteip=192.168.0.0/24 >> "%LOGFILE%" 2>&1
)

:: --- 9. RYCHLA KONTROLA ---
echo [%time%] Kontroluji sluzby... >> "%LOGFILE%"
curl -sS --connect-timeout 5 http://127.0.0.1:9090/ >nul 2>&1
if %errorlevel% neq 0 (
    echo [VAROVANI] OpenWebUI na 127.0.0.1:9090 neodpoveda. >> "%LOGFILE%"
) else (
    echo [%time%] OpenWebUI OK. >> "%LOGFILE%"
)

curl -sS --connect-timeout 5 http://127.0.0.1:9101/health >nul 2>&1
if %errorlevel% neq 0 (
    echo [VAROVANI] Codex Gateway na 127.0.0.1:9101 neodpovida. >> "%LOGFILE%"
) else (
    echo [%time%] Codex Gateway OK. >> "%LOGFILE%"
)

echo [%time%] === HOTOVO === >> "%LOGFILE%"
exit /b 0
