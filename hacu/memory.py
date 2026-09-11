"""Repositorio de memoria SQLite multi-perfil.

Toda operacion pasa por un lock: la conexion se comparte con el hilo de extraccion
en segundo plano (`check_same_thread=False`) y sqlite3 no garantiza consistencia
entre hilos sin serializar. Se activa WAL para que las escrituras de fondo no
bloqueen las lecturas del turno interactivo.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable, Iterable
from pathlib import Path

from .sanitizer import FactSanitizer

Mensaje = dict[str, str]


class HacuMemoryDB:
    """Persistencia de historial inmediato y memoria episodica por perfil."""

    def __init__(self, db_path: Path, logger: logging.Logger) -> None:
        self._log = logger.getChild("memoria")
        self._lock = threading.RLock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._crear_esquema()

    # -------------------------------------------------------------- esquema

    def _crear_esquema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            # Migracion heredada: tablas previas sin particion por usuario.
            for tabla in ("short_term_history", "episodic_memory"):
                cur.execute(f"PRAGMA table_info({tabla})")
                columnas = [c[1] for c in cur.fetchall()]
                if columnas and "user_id" not in columnas:
                    self._log.warning("Esquema antiguo detectado en %s; se recrea", tabla)
                    cur.execute(f"DROP TABLE {tabla}")

            cur.execute(
                """CREATE TABLE IF NOT EXISTS short_term_history (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       user_id TEXT NOT NULL,
                       role TEXT NOT NULL,
                       content TEXT NOT NULL,
                       timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS episodic_memory (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       user_id TEXT NOT NULL,
                       fact TEXT NOT NULL,
                       timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)"""
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_hist_user ON short_term_history(user_id, id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_epi_user ON episodic_memory(user_id, id)")
            self._conn.commit()

    # ------------------------------------------------------- historial corto

    def add_message(self, user_id: str, role: str, content: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO short_term_history (user_id, role, content) VALUES (?, ?, ?)",
                (user_id, role, content),
            )
            self._conn.commit()

    def get_recent_history(self, user_id: str, limit: int = 6) -> list[Mensaje]:
        """Ultimos `limit` mensajes en orden cronologico ascendente."""
        with self._lock:
            filas = self._conn.execute(
                "SELECT role, content FROM short_term_history WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [{"role": r, "content": c} for r, c in reversed(filas)]

    def clear_short_term(self, user_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM short_term_history WHERE user_id = ?", (user_id,))
            self._conn.commit()

    # ------------------------------------------------------ memoria episodica

    def get_all_episodes(self, user_id: str) -> list[str]:
        """Hechos del perfil en orden cronologico ascendente (del mas antiguo al mas reciente)."""
        with self._lock:
            filas = self._conn.execute(
                "SELECT fact FROM episodic_memory WHERE user_id = ? ORDER BY id ASC", (user_id,)
            ).fetchall()
        return [f[0] for f in filas]

    def add_episode(self, user_id: str, fact: str) -> bool:
        """Inserta el hecho salvo que ya exista uno equivalente. Devuelve si hubo insercion."""
        clave = FactSanitizer.clave_dedup(fact)
        with self._lock:
            existentes = {FactSanitizer.clave_dedup(f) for f in self.get_all_episodes(user_id)}
            if clave in existentes:
                self._log.debug("Hecho duplicado descartado para %s: %s", user_id, fact)
                return False
            self._conn.execute(
                "INSERT INTO episodic_memory (user_id, fact) VALUES (?, ?)", (user_id, fact)
            )
            self._conn.commit()
        return True

    def overwrite_episodes(self, user_id: str, facts: Iterable[str]) -> None:
        """Reemplaza el perfil completo de forma atomica."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM episodic_memory WHERE user_id = ?", (user_id,))
            cur.executemany(
                "INSERT INTO episodic_memory (user_id, fact) VALUES (?, ?)",
                [(user_id, f) for f in facts],
            )
            self._conn.commit()

    def count_episodes(self, user_id: str) -> int:
        with self._lock:
            fila = self._conn.execute(
                "SELECT COUNT(*) FROM episodic_memory WHERE user_id = ?", (user_id,)
            ).fetchone()
        return int(fila[0])

    # ------------------------------------------------------------- perfiles

    def list_profiles(self) -> list[tuple[str, int, int]]:
        """(perfil, mensajes, hechos) de todos los perfiles registrados.

        Una sola agregacion en vez de dos subconsultas correlacionadas por perfil:
        el coste deja de crecer con el numero de perfiles multiplicado por sus filas.
        """
        with self._lock:
            filas = self._conn.execute(
                """SELECT user_id, SUM(mensajes), SUM(hechos) FROM (
                       SELECT user_id, COUNT(*) AS mensajes, 0 AS hechos
                         FROM short_term_history GROUP BY user_id
                       UNION ALL
                       SELECT user_id, 0 AS mensajes, COUNT(*) AS hechos
                         FROM episodic_memory GROUP BY user_id)
                    GROUP BY user_id ORDER BY user_id"""
            ).fetchall()
        return [(str(u), int(m), int(h)) for u, m, h in filas]

    # ------------------------------------------------------------- retencion

    def podar_historial(self, horas: int) -> int:
        """Elimina el historial conversacional anterior a `horas`. Devuelve filas borradas.

        La memoria episodica NO se toca: son pocos hechos por perfil y son lo que
        da continuidad entre visitas. Lo que se poda es la transcripcion literal,
        que crece sin limite y es el dato mas sensible que guarda el sistema.
        """
        if horas <= 0:
            return 0
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "DELETE FROM short_term_history "
                "WHERE timestamp < datetime('now', ?)",
                (f"-{int(horas)} hours",),
            )
            borradas = cur.rowcount
            self._conn.commit()
        if borradas:
            self._log.info("Poda de historial: %d mensajes con mas de %dh", borradas, horas)
        return borradas

    def perfiles_vacios(self) -> list[str]:
        """Perfiles que ya no tienen ni historial ni hechos tras una poda."""
        return [p for p, mensajes, hechos in self.list_profiles() if mensajes == 0 and hechos == 0]

    def migrate_profile(self, origen: str, destino: str) -> int:
        """Traslada historial y hechos de `origen` a `destino` y deduplica el resultado.

        Es la respuesta al caso "me llamo Harley, antes dije Astrid": sin esto el
        sistema dejaba dos perfiles paralelos con los hechos repartidos.
        """
        if origen == destino:
            return 0
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("UPDATE short_term_history SET user_id = ? WHERE user_id = ?", (destino, origen))
            movidos = cur.rowcount
            cur.execute("UPDATE episodic_memory SET user_id = ? WHERE user_id = ?", (destino, origen))
            movidos += cur.rowcount
            self._conn.commit()

            vistos: set[str] = set()
            unicos: list[str] = []
            for hecho in self.get_all_episodes(destino):
                clave = FactSanitizer.clave_dedup(hecho)
                if clave not in vistos:
                    vistos.add(clave)
                    unicos.append(hecho)
            self.overwrite_episodes(destino, unicos)

        self._log.info("Perfil migrado: %s -> %s (%d registros)", origen, destino, movidos)
        return movidos

    def reset_profile(self, user_id: str) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM short_term_history WHERE user_id = ?", (user_id,))
            cur.execute("DELETE FROM episodic_memory WHERE user_id = ?", (user_id,))
            self._conn.commit()
        self._log.info("Memoria restablecida para %s", user_id)

    def purge_all(self) -> int:
        """Comando #7: purga global de la base de datos. Devuelve el numero de perfiles borrados."""
        with self._lock:
            perfiles = len(self.list_profiles())
            cur = self._conn.cursor()
            cur.execute("DELETE FROM short_term_history")
            cur.execute("DELETE FROM episodic_memory")
            cur.execute("DELETE FROM sqlite_sequence WHERE name IN ('short_term_history','episodic_memory')")
            self._conn.commit()
            try:
                self._conn.execute("VACUUM")
            except sqlite3.OperationalError:
                self._log.debug("VACUUM omitido: transaccion activa", exc_info=True)
        self._log.warning("Purga global ejecutada: %d perfiles eliminados", perfiles)
        return perfiles

    # -------------------------------------------------------------- saneado

    def sanear(
        self,
        sanitizer: FactSanitizer,
        es_nombre_valido: Callable[[str], bool],
        preservar: str,
    ) -> tuple[int, int]:
        """Depura la base heredada: elimina perfiles espurios y hechos con meta-texto.

        Devuelve (perfiles eliminados, hechos eliminados).
        """
        perfiles_borrados = 0
        hechos_borrados = 0

        for perfil, _, _ in self.list_profiles():
            if perfil != preservar and not es_nombre_valido(perfil):
                self.reset_profile(perfil)
                perfiles_borrados += 1
                self._log.warning("Perfil invalido eliminado en el saneado: %s", perfil)
                continue

            hechos = self.get_all_episodes(perfil)
            limpios: list[str] = []
            vistos: set[str] = set()
            for hecho in hechos:
                normalizado = sanitizer.limpiar(hecho)
                if normalizado is None:
                    hechos_borrados += 1
                    self._log.info("Hecho descartado en el saneado (%s): %s", perfil, hecho[:80])
                    continue
                clave = FactSanitizer.clave_dedup(normalizado)
                if clave in vistos:
                    hechos_borrados += 1
                    continue
                vistos.add(clave)
                limpios.append(normalizado)
            if len(limpios) != len(hechos):
                self.overwrite_episodes(perfil, limpios)

        return perfiles_borrados, hechos_borrados

    def cerrar(self) -> None:
        with self._lock:
            self._conn.close()
