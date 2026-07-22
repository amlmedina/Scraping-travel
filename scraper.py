import os
import sys
import time
from playwright.sync_api import sync_playwright
import pandas as pd

def scrape_bookings():
    print("Iniciando scraper...")
    login_url = "https://abastur.indipris.com/admin"
    bookings_url = "https://abastur.indipris.com/admin/bookings/List.xhtml"
    
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
        time.sleep(3) # Pausa de seguridad
        
        # 2. Navegar a reservas
        print("Navegando a la lista de reservas...")
        page.goto(bookings_url)
        page.wait_for_selector("tbody[id='search-form:BOOKINGS_data']")
        
        # Abrir filtros, limpiar fechas y aplicar
        try:
            print("Abriendo panel de Filtros...")
            page.evaluate("""() => {
                const btn = [...document.querySelectorAll('button, a, span')].find(el => el.textContent.includes('Filtros'));
                if (btn) btn.click();
            }""")
            time.sleep(2)
            
            print("Haciendo clic en 'Eliminar fechas'...")
            page.evaluate("""() => {
                const btn = document.querySelector('.dev-clear-dates') || 
                            [...document.querySelectorAll('button, a, span')].find(el => el.textContent.includes('Eliminar fechas'));
                if (btn) btn.click();
            }""")
            time.sleep(2)
            
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

        # 4. Extraer datos con paginación
        all_rows = []
        page_num = 1
        
        while True:
            print(f"Procesando página {page_num}...")
            # Esperar a que la tabla esté visible y con datos
            page.wait_for_selector("tbody[id='search-form:BOOKINGS_data'] tr")
            time.sleep(1) # Esperar un momento a que terminen de renderizarse los datos
            
            rows = page.query_selector_all("tbody[id='search-form:BOOKINGS_data'] tr")
            print(f"Encontradas {len(rows)} filas en esta página.")
            
            for row in rows:
                cells = row.query_selector_all("td")
                row_data = [cell.inner_text().strip() for cell in cells]
                if row_data and len(row_data) == len(headers):
                    all_rows.append(row_data)
                elif row_data:
                    # Rellenar o ajustar si los tamaños no coinciden exactamente por celdas colapsadas
                    while len(row_data) < len(headers):
                        row_data.append("")
                    all_rows.append(row_data[:len(headers)])
            
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
                
        # 5. Guardar datos en CSV
        script_dir = os.path.dirname(os.path.abspath(__file__))
        output_file = os.path.join(script_dir, "bookings.csv")
        df = pd.DataFrame(all_rows, columns=headers)
        df.to_csv(output_file, index=False, encoding='utf-8-sig')
        print(f"Scraping completado. Se guardaron {len(all_rows)} filas en: {output_file}")
        browser.close()

if __name__ == "__main__":
    scrape_bookings()
