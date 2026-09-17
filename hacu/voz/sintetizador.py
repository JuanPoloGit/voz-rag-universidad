"""Sintesis de voz, con tres motores y un selector.

La eleccion no es de gusto: es de riesgo de exhibicion.

- `SintetizadorPiperEnProceso` es la voz buena y la opcion por defecto. Carga el
  modelo .onnx una sola vez y sintetiza dentro del proceso, en CPU, sin tocar la
  VRAM que necesitan el LLM y el reconocimiento.
- `SintetizadorPiper` hace lo mismo lanzando `python -m piper` por frase. Queda
  como respaldo para cuando no se puede cargar la voz en proceso, y **solo** para
  eso: arrancar el interprete y el modelo en cada punto costaba entre catorce y
  dieciseis segundos de silencio por frase.
- `SintetizadorSistema` usa el sintetizador del sistema operativo (SAPI5 en
  Windows, espeak-ng en Linux). Suena a robot de los noventa, pero existe en
  cualquier maquina y hace que HACU hable el primer dia, sin descargar nada.
- `SintetizadorMudo` no habla. Es lo que se usa en las pruebas y cuando el
  operador quiere la interfaz sin sonido.

`crear_sintetizador` elige, prueba y cae al siguiente si el elegido no arranca:
una exhibicion no puede quedarse muda porque falte un .onnx.

Todos hablan por frases y todos se pueden interrumpir a mitad: si el visitante
vuelve a pulsar el boton, HACU se calla en el acto en vez de terminar el parrafo.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import queue
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from ..config import VozConfig
from .pronunciacion import LEXICO, compilar, para_voz


class Sintetizador(Protocol):
    """Contrato minimo de cualquier motor de voz."""

    nombre: str

    def decir(self, texto: str) -> None:
        """Encola una frase. No bloquea."""

    def silenciar(self) -> None:
        """Corta lo que se este diciendo y vacia la cola."""

    def esperar(self, timeout: float | None = None) -> bool:
        """Bloquea hasta que termine de hablar. True si no quedo nada pendiente."""

    @property
    def hablando(self) -> bool: ...

    def cerrar(self, drenar: bool = True, timeout: float = 30.0) -> None: ...


class SintetizadorMudo:
    """No habla. Registra lo dicho para que las pruebas puedan verificarlo."""

    nombre = "mudo"

    def __init__(self) -> None:
        self.dicho: list[str] = []

    def decir(self, texto: str) -> None:
        if texto.strip():
            self.dicho.append(texto.strip())

    def silenciar(self) -> None:
        return None

    def esperar(self, timeout: float | None = None) -> bool:  # noqa: ARG002
        return True

    @property
    def hablando(self) -> bool:
        return False

    def cerrar(self, drenar: bool = True, timeout: float = 30.0) -> None:  # noqa: ARG002
        return None


class _SintetizadorEnCola:
    """Base comun: una cola, un hilo, un corte limpio y un cierre que NO corta.

    Dos detalles que costaron una silaba cada uno:

    - `cerrar()` drena por defecto. Antes purgaba, y purgar mientras la tarjeta
      todavia tiene medio buffer por reproducir se come el final de la frase:
      "Hola, soy Hacu" se oia "Hola, soy Ha-". Quien quiera cortar de verdad
      —el visitante que vuelve a pulsar el boton— llama a `silenciar()`.
    - `hablando` se calcula con un contador de pendientes, no con la cola mas un
      Event. Entre que el hilo saca la frase de la cola y marca el Event hay una
      ventana en la que la cola esta vacia y el Event sin poner: quien consultara
      justo ahi veia `hablando == False` y cerraba encima de la frase.
    """

    nombre = "generico"

    def __init__(self, logger: logging.Logger,
                 pronunciaciones: tuple[tuple[str, str], ...] = ()) -> None:
        self._log = logger
        # Como se escribe y como se dice no es lo mismo. Se aplica aqui, en el
        # ultimo paso antes del motor, para que la pantalla y la memoria guarden
        # la ortografia de verdad y solo cambie lo que sale por el altavoz.
        self._lexico = compilar(pronunciaciones or LEXICO)
        self._cola: queue.Queue[str | None] = queue.Queue()
        self._cortar = threading.Event()
        self._vivo = True
        self._pendientes = 0
        self._condicion = threading.Condition()
        self._hilo = threading.Thread(target=self._bucle, name="hacu-tts", daemon=True)
        self._hilo.start()

    def decir(self, texto: str) -> None:
        if not texto.strip() or not self._vivo:
            return
        self._cortar.clear()
        with self._condicion:
            self._pendientes += 1
        self._cola.put(texto.strip())

    def silenciar(self) -> None:
        """Corta en seco: vacia la cola y aborta la reproduccion en curso."""
        self._cortar.set()
        descartadas = 0
        while True:
            try:
                self._cola.get_nowait()
                descartadas += 1
            except queue.Empty:
                break
        if descartadas:
            self._descontar(descartadas)
        self._detener_reproduccion()

    @property
    def hablando(self) -> bool:
        with self._condicion:
            return self._pendientes > 0

    def esperar(self, timeout: float | None = None) -> bool:
        """Bloquea hasta que no quede nada por decir. True si termino de hablar."""
        with self._condicion:
            return self._condicion.wait_for(lambda: self._pendientes == 0, timeout)

    def cerrar(self, drenar: bool = True, timeout: float = 30.0) -> None:
        """Termina la frase en curso antes de apagar, salvo que se pida lo contrario."""
        if drenar:
            self.esperar(timeout)
        else:
            self.silenciar()
        self._vivo = False
        self._cola.put(None)
        self._hilo.join(timeout=3.0)

    # ------------------------------------------------------------ a implementar

    def _pronunciar(self, texto: str) -> None:
        raise NotImplementedError

    def _detener_reproduccion(self) -> None:
        return None

    # ---------------------------------------------------------------- internos

    def _descontar(self, cuantas: int = 1) -> None:
        with self._condicion:
            self._pendientes = max(0, self._pendientes - cuantas)
            self._condicion.notify_all()

    def _bucle(self) -> None:
        while True:
            texto = self._cola.get()
            if texto is None:
                return
            try:
                if not self._cortar.is_set():
                    self._pronunciar(para_voz(texto, self._lexico))
            except Exception:
                self._log.error("Fallo al sintetizar %r", texto[:60], exc_info=True)
            finally:
                self._descontar()


@dataclass(frozen=True)
class OrdenPiper:
    """Como invocar a Piper en esta maquina. Lo resuelve `localizar_piper`."""

    base: list[str]
    moderno: bool

    def para(self, voz: str, escala: float) -> list[str]:
        """Linea de ordenes completa para una voz y una velocidad."""
        if self.moderno:
            return [*self.base, "-m", voz, "--output-raw", "--length-scale", f"{escala:.3f}"]
        return [*self.base, "--model", voz, "--output_raw", "--length_scale", f"{escala:.3f}"]


# Silencio que se anade detras de cada frase, en segundos. 0.25 s cubre de sobra
# la latencia tipica de WASAPI compartido (~20-40 ms) y la de un escritorio remoto.
_COLA_SILENCIO_S = 0.25

# Tamano del trozo que se entrega a la tarjeta de una vez, en segundos. Piper
# genera una frase corta en un solo golpe, y escribirla entera dejaba la frase
# completa dentro del buffer del dispositivo: al pedir silencio se cortaba la
# reproduccion, pero lo ya encolado seguia sonando hasta el final de la frase.
# En trozos de 50 ms, callar tarda como mucho eso.
_TROZO_SALIDA_S = 0.05


class SintetizadorPiperEnProceso(_SintetizadorEnCola):
    """Piper cargado dentro del proceso: la voz se carga una vez y ya.

    Es el motor por defecto porque el otro no sirve en escena. Lanzar
    `python -m piper` por frase paga en CADA punto el arranque del interprete,
    la carga de onnxruntime y la del modelo de voz: medido en exhibicion, entre
    catorce y dieciseis segundos de silencio entre frase y frase. Aqui la voz se
    carga al arrancar y cada frase cuesta solo su sintesis.

    Ademas se reproduce por trozos segun los genera Piper y sobre un stream de
    salida que se queda abierto, asi que no hay ni espera a tener la frase entera
    ni chasquido al abrir el dispositivo entre frases.
    """

    nombre = "piper (en proceso)"

    def __init__(self, config: VozConfig, ruta_voz: Path, logger: logging.Logger) -> None:
        from piper import PiperVoice  # noqa: PLC0415

        import sounddevice  # noqa: PLC0415

        self._cfg = config
        self._sd = sounddevice
        self._stream = None
        self._voz = PiperVoice.load(str(ruta_voz))
        self._sintesis = _configuracion_de_sintesis(config)
        self._ruta = ruta_voz
        super().__init__(logger.getChild("tts.piper"), config.pronunciaciones)
        self._calentar()

    def _calentar(self) -> None:
        """Paga la primera inferencia al arrancar y no delante del visitante."""
        try:
            for _ in self._generar("Hola."):
                break
        except Exception:
            self._log.debug("No se pudo precalentar Piper", exc_info=True)

    def _generar(self, texto: str):
        if self._sintesis is not None:
            return self._voz.synthesize(texto, syn_config=self._sintesis)
        return self._voz.synthesize(texto)

    def _pronunciar(self, texto: str) -> None:
        frecuencia = None
        for trozo in self._generar(texto):
            if self._cortar.is_set():
                return
            muestras = np.frombuffer(trozo.audio_int16_bytes, dtype=np.int16)
            muestras = muestras.astype(np.float32) / 32768.0
            if self._sintesis is None:
                muestras = muestras * self._cfg.volumen_tts
            frecuencia = getattr(trozo, "sample_rate", None) or _frecuencia_de_voz(self._cfg)
            if not self._escribir_troceado(muestras, frecuencia):
                return
        if frecuencia and not self._cortar.is_set():
            # Cola de silencio: `write` vuelve cuando la muestra entra en el
            # buffer, no cuando suena. Sin esto se pierde la ultima silaba en
            # salidas con latencia propia (WASAPI compartido, Bluetooth, remoto).
            relleno = np.zeros(int(frecuencia * _COLA_SILENCIO_S), dtype=np.float32)
            self._escribir_troceado(relleno, frecuencia)

    def _escribir_troceado(self, muestras: np.ndarray, frecuencia: int) -> bool:
        """Entrega el audio en trozos cortos. False si hubo que callar a mitad."""
        salto = max(1, int(frecuencia * _TROZO_SALIDA_S))
        for inicio in range(0, len(muestras), salto):
            if self._cortar.is_set():
                return False
            try:
                self._asegurar_stream(frecuencia).write(muestras[inicio:inicio + salto])
            except Exception:
                # `abort()` desde otro hilo puede reventar el write en curso: es
                # exactamente lo que queriamos que pasara.
                return not self._cortar.is_set()
        return True

    def _asegurar_stream(self, frecuencia: int):
        """Un solo stream de salida para toda la sesion, reabierto si hace falta."""
        if self._stream is not None and int(self._stream.samplerate) != int(frecuencia):
            self._cerrar_stream()
        if self._stream is None:
            self._stream = self._sd.OutputStream(
                samplerate=frecuencia, channels=1, dtype="float32",
                device=self._cfg.dispositivo_salida,
            )
        if self._stream.stopped:
            self._stream.start()
        return self._stream

    def _cerrar_stream(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.abort()
            self._stream.close()
        except Exception:
            self._log.debug("Fallo cerrando el stream de salida", exc_info=True)
        self._stream = None

    def _detener_reproduccion(self) -> None:
        if self._stream is not None:
            try:
                self._stream.abort()
            except Exception:
                self._log.debug("Fallo abortando la reproduccion", exc_info=True)

    def cerrar(self, drenar: bool = True, timeout: float = 30.0) -> None:
        super().cerrar(drenar=drenar, timeout=timeout)
        self._cerrar_stream()


def _configuracion_de_sintesis(config: VozConfig):
    """SynthesisConfig de Piper, o None si esta version no lo trae."""
    try:
        from piper import SynthesisConfig  # noqa: PLC0415
    except ImportError:
        return None
    try:
        return SynthesisConfig(
            volume=config.volumen_tts,
            length_scale=1.0 / max(config.velocidad_tts, 0.1),
        )
    except TypeError:
        return None


class SintetizadorPiper(_SintetizadorEnCola):
    """Piper como proceso externo: un `python -m piper` por frase.

    Respaldo del anterior. Solo se usa cuando no se puede importar Piper o no
    esta el .onnx en la carpeta del proyecto, porque paga el arranque completo en
    cada frase.
    """

    nombre = "piper (proceso externo)"

    def __init__(self, config: VozConfig, orden: OrdenPiper, logger: logging.Logger) -> None:
        self._cfg = config
        self._orden = orden
        self._proceso: subprocess.Popen | None = None
        self._voz = _ruta_de_voz(config) or config.piper_voz
        self._frecuencia = _frecuencia_de_voz(config)
        if _ruta_de_voz(config) is None:
            logger.warning("Sin .onnx.json para %s: se asume %d Hz. Si la voz suena "
                           "acelerada o lenta, descargala con `python -m hacu.voz "
                           "--descargar` para que traiga su ficha.",
                           config.piper_voz, self._frecuencia)
        import sounddevice  # noqa: PLC0415

        self._sd = sounddevice
        super().__init__(logger.getChild("tts.piper"), config.pronunciaciones)

    def _pronunciar(self, texto: str) -> None:
        # --output-raw escribe PCM 16 bits mono en la salida estandar, sin
        # cabecera WAV y sin fichero temporal: se reproduce segun llega.
        escala = 1.0 / max(self._cfg.velocidad_tts, 0.1)
        self._proceso = subprocess.Popen(
            self._orden.para(str(self._voz), escala),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, creationflags=_sin_consola(),
        )
        try:
            crudo, _ = self._proceso.communicate(texto.encode("utf-8"), timeout=30)
        except subprocess.TimeoutExpired:
            self._proceso.kill()
            self._log.error("Piper no respondio en 30 s")
            return
        finally:
            self._proceso = None
        if self._cortar.is_set() or not crudo:
            return
        muestras = np.frombuffer(crudo, dtype=np.int16).astype(np.float32) / 32768.0
        # Cola de silencio. `sd.wait()` vuelve cuando la ultima muestra sale hacia
        # la tarjeta, no cuando suena; con salidas que anaden latencia propia
        # —WASAPI compartido, Bluetooth, o un escritorio remoto que recodifica el
        # audio— esa diferencia se come la ultima silaba. Reproducir silencio
        # detras no cuesta nada y garantiza que la voz sale entera.
        relleno = np.zeros(int(self._frecuencia * _COLA_SILENCIO_S), dtype=np.float32)
        salida = np.concatenate([muestras * self._cfg.volumen_tts, relleno])
        self._sd.play(salida, self._frecuencia, device=self._cfg.dispositivo_salida)
        self._sd.wait()

    def _detener_reproduccion(self) -> None:
        try:
            self._sd.stop()
        except Exception:
            pass
        proceso = self._proceso
        if proceso is not None and proceso.poll() is None:
            proceso.kill()


# Banderas de SAPI5: 1 = asincrono, 2 = purgar lo pendiente antes de hablar.
_SAPI_ASINCRONO = 1
_SAPI_PURGAR = 2


class SintetizadorSistema(_SintetizadorEnCola):
    """La voz que ya trae el sistema operativo. Fea, pero siempre esta."""

    nombre = "sistema operativo"

    def __init__(self, config: VozConfig, logger: logging.Logger) -> None:
        self._cfg = config
        self._proceso: subprocess.Popen | None = None
        self._voz_windows = None
        if sys.platform == "win32":
            self._voz_windows = _sapi()
        elif not shutil.which("espeak-ng") and not shutil.which("espeak"):
            raise RuntimeError("No hay sintetizador del sistema (falta espeak-ng)")
        super().__init__(logger.getChild("tts.sistema"), config.pronunciaciones)

    def _pronunciar(self, texto: str) -> None:
        if self._voz_windows is not None:
            # Asincrono y esperando a ratos: con SVSFDefault (sincrono) la
            # llamada no volvia hasta terminar la frase, asi que "callar" no
            # surtia efecto hasta el siguiente punto.
            self._voz_windows.Speak(texto, _SAPI_ASINCRONO)
            while not self._cortar.is_set():
                if self._voz_windows.WaitUntilDone(80):
                    return
            self._detener_reproduccion()
            return
        binario = shutil.which("espeak-ng") or shutil.which("espeak")
        self._proceso = subprocess.Popen(
            [binario, "-v", "es", "-s", str(int(150 * self._cfg.velocidad_tts)), texto],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._proceso.wait()
        self._proceso = None

    def _detener_reproduccion(self) -> None:
        if self._voz_windows is not None:
            try:
                self._voz_windows.Speak("", _SAPI_PURGAR)
            except Exception:
                pass
            return
        proceso = self._proceso
        if proceso is not None and proceso.poll() is None:
            proceso.kill()


# ------------------------------------------------------------------- seleccion


# Orden de preferencia por peticion. "piper" prueba primero el motor en proceso y
# solo cae al proceso externo si no se puede cargar la voz aqui dentro; el motor
# externo nunca se elige antes que el interno, porque paga el arranque por frase.
_CADENAS: dict[str, tuple[str, ...]] = {
    "piper": ("piper-proceso", "piper-externo"),
    "piper-proceso": ("piper-proceso",),
    "piper-externo": ("piper-externo",),
    "sistema": ("sistema",),
}
_CADENA_POR_DEFECTO: tuple[str, ...] = ("piper-proceso", "piper-externo", "sistema")


def cadena_de_motores(peticion: str) -> tuple[str, ...]:
    """Motores a probar, en orden, para el valor de `motor_tts` que se pida."""
    return _CADENAS.get((peticion or "auto").lower(), _CADENA_POR_DEFECTO)


def crear_sintetizador(config: VozConfig, logger: logging.Logger) -> Sintetizador:
    """Elige el mejor motor disponible respetando lo que pida la configuracion."""
    log = logger.getChild("tts")
    peticion = (config.motor_tts or "auto").lower()
    if peticion == "mudo":
        return SintetizadorMudo()

    for motor in cadena_de_motores(peticion):
        try:
            if motor == "piper-proceso":
                ruta = _ruta_de_voz(config)
                if ruta is None:
                    log.info("La voz %s no esta en %s; descargala con "
                             "`python -m hacu.voz --descargar`",
                             config.piper_voz, config.carpeta_voces)
                    continue
                log.info("Sintesis con Piper en proceso: %s", ruta.name)
                return SintetizadorPiperEnProceso(config, ruta, logger)

            if motor == "piper-externo":
                invocacion = localizar_piper(config)
                if invocacion is None:
                    log.info("Piper no esta instalado (pip install piper-tts)")
                    continue
                if not invocacion.moderno and _ruta_de_voz(config) is None:
                    log.info("Falta el modelo de voz %s en %s",
                             config.piper_voz, config.carpeta_voces)
                    continue
                log.warning("Piper como proceso externo: cada frase paga el arranque "
                            "del interprete. Espera pausas largas entre frases.")
                return SintetizadorPiper(config, invocacion, logger)

            log.info("Sintesis con la voz del sistema operativo")
            return SintetizadorSistema(config, logger)
        except Exception:
            log.warning("No se pudo iniciar el motor %s", motor, exc_info=True)

    log.warning("Ningun motor de sintesis disponible: HACU respondera solo por escrito")
    return SintetizadorMudo()


def sintetizar_a_archivo(config: VozConfig, texto: str, destino: Path) -> Path | None:
    """Escribe la frase en un WAV sin reproducirla. Devuelve None si no hay Piper.

    Sirve para separar dos fallos que suenan igual: que la sintesis se corte, o
    que se corte la reproduccion. Si el WAV esta entero, el problema esta en la
    salida de audio (o en el escritorio remoto), no en HACU.
    """
    orden = localizar_piper(config)
    if orden is None:
        return None
    voz = _ruta_de_voz(config) or config.piper_voz
    escala = 1.0 / max(config.velocidad_tts, 0.1)
    texto = para_voz(texto, compilar(config.pronunciaciones or LEXICO))
    base = orden.para(str(voz), escala)
    # Se cambia la salida cruda por un fichero: -f en el paquete moderno,
    # --output_file en el binario antiguo.
    argumentos = [a for a in base if a not in ("--output-raw", "--output_raw")]
    argumentos += ["-f" if orden.moderno else "--output_file", str(destino)]
    completado = subprocess.run(
        argumentos, input=texto.encode("utf-8"), stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False, creationflags=_sin_consola(),
    )
    if completado.returncode or not destino.exists():
        raise RuntimeError(
            completado.stderr.decode("utf-8", "replace").strip() or "Piper no genero el fichero"
        )
    return destino


def localizar_piper(config: VozConfig) -> OrdenPiper | None:
    """Decide como invocar a Piper: paquete `piper-tts`, binario suelto, o nada.

    Se prefiere el paquete de Python porque en Windows se instala con un `pip
    install` y no obliga a descomprimir un zip ni a tocar el PATH, que es donde
    se atasca el montaje en una sala prestada.
    """
    if config.piper_exe is not None and Path(config.piper_exe).exists():
        return OrdenPiper([str(config.piper_exe)], moderno=False)
    if importlib.util.find_spec("piper") is not None:
        return OrdenPiper([sys.executable, "-m", "piper"], moderno=True)
    suelto = shutil.which("piper")
    if suelto:
        return OrdenPiper([suelto], moderno=False)
    nombre = "piper.exe" if sys.platform == "win32" else "piper"
    junto_a_las_voces = config.carpeta_voces / nombre
    return OrdenPiper([str(junto_a_las_voces)], moderno=False) if junto_a_las_voces.exists() else None


def _ruta_de_voz(config: VozConfig) -> Path | None:
    """El .onnx descargado, si esta en la carpeta de voces del proyecto."""
    candidata = Path(config.piper_voz)
    if candidata.suffix == ".onnx" and candidata.exists():
        return candidata
    local = config.carpeta_voces / f"{config.piper_voz}.onnx"
    return local if local.exists() else None


# Frecuencia cuando no hay .onnx.json que leer. NO se deduce de la calidad del
# nombre: parecia razonable que "x_low" fuese siempre 16 kHz, y es falso —
# es_ES-carlfm-x_low es 16000 y es_MX-ald-x_low es 22050. Equivocarse aqui suena
# a ardilla o a camara lenta, asi que se usa el valor mas comun de Piper y se
# avisa en el log de que es una suposicion.
_FRECUENCIA_HABITUAL = 22050


def _frecuencia_de_voz(config: VozConfig, por_defecto: int = _FRECUENCIA_HABITUAL) -> int:
    """Frecuencia de muestreo de la voz, leida de su .onnx.json."""
    ruta = _ruta_de_voz(config)
    if ruta is not None:
        ficha = ruta.with_suffix(ruta.suffix + ".json")
        if ficha.exists():
            try:
                datos = json.loads(ficha.read_text(encoding="utf-8"))
                return int(datos.get("audio", {}).get("sample_rate", por_defecto))
            except Exception:
                pass
    return por_defecto


def _sapi():
    """Voz SAPI5 de Windows, en espanol si el sistema tiene una instalada."""
    import win32com.client  # noqa: PLC0415

    voz = win32com.client.Dispatch("SAPI.SpVoice")
    for disponible in voz.GetVoices():
        if "spanish" in disponible.GetDescription().lower() or "español" in disponible.GetDescription().lower():
            voz.Voice = disponible
            break
    return voz


def _sin_consola() -> int:
    """En Windows evita que cada llamada a Piper abra una ventana negra."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
