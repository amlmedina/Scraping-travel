import asyncio
import json
import os
import re
import sys
import time
from playwright.async_api import async_playwright
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


async def _extract_star_count(cell):
    """Cuenta las estrellas visibles (no hidden) y devuelve un número (ej: 3) para compatibilidad con BigQuery."""
    stars = await cell.query_selector_all("i.fa-star")
    visible = 0
    for star in stars:
        classes = (await star.get_attribute("class")) or ""
        if "hidden" not in classes:
            visible += 1
    return str(visible) if visible else ""


async def _clean_cells_alignment(cells, th_elements):
    """Ajusta celdas td si el HTML de JSF renderizó celdas td extra sin su correspondiente th."""
    if len(cells) <= len(th_elements):
        return cells

    # Si hay 1 celda extra (ej: 22 td vs 21 th en Alojamientos)
    if len(cells) == len(th_elements) + 1:
        cleaned = list(cells)
        for check_idx in range(1, len(cleaned) - 1):
            txt = (await cleaned[check_idx].inner_text()).strip()
            inp = await cleaned[check_idx].query_selector("input:not([type='hidden'])")
            inp_val = ((await inp.get_attribute("value")) or "").strip() if inp else ""

            # Si la celda está totalmente vacía en el DOM
            if not txt and not inp_val:
                next_txt = (await cleaned[check_idx + 1].inner_text()).strip()
                next_inp = await cleaned[check_idx + 1].query_selector("input:not([type='hidden'])")
                next_inp_val = ((await next_inp.get_attribute("value")) or "").strip() if next_inp else ""
                check_val = next_txt or next_inp_val

                # Si la celda siguiente es Apartamento ('No'/'Sí') o Check-in (fecha)
                if check_val in ("No", "Sí", "Si") or re.match(r"^\d{1,2}/\d{1,2}/\d{4}", check_val) or re.match(r"^\d{4}-\d{2}-\d{2}", check_val):
                    cleaned.pop(check_idx)
                    if len(cleaned) == len(th_elements):
                        return cleaned

    return cells


async def _extract_smart_cell(cell, header_name=""):
    """Extrae el valor de una celda de forma inteligente, detectando inputs, selects, estrellas, etc."""
    val = ""
    # Categoría (estrellas)
    if "categoría" in header_name.lower() or "category" in header_name.lower():
        stars = await cell.query_selector_all("i.fa-star")
        if stars:
            val = await _extract_star_count(cell)
    elif await cell.query_selector("select"):
        sel = await cell.query_selector("select")
        opt = await sel.query_selector("option[selected]")
        if opt:
            val = (await opt.inner_text()).strip()
    elif await cell.query_selector("input.ui-autocomplete-input"):
        inp = await cell.query_selector("input.ui-autocomplete-input")
        val = ((await inp.get_attribute("value")) or "").strip()
    elif await cell.query_selector("input.c-text-input:not([type='hidden'])"):
        inp = await cell.query_selector("input.c-text-input:not([type='hidden'])")
        val = ((await inp.get_attribute("value")) or "").strip()
    elif await cell.query_selector("input:not([type='hidden'])"):
        inp = await cell.query_selector("input:not([type='hidden'])")
        val = ((await inp.get_attribute("value")) or "").strip()

    if not val:
        val = (await cell.inner_text()).strip()

    return normalize_date_val(val)


async def _scrape_tab_table(detail_page, tab_selector, panel_id, tab_name, booking_id):
    """Scrapes una pestaña y su tabla dentro de la página de detalle."""
    tab = await detail_page.query_selector(tab_selector)
    if not tab:
        return [], []

    await tab.click()
    await asyncio.sleep(1)

    escaped_panel = panel_id.replace(':', '\\:')
    panel_sel = f"#{escaped_panel}"

    thead = await detail_page.query_selector(f"{panel_sel} thead")
    tbody = await detail_page.query_selector(f"{panel_sel} tbody")

    if not tbody or not thead:
        return [], []

    th_elements = await thead.query_selector_all("th")
    if not th_elements:
        return [], []

    col_info = []  # Lista de tuplas: (index_in_table, column_title)
    for idx, th in enumerate(th_elements):
        title_el = await th.query_selector(".ui-column-title")
        title = (await title_el.inner_text()).strip() if title_el else (await th.inner_text()).strip()
        if not title:
            continue
        col_info.append((idx, title))

    if not col_info:
        return [], []

    columns = [title for _, title in col_info]
    result_rows = []
    rows = await tbody.query_selector_all("tr")
    for row in rows:
        raw_cells = await row.query_selector_all("td")
        if not raw_cells or (len(raw_cells) <= 1 and len(th_elements) > 1):
            continue

        cells = await _clean_cells_alignment(raw_cells, th_elements)

        row_data = {}
        for cell_idx, col_name in col_info:
            if cell_idx < len(cells):
                row_data[col_name] = await _extract_smart_cell(cells[cell_idx], col_name)
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


