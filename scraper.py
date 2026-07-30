import os
import sys
import time
from playwright.sync_api import sync_playwright
import pandas as pd


# ---------- Columnas de la tabla de Alojamientos ----------
HOTEL_COLUMNS = [
    "Estado",
    "Ref. Proveedor Alojamiento",
    "Referencia De Reserva Hotel",
    "Políticas De Cancelación Hotel",
    "Referencia Adicional",
    "Información Del Proveedor",
    "Alojamiento",
    "Ubicación",
    "Destino Hotel",
    "Categoría",
    "Información Fiscal",
    "Apartamento",
    "Check-in",
    "Noches",
    "Habitaciones",
    "Régimen De Comidas",
    "Tarifa",
    "Causa Del Error",
    "Advertencias",
    "Comentarios Al Alojamiento",
]


def _extract_star_count(cell):
    """Cuenta las estrellas visibles (no hidden) dentro de una celda de categoría."""
    stars = cell.query_selector_all("i.fa-star")
    visible = 0
    for star in stars:
        classes = star.get_attribute("class") or ""
        if "hidden" not in classes:
            visible += 1
    return str(visible) if visible else ""


def _extract_smart_cell(cell, header_name=""):
    """Extrae el valor de una celda de forma inteligente, detectando inputs, selects, estrellas, etc."""

    # Categoría (estrellas) — detectar por nombre de header o por presencia de estrellas
    if "categoría" in header_name.lower() or "category" in header_name.lower():
        stars = cell.query_selector_all("i.fa-star")
        if stars:
            return _extract_star_count(cell)

    # Select disabled (ej: Estado)
    sel = cell.query_selector("select")
    if sel:
        opt = sel.query_selector("option[selected]")
        if opt:
            return opt.inner_text().strip()

    # Autocomplete input (ej: Régimen de comidas)
    ac_input = cell.query_selector("input.ui-autocomplete-input")
    if ac_input:
        return (ac_input.get_attribute("value") or "").strip()

    # Input de texto disabled (ej: Habitaciones, Referencias)
    inp = cell.query_selector("input.c-text-input:not([type='hidden'])")
    if inp:
        return (inp.get_attribute("value") or "").strip()

    # Input genérico con value (ej: campos de transporte)
    generic_inp = cell.query_selector("input:not([type='hidden'])")
    if generic_inp:
        val = (generic_inp.get_attribute("value") or "").strip()
        if val:
            return val

    # Default: inner_text
    return cell.inner_text().strip()


def _read_table_headers(detail_page, thead_selector):
    """Lee los encabezados de una tabla dada y devuelve la lista de nombres."""
    headers = []
    th_elements = detail_page.query_selector_all(f"{thead_selector} th")
    for th in th_elements:
        title_el = th.query_selector(".ui-column-title")
        title = title_el.inner_text().strip() if title_el else th.inner_text().strip()
        headers.append(title)
    return headers


def _scrape_tab_table(detail_page, tab_selector, panel_id, tab_name, booking_id):
    """Scrapes una pestaña y su tabla dentro de la página de detalle.

    Lee dinámicamente los encabezados del thead y alinea 1-a-1 con las celdas td
    por índice exacto para evitar desplazamientos de columnas.

    Args:
        detail_page: La página de Playwright.
        tab_selector: Selector CSS del link de la pestaña.
        panel_id: ID del div panel del tab (sin #).
        tab_name: Nombre legible (para logs).
        booking_id: ID del booking (para logs).

    Returns:
        Tuple (rows_list, columns_list).
    """
    # Buscar pestaña
    tab = detail_page.query_selector(tab_selector)
    if not tab:
        return [], []

    tab.click()
    time.sleep(2)

    # Buscar la tabla dentro del panel
    escaped_panel = panel_id.replace(':', '\\:')
    panel_sel = f"#{escaped_panel}"

    # Buscar thead y tbody dinámicamente dentro del panel
    thead = detail_page.query_selector(f"{panel_sel} thead")
    tbody = detail_page.query_selector(f"{panel_sel} tbody")

    if not tbody or not thead:
        return [], []

    th_elements = thead.query_selector_all("th")
    if not th_elements:
        return [], []

    # Leer los títulos de columna y su índice de celda original exacto
    col_info = []  # Lista de tuplas: (index_in_table, column_title)
    for idx, th in enumerate(th_elements):
        title_el = th.query_selector(".ui-column-title")
        title = title_el.inner_text().strip() if title_el else th.inner_text().strip()

        # Ignorar columnas sin título (como el botón Opciones/toggler)
        if not title:
            continue

        col_info.append((idx, title))

    if not col_info:
        return [], []

    columns = [title for _, title in col_info]

    # Extraer filas alineando el índice exacto de la celda td con la columna th
    result_rows = []
    rows = tbody.query_selector_all("tr")
    for row in rows:
        cells = row.query_selector_all("td")
        if not cells:
            continue

        row_data = {}
        for cell_idx, col_name in col_info:
            if cell_idx < len(cells):
                row_data[col_name] = _extract_smart_cell(cells[cell_idx], col_name)
            else:
                row_data[col_name] = ""

        result_rows.append(row_data)

    return result_rows, columns


