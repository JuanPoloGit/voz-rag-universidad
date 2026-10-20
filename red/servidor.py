"""Servidor HTTP/HTTPS autónomo para pruebas de diagnóstico de red y micrófono.

Utiliza exclusivamente la biblioteca estándar de Python (http.server, ssl, socket, threading).
No importa módulos de Django ni componentes de Haku.
"""

from __future__ import annotations

import argparse
import os
import signal
import ssl
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DIRECTORIO_RAIZ = Path(__file__).resolve().parent
HOST_PREDETERMINADO = "172.16.130.73"
PUERTO_HTTP_PREDETERMINADO = 8765
PUERTO_HTTPS_PREDETERMINADO = 8766


class ManejadorDiagnostico(SimpleHTTPRequestHandler):
    """Manejador HTTP que sirve los archivos estáticos de red/ y añade cabeceras anti-caché."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIRECTORIO_RAIZ), **kwargs)

    def end_headers(self) -> None:
        # Forzar no-cache para evitar diagnósticos falsos al alternar esquemas
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, format: str, *args) -> None:
        # Registro en consola con prefijo claro
        sys.stdout.write(f"[{self.log_date_time_string()}] {self.address_string()} - {format % args}\n")
        sys.stdout.flush()


def crear_servidor_http(host: str, puerto: int) -> ThreadingHTTPServer:
    servidor = ThreadingHTTPServer((host, puerto), ManejadorDiagnostico)
    return servidor


def crear_servidor_https(host: str, puerto: int, ruta_cert: Path, ruta_key: Path) -> ThreadingHTTPServer:
    if not ruta_cert.exists():
        raise FileNotFoundError(f"No se encontró el certificado en: {ruta_cert}")
    if not ruta_key.exists():
        raise FileNotFoundError(f"No se encontró la clave privada en: {ruta_key}")

    contexto_ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    contexto_ssl.load_cert_chain(certfile=str(ruta_cert), keyfile=str(ruta_key))

    servidor = ThreadingHTTPServer((host, puerto), ManejadorDiagnostico)
    servidor.socket = contexto_ssl.wrap_socket(servidor.socket, server_side=True)
    return servidor


def parsear_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Servidor de diagnóstico de red AudacIA")
    parser.add_argument("--host", default=HOST_PREDETERMINADO, help=f"Dirección IPv4 a enlazar (default: {HOST_PREDETERMINADO})")
    parser.add_argument("--modo", choices=["http", "https", "dual"], default="dual",
                        help="Modo de ejecución: http, https, o dual (ambos puertos simultáneos)")
    parser.add_argument("--puerto-http", type=int, default=PUERTO_HTTP_PREDETERMINADO, help=f"Puerto HTTP (default: {PUERTO_HTTP_PREDETERMINADO})")
    parser.add_argument("--puerto-https", type=int, default=PUERTO_HTTPS_PREDETERMINADO, help=f"Puerto HTTPS (default: {PUERTO_HTTPS_PREDETERMINADO})")
    parser.add_argument("--cert", default=str(DIRECTORIO_RAIZ / "certificados" / "cert.pem"), help="Ruta al archivo cert.pem")
    parser.add_argument("--key", default=str(DIRECTORIO_RAIZ / "certificados" / "key.pem"), help="Ruta al archivo key.pem")
    return parser.parse_args()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    args = parsear_argumentos()
    servidores = []

    print("=" * 70)
    print(" AUDACIA -- SERVIDOR DE DIAGNOSTICO DE RED Y MICROFONO")
    print("=" * 70)
    print(f"Directorio servido: {DIRECTORIO_RAIZ}")
    print(f"Direccion IP base:  {args.host}")

    if args.modo in ("http", "dual"):
        try:
            http_srv = crear_servidor_http(args.host, args.puerto_http)
            servidores.append(("HTTP", http_srv))
            print(f"[HTTP]  Servidor activo en:  http://{args.host}:{args.puerto_http}/")
        except OSError as e:
            print(f"[ERROR] Al iniciar servidor HTTP en {args.host}:{args.puerto_http} -> {e}")
            if args.modo == "http":
                sys.exit(1)

    if args.modo in ("https", "dual"):
        ruta_cert = Path(args.cert)
        ruta_key = Path(args.key)
        try:
            https_srv = crear_servidor_https(args.host, args.puerto_https, ruta_cert, ruta_key)
            servidores.append(("HTTPS", https_srv))
            print(f"[HTTPS] Servidor activo en: https://{args.host}:{args.puerto_https}/")
        except FileNotFoundError as e:
            print(f"[AVISO] Certificado no disponible: {e}")
            print("        Ejecuta primero: powershell -File red/generar_certificados.ps1")
            if args.modo == "https":
                sys.exit(1)
        except Exception as e:
            print(f"[ERROR] Al iniciar servidor HTTPS en {args.host}:{args.puerto_https} -> {e}")
            if args.modo == "https":
                sys.exit(1)

    if not servidores:
        print("[ERROR] No se pudo iniciar ningun servidor. Abortando.")
        sys.exit(1)

    print("-" * 70)
    print("Presiona Ctrl+C en cualquier momento para detener los servidores.")
    print("-" * 70)

    hilos = []
    for nombre, srv in servidores:
        hilo = threading.Thread(target=srv.serve_forever, daemon=True, name=f"Thread-{nombre}")
        hilo.start()
        hilos.append((nombre, srv, hilo))

    def detener(signum=None, frame=None):
        print("\nDeteniendo servidores de diagnóstico...")
        for nombre, srv, _ in hilos:
            print(f"Cerrando {nombre}...")
            srv.shutdown()
            srv.server_close()
        print("Servidores detenidos correctamente.")
        sys.exit(0)

    signal.signal(signal.SIGINT, detener)
    signal.signal(signal.SIGTERM, detener)

    # Mantener el hilo principal vivo
    try:
        while True:
            threading.Event().wait(1.0)
    except (KeyboardInterrupt, SystemExit):
        detener()


if __name__ == "__main__":
    main()
