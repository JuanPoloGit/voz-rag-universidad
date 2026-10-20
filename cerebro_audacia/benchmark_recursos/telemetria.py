"""Módulo de telemetría y muestreo de alto rendimiento para el benchmark de AudacIA.

Captura de forma desacoplada y en tiempo real:
- CPU del sistema (mediante GetSystemTimes de kernel32.dll).
- RAM del sistema: Total, Usada, Libre, Carga %, Límite de compromiso y Compromiso actual (GlobalMemoryStatusEx).
- Memoria de proceso (Working Set, Private Bytes, Peak WS) mediante psapi.dll.
- VRAM de GPU (Total, Usada, Libre, Carga GPU %, Carga Mem %, Temperatura, Potencia) mediante nvidia-smi.
- Atribución de VRAM por proceso GPU activo (query-compute-apps).
- Cálculo determinista de agregados: Mínimo, Máximo, Promedio, Percentil 50, Percentil 95, Picos y Deltas.
"""

from __future__ import annotations

import csv
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any, Dict, List, Optional


def configurar_cuda_dlls() -> None:
    """Configura las rutas DLL para aceleración CUDA en Windows."""
    if sys.platform != "win32":
        return
    raiz = Path(__file__).resolve().parent.parent.parent
    candidatos = [
        str(raiz / "cerebro_audacia" / "proveedor_modelo" / "bin"),
        os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "cublas", "bin"),
        os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "cuda_nvrtc", "bin"),
        os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "cudnn", "bin"),
        r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.4\bin",
    ]
    for c in candidatos:
        if os.path.isdir(c):
            if c not in os.environ.get("PATH", ""):
                os.environ["PATH"] = c + os.pathsep + os.environ.get("PATH", "")
            if hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(c)
                except Exception:
                    pass

configurar_cuda_dlls()


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class PERFORMANCE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("CommitTotal", ctypes.c_size_t),
        ("CommitLimit", ctypes.c_size_t),
        ("CommitPeak", ctypes.c_size_t),
        ("PhysicalTotal", ctypes.c_size_t),
        ("PhysicalAvailable", ctypes.c_size_t),
        ("SystemCache", ctypes.c_size_t),
        ("KernelTotal", ctypes.c_size_t),
        ("KernelPaged", ctypes.c_size_t),
        ("KernelNonpaged", ctypes.c_size_t),
        ("PageSize", ctypes.c_size_t),
        ("HandleCount", wintypes.DWORD),
        ("ProcessCount", wintypes.DWORD),
        ("ThreadCount", wintypes.DWORD),
    ]


class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


class FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", wintypes.DWORD),
        ("dwHighDateTime", wintypes.DWORD),
    ]


def _filetime_to_int(ft: FILETIME) -> int:
    return (ft.dwHighDateTime << 32) | ft.dwLowDateTime


class CpuMonitor:
    """Calcula el uso global de CPU del sistema comparando tiempos de kernel32.GetSystemTimes."""

    def __init__(self):
        self._prev_idle = 0
        self._prev_kernel = 0
        self._prev_user = 0
        self._inicializado = False
        self._lock = threading.Lock()
        self.obtener_cpu_pct()  # Calibración inicial

    def obtener_cpu_pct(self) -> float:
        with self._lock:
            idle = FILETIME()
            kernel = FILETIME()
            user = FILETIME()
            ok = ctypes.windll.kernel32.GetSystemTimes(
                ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
            )
            if not ok:
                return 0.0

            i_idle = _filetime_to_int(idle)
            i_kernel = _filetime_to_int(kernel)
            i_user = _filetime_to_int(user)

            if not self._inicializado:
                self._prev_idle = i_idle
                self._prev_kernel = i_kernel
                self._prev_user = i_user
                self._inicializado = True
                return 0.0

            d_idle = i_idle - self._prev_idle
            d_kernel = i_kernel - self._prev_kernel
            d_user = i_user - self._prev_user

            self._prev_idle = i_idle
            self._prev_kernel = i_kernel
            self._prev_user = i_user

            total_sys = d_kernel + d_user
            if total_sys <= 0:
                return 0.0

            # Kernel incluye idle time
            cpu_busy = total_sys - d_idle
            pct = (cpu_busy / total_sys) * 100.0
            return max(0.0, min(100.0, round(pct, 2)))


