"""Consola del operador.

Capa de presentacion pura: no contiene reglas de negocio. Recibe ya construidas
sus dependencias y se limita a orquestar el turno y a exponer el panel de mandos.
Los comandos viven en un diccionario, de modo que anadir uno no obliga a tocar el
bucle principal.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from .config import AppConfig
from .memory import HacuMemoryDB
from .routing import Intencion
from .session import HacuSession

_SEPARADOR = "=" * 58

_OPCIONES_AUDIENCIA: dict[str, str] = {
    "A": "General",
    "B": "Tecnico",
    "C": "Infantil",
    "D": "Artistico",
}


class HacuConsole:
    """Bucle interactivo de la exhibicion."""

    def __init__(
        self,
        config: AppConfig,
        logger: logging.Logger,
        sesion: HacuSession,
        db: HacuMemoryDB,
    ) -> None:
        self._cfg = config
        self._log = logger.getChild("consola")
        self._sesion = sesion
        self._db = db
        self._activo = True

        self._comandos: dict[str, Callable[[], None]] = {
            "1": self._cmd_salir,
            "2": self._cmd_limpiar_chat,
            "3": self._cmd_trivia,
            "4": self._cmd_auditar,
            "5": self._cmd_audiencia,
            "6": self._cmd_reset_perfil,
            "7": self._cmd_purga_global,
            "8": self._cmd_gestionar_perfil,
            "0": self.mostrar_panel,
            "?": self.mostrar_panel,
            "menu": self.mostrar_panel,
        }

    # ------------------------------------------------------------------ panel

    def mostrar_panel(self) -> None:
        print("\n" + _SEPARADOR)
        print(" 🎛️  PANEL DE CONTROL DE HACU")
        print(_SEPARADOR)
        print("  [1] 🚪 Apagar / Salir del sistema")
        print("  [2] 🧹 Limpiar chat inmediato (conserva memoria a largo plazo)")
        print("  [3] 🎮 Activar / Desactivar Modo Trivia")
        print("  [4] 🧠 Auditar memoria episodica del perfil activo")
        print("  [5] 🎭 Cambiar perfil de audiencia")
        print("  [6] 🗑️  Restablecer la memoria del perfil activo")
        print("  [7] ☢️  Purgar TODA la base de datos (todos los perfiles)")
        print("  [8] 👤 Gestionar perfil activo (fijar, listar, anonimizar)")
        print("  [0] Volver a mostrar este panel")
        print(_SEPARADOR)
        retencion = self._cfg.memory.retencion_horas
        politica = (
            f"Retencion: el historial se borra a las {retencion}h."
            if retencion else "Retencion: DESACTIVADA, el historial no caduca."
        )
        print(f"  {politica} Los hechos de cada perfil se conservan.")
        print(_SEPARADOR)
        print("O escribe tu pregunta normalmente para hablar con Hacu.\n")

    def _encabezado_turno(self, intencion: Intencion, segundos: float) -> None:
        if not self._cfg.debug_console:
            return
        print(
            f"[i] ⚡ Router ({segundos:.4f}s) -> {intencion.value} | "
            f"Perfil: {self._sesion.usuario_activo} | Audiencia: {self._sesion.estado.perfil_audiencia}"
        )

    # ------------------------------------------------------------------ bucle

    def ejecutar(self) -> None:
        self.mostrar_panel()
        while self._activo:
            try:
                entrada = input("\nVisitante / Operador: ").strip()
            except (KeyboardInterrupt, EOFError):
                print()
                break

            if not entrada:
                continue

            comando = self._comandos.get(entrada.lower())
            if comando is not None:
                comando()
                continue

            try:
                self._responder(entrada)
            except KeyboardInterrupt:
                print("\n[!] Generacion interrumpida por el operador.")
            except Exception:
                self._log.error("Fallo atendiendo el turno", exc_info=True)
                print("\n[!] Ocurrio un problema tecnico. El detalle quedo en el log.")

    def _responder(self, texto: str) -> None:
        intencion, segundos_router = self._sesion.clasificar(texto)
        self._encabezado_turno(intencion, segundos_router)

        print("\n--- HACU ---")
        resultado = self._sesion.turno(
            texto, on_token=lambda t: print(t, end="", flush=True), intencion=intencion
        )
        print("\n" + "-" * 35)

        if resultado.migrado and self._cfg.debug_console:
            print(f"[i] 👤 Perfil migrado: {resultado.usuario_anterior} -> {resultado.usuario}")
        print(
            f"⏱️  Latencia: {resultado.segundos:.2f}s | "
            f"Velocidad: ~{resultado.tokens_por_segundo:.1f} tok/s"
        )

    # --------------------------------------------------------------- comandos

    def _cmd_salir(self) -> None:
        print("Apagando el sistema Hacu...")
        self._activo = False

    def _cmd_limpiar_chat(self) -> None:
        usuario = self._sesion.identidad.usuario_activo
        self._db.clear_short_term(usuario)
        self._sesion.estado.trivia = False
        print(f"🧹 Chat inmediato limpiado para '{usuario}'. La memoria a largo plazo se conserva.")

    def _cmd_trivia(self) -> None:
        self._sesion.estado.trivia = not self._sesion.estado.trivia
        print(f"🎮 Modo Trivia: {'ACTIVADO' if self._sesion.estado.trivia else 'DESACTIVADO'}")

    def _cmd_auditar(self) -> None:
        usuario = self._sesion.identidad.usuario_activo
        print(f"\n🧠 --- MEMORIA EPISODICA [{usuario}] ---")
        episodios = self._db.get_all_episodes(usuario)
        if episodios:
            for idx, hecho in enumerate(episodios, 1):
                print(f"  {idx}. {hecho}")
        else:
            print("  (Sin hechos registrados para este perfil.)")
        print("-" * 50 + "\n")

    def _cmd_audiencia(self) -> None:
        print("\n🎭 Selecciona el perfil de audiencia:")
        print("  [A] General / Estandar")
        print("  [B] Tecnico / Ingenieria")
        print("  [C] Infantil / Educativo")
        print("  [D] Artistico / Humanista")
        seleccion = self._leer("Opcion (A/B/C/D): ").upper()
        self._sesion.estado.perfil_audiencia = _OPCIONES_AUDIENCIA.get(seleccion, "General")
        print(f"✅ Audiencia: {self._sesion.estado.perfil_audiencia}\n")

    def _cmd_reset_perfil(self) -> None:
        usuario = self._sesion.identidad.usuario_activo
        self._db.reset_profile(usuario)
        self._sesion.estado.trivia = False
        print(f"🗑️  Memoria restablecida para '{usuario}'.")

    def _cmd_purga_global(self) -> None:
        perfiles = self._db.list_profiles()
        if not perfiles:
            print("☢️  La base de datos ya esta vacia.")
            return
        print(f"\n☢️  Se eliminaran {len(perfiles)} perfiles con toda su memoria:")
        for nombre, mensajes, hechos in perfiles:
            print(f"    - {nombre}: {mensajes} mensajes, {hechos} hechos")
        if self._leer("Escribe CONFIRMAR para continuar: ") != "CONFIRMAR":
            print("Operacion cancelada.\n")
            return
        borrados = self._db.purge_all()
        self._sesion.identidad.reiniciar()
        self._sesion.estado.trivia = False
        print(f"☢️  Purga completada: {borrados} perfiles eliminados. Perfil activo: "
              f"'{self._sesion.identidad.usuario_activo}'.\n")

    def _cmd_gestionar_perfil(self) -> None:
        print(f"\n👤 Perfil activo: {self._sesion.identidad.usuario_activo}")
        print("  [L] Listar perfiles registrados")
        print("  [F] Fijar manualmente el nombre del visitante")
        print("  [A] Volver al perfil anonimo")
        opcion = self._leer("Opcion (L/F/A): ").upper()

        if opcion == "L":
            perfiles = self._db.list_profiles()
            if not perfiles:
                print("  (No hay perfiles registrados.)\n")
                return
            for nombre, mensajes, hechos in perfiles:
                print(f"    - {nombre}: {mensajes} mensajes, {hechos} hechos")
            print()
        elif opcion == "F":
            propuesto = self._leer("Nombre del visitante: ")
            anterior = self._sesion.identidad.usuario_activo
            nuevo = self._sesion.identidad.fijar_manualmente(propuesto)
            if nuevo is None:
                print("⚠️  Ese texto no se acepta como nombre de visitante.\n")
                return
            if anterior != nuevo and self._leer(f"Mover la memoria de '{anterior}' a '{nuevo}'? (s/n): ").lower() == "s":
                self._db.migrate_profile(anterior, nuevo)
            print(f"✅ Perfil activo: {nuevo}\n")
        elif opcion == "A":
            print(f"✅ Perfil activo: {self._sesion.identidad.reiniciar()}\n")
        else:
            print("Opcion no reconocida.\n")

    @staticmethod
    def _leer(prompt: str) -> str:
        try:
            return input(prompt).strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return ""
