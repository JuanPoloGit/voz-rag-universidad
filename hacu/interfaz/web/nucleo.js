/**
 * Nucleo animado de HACU, version web (canvas 2D).
 *
 * Puerto directo de la logica de pintura de `hacu/interfaz/widgets.py`
 * (clase NucleoHacu): mismos estados, misma paleta, misma distribucion de
 * Fibonacci sobre una esfera, mismas ondas/destellos/rayos/corrientes. Vive
 * aqui -en vez de QPainter- porque el canvas + CSS dan blur, gradientes y
 * composicion "lighter" de verdad, que QPainter solo podia aproximar a
 * mano.
 *
 * Puente con Python: `_conectarPuente()` se engancha a QWebChannel si esta
 * disponible (dentro de QWebEngineView) y escucha las senales
 * `estadoCambiado`/`nivelCambiado` del objeto `puenteHacu` (ver
 * `hacu/interfaz/vista_web.py`). Si no hay QWebChannel -por ejemplo, al
 * abrir este archivo suelto en un navegador para revisarlo- cae en un modo
 * demo que recorre los cinco estados solo, para poder ver el nucleo sin la
 * app de escritorio.
 */
"use strict";

// --- Configuracion por estado, calcada de widgets.py --------------------
const ROTACION_POR_ESTADO = {
  REPOSO: 0.12, ESCUCHANDO: 0.32, PENSANDO: 0.85, HABLANDO: 0.5, ERROR: 0.22,
};
const TURBULENCIA_POR_ESTADO = {
  REPOSO: 0.05, ESCUCHANDO: 0.09, PENSANDO: 0.17, HABLANDO: 0.11, ERROR: 0.20,
};
const VELOCIDAD_LATIDO = {
  REPOSO: 0.9, ESCUCHANDO: 2.4, PENSANDO: 3.4, HABLANDO: 2.0, ERROR: 1.2,
};
const INTERVALO_ONDA = {
  REPOSO: 2.6, ESCUCHANDO: 1.7, PENSANDO: 0.9, HABLANDO: 1.1, ERROR: 1.3,
};
const DURACION_ONDA = 2.2;
const TASA_DESTELLO = {
  REPOSO: 0.15, ESCUCHANDO: 0.45, PENSANDO: 0.95, HABLANDO: 0.65, ERROR: 0.35,
};
const DURACION_DESTELLO = 0.5;
const TASA_RAYO = {
  REPOSO: 0.03, ESCUCHANDO: 0.10, PENSANDO: 0.35, HABLANDO: 0.18, ERROR: 0.12,
};
const DURACION_RAYO = 0.35;
// Tres tonos por estado (no uno solo), igual que _PALETA_ESTADO en Python.
const PALETA_ESTADO = {
  REPOSO: ["#5E6B88", "#3A4A6B", "#26314D"],
  ESCUCHANDO: ["#4ADE80", "#31D9C0", "#8CF5CE"],
  PENSANDO: ["#F2A33C", "#FFD48A", "#F2545B"],
  HABLANDO: ["#31D9C0", "#7C8CF8", "#8CE0FF"],
  ERROR: ["#F2545B", "#F2A33C", "#FF8A80"],
};
// Color "solido" de estado -halo, ondas, tinte de fondo-, igual que
// COLOR_ESTADO en estilos.py.
const COLOR_ESTADO = {
  REPOSO: "#5E6B88", ESCUCHANDO: "#4ADE80", PENSANDO: "#F2A33C",
  HABLANDO: "#31D9C0", ERROR: "#F2545B",
};

const N_PARTICULAS = 220;
const ANGULO_DORADO = Math.PI * (3 - Math.sqrt(5));
const RAZON_AUREA = 0.6180339887;
const N_CORRIENTES = 6;
const PUNTOS_FONDO = 70;
const MARGEN = 18;

function hexARgba(hex, alfa) {
  const n = parseInt(hex.slice(1), 16);
  const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
  return `rgba(${r}, ${g}, ${b}, ${alfa})`;
}