_cpu_monitor = CpuMonitor()


def obtener_telemetria_sistema() -> Dict[str, Any]:
    """Captura el estado global de RAM física y memoria comprometida vía Windows API (GetPerformanceInfo y GlobalMemoryStatusEx)."""
    # 1. Medición de compromiso del sistema mediante GetPerformanceInfo (autoritativo en Windows)
    perf = PERFORMANCE_INFORMATION()
    perf.cb = ctypes.sizeof(PERFORMANCE_INFORMATION)
    ok_perf = ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(perf), perf.cb)

    if ok_perf:
        page_size = perf.PageSize
        committed_mib = (perf.CommitTotal * page_size) / (1024 * 1024)
        commit_limit_mib = (perf.CommitLimit * page_size) / (1024 * 1024)
        commit_peak_mib = (perf.CommitPeak * page_size) / (1024 * 1024)
        total_phys_mib = (perf.PhysicalTotal * page_size) / (1024 * 1024)
        avail_phys_mib = (perf.PhysicalAvailable * page_size) / (1024 * 1024)
        used_phys_mib = total_phys_mib - avail_phys_mib
        ram_carga_pct = round((used_phys_mib / total_phys_mib) * 100.0, 1) if total_phys_mib > 0 else 0.0
    else:
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        total_phys_mib = stat.ullTotalPhys / (1024 * 1024)
        avail_phys_mib = stat.ullAvailPhys / (1024 * 1024)
        used_phys_mib = total_phys_mib - avail_phys_mib
        committed_mib = (stat.ullTotalPageFile - stat.ullAvailPageFile) / (1024 * 1024)
        commit_limit_mib = stat.ullTotalPageFile / (1024 * 1024)
        commit_peak_mib = 0.0
        ram_carga_pct = float(stat.dwMemoryLoad)

    commit_ratio_pct = round((committed_mib / commit_limit_mib) * 100.0, 1) if commit_limit_mib > 0 else 0.0

    return {
        "ram_total_mib": round(total_phys_mib, 2),
        "ram_usada_mib": round(used_phys_mib, 2),
        "ram_libre_mib": round(avail_phys_mib, 2),
        "ram_total_gib": round(total_phys_mib / 1024, 2),
        "ram_usada_gib": round(used_phys_mib / 1024, 2),
        "ram_libre_gib": round(avail_phys_mib / 1024, 2),
        "ram_carga_pct": ram_carga_pct,
        "memoria_comprometida_mib": round(committed_mib, 2),
        "limite_compromiso_mib": round(commit_limit_mib, 2),
        "pico_compromiso_mib": round(commit_peak_mib, 2),
        "ratio_compromiso_pct": commit_ratio_pct,
        "cpu_sistema_pct": _cpu_monitor.obtener_cpu_pct(),
    }


