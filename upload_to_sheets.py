import warnings
warnings.filterwarnings("ignore")

import os
import sys
import json
import uuid
import platform
import re
from datetime import datetime
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials


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


def _get_cdmx_now_str():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Mexico_City")).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _get_historical_counts(sh, script_dir, worksheets):
    """Obtiene los conteos previos tanto de archivo local como de las pestañas en vivo de Google Sheets."""
    historical = {sheet_name: 0 for _, sheet_name in worksheets}

    # 1. Leer de cache local si existe
    local_state_path = os.path.join(script_dir, "last_successful_sync.json")
    if os.path.exists(local_state_path):
        try:
            with open(local_state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in data.get("counts", {}).items():
                    if k in historical:
                        historical[k] = max(historical[k], int(v))
        except Exception as e:
            print(f"  ⚠ Aviso: No se pudo leer {local_state_path}: {e}")

    # 2. Consultar directamente a Google Sheets para asegurar que no disminuyan registros en vivo
    for _, sheet_name in worksheets:
        try:
            ws = sh.worksheet(sheet_name)
            # col_values(1) devuelve todos los valores de la columna A (incluye cabecera)
            vals = ws.col_values(1)
            live_count = max(0, len(vals) - 1)
            historical[sheet_name] = max(historical[sheet_name], live_count)
        except gspread.exceptions.WorksheetNotFound:
            pass
        except Exception as e:
            print(f"  ⚠ Aviso consultando hoja en vivo '{sheet_name}': {e}")

    return historical


def _generate_incident_report(script_dir, comparisons, regressions, stats, meta, origen):
    """Genera un reporte de incidente exhaustivo en JSON y Markdown estructurado para análisis de IA."""
    incident_id = f"INC-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    now_cdmx = _get_cdmx_now_str()
    now_utc = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    # Diagnóstico de posibles causas raíz mediante heurística estructurada
    suspected_causes = []
    if "Reservas" in regressions:
        res_prev = comparisons["Reservas"]["previous_count"]
        res_curr = comparisons["Reservas"]["new_count"]
        if res_curr <= 20 and res_prev > 20:
            suspected_causes.append({
                "category": "PAGINATION_FAILURE",
                "confidence": "HIGH",
                "description": f"El conteo de reservas cayó abruptamente a {res_curr} (de {res_prev}). Esto coincide típicamente con una falla en la detección del botón de paginación (.ui-paginator-next) o fallo en el selector de filas por página (150 rpp), extrayendo solo la primera página de la tabla."
            })
        elif res_curr == 0:
            suspected_causes.append({
                "category": "AUTHENTICATION_OR_FILTER_FAILURE",
                "confidence": "HIGH",
                "description": "Se extrajeron 0 reservas. Es altamente probable que haya fallado el login en Abastur, la sesión haya expirado, o los filtros de fecha ('creationDateFrom') no hayan devuelto resultados."
            })
        else:
            suspected_causes.append({
                "category": "PARTIAL_SCRAPE_OR_TIMEOUT",
                "confidence": "MEDIUM",
                "description": f"Se extrajeron {res_curr} reservas vs {res_prev} esperadas. El scraper pudo haber sufrido un corte de conexión o timeout durante la paginación."
            })

    for table in ["Alojamientos", "Financiero", "Transportes", "Actividades"]:
        if table in regressions and "Reservas" not in regressions:
            suspected_causes.append({
                "category": "DETAIL_TABS_SCRAPING_REGRESSION",
                "confidence": "HIGH",
                "description": f"La tabla principal 'Reservas' se mantuvo, pero '{table}' perdió registros (de {comparisons[table]['previous_count']} a {comparisons[table]['new_count']}). Esto sugiere que la pestaña correspondiente en la vista de detalle de reservas (MPFEditForm:tabView) falló al abrirse, tardó en responder AJAX o su selector cambió en el DOM."
            })

    # Leer las últimas líneas del log local para contexto de IA
    recent_logs = []
    log_path = os.path.join(script_dir, "local_runs.log")
    if os.path.exists(log_path):
        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as lf:
                lines = lf.readlines()
                recent_logs = [line.strip() for line in lines[-50:]]
        except Exception:
            pass

    report_data = {
        "incident_id": incident_id,
        "status": "CRITICAL_REGRESSION_DETECTED",
        "decision": "SYNC_ABORTED",
        "explanation": "El modelo de datos es estrictamente incremental. Se detectaron tablas con menos registros que el estado previo registrado. Para evitar pérdida de datos o corrupción en Looker Studio y reportes, se abortó la sincronización y las pestañas de datos se mantuvieron intactas.",
        "timestamps": {
            "cdmx": now_cdmx,
            "utc": now_utc
        },
        "environment": {
            "runner": origen,
            "hostname": platform.node(),
            "os": f"{platform.system()} {platform.release()}",
            "python_version": sys.version.split()[0],
            "script_dir": script_dir
        },
        "metrics_comparison": comparisons,
        "regressed_tables": regressions,
        "suspected_root_causes": suspected_causes,
        "scraper_execution_meta": meta,
        "recommended_ai_actions": [
            "1. Inspeccionar el HTML y selectores de la página de reservas (List.xhtml) y botón de paginación (.ui-paginator-next).",
            "2. Verificar las credenciales y el flujo de login a https://abastur.indipris.com/admin.",
            "3. Verificar si el filtro de fecha ('01/06/2026') sigue siendo válido o si se modificó el ID de los inputs.",
            "4. Si 'Reservas' está completo pero las pestañas de detalle fallaron, inspeccionar la vista show-booking.xhtml?bookingId=... y los selectores MPFEditForm:tabView.",
            "5. NO forzar la sobreescritura manual sin antes validar que el nuevo scrape contenga al menos la cantidad esperada de registros."
        ],
        "recent_system_logs": recent_logs
    }

    # Guardar reporte JSON
    json_path = os.path.join(script_dir, "incident_report.json")
    with open(json_path, "w", encoding="utf-8") as jf:
        json.dump(report_data, jf, indent=2, ensure_ascii=False)

    # Guardar reporte Markdown para lectura humana
    md_path = os.path.join(script_dir, "incident_report.md")
    md_content = f"""# 🚨 REPORTE DE INCIDENTE: REGRESIÓN DE DATOS DETECTADA

- **ID del Incidente:** `{incident_id}`
- **Fecha y Hora (CDMX):** `{now_cdmx}`
- **Entorno / Origen:** `{origen}` (`{platform.node()}`)
- **Decisión:** **SINCRONIZACIÓN ABORTADA** (Las hojas de Google Sheets NO fueron modificadas).

---

## 📊 Comparación de Métricas (Estado Previo vs Nuevo Intento)

| Pestaña | Filas Previas | Filas Nuevas | Diferencia (Delta) | Cambio % | Estado |
|---|---|---|---|---|---|
"""
    for table, d in comparisons.items():
        status_icon = "❌ REGRESIÓN" if d["status"] == "REGRESSION" else ("✅ OK" if d["status"] == "INCREASED" else "⚪ IGUAL")
        md_content += f"| **{table}** | {d['previous_count']} | {d['new_count']} | {d['delta']} | {d['percentage_change']}% | {status_icon} |\n"

    md_content += """
---

## 🔍 Causas Probables Diagnosticadas
"""
    for cause in suspected_causes:
        md_content += f"- **[{cause['category']}]** (Certeza: {cause['confidence']}): {cause['description']}\n"

    md_content += """
---

## 🤖 Acciones Recomendadas para Auto-Reparación (IA)
"""
    for act in report_data["recommended_ai_actions"]:
        md_content += f"- {act}\n"

    with open(md_path, "w", encoding="utf-8") as mf:
        mf.write(md_content)

    # Registrar en histórico
    history_path = os.path.join(script_dir, "incidents_history.log")
    with open(history_path, "a", encoding="utf-8") as hf:
        hf.write(f"[{now_cdmx}] {incident_id} | REGRESIONES: {', '.join(regressions)} | Origen: {origen}\n")

    print(f"\n❌ ERROR CRÍTICO: Regresión de datos detectada en: {', '.join(regressions)}")
    print(f"📄 Reporte generado para IA en: {json_path}")
    print(f"📄 Reporte Markdown en: {md_path}")
    return json_path


def _save_successful_state(script_dir, stats, origen, duration_str):
    """Guarda en disco local el registro de la sincronización exitosa para futuras comparaciones."""
    state_path = os.path.join(script_dir, "last_successful_sync.json")
    now_cdmx = _get_cdmx_now_str()
    state_data = {
        "timestamp_cdmx": now_cdmx,
        "runner": origen,
        "duration": duration_str,
        "counts": stats
    }
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state_data, f, indent=2, ensure_ascii=False)


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