// PRNG determinista (mulberry32): el mismo "seed" siempre da el mismo
// zigzag para un rayo, para que no tiemble entre frames -equivalente a
// `random.Random(semilla)` en la version Python.
function crearAzar(seed) {
  let s = seed >>> 0;
  return function () {
    s |= 0; s = (s + 0x6D2B79F5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

class NucleoHacuWeb {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.estado = "REPOSO";
    this.nivel = 0.0;
    this.nivelSuave = 0.0;
    this.fase = 0.0;
    this.tiempo = 0.0;
    this.radio = 0;
    this.ondas = [];          // [t0, ...]
    this.proxOnda = 0.0;
    this.destellos = [];      // [{x,y,z,t0}, ...]
    this.rayos = [];          // [{p1,p2,t0,seed}, ...]
    this._ultimoTs = null;

    // Base de particulas: distribucion de Fibonacci, calculada UNA vez
    // (igual que en Python, cada particula se reproyecta cada frame a
    // partir de su indice y del tiempo, no guarda posicion propia).
    this.particulas = [];
    for (let i = 0; i < N_PARTICULAS; i++) {
      const y = 1 - (i / (N_PARTICULAS - 1)) * 2;
      const radioAnillo = Math.sqrt(Math.max(0, 1 - y * y));
      const theta = i * ANGULO_DORADO;
      const x = Math.cos(theta) * radioAnillo;
      const z = Math.sin(theta) * radioAnillo;
      const capa = 0.62 + 0.4 * ((i * RAZON_AUREA) % 1.0);
      const fase = (i * RAZON_AUREA * 2 * Math.PI) % (2 * Math.PI);
      const frecuencia = 0.8 + (i % 7) * 0.15;
      this.particulas.push({ x, y, z, capa, fase, frecuencia });
    }
    // Puntos de fondo (parpadeo lejano), reparto por razon aurea igual que
    // `_pintar_fondo_ambiente`.
    this.puntosFondo = [];
    for (let i = 0; i < PUNTOS_FONDO; i++) {
      this.puntosFondo.push({
        fx: (i * RAZON_AUREA) % 1.0,
        fy: (i * RAZON_AUREA * RAZON_AUREA) % 1.0,
        frecuencia: 0.4 + (i % 5) * 0.07,
        fase: i,
      });
    }

    this._redimensionar();
    window.addEventListener("resize", () => this._redimensionar());
    requestAnimationFrame((ts) => this._cuadro(ts));
  }

  setEstado(estado) {
    if (PALETA_ESTADO[estado] !== undefined) this.estado = estado;
  }

  setNivel(nivel) {
    const objetivo = Math.max(0, Math.min(1, nivel * 8.0));
    const alfa = objetivo > this.nivelSuave ? 0.55 : 0.15;
    this.nivelSuave += (objetivo - this.nivelSuave) * alfa;
    this.nivel = objetivo;
  }

  _redimensionar() {
    const dpr = window.devicePixelRatio || 1;
    const ancho = window.innerWidth;
    const alto = window.innerHeight;
    this.canvas.width = ancho * dpr;
    this.canvas.height = alto * dpr;
    this.canvas.style.width = ancho + "px";
    this.canvas.style.height = alto + "px";
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.anchoCss = ancho;
    this.altoCss = alto;
    this.radio = Math.max(0, Math.min(ancho, alto) / 2 - MARGEN);
  }

  _cuadro(ts) {
    if (this._ultimoTs === null) this._ultimoTs = ts;
    // Paso fijo a ~30 pasos/segundo, igual que el QTimer de Python
    // (1000 // _FPS): mantiene la MISMA sensacion de velocidad que la
    // version de escritorio en vez de acelerarse en pantallas de mas Hz.
    const PASO = 1 / 30;
    let transcurrido = (ts - this._ultimoTs) / 1000;
    transcurrido = Math.min(transcurrido, 0.25); // evita saltos tras pausas largas
    this._ultimoTs = ts;
    this._acumulado = (this._acumulado || 0) + transcurrido;
    while (this._acumulado >= PASO) {
      this._latir(PASO);
      this._acumulado -= PASO;
    }
    this._pintar();
    requestAnimationFrame((t) => this._cuadro(t));
  }

  _latir(paso) {
    const velocidad = VELOCIDAD_LATIDO[this.estado];
    this.fase = (this.fase + (velocidad * paso)) % (2 * Math.PI);
    this.tiempo += paso;
    if (this.estado !== "ESCUCHANDO") this.nivelSuave *= 0.90;
    this._avanzarOndas();
    this._avanzarDestellos();
    this._avanzarRayos();
  }

  _avanzarOndas() {
    if (this.tiempo >= this.proxOnda) {
      this.ondas.push(this.tiempo);
      this.proxOnda = this.tiempo + INTERVALO_ONDA[this.estado];
    }
    const limite = this.tiempo - DURACION_ONDA;
    this.ondas = this.ondas.filter((t0) => t0 > limite);
  }

  _avanzarDestellos() {
    if (Math.random() < TASA_DESTELLO[this.estado] / 30) {
      const y = Math.random() * 2 - 1;
      const theta = Math.random() * 2 * Math.PI;
      const radioAnillo = Math.sqrt(Math.max(0, 1 - y * y));
      this.destellos.push({
        x: Math.cos(theta) * radioAnillo, y, z: Math.sin(theta) * radioAnillo,
        t0: this.tiempo,
      });
    }
    const limite = this.tiempo - DURACION_DESTELLO;
    this.destellos = this.destellos.filter((d) => d.t0 > limite);
  }

  _avanzarRayos() {
    if (Math.random() < TASA_RAYO[this.estado] / 30) {
      const y1 = Math.random() * 1.5 - 0.6;
      const theta1 = Math.random() * 2 * Math.PI;
      const radio1 = Math.sqrt(Math.max(0, 1 - y1 * y1));
      const y2 = Math.max(-1, Math.min(1, y1 + (Math.random() - 0.5)));
      const theta2 = theta1 + (Math.random() - 0.5) * 1.2;
      const radio2 = Math.sqrt(Math.max(0, 1 - y2 * y2));
      this.rayos.push({
        p1: [Math.cos(theta1) * radio1, y1, Math.sin(theta1) * radio1],
        p2: [Math.cos(theta2) * radio2, y2, Math.sin(theta2) * radio2],
        t0: this.tiempo,
        seed: Math.floor(Math.random() * 999999),
      });
    }
    const limite = this.tiempo - DURACION_RAYO;
    this.rayos = this.rayos.filter((r) => r.t0 > limite);
  }

  // ---------------------------------------------------------------- pintura

  _pintar() {
    const ctx = this.ctx;
    const ancho = this.anchoCss, alto = this.altoCss;
    ctx.clearRect(0, 0, ancho, alto);
    if (this.radio <= 0) return;

    const color = COLOR_ESTADO[this.estado];
    const cx = ancho / 2, cy = alto / 2;
    const respiracion = (Math.sin(this.fase) + 1) / 2;
    const energia = this.estado === "ESCUCHANDO" ? this.nivelSuave : respiracion;

    this._pintarFondoAmbiente(color, cx, cy);
    this._pintarOndas(color, cx, cy);
    this._pintarHalo(color, cx, cy, energia);

    const radioNucleo = this.radio * (0.82 + 0.22 * energia);
    const achatado = 0.92 + 0.04 * Math.sin(this.tiempo * 0.3);
    const rotacion = this.tiempo * ROTACION_POR_ESTADO[this.estado];
    const cosenoRot = Math.cos(rotacion), senoRot = Math.sin(rotacion);

    this._pintarCorrientes(cx, cy, energia);
    this._pintarNucleoParticulas(cx, cy, energia, radioNucleo, achatado, cosenoRot, senoRot);
    this._pintarRayos(cx, cy, radioNucleo, achatado, cosenoRot, senoRot);
  }

  _pintarFondoAmbiente(color, cx, cy) {
    const ctx = this.ctx;
    const alcance = Math.max(this.anchoCss, this.altoCss) * 0.8;
    const halo = ctx.createRadialGradient(cx, cy, 0, cx, cy, alcance);
    halo.addColorStop(0, hexARgba(color, 16 / 255));
    halo.addColorStop(1, hexARgba(color, 0));
    ctx.fillStyle = halo;
    ctx.fillRect(0, 0, this.anchoCss, this.altoCss);

    for (const p of this.puntosFondo) {
      const px = p.fx * this.anchoCss, py = p.fy * this.altoCss;
      const parpadeo = (Math.sin(this.tiempo * p.frecuencia + p.fase) + 1) / 2;
      ctx.fillStyle = hexARgba(color, (18 + 40 * parpadeo) / 255);
      ctx.beginPath();
      ctx.arc(px, py, 1.4, 0, 2 * Math.PI);
      ctx.fill();
    }
  }

  _pintarOndas(color, cx, cy) {
    const ctx = this.ctx;
    const alcanceX = Math.max(this.anchoCss / 2 - 4, this.radio);
    const alcanceY = Math.max(this.altoCss / 2 - 4, this.radio);
    for (const t0 of this.ondas) {
      const progreso = (this.tiempo - t0) / DURACION_ONDA;
      if (progreso < 0 || progreso > 1) continue;
      const avance = 1 - (1 - progreso) ** 2;
      const rx = this.radio + (alcanceX - this.radio) * avance;
      const ry = this.radio + (alcanceY - this.radio) * avance;
      const alfa = 120 * (1 - progreso) ** 1.6;
      ctx.strokeStyle = hexARgba(color, alfa / 255);
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      ctx.ellipse(cx, cy, Math.max(rx, 0.01), Math.max(ry, 0.01), 0, 0, 2 * Math.PI);
      ctx.stroke();
    }
  }

  _pintarHalo(color, cx, cy, energia) {
    const ctx = this.ctx;
    const r = this.radio + MARGEN;
    const halo = ctx.createRadialGradient(cx, cy, 0, cx, cy, Math.max(r, 0.01));
    halo.addColorStop(0, hexARgba(color, (40 + 60 * energia) / 255));
    halo.addColorStop(1, hexARgba(color, 0));
    ctx.fillStyle = halo;
    ctx.beginPath();
    ctx.arc(cx, cy, Math.max(r, 0.01), 0, 2 * Math.PI);
    ctx.fill();
  }

  _pintarCorrientes(cx, cy, energia) {
    const ctx = this.ctx;
    const paleta = ["#33bcff", "#b68bff", "#31d9c0"];
    const alcance = Math.max(this.anchoCss, this.altoCss) * 0.62;
    ctx.globalCompositeOperation = "lighter";
    for (let i = 0; i < N_CORRIENTES; i++) {
      const angulo = (i / N_CORRIENTES) * 2 * Math.PI + this.tiempo * 0.06;
      const p0 = [cx, cy];
      const p3 = [cx + Math.cos(angulo) * alcance, cy + Math.sin(angulo) * alcance * 0.55];
      const p1 = [cx + Math.cos(angulo + 0.4) * alcance * 0.35, cy + Math.sin(angulo + 0.4) * alcance * 0.35];
      const p2 = [cx + Math.cos(angulo - 0.2) * alcance * 0.75, cy + Math.sin(angulo - 0.2) * alcance * 0.75];

      const color = paleta[i % paleta.length];
      ctx.strokeStyle = hexARgba(color, (28 + 42 * energia) / 255);
      ctx.lineWidth = 1.3;
      ctx.beginPath();
      ctx.moveTo(p0[0], p0[1]);
      ctx.bezierCurveTo(p1[0], p1[1], p2[0], p2[1], p3[0], p3[1]);
      ctx.stroke();

      const avance = (this.tiempo * (0.25 + 0.1 * (i % 3)) + i * 0.37) % 1.0;
      const punto = cubicaEnT(p0, p1, p2, p3, avance);
      const brillo = ctx.createRadialGradient(punto[0], punto[1], 0, punto[0], punto[1], 6.0);
      brillo.addColorStop(0, "rgba(255,255,255,0.78)");
      brillo.addColorStop(1, "rgba(255,255,255,0)");
      ctx.fillStyle = brillo;
      ctx.beginPath();
      ctx.arc(punto[0], punto[1], 5.0, 0, 2 * Math.PI);
      ctx.fill();
    }
    ctx.globalCompositeOperation = "source-over";
  }

  _pintarNucleoParticulas(cx, cy, energia, radioNucleo, achatado, cosenoRot, senoRot) {
    const ctx = this.ctx;
    const paleta = PALETA_ESTADO[this.estado];
    const turbulencia = TURBULENCIA_POR_ESTADO[this.estado];

    const proyectadas = [];
    for (let i = 0; i < this.particulas.length; i++) {
      const p = this.particulas[i];
      const turbulenciaI = 1 + turbulencia * Math.sin(this.tiempo * p.frecuencia + p.fase);
      const factor = p.capa * turbulenciaI;
      const xr = p.x * cosenoRot - p.z * senoRot;
      const zr = p.x * senoRot + p.z * cosenoRot;
      proyectadas.push([xr * factor, p.y * factor, zr * factor, i]);
    }
    proyectadas.sort((a, b) => a[2] - b[2]);

    for (const [xr, yr, zr, indice] of proyectadas) {
      const profundidad = (zr + 1) / 2;
      const px = cx + xr * radioNucleo;
      const py = cy + yr * radioNucleo * achatado;
      const base = paleta[indice % paleta.length];
      const mezcla = 0.15 + 0.55 * profundidad;
      const [br, bg, bb] = hexARgb(base);
      const r = Math.round(br + (255 - br) * mezcla);
      const g = Math.round(bg + (255 - bg) * mezcla);
      const b = Math.round(bb + (255 - bb) * mezcla);
      const radioPunto = (1.4 + 2.6 * profundidad) * (0.85 + 0.3 * energia);
      const alfa = (90 + 150 * profundidad) / 255;
      const radioBrillo = Math.max(radioPunto * 2.6, 0.01);
      const brillo = ctx.createRadialGradient(px, py, 0, px, py, radioBrillo);
      brillo.addColorStop(0, `rgba(${r},${g},${b},${alfa})`);
      brillo.addColorStop(1, `rgba(${r},${g},${b},0)`);
      ctx.fillStyle = brillo;
      ctx.beginPath();
      ctx.arc(px, py, radioBrillo, 0, 2 * Math.PI);
      ctx.fill();
    }

    const radioCaliente = Math.max(radioNucleo * 0.22, 0.01);
    const caliente = ctx.createRadialGradient(cx, cy, 0, cx, cy, radioCaliente);
    caliente.addColorStop(0, `rgba(255,255,255,${(200 + 40 * energia) / 255})`);
    caliente.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = caliente;
    ctx.beginPath();
    ctx.arc(cx, cy, radioCaliente, 0, 2 * Math.PI);
    ctx.fill();

    this._pintarDestellos(cx, cy, radioNucleo, achatado, cosenoRot, senoRot);
  }

  _pintarDestellos(cx, cy, radioNucleo, achatado, cosenoRot, senoRot) {
    const ctx = this.ctx;
    for (const d of this.destellos) {
      const progreso = (this.tiempo - d.t0) / DURACION_DESTELLO;
      if (progreso < 0 || progreso > 1) continue;
      const xr = d.x * cosenoRot - d.z * senoRot;
      const px = cx + xr * radioNucleo, py = cy + d.y * radioNucleo * achatado;
      const radio = Math.max(3.0 + 20.0 * progreso, 0.01);
      const alfa = 255 * (1 - progreso) ** 2;
      const brillo = ctx.createRadialGradient(px, py, 0, px, py, radio);
      brillo.addColorStop(0, `rgba(255,255,255,${alfa / 255})`);
      brillo.addColorStop(0.5, `rgba(255,255,255,${(alfa * 0.4) / 255})`);
      brillo.addColorStop(1, "rgba(255,255,255,0)");
      ctx.fillStyle = brillo;
      ctx.beginPath();
      ctx.arc(px, py, radio, 0, 2 * Math.PI);
      ctx.fill();
    }
  }

  _pintarRayos(cx, cy, radioNucleo, achatado, cosenoRot, senoRot) {
    if (this.rayos.length === 0) return;
    const ctx = this.ctx;
    ctx.globalCompositeOperation = "lighter";
    const proyectar = (p) => {
      const [x, y, z] = p;
      const xr = x * cosenoRot - z * senoRot;
      return [cx + xr * radioNucleo, cy + y * radioNucleo * achatado];
    };
    for (const rayo of this.rayos) {
      const progreso = (this.tiempo - rayo.t0) / DURACION_RAYO;
      if (progreso < 0 || progreso > 1) continue;
      const a = proyectar(rayo.p1), b = proyectar(rayo.p2);
      const dx = b[0] - a[0], dy = b[1] - a[1];
      const largo = Math.hypot(dx, dy) || 1.0;
      const azar = crearAzar(rayo.seed);
      const segmentos = 5;
      ctx.beginPath();
      ctx.moveTo(a[0], a[1]);
      for (let paso = 1; paso < segmentos; paso++) {
        const t = paso / segmentos;
        const jitter = (azar() * 0.28 - 0.14) * largo;
        ctx.lineTo(a[0] + dx * t - (dy / largo) * jitter, a[1] + dy * t + (dx / largo) * jitter);
      }
      ctx.lineTo(b[0], b[1]);
      const alfa = 230 * (1 - progreso) ** 1.4;
      ctx.strokeStyle = `rgba(220,233,255,${alfa / 255})`;
      ctx.lineWidth = 2.0;
      ctx.stroke();
    }
    ctx.globalCompositeOperation = "source-over";
  }
}

function hexARgb(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function cubicaEnT(p0, p1, p2, p3, t) {
  const mt = 1 - t;
  const a = mt * mt * mt, b = 3 * mt * mt * t, c = 3 * mt * t * t, d = t * t * t;
  return [
    a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
    a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1],
  ];
}

// --- Arranque + puente con Python ----------------------------------------

const lienzo = document.getElementById("lienzo");
const nucleo = new NucleoHacuWeb(lienzo);

function _conectarPuente() {
  if (typeof QWebChannel === "undefined" || typeof qt === "undefined") {
    _iniciarModoDemo();
    return;
  }
  new QWebChannel(qt.webChannelTransport, function (canal) {
    const puente = canal.objects.puenteHacu;
    puente.estadoCambiado.connect(function (estado) { nucleo.setEstado(estado); });
    puente.nivelCambiado.connect(function (nivel) { nucleo.setNivel(nivel); });
  });
}

// Sin QWebEngineView de por medio (por ejemplo, al abrir este archivo en un
// navegador para revisar el diseno) no hay puente real: recorre los cinco
// estados solo, para poder ver el nucleo sin la app de escritorio.
function _iniciarModoDemo() {
  const estados = ["REPOSO", "ESCUCHANDO", "PENSANDO", "HABLANDO", "ERROR"];
  let indice = 0;
  nucleo.setEstado(estados[0]);
  setInterval(() => {
    indice = (indice + 1) % estados.length;
    nucleo.setEstado(estados[indice]);
  }, 4000);
  setInterval(() => {
    nucleo.setNivel(nucleo.estado === "ESCUCHANDO" ? Math.random() * 0.15 : 0);
  }, 120);
}

_conectarPuente();