# ---------- Configuración de pestañas a scrapear ----------
TAB_CONFIG = [
    {
        "key": "hotels",
        "tab_selector": 'a[href="#MPFEditForm\\:tabView\\:tabHotel"]',
        "panel_id": "MPFEditForm:tabView:tabHotel",
        "tab_name": "Alojamientos",
        "icon": "🏨",
        "csv_name": "hotels.csv",
        "sheet_name": "Alojamientos",
    },
    {
        "key": "transports",
        "tab_selector": 'a[href="#MPFEditForm\\:tabView\\:tabTransport"]',
        "panel_id": "MPFEditForm:tabView:tabTransport",
        "tab_name": "Transportes",
        "icon": "✈️",
        "csv_name": "transports.csv",
        "sheet_name": "Transportes",
    },
    {
        "key": "financial",
        "tab_selector": 'a[href="#MPFEditForm\\:tabView\\:tabFinancial"]',
        "panel_id": "MPFEditForm:tabView:tabFinancial",
        "tab_name": "Financiero",
        "icon": "💰",
        "csv_name": "financial.csv",
        "sheet_name": "Financiero",
    },
    {
        "key": "activities",
        "tab_selector": 'a[href="#MPFEditForm\\:tabView\\:tabTickets"]',
        "panel_id": "MPFEditForm:tabView:tabTickets",
        "tab_name": "Actividades",
        "icon": "🎫",
        "csv_name": "activities.csv",
        "sheet_name": "Actividades",
    },
]


def scrape_booking_details(context, base_url, booking_id):
    """Abre la página de detalle de un booking y extrae todas las pestañas configuradas.

    Returns:
        dict con claves por cada tab_config["key"], cada una con:
            - "rows": lista de dicts
            - "columns": lista de nombres de columna
    """
    detail_url = f"{base_url}/admin/bookings/show-booking.xhtml?bookingId={booking_id}"
    detail_page = context.new_page()
    result = {}
    for cfg in TAB_CONFIG:
        result[cfg["key"]] = {"rows": [], "columns": []}

    try:
        detail_page.goto(detail_url, timeout=30000)
        detail_page.wait_for_load_state("networkidle", timeout=15000)
        time.sleep(2)

        for cfg in TAB_CONFIG:
            rows, cols = _scrape_tab_table(
                detail_page,
                tab_selector=cfg["tab_selector"],
                panel_id=cfg["panel_id"],
                tab_name=cfg["tab_name"],
                booking_id=booking_id,
            )
            result[cfg["key"]]["rows"] = rows
            if cols:
                result[cfg["key"]]["columns"] = cols

    except Exception as e:
        print(f"    ✗ Error procesando detalle de {booking_id}: {e}")
    finally:
        detail_page.close()

    return result


