"""Diagnostico de audio:

    python -m hacu.voz                 # lista microfonos y altavoces
    python -m hacu.voz --probar        # graba 3 s y los reproduce
    python -m hacu.voz --calibrar      # mide el ruido de la sala
    python -m hacu.voz --hablar "hola" # prueba el sintetizador elegido

Es lo primero que hay que correr al llegar a la sala, antes de levantar el modelo.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
from dataclasses import replace

from ..config import AppConfig
from . import ServicioDeVoz
from .dispositivos import comprobar, listar_dispositivos
from .microfono import AudioNoDisponible, Microfono, _sounddevice
from .sintetizador import (
    _ruta_de_voz,
    crear_sintetizador,
    localizar_piper,
    sintetizar_a_archivo,
)


def _listar() -> int:
    try:
        dispositivos = listar_dispositivos()
    except AudioNoDisponible as error:
        print(f"❌ {error}")
        return 1
    print("\n--- DISPOSITIVOS DE AUDIO ---")
    for dispositivo in dispositivos:
        print("  " + dispositivo.etiqueta())
    print("\n  🎤 = entrada por defecto   🔊 = salida por defecto")
    print("  Se fijan con HACU_ENTRADA=<indice> y HACU_SALIDA=<indice>.")
    return 0


def _probar(config: AppConfig, segundos: float) -> int:
    micro = Microfono(config.voz, logging.getLogger("hacu"))
    sd = _sounddevice()
    print(f"\n🎤 Grabando {segundos:.0f} s... habla ahora.")
    micro.iniciar()
    inicio = time.perf_counter()
    while time.perf_counter() - inicio < segundos:
        barra = "█" * min(40, int(micro.nivel * 400))
        print(f"\r   nivel |{barra:<40}| {micro.nivel:.4f}", end="", flush=True)
        time.sleep(0.05)
    audio = micro.detener()
    print(f"\n   capturadas {audio.size} muestras ({audio.size / config.voz.frecuencia:.1f} s)")
    if not audio.size:
        print("❌ No entro audio. Revisa el microfono y los permisos del sistema.")
        return 1
    pico = float(abs(audio).max())
    print(f"   pico {pico:.3f}" + ("  ⚠️  muy bajo, acercate al microfono" if pico < 0.02 else ""))
    print("🔊 Reproduciendo lo grabado...")
    sd.play(audio, config.voz.frecuencia, device=config.voz.dispositivo_salida)
    sd.wait()
    return 0


def _calibrar(config: AppConfig) -> int:
    print(f"\n🤫 Midiendo el ruido de la sala {config.voz.calibracion_ms} ms. Silencio, por favor.")
    micro = Microfono(config.voz, logging.getLogger("hacu"))
    umbral = micro.calibrar()
    print(f"   umbral de disparo sugerido: {umbral:.4f}")
    print("   (se aplica solo con HACU_VOZ_AUTO=1; en pulsar-para-hablar no se usa)")
    return 0


def _descargar(config: AppConfig) -> int:
    """Baja la voz de Piper con el descargador del propio paquete."""
    voz = config.voz.piper_voz
    destino = config.voz.carpeta_voces
    destino.mkdir(parents=True, exist_ok=True)
    print(f"\n⬇️  Descargando la voz {voz} en {destino}")
    orden = [sys.executable, "-m", "piper.download_voices", voz, "--data-dir", str(destino)]
    try:
        completado = subprocess.run(orden, check=False)
    except FileNotFoundError:
        print("❌ Falta el paquete de Piper. Instala: pip install piper-tts")
        return 1
    if completado.returncode:
        print("❌ La descarga fallo. Comprueba el nombre de la voz:")
        print(f"   {sys.executable} -m piper.download_voices --help")
        return 1
    print("✅ Voz descargada. Pruebala con --hablar \"Hola, soy Hacu\"")
    return 0


def _hablar(config: AppConfig, texto: str, guardar: Path | None) -> int:
    log = logging.getLogger("hacu")
    logging.basicConfig(level=logging.INFO, format="   %(message)s")
    piper = localizar_piper(config.voz)
    local = _ruta_de_voz(config.voz)
    print(f"\n   Piper            : {' '.join(piper.base) if piper else 'no encontrado'}")
    print(f"   voz de Piper     : {config.voz.piper_voz} "
          f"({local if local else 'no esta en la carpeta del proyecto'})")

    if guardar is not None:
        codigo = _guardar_wav(config, texto, guardar)
        if codigo:
            return codigo

    tts = crear_sintetizador(config.voz, log)
    print(f"   motor            : {getattr(tts, 'nombre', '?')}")
    print(f"🔊 Diciendo: {texto!r}")
    inicio = time.perf_counter()
    tts.decir(texto)
    # `esperar` bloquea hasta que no queda nada pendiente. Sondear `hablando` en
    # un bucle dejaba una ventana en la que la frase ya habia salido de la cola
    # pero todavia no sonaba, y el cierre se comia la ultima silaba.
    if not tts.esperar(timeout=60):
        print("⚠️  La sintesis no termino en 60 s.")
    tts.cerrar()
    print(f"   tardo {time.perf_counter() - inicio:.2f} s")
    return 0


def _guardar_wav(config: AppConfig, texto: str, destino: Path) -> int:
    """Sintetiza a fichero para comprobar si lo que se corta es la voz o la salida."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    try:
        escrito = sintetizar_a_archivo(config.voz, texto, destino)
    except Exception as error:
        print(f"❌ Piper no pudo generar el WAV: {error}")
        return 1
    if escrito is None:
        print("⚠️  Sin Piper no se puede volcar a fichero; la voz del sistema no lo permite.")
        return 0
    bytes_wav = escrito.stat().st_size
    duracion = _duracion_wav(escrito)
    print(f"💾 {escrito} ({bytes_wav / 1024:.0f} KB"
          + (f", {duracion:.2f} s" if duracion else "") + ")")
    print("   Abrelo en la maquina y escuchalo: si el WAV suena entero, el recorte")
    print("   esta en la salida de audio o en el escritorio remoto, no en HACU.")
    return 0


