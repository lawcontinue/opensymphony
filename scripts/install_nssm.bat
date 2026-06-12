@echo off
:: install_nssm.bat - Download NSSM and install OpenSymphony as Windows Service
:: Run as Administrator on 5060Ti

set NSSM_DIR=C:\Tools
set NSSM_EXE=%NSSM_DIR%\nssm.exe
set SYMP_DIR=C:\Users\Administrator\symphony
set LOG_DIR=%SYMP_DIR%\logs

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

:: Download NSSM if not exists
if not exist "%NSSM_EXE%" (
    echo [nssm] Downloading...
    if not exist "%NSSM_DIR%" mkdir "%NSSM_DIR%"
    powershell -Command "Invoke-WebRequest -Uri 'https://nssm.cc/release/nssm-2.24.zip' -OutFile '%TEMP%\nssm.zip'"
    powershell -Command "Expand-Archive '%TEMP%\nssm.zip' -DestinationPath '%TEMP%\nssm' -Force"
    copy "%TEMP%\nssm\nssm-2.24\win64\nssm.exe" "%NSSM_EXE%"
    del "%TEMP%\nssm.zip"
    echo [nssm] Download done
)

:: Disable old schtasks
schtasks /change /tn "Symphony" /disable 2>nul

:: Stop old python processes on port 8000
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8000 ^| findstr LISTEN') do taskkill /f /pid %%a 2>nul

:: Install NSSM service
"%NSSM_EXE%" install OpenSymphony "%SYMP_DIR%\scripts\start_svc_nssm.bat"
"%NSSM_EXE%" set OpenSymphony AppDirectory "%SYMP_DIR%"
"%NSSM_EXE%" set OpenSymphony DisplayName OpenSymphony
"%NSSM_EXE%" set OpenSymphony Start SERVICE_AUTO_START
"%NSSM_EXE%" set OpenSymphony AppStdout "%LOG_DIR%\stdout.log"
"%NSSM_EXE%" set OpenSymphony AppStderr "%LOG_DIR%\stderr.log"
"%NSSM_EXE%" set OpenSymphony AppRotateFiles 1
"%NSSM_EXE%" set OpenSymphony AppRotateBytes 10485760

net start OpenSymphony

echo [nssm] Done. OpenSymphony service installed and started.
