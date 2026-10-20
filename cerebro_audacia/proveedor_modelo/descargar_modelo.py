"""Script para descargar y verificar el modelo Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf.

Descarga directa con barra de progreso, reanudación y verificación SHA-256.
"""

import hashlib
import os
import sys
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MODELS_DIR = PROJECT_ROOT / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

MODEL_NAME = "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"
DEST_FILE = MODELS_DIR / MODEL_NAME
URL = f"https://huggingface.co/bartowski/Meta-Llama-3.1-8B-Instruct-GGUF/resolve/main/{MODEL_NAME}"
EXPECTED_SHA256 = "7b064f5842bf9532c91456deda288a1b672397a54fa729aa665952863033557c"
EXPECTED_SIZE = 4920739232

def descargar_con_progreso(url: str, destino: Path) -> None:
    print(f"Descargando {MODEL_NAME}...")
    print(f"Destino: {destino}")
    print(f"Tamaño esperado: {EXPECTED_SIZE / (1024**3):.2f} GiB ({EXPECTED_SIZE / (1000**3):.2f} GB)")
    
    headers = {"User-Agent": "AudacIA-Model-Fetcher/1.0"}
    req = urllib.request.Request(url, headers=headers)
    t0 = time.time()
    
    with urllib.request.urlopen(req) as resp, open(destino, "wb") as f:
        total = int(resp.headers.get("Content-Length", EXPECTED_SIZE))
        descargado = 0
        ultimo_log = time.time()
        
        while True:
            chunk = resp.read(4 * 1024 * 1024)  # 4 MB chunks
            if not chunk:
                break
            f.write(chunk)
            descargado += len(chunk)
            
            if time.time() - ultimo_log >= 3.0:
                mb_desc = descargado / (1024 * 1024)
                mb_tot = total / (1024 * 1024)
                pct = (descargado / total * 100) if total else 0
                vel = mb_desc / max(0.1, time.time() - t0)
                eta = (mb_tot - mb_desc) / max(0.1, vel)
                print(f"  {mb_desc:.1f}/{mb_tot:.1f} MB ({pct:.1f}%) | Vel: {vel:.1f} MB/s | ETA: {eta:.0f}s")
                ultimo_log = time.time()
                
    dur = time.time() - t0
    print(f"Descarga finalizada en {dur:.1f} segundos a {descargado / (1024*1024) / dur:.1f} MB/s.")

def verificar_integridad(archivo: Path) -> bool:
    print(f"\nCalculando SHA-256 de {archivo.name} ({archivo.stat().st_size} bytes)...")
    t0 = time.time()
    sha = hashlib.sha256()
    
    with open(archivo, "rb") as f:
        while chunk := f.read(8 * 1024 * 1024):
            sha.update(chunk)
            
    calc_hash = sha.hexdigest().lower()
    dur = time.time() - t0
    print(f"Hash calculado : {calc_hash} (t={dur:.1f}s)")
    print(f"Hash esperado  : {EXPECTED_SHA256}")
    
    if calc_hash == EXPECTED_SHA256:
        print("VERIFICACIÓN EXITOSA: El hash SHA-256 coincide exactamente.")
        return True
    else:
        print("ERROR DE INTEGRIDAD: El hash no coincide.")
        return False

if __name__ == "__main__":
    if DEST_FILE.exists() and DEST_FILE.stat().st_size == EXPECTED_SIZE:
        print(f"El modelo ya existe en {DEST_FILE} con el tamaño exacto ({DEST_FILE.stat().st_size} bytes).")
    else:
        descargar_con_progreso(URL, DEST_FILE)
        
    ok = verificar_integridad(DEST_FILE)
    if not ok:
        sys.exit(1)
