import json
import urllib.request
import os
import sys

# Ensure current directory is in path
sys.path.insert(0, os.getcwd())

from hacu.context import estimar_tokens_texto, estimar_tokens_prompt

def tokenizar_llama_server(texto: str) -> list[int]:
    req = urllib.request.Request(
        "http://127.0.0.1:8080/tokenize",
        data=json.dumps({"content": texto}).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())["tokens"]

muestras = {
    "espanol_comun": "Hola, ¿cómo estás hoy? Quisiera consultar sobre el inventario disponible.",
    "json": '{"accion": "consultar_stock", "item_id": 1042, "ubicacion": "Almacen Central", "activo": true}',
    "codigo": "def calcular_total(items):\n    return sum(x.precio * x.cantidad for x in items if x.disponible)",
    "numeros": "1234567890 9876543210 1122334455 9988776655",
    "simbolos": "!@#$%^&*()_+=-[]{};:',.<>/?|~`",
    "cjk": "你好世界，这是一个关于大学语音导览系统的测试。",
    "instrucciones_largas": (
        "Eres HACU, la voz del centro de divulgación e innovación tecnológica de la Universidad de la Costa. "
        "Hablas en español colombiano costeño formal y afable. Explica con claridad cómo funciona el sistema "
        "de inventario automatizado y responde a las preguntas de los visitantes sin extenderte demasiado."
    ),
    "rag_fragmento": (
        "PROYECTO: HOLOSAND (Arena Holográfica Interactiva)\n"
        "DESCRIPCIÓN: Sistema de proyección aumentada sobre arena con sensor de profundidad Kinect v2.\n"
        "SEGURIDAD: No introducir objetos metálicos ni líquidos en el cajón de arena durante el escaneo.\n"
        "UBICACIÓN: Sala B, Stand 4."
    )
}

print(f"{'Muestra':22} | {'Real (llama.cpp)':16} | {'Estimado':8} | {'Ratio':6} | {'Margen Seguro?'}")
print("-" * 70)

for k, txt in muestras.items():
    toks = tokenizar_llama_server(txt)
    real_count = len(toks)
    est_count = estimar_tokens_texto(txt)
    ratio = est_count / real_count
    seguro = "SI (sobrestima)" if est_count >= real_count else "NO (subestima!)"
    print(f"{k:22} | {real_count:16} | {est_count:8} | {ratio:5.2f}x | {seguro}")
