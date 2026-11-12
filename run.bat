@echo off
chcp 65001 >nul
cd /d %~dp0
if not exist .venv (
    echo Dang tao moi truong ao .venv ...
    python -m venv .venv
)
call .venv\Scripts\activate
pip install -q -r requirements.txt
echo.
echo  Mo trinh duyet: http://127.0.0.1:5000
echo  Sinh vien: sv / 123456      Ke toan: ketoan / 123456
echo.
python app.py
pause
