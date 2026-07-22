import os
import sys
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

def upload():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    csv_file = os.path.join(script_dir, "bookings.csv")
    if not os.path.exists(csv_file):
        print(f"Error: El archivo CSV no existe en {csv_file}")
        sys.exit(1)
        
    print("Cargando datos desde CSV...")
    df = pd.read_csv(csv_file)
    
    # Reemplazar valores nulos con cadenas vacías para Google Sheets
    df = df.fillna("")
    
    # Preparar datos para subir (cabecera + filas)
    data_to_upload = [df.columns.values.tolist()] + df.values.tolist()
    
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
        creds_path = "/Users/alejandro/Viajes RM/pivotal-keep-447418-r0-dca467a9d1b3.json"
        print(f"Cargando credenciales locales desde: {creds_path}")
        creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
        
    client = gspread.authorize(creds)
    
    # Abrir la hoja de cálculo
    sheet_url = "https://docs.google.com/spreadsheets/d/1uauLtgcOFg0Gp9432TEzVqZ01M8OrulQSb_DWxeNW-M/edit?usp=sharing"
    print(f"Abriendo la hoja de cálculo: {sheet_url}")
    
    try:
        sh = client.open_by_url(sheet_url)
        worksheet = sh.get_worksheet(0) # Abrir la primera pestaña
        
        print("Limpiando hoja actual y subiendo nuevos datos...")
        worksheet.clear()
        
        # Subir los datos en un solo bloque
        worksheet.update(values=data_to_upload, range_name="A1")
        print("¡Google Sheets actualizado con éxito!")
        
    except gspread.exceptions.APIError as e:
        print(f"Error de API: {e}")
        print("\nIMPORTANTE: Por favor asegúrate de que el documento de Google Sheets esté compartido con el correo del bot:")
        print("bot-sheets@pivotal-keep-447418-r0-iam.gserviceaccount.com")
        print("con permisos de Editor.")
    except Exception as e:
        print(f"Ocurrió un error: {e}")

if __name__ == "__main__":
    upload()
