@echo off
rem Build wrapper. All logic lives in build.py so Chinese paths/names stay safe.
rem Usage: build.bat [--no-pause]
python "%~dp0build.py" %*
