#!/bin/bash
PROJECT_DIR="/home/aj/abastur_scraper"
cd "$PROJECT_DIR"
echo "$(date): Iniciando ejecución local programada..." >> local_runs.log
source venv/bin/activate
python3 scraper.py >> local_runs.log 2>&1
python3 upload_to_sheets.py >> local_runs.log 2>&1
echo "$(date): Ejecución finalizada." >> local_runs.log
