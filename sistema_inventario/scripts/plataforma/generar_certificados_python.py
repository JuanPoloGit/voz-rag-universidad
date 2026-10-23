"""
Generador automático de certificados TLS para desarrollo local y red LAN.
Crea certificados compatibles con navegadores modernos (Chromium, Firefox, Safari),
Uvicorn ASGI y dispositivos conectados a la misma red local (LAN).
"""

import datetime
import ipaddress
import os
import socket
import sys
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def obtener_direcciones_red():
    """Detecta el hostname e IPs locales para incluirlos en el certificado (SAN)."""
    nombres_dns = ["localhost"]
    ips = [ipaddress.IPv4Address("127.0.0.1"), ipaddress.IPv6Address("::1")]

    try:
        host = socket.gethostname().lower()
        if host and host not in nombres_dns:
            nombres_dns.append(host)
            nombres_dns.append(f"{host}.local")
    except Exception:
        pass

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip_lan = s.getsockname()[0]
            if ip_lan and ip_lan not in ("127.0.0.1", "0.0.0.0"):
                ip_obj = ipaddress.IPv4Address(ip_lan)
                if ip_obj not in ips:
                    ips.append(ip_obj)
        finally:
            s.close()
    except Exception:
        pass

    # Intentar resolver todas las IPs asociadas al host
    try:
        _, _, ip_list = socket.gethostbyname_ex(socket.gethostname())
        for ip in ip_list:
            if ip not in ("127.0.0.1", "0.0.0.0") and not ip.startswith("169.254"):
                ip_obj = ipaddress.IPv4Address(ip)
                if ip_obj not in ips:
                    ips.append(ip_obj)
    except Exception:
        pass

    return nombres_dns, ips


def generar_certificados_desarrollo(certs_dir: str, forzar: bool = False):
    os.makedirs(certs_dir, exist_ok=True)
    key_file = os.path.join(certs_dir, "localhost-key.pem")
    cert_file = os.path.join(certs_dir, "localhost.pem")

    if not forzar and os.path.exists(key_file) and os.path.exists(cert_file):
        try:
            with open(cert_file, "rb") as f:
                c = x509.load_pem_x509_certificate(f.read(), default_backend())
            ahora = datetime.datetime.now(datetime.timezone.utc)
            if c.not_valid_after_utc > ahora + datetime.timedelta(days=30):
                print(f"[INFO] Certificado TLS existente válido hasta {c.not_valid_after_utc}. No requiere regeneración.")
                return
        except Exception:
            pass

    print(f"[INFO] Generando nueva clave privada RSA de 2048 bits...")
    key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )

    nombres_dns, ips = obtener_direcciones_red()
    san_entries = [x509.DNSName(d) for d in nombres_dns] + [x509.IPAddress(ip) for ip in ips]
    san = x509.SubjectAlternativeName(san_entries)

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "CO"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Bogota"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "AudacIA Inventario Dev"),
        x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
    ])

    ahora = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - datetime.timedelta(days=1))
        .not_valid_after(ahora + datetime.timedelta(days=825))  # Límite moderno TLS (825 días)
        .add_extension(san, critical=False)
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([
                ExtendedKeyUsageOID.SERVER_AUTH,
                ExtendedKeyUsageOID.CLIENT_AUTH,
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256(), default_backend())
    )

    with open(key_file, "wb") as f:
        f.write(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

    with open(cert_file, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    print(f"[ÉXITO] Certificados generados correctamente:")
    print(f" -> Certificado: {cert_file}")
    print(f" -> Clave privada: {key_file}")
    print(f" -> SANs incluidos: {[s.value for s in san_entries]}")


if __name__ == "__main__":
    destino = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "certs")
    forzar_flag = "--force" in sys.argv
    generar_certificados_desarrollo(os.path.abspath(destino), forzar=forzar_flag)