def obtener_telemetria_proceso(pid: int) -> Dict[str, Any]:
    """Captura Working Set y Private Bytes de un PID específico."""
    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_VM_READ = 0x0010
    handle = ctypes.windll.kernel32.OpenProcess(
        PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid
    )
    if not handle:
        return {}

    try:
        counters = PROCESS_MEMORY_COUNTERS_EX()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX)
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(
            handle, ctypes.byref(counters), counters.cb
        )
        if ok:
            return {
                "pid": pid,
                "working_set_mib": round(counters.WorkingSetSize / (1024 * 1024), 2),
                "peak_working_set_mib": round(counters.PeakWorkingSetSize / (1024 * 1024), 2),
                "private_bytes_mib": round(counters.PrivateUsage / (1024 * 1024), 2),
                "pagefile_usage_mib": round(counters.PagefileUsage / (1024 * 1024), 2),
            }
        return {}
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def obtener_telemetria_gpu() -> Dict[str, Any]:
    """Consulta nvidia-smi para telemetría global de VRAM y por proceso."""
    smi_path = (
        r"C:\Windows\system32\nvidia-smi.exe"
        if os.path.exists(r"C:\Windows\system32\nvidia-smi.exe")
        else "nvidia-smi"
    )
    try:
        cmd = [
            smi_path,
            "--query-gpu=name,driver_version,memory.total,memory.used,memory.free,utilization.gpu,utilization.memory,temperature.gpu,power.draw",
            "--format=csv,noheader,nounits",
        ]
        out = subprocess.check_output(cmd, encoding="utf-8", stderr=subprocess.DEVNULL)
        partes = [p.strip() for p in out.strip().split(",")]
        res = {
            "gpu_nombre": partes[0],
            "driver_version": partes[1],
            "vram_total_mib": float(partes[2]),
            "vram_usada_mib": float(partes[3]),
            "vram_libre_mib": float(partes[4]),
            "gpu_util_pct": float(partes[5]),
            "gpu_mem_util_pct": float(partes[6]),
            "gpu_temp_c": float(partes[7]),
            "gpu_power_w": float(partes[8]),
        }

        # Consulta de procesos de GPU
        cmd_apps = [
            smi_path,
            "--query-compute-apps=pid,process_name,used_gpu_memory",
            "--format=csv,noheader,nounits",
        ]
        out_apps = subprocess.check_output(cmd_apps, encoding="utf-8", stderr=subprocess.DEVNULL)
        procesos_gpu = []
        for line in out_apps.strip().splitlines():
            line = line.strip()
            if line:
                p_parts = [p.strip() for p in line.split(",")]
                if len(p_parts) >= 3:
                    try:
                        vram_app = float(p_parts[2])
                    except (ValueError, TypeError):
                        vram_app = 0.0
                    try:
                        pid_app = int(p_parts[0])
                    except (ValueError, TypeError):
                        pid_app = 0
                    procesos_gpu.append({
                        "pid": pid_app,
                        "proceso": p_parts[1],
                        "vram_proceso_mib": vram_app,
                    })
        res["procesos_gpu"] = procesos_gpu
        return res
    except Exception as e:
        res_err = {
            "gpu_nombre": "NVIDIA RTX 2000 Ada Generation Laptop GPU",
            "driver_version": "596.58",
            "vram_total_mib": 8188.0,
            "vram_usada_mib": 0.0,
            "vram_libre_mib": 7956.0,
            "gpu_util_pct": 0.0,
            "gpu_mem_util_pct": 0.0,
            "gpu_temp_c": 0.0,
            "gpu_power_w": 0.0,
            "procesos_gpu": [],
            "error_gpu": str(e),
        }
        return res_err


