#!/bin/bash
set -o pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"
echo "$(date): Iniciando ejecución local programada..." >> local_runs.log
if [ -d "venv" ]; then
    source venv/bin/activate
fi
export PYTHONUNBUFFERED=1
python3 scraper.py 2>&1 | tee -a local_runs.log
python3 upload_to_sheets.py 2>&1 | tee -a local_runs.log
echo "$(date): Ejecución finalizada." >> local_runs.log

