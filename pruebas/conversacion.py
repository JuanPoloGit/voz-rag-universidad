"""Ejecuta el guion conversacional contra el modelo real.

    python -m pruebas.conversacion            # la visita completa
    python -m pruebas.conversacion --voz      # y ademas la escuchas, como en la tarima
    python -m pruebas.conversacion --listar   # el guion con sus criterios
    python -m pruebas.conversacion --desde G15

A diferencia de la bateria, el orden NO se baraja: una conversacion tiene hilo y
cada turno depende del anterior. Usa una base temporal, asi que no toca la
memoria real.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path

from hacu.bootstrap import cerrar, construir
from hacu.config import PROJECT_ROOT, AppConfig
from hacu.logging_setup import configurar_logging
from hacu.routing import NEGACIONES, normalizar

from .bateria import detectar_guiones, revisar_estilo
from .guion import GUION, Longitud, Tipo, Turno

# La lista de negaciones vive en `hacu.routing`: el runtime tambien la necesita
# —para saber si acaba de negar algo y detectar que le estan presionando— y dos
# copias se habrian separado al primer ajuste.
_NEGACIONES = NEGACIONES

# El minimo de EXTENSA empezo en 480 y marcaba como fallo respuestas completas:
# la explicacion de Orion traia los cuatro eslabones de la cadena en 451 caracteres.
# El numero de caracteres mide verboseria, no cobertura; quien mide cobertura es
# `debe_contener`. Se baja a 300 —por debajo de eso no cabe una cadena desarrollada,
# una sola frase ronda los 150— y se exigen mas terminos en los turnos extendidos.
_LIMITES = {Longitud.BREVE: (0, 420), Longitud.NORMAL: (0, 1400), Longitud.EXTENSA: (300, 2400)}

# Una correccion correcta contiene el termino equivocado precisamente para
# negarlo ("no vuela, es terrestre"). Buscar la subcadena a secas marcaria como
# fallo justo la respuesta que queriamos.
_NEGADORES: tuple[str, ...] = (
    "no ", "sin ", "tampoco", "nunca", "jamas", "en vez de", "en lugar de",
    "no es", "mas que", "aunque no",
)
_VENTANA_NEGACION = 34


def aparece_afirmado(plano: str, termino: str) -> bool:
    """True si el termino aparece sin una negacion inmediatamente delante."""
    objetivo = normalizar(termino)
    desde = 0
    while (i := plano.find(objetivo, desde)) != -1:
        ventana = plano[max(0, i - _VENTANA_NEGACION):i]
        if not any(neg in ventana for neg in _NEGADORES):
            return True
        desde = i + len(objetivo)
    return False


@dataclass
class ResultadoTurno:
    orden: int
    id: str
    tipo: str
    texto: str
    respuesta: str = ""
    perfil: str = ""
    intencion: str = ""
    segundos: float = 0.0
    caracteres: int = 0
    faltan: list[str] = field(default_factory=list)
    prohibidos: list[str] = field(default_factory=list)
    sin_negacion: bool = False
    longitud_fuera: str = ""
    perfil_incorrecto: str = ""
    alertas: list[str] = field(default_factory=list)
    criterio: str = ""

    @property
    def ok(self) -> bool:
        return not (self.faltan or self.prohibidos or self.sin_negacion
                    or self.longitud_fuera or self.perfil_incorrecto)


def evaluar(turno: Turno, respuesta: str, perfil: str) -> ResultadoTurno:
    """Contrasta la respuesta con lo que el turno exige."""
    plano = normalizar(respuesta)
    fila = ResultadoTurno(orden=0, id=turno.id, tipo=turno.tipo.value, texto=turno.texto,
                          respuesta=respuesta, perfil=perfil, criterio=turno.criterio,
                          caracteres=len(respuesta))

    # Una entrada puede ofrecer alternativas con "|": varias respuestas pueden
    # ser correctas y exigir una sola palabra convierte la prueba en una trampa.
    fila.faltan = [
        exigido for exigido in turno.debe_contener
        if not any(normalizar(v) in plano for v in exigido.split("|"))
    ]
    fila.prohibidos = [d for d in turno.no_debe_contener if aparece_afirmado(plano, d)]
    if turno.debe_negar and not any(n in plano for n in _NEGACIONES):
        fila.sin_negacion = True

    minimo, maximo = _LIMITES[turno.longitud]
    if len(respuesta) < minimo:
        fila.longitud_fuera = f"corta ({len(respuesta)} < {minimo})"
    elif len(respuesta) > maximo:
        fila.longitud_fuera = f"larga ({len(respuesta)} > {maximo})"

    if turno.perfil_esperado and perfil != turno.perfil_esperado:
        fila.perfil_incorrecto = f"{perfil} (esperado {turno.perfil_esperado})"

    fila.alertas = revisar_estilo(turno.texto, respuesta)
    return fila


def preparar(turnos: list[Turno]) -> tuple[list[Turno], list[str]]:
    """Ajusta el subconjunto para que solo exija lo que puede cumplirse.

    Con `--tipo CORRECCION` el turno en el que la visitante dice su nombre no se
    ejecuta, asi que el perfil sigue siendo el anonimo: exigir "Camila" en los
    turnos siguientes achacaba al modelo un fallo del filtro.
    """
    presentados: set[str] = set()
    ajustados: list[Turno] = []
    retirados: list[str] = []
    for turno in turnos:
        if turno.presenta_perfil and turno.perfil_esperado:
            presentados.add(turno.perfil_esperado)
        if turno.perfil_esperado and turno.perfil_esperado not in presentados:
            retirados.append(turno.id)
            turno = replace(turno, perfil_esperado=None)
        ajustados.append(turno)
    return ajustados, retirados


def ejecutar(turnos: list[Turno], config: AppConfig, voz=None) -> list[ResultadoTurno]:
    logger = configurar_logging(config.log_file, debug_console=False)
    print("🔧 Levantando HACU...")
    comp = construir(config, logger, progreso=lambda m: print(f"   {m}"))
    resultados: list[ResultadoTurno] = []
    try:
        for i, turno in enumerate(turnos, 1):
            print(f"\n[{i:02d}/{len(turnos)}] {turno.id} · {turno.tipo.value}\n> {turno.texto}")
            # Con voz, el ensayo suena como en escena: se habla frase a frase y
            # no se pasa al turno siguiente hasta que HACU termina de hablar.
            locutor = voz.locutor() if voz is not None and voz.puede_hablar else None
            salida = comp.sesion.turno(
                turno.texto, on_token=locutor.alimentar if locutor else None
            )
            if locutor is not None:
                locutor.cerrar()
                voz.esperar_a_que_calle(timeout=180)
            comp.extractor.esperar_vacio()

            fila = evaluar(turno, salida.respuesta, salida.usuario)
            fila.orden, fila.segundos = i, round(salida.segundos, 2)
            fila.intencion = salida.intencion.value

            corte = salida.respuesta[:260]
            print(f"< {corte}{'...' if len(salida.respuesta) > 260 else ''}")
            print(f"  perfil={salida.usuario} · {salida.intencion.value} · "
                  f"{salida.segundos:.2f}s · {len(salida.respuesta)} chars")
            if fila.faltan:
                print(f"  ❌ falta en la respuesta: {', '.join(fila.faltan)}")
            if fila.prohibidos:
                print(f"  ❌ contiene lo prohibido: {', '.join(fila.prohibidos)}")
            if fila.sin_negacion:
                print("  ❌ no admite que el dato no está documentado (posible invención)")
            if fila.longitud_fuera:
                print(f"  ❌ longitud {fila.longitud_fuera}")
            if fila.perfil_incorrecto:
                print(f"  ❌ perfil {fila.perfil_incorrecto}")
            for alerta in fila.alertas:
                print(f"  ⚠️  {alerta}")
            resultados.append(fila)

        print("\n" + "=" * 62)
        print(" MEMORIA AL CERRAR LA VISITA")
        print("=" * 62)
        for perfil, mensajes, hechos in comp.db.list_profiles():
            print(f"  {perfil} ({mensajes} mensajes, {hechos} hechos)")
            for hecho in comp.db.get_all_episodes(perfil):
                print(f"      - {hecho}")
    finally:
        cerrar(comp)
    return resultados


def resumir(resultados: list[ResultadoTurno]) -> int:
    fallos = [r for r in resultados if not r.ok]
    print("\n" + "=" * 62)
    print(f" RESUMEN DE LA VISITA — {len(resultados)} turnos")
    print("=" * 62)
    print(f"  Turnos correctos       : {len(resultados) - len(fallos)}/{len(resultados)}")

    por_tipo: dict[str, list[ResultadoTurno]] = {}
    for fila in resultados:
        por_tipo.setdefault(fila.tipo, []).append(fila)
    for tipo, filas in por_tipo.items():
        buenos = sum(1 for f in filas if f.ok)
        print(f"     {tipo:12} {buenos}/{len(filas)}")

    tiempos = [r.segundos for r in resultados if r.segundos]
    if tiempos:
        print(f"  Latencia media/max     : {sum(tiempos)/len(tiempos):.2f}s / {max(tiempos):.2f}s")
    largos = [r for r in resultados if r.longitud_fuera.startswith("larga")]
    print(f"  Respuestas demasiado largas: {len(largos)}")
    guiones = detectar_guiones(resultados, minimo=2)
    print(f"  Frases repetidas       : {len(guiones)}")
    for frase, veces in guiones[:3]:
        print(f"      {veces}x  \"{frase[:66]}...\"")
    con_alertas = [r for r in resultados if r.alertas]
    print(f"  Turnos con alertas     : {len(con_alertas)}")

    for fila in fallos:
        print(f"\n  ❌ {fila.id} ({fila.tipo}) :: {fila.texto[:62]}")
        print(f"     criterio: {fila.criterio}")
        for etiqueta, valor in (("falta", fila.faltan), ("prohibido", fila.prohibidos)):
            if valor:
                print(f"     {etiqueta}: {', '.join(valor)}")
        if fila.sin_negacion:
            print("     no admitió que el dato no está documentado")
        if fila.longitud_fuera:
            print(f"     longitud: {fila.longitud_fuera}")
        if fila.perfil_incorrecto:
            print(f"     perfil: {fila.perfil_incorrecto}")
    return 1 if fallos else 0


def listar() -> str:
    lineas = ["# Guion de visita", "", f"{len(GUION)} turnos encadenados con un mismo visitante.", ""]
    for turno in GUION:
        lineas += [f"### {turno.id} · {turno.tipo.value} — «{turno.texto}»", "",
                   f"- **Criterio**: {turno.criterio}"]
        if turno.debe_contener:
            lineas.append(f"- **Debe contener**: {', '.join(turno.debe_contener)}")
        if turno.no_debe_contener:
            lineas.append(f"- **No puede contener**: {', '.join(turno.no_debe_contener)}")
        if turno.debe_negar:
            lineas.append("- **Debe admitir que no tiene el dato**")
        lineas += [f"- **Longitud esperada**: {turno.longitud.value}", ""]
    return "\n".join(lineas)


def main() -> int:
    parser = argparse.ArgumentParser(description="Guion conversacional de HACU")
    parser.add_argument("--listar", action="store_true")
    parser.add_argument("--voz", action="store_true",
                        help="ademas de medirlo, lo dice en voz alta")
    parser.add_argument("--desde", type=str, default="", help="empieza en ese id (p.ej. G15)")
    parser.add_argument("--hasta", type=str, default="", help="termina en ese id (p.ej. G50)")
    parser.add_argument("--tipo", type=str, default="", help="solo turnos de ese tipo")
    parser.add_argument("--salida", type=Path, default=PROJECT_ROOT / "pruebas" / "informes")
    args = parser.parse_args()

    if args.listar:
        print(listar())
        return 0

    turnos = list(GUION)
    # Cien turnos son entre veinte y treinta y cinco minutos de GPU. `--desde` y
    # `--hasta` permiten partir la visita en dos sesiones sin editar el guion.
    if args.hasta:
        ids = [t.id for t in turnos]
        if args.hasta.upper() in ids:
            turnos = turnos[: ids.index(args.hasta.upper()) + 1]
    if args.desde:
        ids = [t.id for t in turnos]
        if args.desde.upper() in ids:
            turnos = turnos[ids.index(args.desde.upper()):]
    if args.tipo:
        turnos = [t for t in turnos if t.tipo is Tipo(args.tipo.upper())]
    if not turnos:
        print("No hay turnos que ejecutar.")
        return 1

    parcial = len(turnos) < len(GUION)
    turnos, sin_perfil = preparar(turnos)
    if parcial:
        print(f"⚠️  Corrida parcial: {len(turnos)} de {len(GUION)} turnos. La conversacion "
              "no es la misma, asi que los resultados no son comparables con la completa.")
    if sin_perfil:
        print(f"⚠️  Sin el turno de presentacion, no se exige perfil en: {', '.join(sin_perfil)}")

    temporal = Path(tempfile.mkdtemp(prefix="hacu-guion-"))
    config = AppConfig.from_env()
    config = replace(config, memory=replace(config.memory, db_path=temporal / "memoria.db"))
    print(f"🧪 Memoria temporal en {temporal}")

    voz = None
    if args.voz:
        # Solo la boca: el guion escribe las preguntas, no las dice nadie.
        from hacu.voz import ServicioDeVoz

        voz = ServicioDeVoz(replace(config.voz, solo_salida=True, activa=False),
                            configurar_logging(config.log_file, debug_console=False))
        print(f"🔊 Voz: {voz.motor if voz.puede_hablar else 'NO disponible'}")

    try:
        resultados = ejecutar(turnos, config, voz)
    finally:
        if voz is not None:
            voz.cerrar(drenar=True)
        shutil.rmtree(temporal, ignore_errors=True)

    args.salida.mkdir(parents=True, exist_ok=True)
    marca = datetime.now().strftime("%Y%m%d-%H%M%S")
    destino = args.salida / f"guion-{marca}.json"
    destino.write_text(json.dumps([asdict(r) for r in resultados], ensure_ascii=False, indent=2),
                       encoding="utf-8")
    print(f"\n📄 Informe: {destino.name}")
    return resumir(resultados)


if __name__ == "__main__":
    sys.exit(main())
