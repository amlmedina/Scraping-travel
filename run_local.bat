@echo off
cd C:\Users\aj\abastur_scraper
call venv\Scripts\activate.bat
python scraper.py >> local_runs.log 2>&1
python upload_to_sheets.py >> local_runs.log 2>&1
