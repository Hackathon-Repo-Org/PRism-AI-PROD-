@echo off
rem PRism-AI launcher: works without "pip install -e .".
rem Run  prismai  from this folder, or add this folder to PATH to use it anywhere.
setlocal
set PRISM_ENTRY=prismai
python "%~dp0prism.py" %*
