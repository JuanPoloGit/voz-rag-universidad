import os
import requests
from bs4 import BeautifulSoup

def scrape_institutional_page(url, name_identifier):
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/119.0'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error al conectar con {url}: {e}")
        return

    soup = BeautifulSoup(response.text, 'html.parser')

    # Eliminar elementos de ruido estructural, menús, scripts, estilos y clases de Elementor
    noise_tags = [
        "header", "footer", "nav", "aside", "script", "style", 
        ".elementor-background-overlay", ".elementor-location-header", 
        ".elementor-location-footer", "noscript"
    ]
    for selector in noise_tags:
        for element in soup.select(selector):
            element.decompose()

    # Aislar el contenido principal de la web
    main_content = (
        soup.find('main') or 
        soup.find('article') or 
        soup.find('div', class_='content') or 
        soup.find('div', class_='site-content')
    )

    raw_text = main_content.get_text(separator='\n', strip=True) if main_content else soup.get_text(separator='\n', strip=True)

    # Filtrar líneas vacías y textos redundantes cortos
    lines = [line.strip() for line in raw_text.splitlines() if line.strip() and len(line.strip()) > 1]
    cleaned_text = '\n\n'.join(lines)

    # Guardar con sufijo '_scrape' para evitar conflictos con archivos manuales
    os.makedirs("documents", exist_ok=True)
    filename = f"{name_identifier}_scrape.md"
    file_path = os.path.join("documents", filename)

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(cleaned_text)
    
    print(f"Generado correctamente: {file_path}")

if __name__ == "__main__":
    # Define aquí tus objetivos de scraping con sus URLs correspondientes
    targets = {
        "audacia_unisimond": "https://audacia.ai/",
        "universidad_simon_bolivar": "https://www.unisimon.edu.co"
    }

    for identifier, target_url in targets.items():
        scrape_institutional_page(target_url, identifier)

def ejecutar_scraping_profundo():
    """Función de compatibilidad para ejecutar el scraping desde el orquestador."""
    targets = {
        "audacia_unisimond": "https://audacia.unisimon.edu.co",
        "universidad_simon_bolivar": "https://www.unisimon.edu.co"
    }

    for identifier, target_url in targets.items():
        scrape_institutional_page(target_url, identifier)
    
    print("Rastreo profundo completado y guardado con sufijo _scrape.")