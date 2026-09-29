@echo off
setlocal
set "ROOT=%~dp0.."
set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist "%ROOT%\dist" mkdir "%ROOT%\dist"
"%CSC%" /nologo /target:winexe /out:"%ROOT%\dist\ThermalMonitor.exe" /r:System.Windows.Forms.dll /r:System.Drawing.dll /r:System.Security.dll "%ROOT%\src\Program.cs"
