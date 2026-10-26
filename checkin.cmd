@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

rem Pick Python: prefer PATH python; fall back to local WANAN env if mitmproxy missing.
rem On another machine, change the fallback path below, or just: set "PY=python"
set "PY=python"
"%PY%" -c "import mitmproxy" >nul 2>&1
if errorlevel 1 set "PY=D:\programs\Miniconda\envs\WANAN\python.exe"

echo ============================================================
echo    Good Night Chengdian - one-click check-in
echo ============================================================
echo.

rem pass all args through to checkin.py (e.g. --dry-run / --no-submit)
"%PY%" checkin.py %*
set "RC=%errorlevel%"

echo.
echo ------------------------------------------------------------
if "%RC%"=="0"    goto ok
if "%RC%"=="1"    goto warn
if "%RC%"=="2"    goto fail
if "%RC%"=="9009" goto nopy
goto unknown

:ok
echo    [OK] Check-in succeeded, or already done today.
goto done

:warn
echo    [WARN] Result uncertain / possibly abnormal. See output above and phone notification.
goto done

:fail
echo    [FAIL] Check-in failed. Check the log above (WeChat not logged in / CA not trusted / timeout).
goto done

:nopy
echo    [ERROR] Python not found (%PY%). Fix the PY setting at the top of this script.
goto done

:unknown
echo    [ERROR] Exit code = %RC%. Send the full output above to the maintainer.
goto done

:done
echo ------------------------------------------------------------
echo.
echo Press any key to close...
pause >nul