def _duracion_wav(ruta: Path) -> float | None:
    """Duracion en segundos leyendo solo la cabecera, sin dependencias extra."""
    try:
        with wave.open(str(ruta), "rb") as fichero:
            return fichero.getnframes() / float(fichero.getframerate())
    except Exception:
        return None


def _transcribir(config: AppConfig, ruta: Path) -> int:
    """Reconocimiento a partir de un fichero: la unica via sin microfono."""
    if not ruta.exists():
        print(f"❌ No existe {ruta}")
        return 1
    print(f"\n📝 Transcribiendo {ruta.name} ...")
    servicio = ServicioDeVoz(replace(config.voz, activa=True), logging.getLogger("hacu"))
    try:
        inicio = time.perf_counter()
        texto = servicio.transcribir_archivo(ruta)
    except ImportError:
        print("❌ Falta soundfile para leer el WAV: pip install soundfile")
        return 1
    except Exception as error:
        print(f"❌ Fallo el reconocimiento: {error}")
        return 1
    finally:
        servicio.cerrar()
    print(f"   ({time.perf_counter() - inicio:.2f} s)")
    print(f"   → {texto!r}" if texto.strip() else "   → no se entendio nada")
    return 0


# Frases de exhibicion con la palabra que TIENE que sobrevivir. Son justo los
# nombres propios que un reconocedor generico destroza: medido, sin sembrar el
# vocabulario "AudacIA" sale "a UDAC ya" y "Holosand" sale "olo San".
_FRASES_AUTOPRUEBA: tuple[tuple[str, str], ...] = (
    ("¿Qué proyectos tiene AudacIA?", "audacia"),
    ("Cuéntame todo sobre Holosand y el sensor Kinect.", "holosand"),
    ("Explícame el proyecto Orion para personas con discapacidad visual.", "orion"),
    ("Hola, me llamo Camila y estudio ingeniería de sistemas.", "camila"),
)