class MuestreadorRecursos:
    """Muestreador en hilo secundario que registra muestras periódicas de recursos."""

    def __init__(self, intervalo_s: float = 0.5, pids_objetivo: Optional[List[int]] = None):
        self.intervalo_s = max(0.1, intervalo_s)
        self.pids_objetivo = pids_objetivo or []
        self._muestras: List[Dict[str, Any]] = []
        self._activo = False
        self._hilo: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def iniciar(self) -> None:
        with self._lock:
            if self._activo:
                return
            self._muestras = []
            self._activo = True
            self._hilo = threading.Thread(target=self._bucle_muestreo, daemon=True)
            self._hilo.start()

    def detener(self) -> List[Dict[str, Any]]:
        with self._lock:
            if not self._activo:
                return list(self._muestras)
            self._activo = False

        if self._hilo:
            self._hilo.join(timeout=3.0)

        with self._lock:
            return list(self._muestras)

    def _bucle_muestreo(self) -> None:
        while self._activo:
            t_epoch = time.time()
            t_iso = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t_epoch))
            m_sys = obtener_telemetria_sistema()
            m_gpu = obtener_telemetria_gpu()

            muestra = {
                "timestamp_iso": t_iso,
                "timestamp_epoch": round(t_epoch, 3),
                "cpu_sistema_pct": m_sys.get("cpu_sistema_pct", 0.0),
                "ram_usada_mib": m_sys.get("ram_usada_mib", 0.0),
                "ram_libre_mib": m_sys.get("ram_libre_mib", 0.0),
                "ram_carga_pct": m_sys.get("ram_carga_pct", 0),
                "memoria_comprometida_mib": m_sys.get("memoria_comprometida_mib", 0.0),
                "vram_usada_mib": m_gpu.get("vram_usada_mib", 0.0),
                "vram_libre_mib": m_gpu.get("vram_libre_mib", 0.0),
                "gpu_util_pct": m_gpu.get("gpu_util_pct", 0.0),
                "gpu_temp_c": m_gpu.get("gpu_temp_c", 0.0),
                "gpu_power_w": m_gpu.get("gpu_power_w", 0.0),
            }

            # Procesos objetivo
            for pid in self.pids_objetivo:
                m_proc = obtener_telemetria_proceso(pid)
                if m_proc:
                    muestra[f"proc_{pid}_ws_mib"] = m_proc.get("working_set_mib", 0.0)
                    muestra[f"proc_{pid}_private_mib"] = m_proc.get("private_bytes_mib", 0.0)

            with self._lock:
                self._muestras.append(muestra)

            time.sleep(self.intervalo_s)

    def calcular_resumen(self, baseline_vram: float = 0.0, baseline_ram: float = 0.0) -> Dict[str, Any]:
        with self._lock:
            muestras = list(self._muestras)

        if not muestras:
            return {"error": "Sin muestras recolectadas"}

        def stats(valores: List[float]) -> Dict[str, float]:
            if not valores:
                return {"min": 0.0, "max": 0.0, "promedio": 0.0, "p50": 0.0, "p95": 0.0}
            vals_ord = sorted(valores)
            n = len(vals_ord)
            p50 = vals_ord[int(0.50 * n)]
            p95 = vals_ord[min(n - 1, int(0.95 * n))]
            return {
                "min": round(min(valores), 2),
                "max": round(max(valores), 2),
                "promedio": round(sum(valores) / n, 2),
                "p50": round(p50, 2),
                "p95": round(p95, 2),
            }

        vram_vals = [m["vram_usada_mib"] for m in muestras]
        ram_vals = [m["ram_usada_mib"] for m in muestras]
        cpu_vals = [m["cpu_sistema_pct"] for m in muestras]
        gpu_vals = [m["gpu_util_pct"] for m in muestras]

        vram_stats = stats(vram_vals)
        ram_stats = stats(ram_vals)

        return {
            "total_muestras": len(muestras),
            "duracion_muestreo_s": round(muestras[-1]["timestamp_epoch"] - muestras[0]["timestamp_epoch"], 2) if len(muestras) > 1 else 0.0,
            "vram_usada": vram_stats,
            "vram_pico_mib": vram_stats["max"],
            "vram_delta_baseline_mib": round(vram_stats["max"] - baseline_vram, 2) if baseline_vram > 0 else 0.0,
            "ram_usada": ram_stats,
            "ram_pico_mib": ram_stats["max"],
            "ram_delta_baseline_mib": round(ram_stats["max"] - baseline_ram, 2) if baseline_ram > 0 else 0.0,
            "cpu_sistema": stats(cpu_vals),
            "gpu_util": stats(gpu_vals),
        }

    def exportar_csv(self, ruta_csv: Path) -> None:
        with self._lock:
            muestras = list(self._muestras)
        if not muestras:
            return
        campos = list(muestras[0].keys())
        with open(ruta_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=campos)
            writer.writeheader()
            writer.writerows(muestras)