def scrape_bookings():
    print("Iniciando scraper...")
    base_url = "https://abastur.indipris.com"
    login_url = f"{base_url}/admin"
    bookings_url = f"{base_url}/admin/bookings/List.xhtml"

    username = os.environ.get("ABASTUR_USER", "abastur@indipris.com")
    password = os.environ.get("ABASTUR_PASS", "AbasTur_12341!")

    with sync_playwright() as p:
        # Iniciamos Chromium
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1366, "height": 768})
        page = context.new_page()

        # 1. Login
        print("Accediendo a la página de login...")
        page.goto(login_url)
        page.wait_for_selector("input[type='text'], input[type='email']")

        # Llenar credenciales
        page.fill("input[id='login-form:login-content:login:Email']", username)
        page.fill("input[id='login-form:login-content:login:j_password']", password)

        print("Enviando formulario de login...")
        page.click("button[id='login-form:login-content:login:signin']")

        # Esperar a la redirección o a que cargue la interfaz de admin
        page.wait_for_url("**/admin/**")
        time.sleep(3)  # Pausa de seguridad

        # 2. Navegar a reservas
        print("Navegando a la lista de reservas...")
        page.goto(bookings_url)
        page.wait_for_selector("tbody[id='search-form:BOOKINGS_data']")

        # Abrir filtros, aplicar rango de fechas y aplicar
        try:
            print("Abriendo panel de Filtros...")
            page.evaluate("""() => {
                const btn = [...document.querySelectorAll('button, a, span')].find(el => el.textContent.includes('Filtros'));
                if (btn) btn.click();
            }""")
            time.sleep(2)

            # Establecer fecha de inicio fija al 1 de junio de 2026
            start_date = "01/06/2026"
            print(f"Estableciendo fecha de inicio de búsqueda a: {start_date}")

            # Rellenar las fechas directamente en el DOM mediante JavaScript sin disparar eventos de red intermedios
            print("Rellenando campos de fecha en el DOM...")
            page.evaluate(f"""(date) => {{
                const fromInput = document.getElementById('search-form:booking-filters:creationDateFrom_input');
                const toInput = document.getElementById('search-form:booking-filters:creationDateTo_input');
                if (fromInput) fromInput.value = date;
                if (toInput) toInput.value = '';
            }}""", start_date)

            time.sleep(1)

            print("Haciendo clic en 'Aplicar'...")
            page.evaluate("""() => {
                const btn = [...document.querySelectorAll('button, a, span')].find(el => el.textContent.includes('Aplicar'));
                if (btn) btn.click();
            }""")

            print("Esperando a que la tabla se actualice...")
            time.sleep(5)
            page.wait_for_selector("tbody[id='search-form:BOOKINGS_data']")
        except Exception as e:
            print(f"Error aplicando filtros: {e}")

        # 3. Obtener cabeceras
        headers = []
        header_elements = page.query_selector_all("thead[id='search-form:BOOKINGS_head'] th")
        for th in header_elements:
            title_el = th.query_selector(".ui-column-title")
            title = title_el.inner_text().strip() if title_el else th.inner_text().strip()
            # Si el título está vacío, poner un nombre genérico o índice
            if not title:
                title = "Acciones" if th.query_selector("a, i") else f"Columna_{len(headers)}"
            headers.append(title)

        print(f"Cabeceras detectadas ({len(headers)}): {headers}")

        # 4. Extraer datos con paginación + bookingIds
        all_rows = []
        booking_ids = []  # Lista paralela de IDs extraídos de los links
        page_num = 1

        while True:
            print(f"Procesando página {page_num}...")
            # Esperar a que la tabla esté visible y con datos
            page.wait_for_selector("tbody[id='search-form:BOOKINGS_data'] tr")
            time.sleep(1)  # Esperar a que terminen de renderizarse los datos

            rows = page.query_selector_all("tbody[id='search-form:BOOKINGS_data'] tr")
            print(f"Encontradas {len(rows)} filas en esta página.")

            for row in rows:
                cells = row.query_selector_all("td")
                row_data = [cell.inner_text().strip() for cell in cells]

                # Extraer bookingId del link de la primera celda
                bid = ""
                first_link = row.query_selector("td a[href*='show-booking']")
                if first_link:
                    href = first_link.get_attribute("href") or ""
                    # href = /admin/bookings/show-booking.xhtml?bookingId=OLR-75846
                    if "bookingId=" in href:
                        bid = href.split("bookingId=")[-1]

                if row_data and len(row_data) == len(headers):
                    all_rows.append(row_data)
                    booking_ids.append(bid)
                elif row_data:
                    # Rellenar o ajustar si los tamaños no coinciden exactamente
                    while len(row_data) < len(headers):
                        row_data.append("")
                    all_rows.append(row_data[:len(headers)])
                    booking_ids.append(bid)

            # Buscar el botón de siguiente página
            next_btn = page.query_selector("a.ui-paginator-next")
            if next_btn:
                classes = next_btn.get_attribute("class") or ""
                if "ui-state-disabled" in classes:
                    print("Se alcanzó la última página.")
                    break
                else:
                    print("Navegando a la siguiente página...")
                    next_btn.click()
                    page_num += 1
                    # Esperar a que el spinner o el AJAX termine
                    time.sleep(3)
            else:
                print("No se encontró el botón de paginación.")
                break

        # 5. Guardar datos de reservas en CSV
        script_dir = os.path.dirname(os.path.abspath(__file__))
        output_file = os.path.join(script_dir, "bookings.csv")
        df = pd.DataFrame(all_rows, columns=headers)
        df.to_csv(output_file, index=False, encoding='utf-8-sig')
        print(f"Scraping de reservas completado. Se guardaron {len(all_rows)} filas en: {output_file}")

        # ──────────────────────────────────────────────────────────────
        # 6. Extraer detalle de todas las pestañas por booking
        # ──────────────────────────────────────────────────────────────
        tab_names = ", ".join(c["tab_name"] for c in TAB_CONFIG)
        print("\n" + "=" * 60)
        print(f"FASE 2: Extrayendo detalles ({tab_names})...")
        print("=" * 60)

        # Acumuladores por tipo de tab
        all_rows_by_key = {cfg["key"]: [] for cfg in TAB_CONFIG}
        columns_by_key = {cfg["key"]: [] for cfg in TAB_CONFIG}
        total = len(booking_ids)

        # Determinar la referencia de reserva de cada booking
        ref_col_idx = None
        for i, h in enumerate(headers):
            if "referencia de reserva" in h.lower() and "agencia" not in h.lower() and "hotel" not in h.lower():
                ref_col_idx = i
                break

        for idx, bid in enumerate(booking_ids):
            if not bid:
                continue

            booking_ref = all_rows[idx][ref_col_idx] if ref_col_idx is not None else bid
            print(f"\n[{idx + 1}/{total}] Procesando booking {bid} (ref: {booking_ref})...")

            details = scrape_booking_details(context, base_url, bid)

            found_any = False
            for cfg in TAB_CONFIG:
                key = cfg["key"]
                tab_rows = details[key]["rows"]
                if tab_rows:
                    found_any = True
                    print(f"    {cfg['icon']} {len(tab_rows)} {cfg['tab_name'].lower()}")
                    # Guardar columnas la primera vez que se detectan
                    if not columns_by_key[key] and details[key]["columns"]:
                        columns_by_key[key] = details[key]["columns"]
                    for row in tab_rows:
                        row["Booking_Referencia"] = booking_ref
                        row["Booking_ID"] = bid
                    all_rows_by_key[key].extend(tab_rows)

            if not found_any:
                print(f"    — Sin datos en ninguna pestaña")

        # 7. Guardar CSVs por tipo
        for cfg in TAB_CONFIG:
            key = cfg["key"]
            csv_file = os.path.join(script_dir, cfg["csv_name"])
            rows_data = all_rows_by_key[key]
            cols = columns_by_key[key]

            if rows_data:
                df_tab = pd.DataFrame(rows_data)
                # Reordenar: columnas de relación al inicio
                final_cols = ["Booking_Referencia", "Booking_ID"] + [
                    c for c in df_tab.columns if c not in ("Booking_Referencia", "Booking_ID")
                ]
                df_tab = df_tab[final_cols]
                df_tab.to_csv(csv_file, index=False, encoding='utf-8-sig')
                print(f"\n{cfg['icon']} {cfg['tab_name']}: {len(rows_data)} filas → {cfg['csv_name']}")
            else:
                empty_cols = ["Booking_Referencia", "Booking_ID"] + (cols if cols else [])
                pd.DataFrame(columns=empty_cols).to_csv(csv_file, index=False, encoding='utf-8-sig')

        browser.close()
        print("\n✅ Scraping completo.")


if __name__ == "__main__":
    scrape_bookings()