def _autoprueba(config: AppConfig) -> int:
    """Cierra el circuito sin microfono: HACU se dicta a si mismo y se transcribe.

    Es la unica forma de saber si el reconocimiento funciona en esta maquina
    cuando no hay entrada de audio —una sesion remota, un microfono que aun no ha
    llegado— y ademas comprueba lo que de verdad importa en escena: que los
    nombres propios de la exhibicion sobrevivan al reconocedor.
    """
    from ..routing import normalizar  # noqa: PLC0415

    logging.basicConfig(level=logging.ERROR)
    log = logging.getLogger("hacu")
    temporal = Path(tempfile.mkdtemp(prefix="hacu-autoprueba-"))
    print("\n🔁 Autoprueba de voz: sintetizar → transcribir, sin microfono.")

    servicio = ServicioDeVoz(replace(config.voz, activa=True), log)
    aciertos = 0
    try:
        for frase, clave in _FRASES_AUTOPRUEBA:
            destino = temporal / f"{clave}.wav"
            try:
                if sintetizar_a_archivo(config.voz, frase, destino) is None:
                    print("⚠️  Hace falta Piper para la autoprueba: pip install piper-tts")
                    return 1
            except Exception as error:
                print(f"❌ No se pudo sintetizar: {error}")
                return 1

            inicio = time.perf_counter()
            try:
                salida = servicio.transcribir_archivo(destino)
            except Exception as error:
                print(f"❌ No se pudo transcribir: {error}")
                return 1
            ok = normalizar(clave) in normalizar(salida)
            aciertos += ok
            print(f"\n  {'✅' if ok else '❌'} [{time.perf_counter() - inicio:4.1f}s] {salida.strip()}")
            print(f"      dicho: {frase}")
            if not ok:
                print(f"      se perdio la palabra {clave!r}")
    finally:
        servicio.cerrar()
        shutil.rmtree(temporal, ignore_errors=True)

    total = len(_FRASES_AUTOPRUEBA)
    print(f"\n  {aciertos}/{total} nombres propios reconocidos")
    if aciertos < total:
        print("  Si falla mas de uno, revisa el vocabulario en VozConfig o sube HACU_STT=medium.")
    return 0 if aciertos == total else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Diagnostico de audio de HACU")
    parser.add_argument("--probar", action="store_true", help="graba y reproduce")
    parser.add_argument("--segundos", type=float, default=3.0)
    parser.add_argument("--calibrar", action="store_true", help="mide el ruido de sala")
    parser.add_argument("--hablar", type=str, default="", help="frase de prueba del sintetizador")
    parser.add_argument("--descargar", action="store_true", help="baja la voz de Piper")
    parser.add_argument("--guardar", type=Path, default=None,
                        help="vuelca la frase a un WAV en vez de fiarse del altavoz")
    parser.add_argument("--transcribir", type=Path, default=None,
                        help="reconoce un WAV del disco (prueba el STT sin microfono)")
    parser.add_argument("--autoprueba", action="store_true",
                        help="sintetiza frases y se las transcribe: circuito completo sin microfono")
    args = parser.parse_args(argv[1:])

    config = replace(AppConfig.from_env(), voz=replace(AppConfig.from_env().voz, activa=True))
    # Que no haya entrada de audio no puede impedir el resto del diagnostico:
    # `--autoprueba`, `--transcribir`, `--hablar` y `--descargar` existen
    # precisamente para las maquinas donde no hay microfono.
    codigo = _listar()
    necesita_entrada = not (args.autoprueba or args.transcribir or args.hablar or args.descargar)
    if codigo and necesita_entrada:
        return codigo
    codigo = 0

    problemas = comprobar(config.voz)
    if problemas:
        print("\n⚠️  Problemas detectados:")
        for problema in problemas:
            print(f"   - {problema}")

    if args.descargar:
        codigo = max(codigo, _descargar(config))
    if args.calibrar:
        codigo = max(codigo, _calibrar(config))
    if args.probar:
        codigo = max(codigo, _probar(config, args.segundos))
    if args.hablar:
        codigo = max(codigo, _hablar(config, args.hablar, args.guardar))
    if args.transcribir:
        codigo = max(codigo, _transcribir(config, args.transcribir))
    if args.autoprueba:
        codigo = max(codigo, _autoprueba(config))
    if not (args.calibrar or args.probar or args.hablar or args.descargar
            or args.transcribir or args.autoprueba):
        servicio = ServicioDeVoz(config.voz, logging.getLogger("hacu"))
        print(f"\n   oido : {'listo' if servicio.disponible else 'NO disponible'}")
        print(f"   voz  : {servicio.motor if servicio.puede_hablar else 'NO disponible'}")
        servicio.cerrar()
        print("\n   Prueba completa: python -m hacu.voz --probar --hablar \"Hola, soy Hacu\"")
    return codigo


if __name__ == "__main__":
    sys.exit(main(sys.argv))
