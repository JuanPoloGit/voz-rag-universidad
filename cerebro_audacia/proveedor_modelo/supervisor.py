"""Supervisor y gestor de ciclo de vida desacoplado para llama-server.exe en AudacIA.

Garantías de resiliencia:
1. Propiedad estricta del proceso: solo controla la instancia de AudacIA; no mata procesos ajenos.
2. Prevención de supervisores duplicados mediante cerrojo exclusivo en disco.
3. Detección de procesos muertos (DEAD), lentos/no responsivos (UNRESPONSIVE) y saludables (HEALTHY).
4. Límite estricto de reinicios consecutivos (3 por defecto) con retroceso exponencial (backoff).
5. Transición a estado AGOTADO (EXHAUSTED) para evitar bucles infinitos de reinicio ante fallos fatales de GPU/OOM.
6. Limpieza segura y validación de PIDs obsoletos o reutilizados por el sistema operativo.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from enum import Enum
from pathlib import Path
from typing import Any, NamedTuple, Optional

logger = logging.getLogger("cerebro_audacia.supervisor")


class EstadoSupervisor(str, Enum):
    """Estados del supervisor de inferencia."""

    DETENIDO = "DETENIDO"
    INICIANDO = "INICIANDO"
    SALUDABLE = "SALUDABLE"
    NO_RESPONSIVO = "NO_RESPONSIVO"
    REINICIANDO = "REINICIANDO"
    AGOTADO = "AGOTADO"
    FALLIDO = "FALLIDO"


class RegistroFallo(NamedTuple):
    timestamp: float
    motivo: str
    codigo_salida: Optional[int]


class SupervisorLlamaServer:
    """Supervisor de proceso para el servidor de inferencia llama-server de AudacIA."""

    def __init__(
        self,
        directorio_base: Optional[Path] = None,
        max_reinicios_consecutivos: int = 3,
        espera_base_s: float = 1.0,
        factor_backoff: float = 2.0,
        ventana_estabilidad_s: float = 60.0,
    ) -> None:
        self.directorio_base = (
            directorio_base
            if directorio_base is not None
            else Path(__file__).resolve().parent
        )
        self.archivo_estado = self.directorio_base / "llama_server_estado.json"
        self.archivo_lock = self.directorio_base / "llama_supervisor.lock"
        self.archivo_log = self.directorio_base / "llama_supervisor.log"

        self.max_reinicios_consecutivos = max(1, max_reinicios_consecutivos)
        self.espera_base_s = max(0.1, espera_base_s)
        self.factor_backoff = max(1.0, factor_backoff)
        self.ventana_estabilidad_s = max(1.0, ventana_estabilidad_s)

        self._estado = EstadoSupervisor.DETENIDO
        self._reinicios_consecutivos = 0
        self._historial_fallos: list[RegistroFallo] = []
        self._ultimo_exito_salud: float = 0.0
        self._proceso: Optional[subprocess.Popen[Any]] = None
        self._pid_supervisado: Optional[int] = None
        self._lock_fh: Optional[Any] = None
        self._hilo_supervisor: Optional[threading.Thread] = None
        self._detener_solicitado = threading.Event()
        self._lock_interno = threading.Lock()

    @property
    def estado(self) -> EstadoSupervisor:
        with self._lock_interno:
            return self._estado

    @property
    def reinicios_consecutivos(self) -> int:
        with self._lock_interno:
            return self._reinicios_consecutivos

    @property
    def historial_fallos(self) -> list[RegistroFallo]:
        with self._lock_interno:
            return list(self._historial_fallos)

    def adquirir_bloqueo_supervisor(self) -> bool:
        """Adquiere un bloqueo exclusivo de archivo para evitar supervisores duplicados."""
        try:
            self.archivo_lock.parent.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                import msvcrt
                fh = open(self.archivo_lock, "w")
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    fh.write(f"PID={os.getpid()}\nTIMESTAMP={time.time()}\n")
                    fh.flush()
                    self._lock_fh = fh
                    return True
                except (OSError, IOError):
                    fh.close()
                    return False
            else:
                import fcntl
                fh = open(self.archivo_lock, "w")
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fh.write(f"PID={os.getpid()}\nTIMESTAMP={time.time()}\n")
                    fh.flush()
                    self._lock_fh = fh
                    return True
                except (OSError, IOError):
                    fh.close()
                    return False
        except Exception as e:
            logger.error("Error adquiriendo lock de supervisor: %s", e)
            return False

    def liberar_bloqueo_supervisor(self) -> None:
        """Libera el cerrojo de supervisor."""
        if self._lock_fh:
            try:
                if os.name == "nt":
                    import msvcrt
                    try:
                        msvcrt.locking(self._lock_fh.fileno(), msvcrt.LK_UNLCK, 1)
                    except Exception:
                        pass
                else:
                    import fcntl
                    try:
                        fcntl.flock(self._lock_fh.fileno(), fcntl.LOCK_UN)
                    except Exception:
                        pass
                self._lock_fh.close()
            except Exception:
                pass
            self._lock_fh = None
        if self.archivo_lock.exists():
            try:
                self.archivo_lock.unlink()
            except Exception:
                pass

    def verificar_propiedad_proceso(self, pid: int, nombre_esperado: str = "llama-server") -> bool:
        """Comprueba de forma fiable si el PID pertenece a una instancia válida de llama-server."""
        if not isinstance(pid, int) or pid <= 0:
            return False

        if os.name == "nt":
            try:
                cmd = ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"]
                salida = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
                for linea in salida.strip().splitlines():
                    if str(pid) in linea:
                        partes = [p.strip(' "') for p in linea.split(",")]
                        if partes and nombre_esperado.lower() in partes[0].lower():
                            return True
                return False
            except Exception:
                return False
        else:
            try:
                os.kill(pid, 0)
                comm_path = Path(f"/proc/{pid}/comm")
                if comm_path.exists():
                    return nombre_esperado.lower() in comm_path.read_text(encoding="utf-8").lower()
                return True
            except (OSError, ProcessLookupError):
                return False

    def leer_estado_disco(self) -> Optional[dict[str, Any]]:
        """Lee el archivo de estado de AudacIA si existe y valida vigencia."""
        if not self.archivo_estado.exists():
            return None
        try:
            with open(self.archivo_estado, "r", encoding="utf-8") as f:
                datos = json.load(f)
            pid = datos.get("pid")
            if pid and self.verificar_propiedad_proceso(int(pid)):
                return datos
            else:
                # PID obsoleto o proceso terminado
                self.archivo_estado.unlink(missing_ok=True)
                return None
        except Exception:
            self.archivo_estado.unlink(missing_ok=True)
            return None

    def registrar_estado_disco(self, pid: int, puerto: int, perfil: str, ejecutable: str, modelo: str) -> None:
        """Escribe la metadata de propiedad del proceso AudacIA."""
        estado = {
            "pid": pid,
            "puerto": puerto,
            "perfil": perfil,
            "ejecutable_ruta": str(ejecutable),
            "modelo_ruta": str(modelo),
            "iniciado_en": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "audacia_id": f"audacia-llama-server-{puerto}",
        }
        with open(self.archivo_estado, "w", encoding="utf-8") as f:
            json.dump(estado, f, indent=2, ensure_ascii=False)

    def comprobar_salud_endpoint(self, url_salud: str = "http://127.0.0.1:8080/health", timeout_s: float = 2.0) -> bool:
        """Sondea el endpoint /health del servidor."""
        try:
            req = urllib.request.Request(url_salud, headers={"User-Agent": "AudacIA-Supervisor/1.0"})
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                if resp.status == 200:
                    cuerpo = resp.read().decode("utf-8", errors="replace")
                    datos = json.loads(cuerpo)
                    return datos.get("status") in ("ok", "ready")
        except Exception:
            return False
        return False

    def supervisar_paso(
        self,
        puerto: int = 8080,
        comando_arranque: Optional[list[str]] = None,
        url_salud: Optional[str] = None,
    ) -> EstadoSupervisor:
        """Ejecuta una ronda de supervisión determinista (evaluación de estado y auto-recuperación)."""
        url = url_salud or f"http://127.0.0.1:{puerto}/health"

        with self._lock_interno:
            # 1. Si el supervisor está agotado, rechazar reinicios automáticos
            if self._estado == EstadoSupervisor.AGOTADO:
                return EstadoSupervisor.AGOTADO

            # 2. Verificar estado del proceso supervisado
            proceso_vivo = False
            if self._proceso is not None:
                if self._proceso.poll() is None:
                    proceso_vivo = True
            elif self._pid_supervisado is not None:
                proceso_vivo = self.verificar_propiedad_proceso(self._pid_supervisado)
            else:
                # Comprobar si hay un proceso registrado en disco
                datos_disco = self.leer_estado_disco()
                if datos_disco:
                    self._pid_supervisado = datos_disco["pid"]
                    proceso_vivo = True

            # Caso A: El proceso está vivo
            if proceso_vivo:
                es_saludable = self.comprobar_salud_endpoint(url)
                ahora = time.monotonic()
                if es_saludable:
                    self._estado = EstadoSupervisor.SALUDABLE
                    # Si ha permanecido saludable durante la ventana de estabilidad, resetear contador
                    if self._ultimo_exito_salud > 0 and (ahora - self._ultimo_exito_salud) >= self.ventana_estabilidad_s:
                        if self._reinicios_consecutivos > 0:
                            logger.info("Servidor estable por %ss: reseteando contador de reinicios.", self.ventana_estabilidad_s)
                            self._reinicios_consecutivos = 0
                    self._ultimo_exito_salud = ahora
                    return EstadoSupervisor.SALUDABLE
                else:
                    self._estado = EstadoSupervisor.NO_RESPONSIVO
                    return EstadoSupervisor.NO_RESPONSIVO

            # Caso B: El proceso ha muerto o no existe
            codigo_salida = self._proceso.poll() if self._proceso else None
            motivo = f"Proceso no activo o terminado inesperadamente (exit_code={codigo_salida})."
            self._historial_fallos.append(RegistroFallo(timestamp=time.time(), motivo=motivo, codigo_salida=codigo_salida))

            self._reinicios_consecutivos += 1
            logger.warning(
                "Fallo de proceso detectado (#%d/%d). Motivo: %s",
                self._reinicios_consecutivos,
                self.max_reinicios_consecutivos,
                motivo,
            )

            # Verificar si se superó el límite de reintentos consecutivos
            if self._reinicios_consecutivos > self.max_reinicios_consecutivos:
                self._estado = EstadoSupervisor.AGOTADO
                logger.critical(
                    "Límite de reinicios consecutivos (%d) excedido. "
                    "Se declara el Cerebro Central en estado AGOTADO para evitar bucles de OOM/Crash.",
                    self.max_reinicios_consecutivos,
                )
                self.archivo_estado.unlink(missing_ok=True)
                return EstadoSupervisor.AGOTADO

            # Intentar reinicio acotado si se suministró comando_arranque
            if comando_arranque:
                self._estado = EstadoSupervisor.REINICIANDO
                espera = self.espera_base_s * (self.factor_backoff ** (self._reinicios_consecutivos - 1))
                logger.info("Aplicando retroceso progresivo (espera de %.2fs) antes de reiniciar.", espera)
                time.sleep(espera)

                try:
                    self._proceso = subprocess.Popen(
                        comando_arranque,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    self._pid_supervisado = self._proceso.pid
                    self.registrar_estado_disco(
                        pid=self._proceso.pid,
                        puerto=puerto,
                        perfil="supervisor_auto",
                        ejecutable=comando_arranque[0],
                        modelo="configurado",
                    )
                    self._estado = EstadoSupervisor.INICIANDO
                    return EstadoSupervisor.INICIANDO
                except Exception as ex:
                    logger.error("Error al re-lanzar proceso en reinicio: %s", ex)
                    self._estado = EstadoSupervisor.FALLIDO
                    return EstadoSupervisor.FALLIDO

            self._estado = EstadoSupervisor.DETENIDO
            return EstadoSupervisor.DETENIDO

    def restablecer_supervisor(self) -> None:
        """Restablece el contador de reinicios y saca al supervisor del estado AGOTADO tras intervención."""
        with self._lock_interno:
            self._reinicios_consecutivos = 0
            self._estado = EstadoSupervisor.DETENIDO
            self._historial_fallos.clear()
            logger.info("Supervisor restablecido manualmente: estado DETENIDO, contador de reinicios en cero.")

    def detener(self, timeout_gracia_s: float = 3.0) -> bool:
        """Detiene limpiamente el proceso supervisado y libera recursos."""
        self._detener_solicitado.set()
        with self._lock_interno:
            pid = self._pid_supervisado or (self._proceso.pid if self._proceso else None)
            if not pid:
                datos = self.leer_estado_disco()
                if datos:
                    pid = datos.get("pid")

            if pid and self.verificar_propiedad_proceso(pid):
                try:
                    if self._proceso and self._proceso.pid == pid:
                        self._proceso.terminate()
                        try:
                            self._proceso.wait(timeout=timeout_gracia_s)
                        except subprocess.TimeoutExpired:
                            self._proceso.kill()
                            self._proceso.wait(timeout=1.0)
                    else:
                        # Si no tenemos el Popen pero sí el PID verificado de AudacIA
                        if os.name == "nt":
                            subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        else:
                            os.kill(pid, 9)
                except Exception as e:
                    logger.warning("Error deteniendo PID %s: %s", pid, e)

            self._proceso = None
            self._pid_supervisado = None
            self._estado = EstadoSupervisor.DETENIDO
            self.archivo_estado.unlink(missing_ok=True)
            self.liberar_bloqueo_supervisor()
            return True
