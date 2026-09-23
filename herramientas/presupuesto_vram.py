"""Cuanta VRAM ocupa una combinacion de modelo, contexto y cuantizacion.

Existe para no elegir modelo a ojo. La cuenta no tiene misterio, pero los tres
sumandos se olvidan siempre y el que mas sorprende no son los pesos: es la cache
KV, que crece LINEALMENTE con `n_ctx` y que en un modelo de 14B a 16k ocupa mas
que medio modelo de 8B.

    python -m herramientas.presupuesto_vram                 # lo que hay hoy
    python -m herramientas.presupuesto_vram --todos         # la tabla entera
    python -m herramientas.presupuesto_vram --modelo qwen3-14b --ctx 16384

Las cifras de pesos son tamanos reales de fichero GGUF, consultados en
huggingface.co el 21/09/2026. Las de arquitectura (capas, cabezas KV) son
publicas. Lo unico estimado es el buffer de computo, y va marcado como tal.

La formula de la cache supone atencion global en TODAS las capas. Eso es cierto
para Llama y Qwen, y sobreestima a Gemma 3, que usa ventana deslizante en cinco
de cada seis capas. Da igual: Gemma queda descartada por otro motivo, y para el
resto la cuenta es exacta.

Caber en la VRAM no basta: el presupuesto de contexto (system prompt + corpus +
perfil + historial) impone un `n_ctx` minimo por debajo del cual HACU se niega a
arrancar. La tabla lo cruza y marca esas filas, porque son justo las que parecen
buenas —un modelo grande con poco contexto entra de sobra en la tarjeta— y no
sirven.

NO sustituye a medir: `nvidia-smi` con HACU arrancado da la cifra de verdad, y
`ModelConfig(verbose=True)` hace que llama.cpp imprima su propio desglose. Esto
sirve para descartar combinaciones antes de bajarse nueve gigas.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

GIB = 1024 ** 3
# Margen que NO se toca. La GPU es la de una portatil y ademas dibuja la
# pantalla: llenar los 12 GB es como quedarse sin frenos.
MARGEN_SEGURIDAD_GIB = 1.5


@dataclass(frozen=True)
class Modelo:
    """Un GGUF concreto: lo que pesa y lo que le cuesta cada token de contexto."""

    nombre: str
    pesos_gb: float          # tamano del fichero .gguf, en GB decimales
    capas: int
    cabezas_kv: int
    dim_cabeza: int
    rol_sistema: bool        # ¿su plantilla admite mensaje de sistema?
    nota: str = ""

    @property
    def pesos_gib(self) -> float:
        return self.pesos_gb * 1e9 / GIB

    def kv_por_token(self, bytes_por_valor: int = 2) -> int:
        """K y V, en todas las capas. 2 bytes = fp16; 1 byte ≈ q8_0."""
        return 2 * self.cabezas_kv * self.dim_cabeza * bytes_por_valor * self.capas

    def kv_gib(self, n_ctx: int, bytes_por_valor: int = 2) -> float:
        return self.kv_por_token(bytes_por_valor) * n_ctx / GIB


# El que corre hoy y los candidatos reales para 12 GB.
MODELOS: tuple[Modelo, ...] = (
    Modelo("llama-3.1-8b-q4_k_m", 4.92, 32, 8, 128, True, "el que corre hoy"),
    Modelo("llama-3.1-8b-q6_k", 6.60, 32, 8, 128, True, "mismo modelo, menos perdida"),
    Modelo("qwen3-8b-q5_k_m", 5.85, 36, 8, 128, True, "mismo tamano, familia mas nueva"),
    Modelo("qwen3-8b-q6_k", 6.73, 36, 8, 128, True, ""),
    Modelo("qwen3-14b-q4_k_m", 9.00, 40, 8, 128, True, "salto de verdad, pero justo"),
    Modelo("gemma-3-12b-q4_k_m", 6.70, 48, 8, 256, False,
           "DESCARTADO: su plantilla omite el mensaje de sistema"),
)

# Lo que acompana al modelo en la tarjeta. El reconocimiento no es una constante:
# cambiarlo de `small` a `large-v3-turbo` es el giga que mas se nota. La unica
# cifra MEDIDA aqui es la de `small` (0.50 GiB en int8_float16 sobre CUDA); las
# demas escalan por numero de parametros sobre ese ancla —0,26 GiB de activaciones
# mas un byte por parametro— y van marcadas como estimadas. `nvidia-smi` con HACU
# arrancado da la de verdad.
_PARAMETROS_M = {"tiny": 39, "base": 74, "small": 244, "medium": 769,
                 "large-v3-turbo": 809, "turbo": 809, "large-v3": 1550, "large": 1550}
# El ancla medida: `small` ocupa 0.50 GiB. Las activaciones se DERIVAN de ella en
# vez de escribirse a mano, para que la unica cifra medida salga exacta de la
# formula y no a 0.487 por un redondeo.
_STT_SMALL_MEDIDO_GIB = 0.50
_ACTIVACIONES_GIB = _STT_SMALL_MEDIDO_GIB - _PARAMETROS_M["small"] * 1e6 / GIB


def stt_gib(modelo: str = "small") -> float:
    """VRAM del reconocedor en int8. `small` medido; el resto, estimado."""
    millones = _PARAMETROS_M.get(modelo)
    if millones is None:
        conocidos = ", ".join(sorted(_PARAMETROS_M))
        raise ValueError(f"Modelo de voz desconocido: {modelo!r}. Conocidos: {conocidos}")
    return millones * 1e6 / GIB + _ACTIVACIONES_GIB


STT_GIB = stt_gib("small")
BUFFER_COMPUTO_GIB = 0.60  # estimado, depende de n_batch


def n_ctx_minimo() -> int | None:
    """El `n_ctx` mas pequeno con el que HACU arranca, o None si no se sabe.

    Caber en la VRAM no basta. El system prompt, el corpus recuperado, los
    hechos del perfil y el historial suman un presupuesto fijo, y `bootstrap`
    se niega a arrancar si no cabe —con razon: llama.cpp trunca por la
    izquierda y lo primero que se come es el system prompt, asi que HACU
    cambiaria de personalidad sin avisar—.

    Sin esta comprobacion la tabla decia "cabe" en filas donde el sistema ni
    arranca: un 14B a 8k entra de sobra en 12 GB y deja el presupuesto de
    contexto a la mitad de lo que necesita.
    """
    try:
        from dataclasses import replace

        from hacu.config import AppConfig
    except ImportError:
        return None
    base = AppConfig()
    for ctx in range(4096, 131073, 1024):
        cfg = replace(base, model=replace(base.model, n_ctx=ctx))
        prompt, disponible = cfg.presupuesto_contexto()
        if prompt < disponible:
            return ctx
    return None


CTX_MINIMO = n_ctx_minimo()


@dataclass(frozen=True)
class Presupuesto:
    modelo: Modelo
    n_ctx: int
    kv_8bits: bool
    stt_en_gpu: bool
    modelo_stt: str = "small"

    @property
    def kv(self) -> float:
        return self.modelo.kv_gib(self.n_ctx, 1 if self.kv_8bits else 2)

    @property
    def stt(self) -> float:
        return stt_gib(self.modelo_stt) if self.stt_en_gpu else 0.0

    @property
    def total(self) -> float:
        return self.modelo.pesos_gib + self.kv + BUFFER_COMPUTO_GIB + self.stt

    def cabe_en(self, vram_gib: float) -> bool:
        """Cabe en la tarjeta Y el sistema arranca con ese contexto."""
        return self.entra_en_vram(vram_gib) and self.contexto_suficiente

    def entra_en_vram(self, vram_gib: float) -> bool:
        return self.total <= vram_gib - MARGEN_SEGURIDAD_GIB

    @property
    def contexto_suficiente(self) -> bool:
        return CTX_MINIMO is None or self.n_ctx >= CTX_MINIMO


def calcular(nombre: str, n_ctx: int, kv_8bits: bool = False,
             stt_en_gpu: bool = True, modelo_stt: str = "small") -> Presupuesto:
    """Presupuesto de una combinacion. Lanza ValueError si el modelo no esta."""
    for modelo in MODELOS:
        if modelo.nombre == nombre:
            return Presupuesto(modelo, n_ctx, kv_8bits, stt_en_gpu, modelo_stt)
    conocidos = ", ".join(m.nombre for m in MODELOS)
    raise ValueError(f"Modelo desconocido: {nombre!r}. Conocidos: {conocidos}")


def _linea(p: Presupuesto, vram: float) -> str:
    if not p.contexto_suficiente:
        veredicto = f"NO · HACU no arranca con menos de {CTX_MINIMO} de contexto"
    elif not p.entra_en_vram(vram):
        veredicto = "NO"
    else:
        veredicto = "cabe"
    aviso = "" if p.modelo.rol_sistema else "  ⚠ sin rol de sistema"
    return (f"  {p.modelo.nombre:22} ctx={p.n_ctx:>6}  "
            f"pesos {p.modelo.pesos_gib:5.2f}  kv {p.kv:5.2f}  "
            f"total {p.total:5.2f} GiB  {veredicto}{aviso}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Presupuesto de VRAM de HACU")
    parser.add_argument("--modelo", default="llama-3.1-8b-q4_k_m")
    parser.add_argument("--ctx", type=int, default=16384)
    parser.add_argument("--vram", type=float, default=12.0, help="GiB de la tarjeta")
    parser.add_argument("--kv8", action="store_true",
                        help="cache KV a 8 bits (type_k/type_v=q8_0): la parte a la mitad")
    parser.add_argument("--stt-cpu", action="store_true",
                        help="mueve el reconocimiento a CPU y libera 0,5 GiB")
    parser.add_argument("--stt", default="small",
                        help="modelo de reconocimiento que acompana en la tarjeta")
    parser.add_argument("--todos", action="store_true", help="la tabla entera")
    args = parser.parse_args(argv)

    print(f"Tarjeta: {args.vram:.0f} GiB · margen reservado {MARGEN_SEGURIDAD_GIB} GiB "
          f"· buffer de computo {BUFFER_COMPUTO_GIB} GiB (estimado)")
    print(f"Reconocimiento: {'CPU' if args.stt_cpu else f'{args.stt} en GPU ({stt_gib(args.stt):.2f} GiB)'}"
          f" · cache KV a {'8' if args.kv8 else '16'} bits\n")

    if args.todos:
        for modelo in MODELOS:
            for ctx in (8192, 16384, 24576, 32768):
                p = Presupuesto(modelo, ctx, args.kv8, not args.stt_cpu, args.stt)
                print(_linea(p, args.vram))
            if modelo.nota:
                print(f"  {'':22} └─ {modelo.nota}")
            print()
        return 0

    p = calcular(args.modelo, args.ctx, args.kv8, not args.stt_cpu, args.stt)
    print(_linea(p, args.vram))
    if p.modelo.nota:
        print(f"  └─ {p.modelo.nota}")
    return 0 if p.cabe_en(args.vram) else 1


if __name__ == "__main__":
    raise SystemExit(main())
