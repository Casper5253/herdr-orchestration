@echo off
rem Questlog MCP server for Devin, run with OpenCode's agent token (creator, 2026-10-31).
rem The token stays in the Windows user environment; no config file holds it.
set "QUESTLOG_TOKEN=%QUESTLOG_TOKEN_OPENCODE%"
if not defined QUESTLOG_URL set "QUESTLOG_URL=http://127.0.0.1:4180"
node "%USERPROFILE%\Documents\Projects\todo-rpg\bin\questlog.mjs" mcp
