@echo off
:: OpenSymphony Windows Service Wrapper for schtasks
:: Updated 2026-06-03: keys moved to .env.bat

cd /d C:\Users\Administrator\symphony_fw

call C:\Users\Administrator\symphony\.env.bat

set PYTHONPATH=C:\Users\Administrator\symphony_fw
set PORT=8000

python run_5060ti.py --host 0.0.0.0 --port %PORT%
