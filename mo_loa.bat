@echo off
title Xuan CF - Man Hinh Loa (Tu Dong Phat Nhac)
echo ========================================================
echo     DANG MO MAN HINH LOA XUAN CF (AUTO-PLAY NHAC)
echo ========================================================
echo.
echo Dang khoi chay trinh duyet voi che do tu dong phat am thanh 100%%...
start "" "chrome.exe" --autoplay-policy=no-user-gesture-required --app=http://localhost:5000/speaker 2>nul || start "" "msedge.exe" --autoplay-policy=no-user-gesture-required --app=http://localhost:5000/speaker 2>nul || start http://localhost:5000/speaker

