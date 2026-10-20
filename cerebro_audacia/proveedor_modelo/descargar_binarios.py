"""Script para descargar y extraer la distribución oficial de llama.cpp con soporte CUDA para Windows."""

import os
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

DEST_DIR = Path(__file__).resolve().parent / "bin"
DEST_DIR.mkdir(parents=True, exist_ok=True)

URLS = [
    ("llama-b11669-bin-win-cuda-12.4-x64.zip", "https://github.com/ggml-org/llama.cpp/releases/download/b11669/llama-b11669-bin-win-cuda-12.4-x64.zip"),
    ("cudart-llama-bin-win-cuda-12.4-x64.zip", "https://github.com/ggml-org/llama.cpp/releases/download/b11669/cudart-llama-bin-win-cuda-12.4-x64.zip"),
]

def descargar_con_progreso(url: str, destino: Path) -> None:
    print(f"Descargando {url} -> {destino.name}...")
    t0 = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    
    with urllib.request.urlopen(req) as resp, open(destino, "wb") as f:
        total = int(resp.headers.get("Content-Length", 0))
        descargado = 0
        ultimo_log = time.time()
        
        while True:
            chunk = resp.read(1024 * 1024)  # 1 MB
            if not chunk:
                break
            f.write(chunk)
            descargado += len(chunk)
            
            if time.time() - ultimo_log > 2.0:
                mb_desc = descargado / (1024 * 1024)
                mb_tot = total / (1024 * 1024)
                pct = (descargado / total * 100) if total else 0
                vel = mb_desc / max(0.1, time.time() - t0)
                print(f"  {mb_desc:.1f}/{mb_tot:.1f} MB ({pct:.1f}%) a {vel:.1f} MB/s...")
                ultimo_log = time.time()
                
    dur = time.time() - t0
    print(f"  Descarga completa: {descargado / (1024*1024):.1f} MB en {dur:.1f}s")

for nombre_zip, url in URLS:
    ruta_zip = DEST_DIR / nombre_zip
    if not ruta_zip.exists() or ruta_zip.stat().st_size < 1000000:
        descargar_con_progreso(url, ruta_zip)
    else:
        print(f"Archivo ya descargado: {nombre_zip} ({ruta_zip.stat().st_size / (1024*1024):.1f} MB)")
        
    print(f"Extrayendo {nombre_zip} en {DEST_DIR}...")
    with zipfile.ZipFile(ruta_zip, "r") as z:
        z.extractall(DEST_DIR)
    print(f"Extracción completada: {nombre_zip}")

print("\nVerificando binarios extraídos:")
for item in DEST_DIR.iterdir():
    if item.is_file() and item.suffix.lower() in (".exe", ".dll"):
        print(f"  {item.name} ({item.stat().st_size / (1024*1024):.2f} MB)")
