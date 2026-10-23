"""
Generador automático de certificados TLS para desarrollo local (localhost / 127.0.0.1).
Utiliza cryptography para generar certificados compatibles con Uvicorn y TLS/HTTPS.
"""

import datetime
import ipaddress
import os
import sys
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def generar_certificados_desarrollo(certs_dir: str):
    os.makedirs(certs_dir, exist_ok=True)
    key_file = os.path.join(certs_dir, "localhost-key.pem")
    cert_file = os.path.join(certs_dir, "localhost.pem")

    print(f"[INFO] Generando clave privada RSA de 2048 bits...")
    key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )

    # Nombres alternativos del sujeto (SAN) obligatorios para TLS moderno
    import socket
    san_list = [
        x509.DNSName("localhost"),
        x509.DNSName("inventario-audacia.local"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        x509.IPAddress(ipaddress.IPv6Address("::1")),
    ]
    try:
        host_actual = socket.gethostname()
        if host_actual:
            san_list.append(x509.DNSName(host_actual.lower()))
            san_list.append(x509.DNSName(f"{host_actual.lower()}.local"))
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip_lan = s.getsockname()[0]
            if ip_lan and ip_lan not in ("127.0.0.1", "::1"):
                san_list.append(x509.IPAddress(ipaddress.IPv4Address(ip_lan)))
        finally:
            s.close()
    except Exception:
        pass

    san = x509.SubjectAlternativeName(san_list)

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
        .not_valid_after(ahora + datetime.timedelta(days=365))
        .add_extension(san, critical=False)
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .sign(key, hashes.SHA256(), default_backend())
    )

    # Escribir clave privada
    with open(key_file, "wb") as f:
        f.write(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

    # Escribir certificado
    with open(cert_file, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    print(f"[ÉXITO] Certificados generados correctamente:")
    print(f" -> Certificado: {cert_file}")
    print(f" -> Clave privada: {key_file}")


if __name__ == "__main__":
    destino = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "certs")
    generar_certificados_desarrollo(os.path.abspath(destino))
