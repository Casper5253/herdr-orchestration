@echo off
rem hx launcher: load hx only from the activated runtime pointer, never from the
rem source checkout. -I ignores an inherited PYTHONPATH and the cwd.
setlocal
set "HXROOT=%HX_RUNTIME_ROOT%"
if "%HXROOT%"=="" set "HXROOT=%USERPROFILE%\.hx-runtime"
if not exist "%HXROOT%\current" (
    echo hx: no runtime activated; run hx activate from the source checkout 1>&2
    exit /b 1
)
set /p HXCUR=<"%HXROOT%\current"
if not exist "%HXCUR%\hx\__main__.py" (
    echo hx: activated runtime missing at %HXCUR%; run hx activate --rollback 1>&2
    exit /b 1
)
set "HX_RUNTIME_DIR=%HXCUR%"
"__HX_PYTHON__" -I -c "import os,sys; sys.path.insert(0, os.environ['HX_RUNTIME_DIR']); from hx.__main__ import main; raise SystemExit(main())" %*
exit /b %ERRORLEVEL%
