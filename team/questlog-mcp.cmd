@echo off
rem Jotarium (Questlog) MCP server for an agent, run with that agent's token.
rem Usage: questlog-mcp.cmd QUESTLOG_TOKEN_<AGENT>
rem The token is read from the Windows user environment in the registry, so a
rem token rotated after the Herdr app started still reaches new sessions.
rem No config file holds a token (13 Nov rotation, Q-328).
set "QUESTLOG_TOKEN="
for /f "tokens=2,*" %%A in ('reg query HKCU\Environment /v %~1 2^>nul ^| findstr /i "%~1"') do set "QUESTLOG_TOKEN=%%B"
if not defined QUESTLOG_TOKEN (
  echo questlog-mcp: %~1 is not set in the user environment 1>&2
  exit /b 1
)
if not defined QUESTLOG_URL set "QUESTLOG_URL=http://127.0.0.1:4180"
node "%USERPROFILE%\Documents\Projects\todo-rpg\bin\questlog.mjs" mcp