async def scrape_booking_details(context, base_url, booking_id, sem):
    """Abre la página de detalle de un booking y extrae todas las pestañas configuradas con reintentos."""
    async with sem:
        detail_url = f"{base_url}/admin/bookings/show-booking.xhtml?bookingId={booking_id}"
        result = {}
        for cfg in TAB_CONFIG:
            result[cfg["key"]] = {"rows": [], "columns": []}

        for attempt in range(1, 4):
            detail_page = None
            try:
                detail_page = await context.new_page()
                await detail_page.goto(detail_url, wait_until="domcontentloaded", timeout=40000)
                await asyncio.sleep(1)

                for cfg in TAB_CONFIG:
                    rows, cols = await _scrape_tab_table(
                        detail_page,
                        tab_selector=cfg["tab_selector"],
                        panel_id=cfg["panel_id"],
                        tab_name=cfg["tab_name"],
                        booking_id=booking_id,
                    )
                    result[cfg["key"]]["rows"] = rows
                    if cols:
                        result[cfg["key"]]["columns"] = cols
                return result
            except Exception as e:
                if attempt < 3:
                    await asyncio.sleep(2 * attempt)
                else:
                    print(f"    ✗ Error procesando detalle de {booking_id} tras 3 intentos: {e}")
            finally:
                if detail_page:
                    try:
                        await detail_page.close()
                    except Exception:
                        pass

        return result


