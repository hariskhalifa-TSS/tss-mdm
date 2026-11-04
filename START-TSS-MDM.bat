@echo off
title TSS MDM server
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-tss-mdm.ps1"
