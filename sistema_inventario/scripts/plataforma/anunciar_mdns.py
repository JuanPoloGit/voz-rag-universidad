"""
Servicio mDNS para publicar el nombre de dominio local 'inventario-audacia.local'
en la red Wi-Fi / LAN utilizando Zeroconf (Multicast DNS, RFC 6762).
"""

import argparse
import ipaddress
import logging
import os
import signal
import socket
import sys
import time
from typing import Optional

try:
    from zeroconf import IPVersion, ServiceInfo, Zeroconf
except ImportError:
    print("[ERROR] El paquete 'zeroconf' no está instalado en el entorno virtual.")
    print("        Instálalo con: pip install zeroconf")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mDNS")


def obtener_ip_lan() -> str:
    """Detecta la IP local de la interfaz activa conectada a la red."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


class AnunciadorMDNS:
    """Registra y anuncia el servicio HTTPS y el host local en la red local."""

    def __init__(self, hostname: str = "inventario-audacia.local", port: int = 8000, ip: Optional[str] = None):
        self.raw_hostname = hostname.rstrip(".")
        self.server_name = f"{self.raw_hostname}."
        self.port = port
        self.ip = ip or obtener_ip_lan()
        self.zeroconf: Optional[Zeroconf] = None
        self.info: Optional[ServiceInfo] = None
        self._running = False

    def iniciar(self):
        logger.info(f"Iniciando anunciador mDNS para '{self.raw_hostname}' -> {self.ip}:{self.port}...")
        self.zeroconf = Zeroconf(ip_version=IPVersion.V4Only)

        desc = {
            "path": "/",
            "app": "Inventario AudacIA",
            "version": "1.0",
        }

        self.info = ServiceInfo(
            type_="_https._tcp.local.",
            name=f"Inventario AudacIA._https._tcp.local.",
            addresses=[socket.inet_aton(self.ip)],
            port=self.port,
            properties=desc,
            server=self.server_name,
        )

        self.zeroconf.register_service(self.info)
        self._running = True
        logger.info("[OK] Servicio mDNS activo.")
        logger.info(f"     URL en la red local: https://{self.raw_hostname}:{self.port}/")
        logger.info(f"     IP de destino:       https://{self.ip}:{self.port}/")

    def detener(self):
        if self._running and self.zeroconf and self.info:
            logger.info("Deteniendo anuncio mDNS y liberando registros...")
            try:
                self.zeroconf.unregister_service(self.info)
            except Exception:
                pass
            try:
                self.zeroconf.close()
            except Exception:
                pass
            self._running = False
            logger.info("[OK] mDNS detenido.")

    def esperar(self):
        def sig_handler(sig, frame):
            self.detener()
            sys.exit(0)

        signal.signal(signal.SIGINT, sig_handler)
        signal.signal(signal.SIGTERM, sig_handler)

        try:
            while self._running:
                time.sleep(1)
        except (KeyboardInterrupt, SystemExit):
            self.detener()


def main():
    parser = argparse.ArgumentParser(description="Publicar 'inventario-audacia.local' en la LAN vía mDNS.")
    parser.add_argument("--host", default="inventario-audacia.local", help="Nombre mDNS (default: inventario-audacia.local)")
    parser.add_argument("--port", type=int, default=8000, help="Puerto del servicio (default: 8000)")
    parser.add_argument("--ip", default=None, help="IP específica para anunciar (default: detección automática)")

    args = parser.parse_args()
    anunciador = AnunciadorMDNS(hostname=args.host, port=args.port, ip=args.ip)
    anunciador.iniciar()
    anunciador.esperar()


if __name__ == "__main__":
    main()