async def scrape_bookings():
    start_time = time.time()
    print("Iniciando scraper optimizado...")
    base_url = "https://abastur.indipris.com"
    login_url = f"{base_url}/admin"
    bookings_url = f"{base_url}/admin/bookings/List.xhtml"

    username = os.environ.get("ABASTUR_USER", "abastur@indipris.com")
    password = os.environ.get("ABASTUR_PASS", "AbasTur_12341!")
    start_date = os.environ.get("START_DATE", "01/06/2026")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1366, "height": 768})

        # Bloquear imágenes, fuentes y multimedia para acelerar la carga
        await context.route(
            "**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf,eot}",
            lambda route: route.abort()
        )
        page = await context.new_page()

        # 1. Login
        print("Accediendo a la página de login...")
        await page.goto(login_url, wait_until="domcontentloaded")
        await page.wait_for_selector("input[type='text'], input[type='email']")

        # Llenar credenciales
        await page.fill("input[id='login-form:login-content:login:Email']", username)
        await page.fill("input[id='login-form:login-content:login:j_password']", password)

        print("Enviando formulario de login...")
        await page.click("button[id='login-form:login-content:login:signin']")
        await page.wait_for_url("**/admin/**", timeout=20000)

        # 2. Navegar a reservas
        print("Navegando a la lista de reservas...")
        await page.goto(bookings_url, wait_until="domcontentloaded")
        await page.wait_for_selector("tbody[id='search-form:BOOKINGS_data']")

        # Abrir filtros, aplicar rango de fechas y aplicar
        try:
            print("Abriendo panel de Filtros...")
            await page.evaluate("""() => {
                const btn = [...document.querySelectorAll('button, a, span')].find(el => el.textContent.includes('Filtros'));
                if (btn) btn.click();
            }""")
            await asyncio.sleep(2)

            print(f"Estableciendo fecha de inicio de búsqueda a: {start_date}")
            await page.evaluate(f"""(date) => {{
                const fromInput = document.getElementById('search-form:booking-filters:creationDateFrom_input');
                const toInput = document.getElementById('search-form:booking-filters:creationDateTo_input');
                if (fromInput) {{
                    fromInput.value = date;
                    fromInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    fromInput.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}
                if (toInput) {{
                    toInput.value = '';
                    toInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    toInput.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}
            }}""", start_date)

            await asyncio.sleep(1)

            print("Haciendo clic en 'Aplicar'...")
            # Capturar el texto del paginador previo para esperar el cambio
            prev_paginator = ""
            prev_pag_el = await page.query_selector(".ui-paginator-current")
            if prev_pag_el:
                prev_paginator = (await prev_pag_el.inner_text()).strip()

            await page.evaluate("""() => {
                const btn = [...document.querySelectorAll('button, a, span')].find(el => el.textContent.includes('Aplicar'));
                if (btn) btn.click();
            }""")

            print("Esperando a que la tabla se actualice con los filtros...")
            try:
                await page.wait_for_function(
                    "oldText => { const el = document.querySelector('.ui-paginator-current'); return el && el.innerText.trim() !== oldText; }",
                    arg=prev_paginator,
                    timeout=10000
                )
            except Exception:
                await asyncio.sleep(4)

            await page.wait_for_selector("tbody[id='search-form:BOOKINGS_data'] tr")

            # Optimización: Cambiar a 150 registros por página
            rpp_select = await page.query_selector("select.ui-paginator-rpp-options")
            if rpp_select:
                print("Configurando paginación a 150 filas por página para máxima velocidad...")
                prev_pag_el = await page.query_selector(".ui-paginator-current")
                prev_pag_txt = (await prev_pag_el.inner_text()).strip() if prev_pag_el else ""
                await rpp_select.select_option("150")
                try:
                    await page.wait_for_function(
                        "oldText => { const el = document.querySelector('.ui-paginator-current'); return el && el.innerText.trim() !== oldText; }",
                        arg=prev_pag_txt,
                        timeout=10000
                    )
                except Exception:
                    await asyncio.sleep(4)

        except Exception as e:
            print(f"Error aplicando filtros: {e}")

        # 3. Obtener cabeceras
        headers = []
        header_elements = await page.query_selector_all("thead[id='search-form:BOOKINGS_head'] th")
        for th in header_elements:
            title_el = await th.query_selector(".ui-column-title")
            title = (await title_el.inner_text()).strip() if title_el else (await th.inner_text()).strip()
            if not title:
                title = "Acciones" if await th.query_selector("a, i") else f"Columna_{len(headers)}"
            headers.append(title)

        print(f"Cabeceras detectadas ({len(headers)}): {headers}")

        # 4. Extraer datos con paginación completa + bookingIds
        all_rows = []
        booking_ids = []
        page_num = 1

        while True:
            await page.wait_for_selector("tbody[id='search-form:BOOKINGS_data'] tr")
            rows = await page.query_selector_all("tbody[id='search-form:BOOKINGS_data'] tr")
            report_el = await page.query_selector(".ui-paginator-current")
            report_text = (await report_el.inner_text()).strip() if report_el else f"Página {page_num}"
            print(f"Procesando página {page_num} [{report_text}] — {len(rows)} filas...")

            for row in rows:
                cells = await row.query_selector_all("td")
                if len(cells) <= 1 and len(headers) > 1:
                    continue

                row_data = [normalize_date_val((await cell.inner_text()).strip()) for cell in cells]

                # Extraer bookingId del link de la primera celda
                bid = ""
                first_link = await row.query_selector("td a[href*='show-booking']")
                if first_link:
                    href = (await first_link.get_attribute("href")) or ""
                    if "bookingId=" in href:
                        bid = href.split("bookingId=")[-1]

                if row_data and len(row_data) == len(headers):
                    all_rows.append(row_data)
                    booking_ids.append(bid)
                elif row_data:
                    while len(row_data) < len(headers):
                        row_data.append("")
                    all_rows.append(row_data[:len(headers)])
                    booking_ids.append(bid)

            # Buscar el botón de siguiente página (es un <button>, no <a>)
            next_btn = await page.query_selector("button.ui-paginator-next, .ui-paginator-next")
            if next_btn:
                classes = (await next_btn.get_attribute("class")) or ""
                disabled_attr = await next_btn.get_attribute("disabled")
                if "ui-state-disabled" in classes or disabled_attr is not None:
                    print("Se alcanzó la última página.")
                    break
                else:
                    print("Navegando a la siguiente página...")
                    first_tr = await page.query_selector("tbody[id='search-form:BOOKINGS_data'] tr")
                    first_text = (await first_tr.inner_text()).strip() if first_tr else ""

                    await next_btn.click()
                    page_num += 1

                    try:
                        await page.wait_for_function(
                            "oldText => { const el = document.querySelector('tbody[id=\"search-form:BOOKINGS_data\"] tr'); return el && el.innerText.trim() !== oldText; }",
                            arg=first_text,
                            timeout=10000
                        )
                    except Exception:
                        await asyncio.sleep(4)
            else:
                print("No se encontró el botón de paginación.")
                break

        # 5. Guardar datos de reservas en CSV
        script_dir = os.path.dirname(os.path.abspath(__file__))
        output_file = os.path.join(script_dir, "bookings.csv")
        df = pd.DataFrame(all_rows, columns=headers)
        df.to_csv(output_file, index=False, encoding='utf-8-sig')
        print(f"\n✓ Scraping de reservas completado: {len(all_rows)} reservas guardadas en {output_file}")

        # ──────────────────────────────────────────────────────────────
        # 6. Extraer detalle de todas las pestañas por booking en PARALELO
        # ──────────────────────────────────────────────────────────────
        tab_names = ", ".join(c["tab_name"] for c in TAB_CONFIG)
        total = len(booking_ids)
        print("\n" + "=" * 60)
        print(f"FASE 2: Extrayendo detalles ({tab_names}) para {total} reservas en paralelo...")
        print("=" * 60)

        # Determinar la referencia de reserva de cada booking
        ref_col_idx = None
        for i, h in enumerate(headers):
            if "referencia de reserva" in h.lower() and "agencia" not in h.lower() and "hotel" not in h.lower():
                ref_col_idx = i
                break

        all_rows_by_key = {cfg["key"]: [] for cfg in TAB_CONFIG}
        columns_by_key = {cfg["key"]: [] for cfg in TAB_CONFIG}

        sem = asyncio.Semaphore(4)  # 4 páginas simultáneas para estabilidad y evitar timeouts
        completed_count = 0

        async def worker(idx, bid, booking_ref):
            nonlocal completed_count
            if not bid:
                return idx, bid, booking_ref, {}
            details = await scrape_booking_details(context, base_url, bid, sem)
            completed_count += 1
            icons = []
            for cfg in TAB_CONFIG:
                k = cfg["key"]
                cnt = len(details[k]["rows"])
                if cnt > 0:
                    icons.append(f"{cfg['icon']} {cnt}")
            ic_str = " | ".join(icons) if icons else "—"
            print(f"  [{completed_count}/{total}] Booking {bid} (ref: {booking_ref}) → {ic_str}")
            return idx, bid, booking_ref, details

        tasks = []
        for idx, bid in enumerate(booking_ids):
            b_ref = all_rows[idx][ref_col_idx] if ref_col_idx is not None else bid
            tasks.append(worker(idx, bid, b_ref))

        results = await asyncio.gather(*tasks)

        for idx, bid, booking_ref, details in results:
            for cfg in TAB_CONFIG:
                key = cfg["key"]
                tab_rows = details.get(key, {}).get("rows", [])
                cols = details.get(key, {}).get("columns", [])
                if tab_rows:
                    if not columns_by_key[key] and cols:
                        columns_by_key[key] = cols
                    for row in tab_rows:
                        row["Booking_Referencia"] = booking_ref
                        row["Booking_ID"] = bid
                    all_rows_by_key[key].extend(tab_rows)

        # 7. Guardar CSVs por tipo
        for cfg in TAB_CONFIG:
            key = cfg["key"]
            csv_file = os.path.join(script_dir, cfg["csv_name"])
            rows_data = all_rows_by_key[key]
            cols = columns_by_key[key]

            if rows_data:
                df_tab = pd.DataFrame(rows_data)
                final_cols = ["Booking_Referencia", "Booking_ID"] + [
                    c for c in df_tab.columns if c not in ("Booking_Referencia", "Booking_ID")
                ]
                df_tab = df_tab[final_cols]
                df_tab.to_csv(csv_file, index=False, encoding='utf-8-sig')
                print(f"\n{cfg['icon']} {cfg['tab_name']}: {len(rows_data)} filas → {cfg['csv_name']}")
            else:
                empty_cols = ["Booking_Referencia", "Booking_ID"] + (cols if cols else [])
                pd.DataFrame(columns=empty_cols).to_csv(csv_file, index=False, encoding='utf-8-sig')
                print(f"\n{cfg['icon']} {cfg['tab_name']}: 0 filas → {cfg['csv_name']}")

        await browser.close()

        # Calcular tiempo de ejecución
        elapsed = round(time.time() - start_time)
        mins, secs = divmod(elapsed, 60)
        duration_fmt = f"{mins} min {secs} seg" if mins > 0 else f"{secs} seg"

        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            ts_cdmx = datetime.now(ZoneInfo("America/Mexico_City")).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            ts_cdmx = time.strftime("%Y-%m-%d %H:%M:%S")

        meta = {
            "duration_seconds": elapsed,
            "duration_formatted": duration_fmt,
            "timestamp_cdmx": ts_cdmx,
        }
        meta_path = os.path.join(script_dir, "execution_meta.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

        print(f"\n✅ Scraping completo en {duration_fmt} (Hora CDMX: {ts_cdmx}).")


def main():
    asyncio.run(scrape_bookings())


if __name__ == "__main__":
    main()