def _upload_timestamp_worksheet(sh, stats, script_dir, status="Exitoso", alert_msg=""):
    """Crea o actualiza la pestaña 'Ultima_Actualizacion' con la fecha/hora en zona horaria Ciudad de México (CDMX)."""
    now_str = _get_cdmx_now_str()

    if os.environ.get("GITHUB_ACTIONS") == "true":
        origen = "GitHub Actions"
    else:
        origen = "IndiprisTI"

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
        ["Estado del Proceso", status],
    ]

    if alert_msg:
        data_to_upload.append(["Alerta / Motivo", alert_msg])

    for name, count in stats.items():
        data_to_upload.append([f"Filas en {name}", count])

    worksheet_name = "Ultima_Actualizacion"
    try:
        worksheet = sh.worksheet(worksheet_name)
        worksheet.clear()
    except gspread.exceptions.WorksheetNotFound:
        worksheet = sh.add_worksheet(title=worksheet_name, rows=25, cols=5)

    worksheet.update(values=data_to_upload, range_name="A1")
    print(f"\n  ✓ Pestaña '{worksheet_name}' actualizada (Estado: {status}, Origen: {origen}, Fecha CDMX: {now_str})")


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

    origen = "GitHub Actions" if os.environ.get("GITHUB_ACTIONS") == "true" else "IndiprisTI"

    # Leer metadatos del scraper
    meta_path = os.path.join(script_dir, "execution_meta.json")
    meta = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            pass

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

    sheet_url = "https://docs.google.com/spreadsheets/d/1uauLtgcOFg0Gp9432TEzVqZ01M8OrulQSb_DWxeNW-M/edit?usp=sharing"
    print(f"Abriendo la hoja de cálculo: {sheet_url}")

    try:
        sh = client.open_by_url(sheet_url)

        worksheets = [
            (bookings_csv, "Reservas"),
            (hotels_csv, "Alojamientos"),
            (transports_csv, "Transportes"),
            (financial_csv, "Financiero"),
            (activities_csv, "Actividades"),
        ]

        # -------------------------------------------------------------
        # VALIDACIÓN DEL MODELO INCREMENTAL
        # -------------------------------------------------------------
        print("\nVerificando invariante del modelo incremental...")
        historical_counts = _get_historical_counts(sh, script_dir, worksheets)

        current_counts = {}
        for csv_path, sheet_name in worksheets:
            if os.path.exists(csv_path):
                try:
                    df = pd.read_csv(csv_path)
                    current_counts[sheet_name] = len(df)
                except Exception:
                    current_counts[sheet_name] = 0
            else:
                current_counts[sheet_name] = 0

        regressions = []
        comparisons = {}
        for _, sheet_name in worksheets:
            prev = historical_counts.get(sheet_name, 0)
            curr = current_counts.get(sheet_name, 0)
            delta = curr - prev
            pct = round((delta / prev) * 100, 2) if prev > 0 else 0
            is_regressed = prev > 0 and curr < prev
            status = "REGRESSION" if is_regressed else ("INCREASED" if curr > prev else "STABLE")

            if is_regressed:
                regressions.append(sheet_name)

            comparisons[sheet_name] = {
                "previous_count": prev,
                "new_count": curr,
                "delta": delta,
                "percentage_change": pct,
                "status": status
            }

        # Imprimir resumen de comparación en consola
        for sname, cdata in comparisons.items():
            sym = "❌" if cdata["status"] == "REGRESSION" else ("▲" if cdata["status"] == "INCREASED" else "=")
            print(f"  {sym} {sname}: {cdata['new_count']} filas (Previas: {cdata['previous_count']} | Delta: {cdata['delta']:+d})")

        # SI HAY REGRESIÓN: NO ACTUALIZAR Y EMITIR REPORTE DETALLADO
        if regressions:
            alert_text = f"Regresión detectada en: {', '.join(regressions)}. Sincronización abortada."
            print("\n" + "!" * 70)
            print("🚨 ATENCIÓN: El modelo es incremental y se detectaron menos registros.")
            print("   Por seguridad, NO se modificarán las pestañas de datos en Google Sheets.")
            print("!" * 70)

            # Generar reporte estructurado para la IA
            _generate_incident_report(script_dir, comparisons, regressions, current_counts, meta, origen)

            # Actualizar únicamente la pestaña de estado en Google Sheets con la advertencia
            try:
                _upload_timestamp_worksheet(
                    sh,
                    historical_counts,
                    script_dir,
                    status="ERROR - REGRESIÓN DE DATOS (Abortado)",
                    alert_msg=f"Intento con menos filas en: {', '.join(regressions)}. Datos protegidos sin cambios."
                )
            except Exception as e:
                print(f"  ⚠ No se pudo registrar la alerta en Ultima_Actualizacion: {e}")

            # Salir con código de error
            sys.exit(1)

        # -------------------------------------------------------------
        # ACTUALIZACIÓN EXITOSA (Sin regresión)
        # -------------------------------------------------------------
        stats = {}
        for idx, (csv_path, sheet_name) in enumerate(worksheets):
            row_count = _upload_csv_to_worksheet(sh, csv_path, sheet_name, idx)
            stats[sheet_name] = row_count

        # Subir pestaña con fecha, origen y tiempo de ejecución
        _upload_timestamp_worksheet(sh, stats, script_dir, status="Exitoso")

        # Guardar estado exitoso local
        duration_str = meta.get("duration_formatted", "N/D")
        _save_successful_state(script_dir, stats, origen, duration_str)

        print("\n✅ ¡Google Sheets actualizado con éxito cumpliendo el modelo incremental!")

    except gspread.exceptions.APIError as e:
        print(f"Error de API: {e}")
        print("\nIMPORTANTE: Por favor asegúrate de que el documento de Google Sheets esté compartido con el correo del bot:")
        print("bot-sheets@pivotal-keep-447418-r0-iam.gserviceaccount.com con permisos de Editor.")
        sys.exit(1)
    except PermissionError:
        print("\nERROR DE PERMISOS (403): La cuenta de servicio no tiene acceso a esta hoja de cálculo.")
        sys.exit(1)
    except SystemExit:
        raise
    except Exception as e:
        print(f"Ocurrió un error inesperado: {e}")
        sys.exit(1)


if __name__ == "__main__":
    upload()
