@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

rem ---------------------------------------------------------------
rem 选择 Python 解释器（不写死任何与本机相关的路径）
rem   1) PATH 上的 python，前提是它已经装了 mitmproxy
rem   2) python_path.txt（已 gitignore）：写一行，内容是装了
rem      mitmproxy 的 python.exe 绝对路径
rem 注意：本文件按 UTF-8（无 BOM）保存，靠 chcp 65001 正确显示中文，
rem       请勿另存为 GBK/ANSI，否则窗口里会是乱码。
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
echo [错误] 没找到装了 mitmproxy 的 Python。
echo.
echo   下面两件事做任意一件，然后重新双击本文件：
echo     1) 给 PATH 上的 Python 装依赖：
echo          python -m pip install mitmproxy
echo     2) 或者在本文件旁边新建 python_path.txt，里面写一行：
echo          装了 mitmproxy 的 python.exe 的完整路径
echo.
echo   已经装了却还是报错？先跑一次环境诊断看原因：
echo          python checkin.py --doctor
echo.
pause
exit /b 1

:have_py
echo ============================================================
echo    晚安成电 · 小程序一键打卡
echo ============================================================
echo.

rem 参数原样透传给 checkin.py（如 --dry-run / --no-submit / --doctor）
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
echo    [成功] 打卡已完成，或今天已经打过了。
goto done

:warn
echo    [注意] 结果不确定，甚至可能被判异常。请看上面的输出和手机推送。
goto done

:fail
echo    [失败] 打卡失败。请看上面的输出：微信没登录 / CA 证书未信任 / 等 ticket 超时等。
echo           环境类问题先跑一次：python checkin.py --doctor
goto done

:nopy
echo    [错误] 没找到 Python。请检查 python_path.txt，或看上面的报错。
goto done

:unknown
echo    [错误] 退出码 = %RC%。请把上面的完整输出发给维护者。
goto done

:done
echo ------------------------------------------------------------
echo.
echo 按任意键关闭窗口 ...
pause >nul
