@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

rem ---------------------------------------------------------------
rem  Pick the Python interpreter (no machine-specific path hardcoded).
rem    1) python on PATH, if it already has mitmproxy
rem    2) python_path.txt (gitignored): one line = full path to a
rem       python.exe that has mitmproxy
rem ---------------------------------------------------------------
set "PY=python"
"%PY%" -c "import mitmproxy" >nul 2>&1
if not errorlevel 1 goto :have_py

set "PY="
if not exist "python_path.txt" goto :no_py
for /f "usebackq delims=" %%p in ("python_path.txt") do set "PY=%%p"
if not defined PY goto :no_py
"%PY%" -c "import mitmproxy" >nul 2>&1
if not errorlevel 1 goto :have_py

:no_py
echo [ERROR] No Python with mitmproxy was found.
echo.
echo   Fix either one, then re-run:
echo     1) Install mitmproxy into your PATH Python:
echo           python -m pip install mitmproxy
echo     2) Or create python_path.txt next to this script, containing
echo        one line: the full path to a Python that has mitmproxy.
echo.
pause
exit /b 1

:have_py
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
echo    [ERROR] Python not found. Check python_path.txt, or see the error above.
goto done

:unknown
echo    [ERROR] Exit code = %RC%. Send the full output above to the maintainer.
goto done

:done
echo ------------------------------------------------------------
echo.
echo Press any key to close...
pause >nul
