import os
import sys
from datetime import datetime
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials


import re

def normalize_date_val(val_str):
    """Normaliza cadenas de fecha a formato ISO (YYYY-MM-DD o YYYY-MM-DD HH:MM:SS) para Looker Studio."""
    if not isinstance(val_str, str) or not val_str.strip():
        return ""

    s = re.sub(r"\s*\([A-Z]{3,4}\)", "", val_str.strip()).strip()

    m_dt = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?$", s)
    if m_dt:
        d, m, y, h, mn, sec = m_dt.groups()
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d} {int(h):02d}:{int(mn):02d}:{int(sec or 0):02d}"

    m_d = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m_d:
        d, m, y = m_d.groups()
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"

    return s


def _upload_csv_to_worksheet(sh, csv_path, worksheet_name, worksheet_index):
    """Sube un CSV a una pestaña del Google Sheet. La crea si no existe."""
    if not os.path.exists(csv_path):
        print(f"  ⚠ No se encontró {csv_path}, saltando '{worksheet_name}'.")
        return 0

    print(f"\nCargando datos desde {os.path.basename(csv_path)}...")
    df = pd.read_csv(csv_path)
    df = df.fillna("")

    # Normalizar todas las celdas de fecha a formato ISO para Looker Studio
    for col in df.columns:
        df[col] = df[col].astype(str).apply(normalize_date_val)

    data_to_upload = [df.columns.values.tolist()] + df.values.tolist()

    # Buscar o crear la pestaña
    try:
        worksheet = sh.worksheet(worksheet_name)
        print(f"  Pestaña '{worksheet_name}' encontrada, limpiando...")
        worksheet.clear()
    except gspread.exceptions.WorksheetNotFound:
        print(f"  Creando pestaña '{worksheet_name}'...")
        worksheet = sh.add_worksheet(
            title=worksheet_name,
            rows=max(len(data_to_upload) + 10, 100),
            cols=max(len(data_to_upload[0]) + 5, 30) if data_to_upload else 30,
        )

    worksheet.update(values=data_to_upload, range_name="A1")
    print(f"  ✓ '{worksheet_name}' actualizada con {len(df)} filas.")
    return len(df)


def _upload_timestamp_worksheet(sh, stats, script_dir):
    """Crea o actualiza la pestaña 'Ultima_Actualizacion' con la fecha/hora en zona horaria Ciudad de México (CDMX)."""
    import json
    try:
        from zoneinfo import ZoneInfo
        now_str = datetime.now(ZoneInfo("America/Mexico_City")).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Detectar si la ejecución es desde GitHub Actions o Servidor/Local (IndiprisTI)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        origen = "GitHub Actions"
    else:
        origen = "IndiprisTI"

    # Leer tiempo de ejecución guardado por scraper.py
    meta_path = os.path.join(script_dir, "execution_meta.json")
    duration_str = "N/D"
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
                duration_str = meta.get("duration_formatted", "N/D")
        except Exception:
            pass

    data_to_upload = [
        ["Métrica / Campo", "Valor"],
        ["Última Actualización (CDMX)", now_str],
        ["Origen de Actualización", origen],
        ["Tiempo de Ejecución (Scraper)", duration_str],
        ["Estado del Proceso", "Exitoso"],
    ]

    for name, count in stats.items():
        data_to_upload.append([f"Filas en {name}", count])

    worksheet_name = "Ultima_Actualizacion"
    try:
        worksheet = sh.worksheet(worksheet_name)
        worksheet.clear()
    except gspread.exceptions.WorksheetNotFound:
        worksheet = sh.add_worksheet(title=worksheet_name, rows=20, cols=5)

    worksheet.update(values=data_to_upload, range_name="A1")
    print(f"\n  ✓ Pestaña '{worksheet_name}' actualizada (Origen: {origen}, Tiempo: {duration_str}, Fecha CDMX: {now_str})")


def upload():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    bookings_csv = os.path.join(script_dir, "bookings.csv")
    hotels_csv = os.path.join(script_dir, "hotels.csv")
    transports_csv = os.path.join(script_dir, "transports.csv")
    financial_csv = os.path.join(script_dir, "financial.csv")
    activities_csv = os.path.join(script_dir, "activities.csv")

    if not os.path.exists(bookings_csv):
        print(f"Error: El archivo CSV no existe en {bookings_csv}")
        sys.exit(1)

    import json

    # Configurar credenciales
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]

    print("Autenticando con Google Sheets API...")
    gcp_json_str = os.environ.get("GCP_SERVICE_ACCOUNT_JSON")
    if gcp_json_str:
        print("Cargando credenciales desde la variable de entorno...")
        info = json.loads(gcp_json_str)
        creds = Credentials.from_service_account_info(info, scopes=scopes)
    else:
        creds_path = os.path.join(script_dir, "gcp_key.json")
        if not os.path.exists(creds_path):
            creds_path = "/Users/alejandro/Viajes RM/pivotal-keep-447418-r0-dca467a9d1b3.json"
        print(f"Cargando credenciales locales desde: {creds_path}")
        creds = Credentials.from_service_account_file(creds_path, scopes=scopes)

    client = gspread.authorize(creds)

    # Abrir la hoja de cálculo
    sheet_url = "https://docs.google.com/spreadsheets/d/1uauLtgcOFg0Gp9432TEzVqZ01M8OrulQSb_DWxeNW-M/edit?usp=sharing"
    print(f"Abriendo la hoja de cálculo: {sheet_url}")

    try:
        sh = client.open_by_url(sheet_url)

        # Subir cada CSV a su pestaña
        worksheets = [
            (bookings_csv, "Reservas"),
            (hotels_csv, "Alojamientos"),
            (transports_csv, "Transportes"),
            (financial_csv, "Financiero"),
            (activities_csv, "Actividades"),
        ]

        stats = {}
        for idx, (csv_path, sheet_name) in enumerate(worksheets):
            row_count = _upload_csv_to_worksheet(sh, csv_path, sheet_name, idx)
            stats[sheet_name] = row_count

        # Subir pestaña con fecha, origen y tiempo de ejecución
        _upload_timestamp_worksheet(sh, stats, script_dir)

        print("\n✅ ¡Google Sheets actualizado con éxito!")

    except gspread.exceptions.APIError as e:
        print(f"Error de API: {e}")
        print("\nIMPORTANTE: Por favor asegúrate de que el documento de Google Sheets esté compartido con el correo del bot:")
        print("bot-sheets@pivotal-keep-447418-r0-iam.gserviceaccount.com")
        print("con permisos de Editor.")
    except PermissionError:
        print("\nERROR DE PERMISOS (403): La cuenta de servicio no tiene acceso a esta hoja de cálculo.")
        print("Por favor, ve a tu Google Sheet, haz clic en 'Compartir' y agrega el siguiente correo como EDITOR:")
        print("  bot-sheets@pivotal-keep-447418-r0-iam.gserviceaccount.com")
    except Exception as e:
        print(f"Ocurrió un error inesperado: {e}")


if __name__ == "__main__":
    upload()

