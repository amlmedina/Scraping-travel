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


JS_EXTRACT_PANEL = """(panelId) => {
    const panel = document.getElementById(panelId);
    if (!panel) return { rows: [], cols: [] };
    const thead = panel.querySelector('thead');
    const tbody = panel.querySelector('tbody');
    if (!thead || !tbody) return { rows: [], cols: [] };

    const ths = Array.from(thead.querySelectorAll('th'));
    const cols = [];
    const colIndices = [];
    ths.forEach((th, idx) => {
        const titleEl = th.querySelector('.ui-column-title');
        const title = (titleEl ? titleEl.innerText : th.innerText).trim();
        if (title) {
            cols.push(title);
            colIndices.push(idx);
        }
    });

    const rows = [];
    for (const tr of tbody.querySelectorAll('tr')) {
        let tds = Array.from(tr.querySelectorAll('td'));
        if (!tds.length || (tds.length <= 1 && ths.length > 1)) continue;

        // Limpieza de alineación (celda fantasma en JSF)
        if (tds.length === ths.length + 1) {
            for (let i = 1; i < tds.length - 1; i++) {
                const txt = tds[i].innerText.trim();
                const inp = tds[i].querySelector("input:not([type='hidden'])");
                const val = inp ? inp.value.trim() : '';
                if (!txt && !val) {
                    const nextTxt = tds[i+1].innerText.trim();
                    const nextInp = tds[i+1].querySelector("input:not([type='hidden'])");
                    const nextVal = (nextInp ? nextInp.value.trim() : '') || nextTxt;
                    if (['No', 'Sí', 'Si'].includes(nextVal) || /^\\d{1,2}\\/\\d{1,2}\\/\\d{4}/.test(nextVal) || /^\\d{4}-\\d{2}-\\d{2}/.test(nextVal)) {
                        tds.splice(i, 1);
                        break;
                    }
                }
            }
        }

        const rowData = {};
        cols.forEach((col, cIdx) => {
            const tdIdx = colIndices[cIdx];
            if (tdIdx < tds.length) {
                const td = tds[tdIdx];
                const inps = Array.from(td.querySelectorAll("input:not([type='hidden'])"))
                    .map(i => i.value.trim())
                    .filter(Boolean);
                const sels = Array.from(td.querySelectorAll('select'))
                    .map(s => s.options[s.selectedIndex] ? s.options[s.selectedIndex].text.trim() : '')
                    .filter(Boolean);
                const stars = td.querySelectorAll('i.fa-star:not(.hidden)');
                const normCol = col.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
                const isRoomCol = normCol.includes('habitacion') || normCol.includes('room');
                let cellVal = '';

                if (col.toLowerCase().includes('categoría') || col.toLowerCase().includes('category')) {
                    cellVal = stars.length > 0 ? String(stars.length) : td.innerText.trim();
                } else if (inps.length > 0) {
                    if (inps.length > 1 && isRoomCol) {
                        cellVal = inps.map((val, idx) => `Habitación ${idx + 1}: ${val}`).join('\\n');
                    } else if (inps.length > 1) {
                        cellVal = inps.join('\\n');
                    } else {
                        cellVal = inps[0];
                    }
                } else if (sels.length > 0) {
                    if (sels.length > 1 && isRoomCol) {
                        cellVal = sels.map((val, idx) => `Habitación ${idx + 1}: ${val}`).join('\\n');
                    } else if (sels.length > 1) {
                        cellVal = sels.join('\\n');
                    } else {
                        cellVal = sels[0];
                    }
                } else {
                    const lines = td.innerText.split('\\n').map(l => l.trim()).filter(Boolean);
                    if (lines.length > 1 && isRoomCol) {
                        cellVal = lines.map((val, idx) => `Habitación ${idx + 1}: ${val}`).join('\\n');
                    } else {
                        cellVal = td.innerText.trim();
                    }
                }
                rowData[col] = cellVal;
            } else {
                rowData[col] = '';
            }
        });
        rows.push(rowData);
    }
    return { rows, cols };
}"""


def _normalize_extracted_table(raw_data):
    """Aplica normalización de títulos de columna y fechas ISO de forma vectorizada en Python."""
    if not raw_data or not raw_data.get("rows"):
        return {"rows": [], "columns": [c.title() for c in raw_data.get("cols", [])] if raw_data else []}
    cols = [c.title() for c in raw_data.get("cols", [])]
    rows = []
    for r in raw_data["rows"]:
        norm_row = {}
        for k, v in r.items():
            norm_k = k.title()
            norm_v = normalize_date_val(v) if isinstance(v, str) else v
            norm_row[norm_k] = norm_v
        rows.append(norm_row)
    return {"rows": rows, "columns": cols}


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
    """Abre la página de detalle de un booking y extrae todas las pestañas a máxima velocidad."""
    async with sem:
        detail_url = f"{base_url}/admin/bookings/show-booking.xhtml?bookingId={booking_id}"
        result = {cfg["key"]: {"rows": [], "columns": []} for cfg in TAB_CONFIG}

        for attempt in range(1, 4):
            detail_page = None
            try:
                detail_page = await context.new_page()
                await detail_page.goto(detail_url, wait_until="domcontentloaded", timeout=35000)

                # 1. Alojamientos y Financiero ya están pre-renderizados en el DOM inicial
                raw_hotels = await detail_page.evaluate(JS_EXTRACT_PANEL, "MPFEditForm:tabView:tabHotel")
                raw_financial = await detail_page.evaluate(JS_EXTRACT_PANEL, "MPFEditForm:tabView:tabFinancial")
                result["hotels"] = _normalize_extracted_table(raw_hotels)
                result["financial"] = _normalize_extracted_table(raw_financial)

                # 2. Transportes: Solo si la pestaña existe en el DOM de esta reserva
                t_tab = await detail_page.query_selector('a[href="#MPFEditForm:tabView:tabTransport"]')
                if t_tab:
                    try:
                        await t_tab.click()
                        await detail_page.wait_for_selector('[id="MPFEditForm:tabView:tabTransport"] tbody', timeout=2500)
                        raw_t = await detail_page.evaluate(JS_EXTRACT_PANEL, "MPFEditForm:tabView:tabTransport")
                        result["transports"] = _normalize_extracted_table(raw_t)
                    except Exception:
                        pass

                # 3. Actividades: Solo si la pestaña existe en el DOM de esta reserva
                a_tab = await detail_page.query_selector('a[href="#MPFEditForm:tabView:tabTickets"]')
                if a_tab:
                    try:
                        await a_tab.click()
                        await detail_page.wait_for_selector('[id="MPFEditForm:tabView:tabTickets"] tbody', timeout=2500)
                        raw_a = await detail_page.evaluate(JS_EXTRACT_PANEL, "MPFEditForm:tabView:tabTickets")
                        result["activities"] = _normalize_extracted_table(raw_a)
                    except Exception:
                        pass

                return result
            except Exception as e:
                if attempt < 3:
                    await asyncio.sleep(1.5 * attempt)
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
        concurrency_limit = int(os.environ.get("CONCURRENCY", "8"))
        print(f"Configurando concurrencia a {concurrency_limit} tareas en paralelo...")
        sem = asyncio.Semaphore(concurrency_limit)
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
