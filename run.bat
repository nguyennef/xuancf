@echo off
title Xuan CF - Music & Karaoke Server
color 0b
echo ========================================================
echo         XUAN CF - WEB APPS CHON BAI HAT YOUTUBE
echo ========================================================
echo.
echo Dang khoi dong may chu Flask & WebSocket...
echo * Ban co the chay mo_loa.bat de mo Man hinh Loa tu dong phat am thanh 100%%.
echo.
set PYTHONIOENCODING=utf-8
python app.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [LOI] Khong the khoi dong ung dung. Vui long kiem tra Python va pip install -r requirements.txt
    pause
)

