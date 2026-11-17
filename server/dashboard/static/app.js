"use strict";

/* ============================================================================
 * AulaSense — lógica del dashboard (una página, cuatro pestañas).
 * Regla AGENTS.md: TODO dato dinámico se pinta con textContent o con esc();
 * aquí no se usa innerHTML con datos (cero innerHTML en esta app).
 * ========================================================================== */

/* ------------------------------ utilidades ------------------------------- */
function esc(valor) {
  // Escape HTML de reserva: úsalo si en el futuro se añade cualquier innerHTML.
  return String(valor ?? "").replace(/[&<>"']/g, (caracter) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[caracter]);
}

function el(tag, clase, texto) {
  const nodo = document.createElement(tag);
  if (clase) nodo.className = clase;
  if (texto !== undefined && texto !== null) nodo.textContent = String(texto);
  return nodo;
}

function vacio(valor) {
  return valor === null || valor === undefined || valor === "";
}

function fmtMs(ms) {
  if (vacio(ms)) return "—";
  const fecha = new Date(Number(ms));
  if (Number.isNaN(fecha.getTime())) return "—";
  return fecha.toLocaleString();
}

function fmtHora(ms) {
  if (vacio(ms)) return "—";
  const fecha = new Date(Number(ms));
  if (Number.isNaN(fecha.getTime())) return "—";
  return fecha.toLocaleTimeString();
}

function fmtNum(valor, decimales = 1) {
  if (valor === null || valor === undefined || valor === "") return "—";
  const numero = Number(valor);
  if (!Number.isFinite(numero)) return "—";
  return numero.toFixed(decimales);
}

function hace(ms) {
  if (vacio(ms)) return "—";
  const delta = Date.now() - Number(ms);
  if (!Number.isFinite(delta)) return "—";
  if (delta < 0) return "en el futuro";
  const segundos = Math.round(delta / 1000);
  if (segundos < 60) return `hace ${segundos} s`;
  const minutos = Math.round(segundos / 60);
  if (minutos < 60) return `hace ${minutos} min`;
  return `hace ${Math.round(minutos / 60)} h`;
}

function obtener(url) {
  return fetch(url, { headers: { Accept: "application/json" } }).then((respuesta) => {
    if (!respuesta.ok) throw new Error(`${url} → HTTP ${respuesta.status}`);
    return respuesta.json();
  });
}

/* -------------------------------- estado --------------------------------- */
const estado = {
  dispositivos: [],
  evaluaciones: [],          // más recientes primero
  eventos: [],
  idsEvaluaciones: new Set(),
  idsEventos: new Set(),
  resumen: null,
  validacion: null,
  validacionActiva: null,
  ultimoEstado: {},
  estadoPrevioPorDevice: {},
  pagina: 0,
  paginaTam: 25,
  ws: null,
  reintentoMs: 1000,
  alertas: false,
};

const MAX_EVALUACIONES = 500;
const MAX_EVENTOS = 120;

/* ------------------------------- WebSocket ------------------------------- */
function pintarBadgeWs(texto, tipo) {
  const badge = document.getElementById("ws-badge");
  badge.textContent = texto;
  badge.className = "ml-auto text-xs " + (
    tipo === "vivo" ? "text-emerald-400" :
    tipo === "stale" ? "text-amber-400" : "text-slate-400"
  );
}

function conectarWS() {
  const protocolo = location.protocol === "https:" ? "wss:" : "ws:";
  let socket;
  try {
    socket = new WebSocket(`${protocolo}//${location.host}/ws`);
  } catch (error) {
    programarReintento();
    return;
  }
  estado.ws = socket;
  socket.onopen = () => {
    estado.reintentoMs = 1000;
    pintarBadgeWs("WS en vivo", "vivo");
    cargarTodo();
  };
  socket.onmessage = (evento) => {
    let mensaje;
    try {
      mensaje = JSON.parse(evento.data);
    } catch (error) {
      return;
    }
    manejarMensaje(mensaje);
  };
  socket.onclose = () => {
    pintarBadgeWs("WS desconectado — reintentando", "stale");
    programarReintento();
  };
  socket.onerror = () => {
    try { socket.close(); } catch (error) { /* noop */ }
  };
}

function programarReintento() {
  const espera = estado.reintentoMs;
  estado.reintentoMs = Math.min(estado.reintentoMs * 2, 30000);
  setTimeout(conectarWS, espera);
}

function manejarMensaje(mensaje) {
  if (!mensaje || !mensaje.type) return;
  if (mensaje.type === "hello") {
    estado.ultimoEstado = mensaje.ultimo_estado || {};
    estado.evaluaciones = [];
    estado.idsEvaluaciones = new Set();
    (mensaje.evaluaciones || []).forEach((fila) => {
      if (!estado.idsEvaluaciones.has(fila.id)) {
        estado.evaluaciones.push(fila);
        estado.idsEvaluaciones.add(fila.id);
      }
    });
    cargarTodo();
    pintarTodo();
    return;
  }
  if (mensaje.type === "evaluacion") {
    agregarEvaluaciones([mensaje.fila]);
    actualizarDeviceConEvaluacion(mensaje.fila);
    return;
  }
  if (mensaje.type === "evaluaciones") {
    agregarEvaluaciones(mensaje.filas || []);
    (mensaje.filas || []).forEach(actualizarDeviceConEvaluacion);
    return;
  }
  if (mensaje.type === "evento") {
    agregarEventos([mensaje.fila]);
    return;
  }
  if (mensaje.type === "eventos") {
    agregarEventos(mensaje.filas || []);
    return;
  }
  if (mensaje.type === "telemetria") {
    actualizarDeviceConTelemetria(mensaje.fila);
  }
}

function agregarEvaluaciones(filas) {
  let nuevas = 0;
  (filas || []).forEach((fila) => {
    if (fila && !estado.idsEvaluaciones.has(fila.id)) {
      estado.evaluaciones.unshift(fila);
      estado.idsEvaluaciones.add(fila.id);
      nuevas += 1;
    }
  });
  if (nuevas === 0) return;
  if (estado.evaluaciones.length > MAX_EVALUACIONES) {
    const recortadas = estado.evaluaciones.splice(MAX_EVALUACIONES);
    recortadas.forEach((fila) => estado.idsEvaluaciones.delete(fila.id));
  }
  programarPintado();
}

function agregarEventos(filas) {
  let nuevas = 0;
  (filas || []).forEach((fila) => {
    if (fila && !estado.idsEventos.has(fila.id)) {
      estado.eventos.unshift(fila);
      estado.idsEventos.add(fila.id);
      nuevas += 1;
    }
  });
  if (nuevas === 0) return;
  if (estado.eventos.length > MAX_EVENTOS) {
    const recortados = estado.eventos.splice(MAX_EVENTOS);
    recortados.forEach((fila) => estado.idsEventos.delete(fila.id));
  }
  programarPintado();
}

function actualizarDeviceConEvaluacion(fila) {
  if (!fila) return;
  const device = estado.dispositivos.find((item) => item.device_id === fila.device_id);
  if (!device) {
    cargarTodo();
    return;
  }
  const previo = estado.estadoPrevioPorDevice[device.device_id];
  device.estado = fila.state;
  device.score = fila.score;
  device.ultima_evaluacion_ms = fila.ts_ms;
  device.ultima_evaluacion_origen = fila.origen;
  device.en_vivo = true;
  if (previo !== "motion" && fila.state === "motion") {
    dispararAlerta(device.device_id);
  }
  estado.estadoPrevioPorDevice[device.device_id] = fila.state;
  programarPintado();
}

function actualizarDeviceConTelemetria(fila) {
  if (!fila) return;
  const device = estado.dispositivos.find((item) => item.device_id === fila.device_id);
  if (device) {
    device.telemetria = fila;
    programarPintado();
  }
}

/* --------------------------- alerta flash/beep --------------------------- */
function dispararAlerta(deviceId) {
  const tarjeta = document.querySelector(`[data-device="${deviceId}"]`);
  if (tarjeta) {
    tarjeta.classList.remove("flash");
    void tarjeta.offsetWidth; // reinicia la animación CSS
    tarjeta.classList.add("flash");
  }
  if (estado.alertas) beep();
}

let contextoAudio = null;
function beep() {
  try {
    if (!contextoAudio) contextoAudio = new (window.AudioContext || window.webkitAudioContext)();
    const oscilador = contextoAudio.createOscillator();
    const ganancia = contextoAudio.createGain();
    oscilador.type = "sine";
    oscilador.frequency.value = 880;
    ganancia.gain.setValueAtTime(0.08, contextoAudio.currentTime);
    ganancia.gain.exponentialRampToValueAtTime(0.0001, contextoAudio.currentTime + 0.15);
    oscilador.connect(ganancia).connect(contextoAudio.destination);
    oscilador.start();
    oscilador.stop(contextoAudio.currentTime + 0.16);
  } catch (error) {
    /* sin audio disponible: no es crítico */
  }
}

/* ------------------------------ pintado ---------------------------------- */
let pintadoPendiente = false;
function programarPintado() {
  if (pintadoPendiente) return;
  pintadoPendiente = true;
  setTimeout(() => {
    pintadoPendiente = false;
    pintarTodo();
  }, 120);
}

function pintarTodo() {
  pintarKpis();
  pintarDispositivos();
  pintarFeed();
  pintarSerieScore();
}

function celdaKpi(titulo, valor) {
  const caja = el("div", "tarjeta p-3");
  caja.appendChild(el("div", "text-xs uppercase tracking-wide text-slate-400", titulo));
  caja.appendChild(el("div", "text-2xl font-semibold mt-1", valor));
  return caja;
}

function pintarKpis() {
  const contenedor = document.getElementById("kpis");
  const contenedorReportes = document.getElementById("kpis-reportes");
  const resumen = estado.resumen;
  contenedor.replaceChildren();
  contenedorReportes.replaceChildren();
  if (!resumen) {
    contenedor.appendChild(el("p", "text-sm text-slate-400", "Cargando KPIs…"));
    return;
  }
  const ultima = resumen.ultima_actividad_ms;
  const tarjetas = [
    ["Evaluaciones", fmtNum(resumen.total_evaluaciones, 0)],
    ["% motion", `${fmtNum(resumen.pct_motion, 1)} %`],
    ["Dispositivos", fmtNum(resumen.dispositivos, 0)],
    ["Última actividad", hace(ultima)],
  ];
  tarjetas.forEach(([titulo, valor]) => {
    contenedor.appendChild(celdaKpi(titulo, valor));
    contenedorReportes.appendChild(celdaKpi(titulo, valor));
  });
}

function celdaMetrica(titulo, valor) {
  const caja = el("div", "celda-metrica");
  caja.appendChild(el("div", "text-[0.65rem] uppercase tracking-wide text-slate-400", titulo));
  caja.appendChild(el("div", "text-sm font-medium text-slate-100", valor));
  return caja;
}

function tarjetaDispositivo(device) {
  const caja = el("div", "tarjeta p-4 space-y-3");
  caja.dataset.device = device.device_id;

  // en_vivo se recalcula en cliente (la última evaluación <120 s manda, SPEC §9).
  const edadMs = Date.now() - Number(device.ultima_evaluacion_ms);
  const enVivo = Number.isFinite(edadMs) && edadMs >= -5000 && edadMs <= 120000;

  const cabecera = el("div", "flex flex-wrap items-center gap-2");
  cabecera.appendChild(el("span", "text-base font-semibold", device.label || device.device_id));
  if (enVivo) {
    cabecera.appendChild(el("span", "badge-vivo", "EN VIVO"));
  } else {
    cabecera.appendChild(el("span", "badge-stale", "STALE"));
  }
  if (device.ultima_evaluacion_origen === "sintetico") {
    cabecera.appendChild(el("span", "badge-simulado", "SIMULADO"));
  }
  const id = el("span", "ml-auto text-[0.68rem] text-slate-500", device.device_id);
  cabecera.appendChild(id);
  caja.appendChild(cabecera);

  const estadoClase = device.estado === "motion" ? "estado-motion"
    : device.estado === "idle" ? "estado-idle" : "estado-nulo";
  const estadoTexto = device.estado ? device.estado.toUpperCase() : "SIN DATOS";
  const filaEstado = el("div", "flex items-end gap-3");
  filaEstado.appendChild(el("span", `text-4xl font-bold ${estadoClase}`, estadoTexto));
  filaEstado.appendChild(el("span", "text-sm text-slate-400", `score ${fmtNum(device.score, 2)}`));
  caja.appendChild(filaEstado);

  // Barra simple de score (sin gauge externo).
  const barraFondo = el("div", "h-2 rounded bg-slate-800 overflow-hidden");
  const barra = el("div", "h-2 rounded bg-emerald-500");
  const score = Number(device.score);
  const ancho = Number.isFinite(score) ? Math.max(0, Math.min(100, score * 100)) : 0;
  barra.style.width = `${ancho}%`;
  barraFondo.appendChild(barra);
  caja.appendChild(barraFondo);

  const tel = device.telemetria || {};
  const metricas = el("div", "grid grid-cols-3 gap-2");
  metricas.appendChild(celdaMetrica("fps (CSI acept.)", fmtNum(tel.csi_accepted_pps, 1)));
  metricas.appendChild(celdaMetrica("pps (generador)", fmtNum(tel.generator_pps, 1)));
  metricas.appendChild(celdaMetrica("detección avg (µs)", fmtNum(tel.detection_avg_us, 0)));
  metricas.appendChild(celdaMetrica("CPU %", fmtNum(tel.runtime_load_percent, 1)));
  metricas.appendChild(celdaMetrica("heap libre (KB)", fmtNum(tel.free_memory_kb, 0)));
  metricas.appendChild(celdaMetrica("RSSI (dBm)", fmtNum(tel.wifi_rssi_dbm, 0)));
  metricas.appendChild(celdaMetrica("die temp (°C)", fmtNum(tel.die_temp_c, 1)));
  caja.appendChild(metricas);

  const detalle = el(
    "div",
    "text-[0.7rem] text-slate-400",
    `motion/hora (24 h): ${fmtNum(device.motion_por_hora, 2)} · últimos inicios: ${fmtNum(device.motion_inicios_24h, 0)}`
      + ` · última eval: ${hace(device.ultima_evaluacion_ms)}`
      + (device.ultimo_evento ? ` · último evento: ${device.ultimo_evento.tipo}` : ""),
  );
  caja.appendChild(detalle);
  return caja;
}

function pintarDispositivos() {
  const contenedor = document.getElementById("devices");
  contenedor.replaceChildren();
  if (!estado.dispositivos.length) {
    contenedor.appendChild(el("p", "text-sm text-slate-400",
      "Sin dispositivos todavía. Publica un evento sintético o espera la ingesta."));
    return;
  }
  estado.dispositivos.forEach((device) => contenedor.appendChild(tarjetaDispositivo(device)));
}

function pintarFeed() {
  const contenedor = document.getElementById("feed-eventos");
  contenedor.replaceChildren();
  if (!estado.eventos.length) {
    contenedor.appendChild(el("p", "text-sm text-slate-400", "Sin eventos…"));
    return;
  }
  estado.eventos.slice(0, 80).forEach((evento) => {
    const item = el("div", "feed-item flex flex-wrap items-center gap-2");
    item.appendChild(el("span", "font-medium text-slate-200", evento.tipo));
    item.appendChild(el("span", "text-slate-400", fmtHora(evento.ts_ms)));
    item.appendChild(el("span", "text-slate-500", evento.device_id));
    if (evento.origen === "sintetico") {
      item.appendChild(el("span", "badge-simulado", "SIMULADO"));
    }
    if (evento.duracion_ms !== null && evento.duracion_ms !== undefined) {
      item.appendChild(el("span", "text-slate-500", `${fmtNum(evento.duracion_ms / 1000, 1)} s en motion`));
    }
    contenedor.appendChild(item);
  });
}

/* ------------------------- gráfica serie de score ------------------------ */
let graficaScore = null;
function asegurarGraficaScore() {
  if (graficaScore || typeof Chart === "undefined") return;
  const lienzo = document.getElementById("chart-score");
  if (!lienzo) return;
  graficaScore = new Chart(lienzo, {
    type: "line",
    data: {
      labels: [],
      datasets: [{
        label: "score",
        data: [],
        borderColor: "#34d399",
        backgroundColor: "rgba(52, 211, 153, .18)",
        fill: true,
        tension: 0.25,
        pointRadius: 0,
        borderWidth: 2,
      }],
    },
    options: {
      animation: false,
      responsive: true,
      scales: {
        y: { min: 0, max: 1, ticks: { color: "#94a3b8" }, grid: { color: "#1e293b" } },
        x: { ticks: { color: "#94a3b8", maxTicksLimit: 8 }, grid: { display: false } },
      },
      plugins: { legend: { display: false } },
    },
  });
}

function pintarSerieScore() {
  asegurarGraficaScore();
  if (!graficaScore) return;
  const filas = estado.evaluaciones.slice(0, 120).reverse();
  graficaScore.data.labels = filas.map((fila) => fmtHora(fila.ts_ms));
  graficaScore.data.datasets[0].data = filas.map((fila) => Number(fila.score));
  graficaScore.update("none");
  const info = document.getElementById("chart-score-info");
  if (info) info.textContent = `últimas ${filas.length} evaluaciones`;
}

/* --------------------------- pestañas y carga ---------------------------- */
function activarPestana(nombre) {
  document.querySelectorAll("#pestanas button").forEach((boton) => {
    const activa = boton.dataset.tab === nombre;
    boton.className = `rounded-lg px-3 py-1.5 text-sm ${
      activa ? "pestana-activa" : "bg-slate-800 hover:bg-slate-700"}`;
  });
  ["monitoreo", "reportes", "validacion", "galeria"].forEach((tab) => {
    const seccion = document.getElementById(`tab-${tab}`);
    if (seccion) seccion.hidden = tab !== nombre;
  });
  if (nombre === "reportes") cargarReportes();
  if (nombre === "validacion") cargarValidacion();
  if (nombre === "galeria") cargarGaleria();
}

function cargarTodo() {
  Promise.all([
    obtener("/api/dispositivos"),
    obtener("/api/resumen"),
    obtener("/api/eventos?limite=100"),
  ]).then(([dispositivos, resumen, eventos]) => {
    estado.dispositivos = dispositivos.dispositivos || [];
    estado.dispositivos.forEach((device) => {
      estado.estadoPrevioPorDevice[device.device_id] = device.estado;
    });
    estado.resumen = resumen;
    agregarEventos(eventos.filas || []);
    pintarTodo();
  }).catch((error) => console.warn("No se pudo cargar el snapshot:", error.message));
}

/* ------------------------------- reportes -------------------------------- */
let graficaEventos = null;
let graficaHoras = null;

function pintarTablaEvaluaciones() {
  const cuerpo = document.querySelector("#tabla-evaluaciones tbody");
  cuerpo.replaceChildren();
  const inicio = estado.pagina * estado.paginaTam;
  const filas = estado.evaluaciones.slice(inicio, inicio + estado.paginaTam);
  filas.forEach((fila) => {
    const tr = document.createElement("tr");
    const valores = [
      fila.id, fila.device_id, fmtMs(fila.ts_ms), fila.state,
      fmtNum(fila.score, 3), fila.hora_sinc ? "sí" : "no",
    ];
    valores.forEach((valor) => {
      const td = document.createElement("td");
      td.textContent = String(valor);
      tr.appendChild(td);
    });
    const tdOrigen = document.createElement("td");
    if (fila.origen === "sintetico") {
      tdOrigen.appendChild(el("span", "badge-simulado", "SIMULADO"));
    } else {
      tdOrigen.textContent = "real";
    }
    tr.appendChild(tdOrigen);
    const tdRecibido = document.createElement("td");
    tdRecibido.textContent = fila.recibido_en || "—";
    tr.appendChild(tdRecibido);
    cuerpo.appendChild(tr);
  });
  const totalPaginas = Math.max(1, Math.ceil(estado.evaluaciones.length / estado.paginaTam));
  document.getElementById("pagina-info").textContent =
    `página ${Math.min(estado.pagina + 1, totalPaginas)} de ${totalPaginas}`;
}

function pintarGraficaEventos() {
  if (typeof Chart === "undefined") return;
  const lienzo = document.getElementById("chart-eventos");
  if (!lienzo) return;
  const porTipo = (estado.resumen && estado.resumen.eventos_por_tipo) || {};
  const etiquetas = Object.keys(porTipo);
  const valores = etiquetas.map((clave) => porTipo[clave]);
  if (graficaEventos) {
    graficaEventos.data.labels = etiquetas;
    graficaEventos.data.datasets[0].data = valores;
    graficaEventos.update("none");
    return;
  }
  graficaEventos = new Chart(lienzo, {
    type: "bar",
    data: {
      labels: etiquetas,
      datasets: [{ label: "eventos", data: valores, backgroundColor: "#3b82f6" }],
    },
    options: {
      animation: false,
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        y: { beginAtZero: true, ticks: { color: "#94a3b8" }, grid: { color: "#1e293b" } },
        x: { ticks: { color: "#94a3b8" }, grid: { display: false } },
      },
    },
  });
}

function pintarGraficaHoras() {
  if (typeof Chart === "undefined") return;
  const lienzo = document.getElementById("chart-horas");
  if (!lienzo) return;
  const conteo = new Array(24).fill(0);
  estado.eventos
    .filter((evento) => evento.tipo === "MOVIMIENTO_INICIO")
    .forEach((evento) => {
      const hora = new Date(Number(evento.ts_ms)).getHours();
      if (Number.isFinite(hora)) conteo[hora] += 1;
    });
  const etiquetas = conteo.map((_, hora) => `${String(hora).padStart(2, "0")}h`);
  if (graficaHoras) {
    graficaHoras.data.datasets[0].data = conteo;
    graficaHoras.update("none");
    return;
  }
  graficaHoras = new Chart(lienzo, {
    type: "bar",
    data: {
      labels: etiquetas,
      datasets: [{ label: "inicios motion", data: conteo, backgroundColor: "#34d399" }],
    },
    options: {
      animation: false,
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        y: { beginAtZero: true, ticks: { color: "#94a3b8" }, grid: { color: "#1e293b" } },
        x: { ticks: { color: "#94a3b8", maxTicksLimit: 12 }, grid: { display: false } },
      },
    },
  });
}

function cargarReportes() {
  Promise.all([
    obtener("/api/evaluaciones?limite=500"),
    obtener("/api/resumen"),
    obtener("/api/eventos?limite=500"),
  ]).then(([evaluaciones, resumen, eventos]) => {
    estado.evaluaciones = evaluaciones.filas || [];
    estado.idsEvaluaciones = new Set(estado.evaluaciones.map((fila) => fila.id));
    estado.resumen = resumen;
    estado.eventos = eventos.filas || [];
    estado.idsEventos = new Set(estado.eventos.map((fila) => fila.id));
    estado.pagina = 0;
    pintarKpis();
    pintarTablaEvaluaciones();
    pintarGraficaEventos();
    pintarGraficaHoras();
    pintarFeed();
  }).catch((error) => console.warn("Reportes:", error.message));
}

/* ------------------------------ validación ------------------------------- */
function pintarValidacion(datos) {
  estado.validacionActiva = datos.sesion_activa || null;
  const nota = document.getElementById("validacion-nota");
  nota.textContent = datos.nota || "";
  const cuerpoMatriz = document.querySelector("#tabla-matriz tbody");
  cuerpoMatriz.replaceChildren();
  const matriz = datos.matriz || {};
  const filas = [
    ["verdad motion", matriz.verdaderos_positivos, matriz.falsos_negativos],
    ["verdad idle", matriz.falsos_positivos, matriz.verdaderos_negativos],
  ];
  filas.forEach(([titulo, positivo, negativo]) => {
    const tr = document.createElement("tr");
    const th = document.createElement("th");
    th.textContent = titulo;
    tr.appendChild(th);
    [positivo, negativo].forEach((valor) => {
      const td = document.createElement("td");
      td.textContent = fmtNum(valor, 0);
      tr.appendChild(td);
    });
    cuerpoMatriz.appendChild(tr);
  });

  const kpis = document.getElementById("validacion-kpis");
  kpis.replaceChildren();
  kpis.appendChild(celdaKpi("Recall", matriz.recall === null || matriz.recall === undefined
    ? "—" : `${fmtNum(matriz.recall * 100, 1)} %`));
  kpis.appendChild(celdaKpi("FPR", matriz.fpr === null || matriz.fpr === undefined
    ? "—" : `${fmtNum(matriz.fpr * 100, 1)} %`));
  kpis.appendChild(celdaKpi("Sesiones", fmtNum(matriz.n_sesiones, 0)));
  kpis.appendChild(celdaKpi("Muestras (evals)", fmtNum(
    (datos.sesiones || []).reduce((suma, fila) => suma + (fila.evaluaciones || 0), 0), 0)));

  const cuerpoSesiones = document.querySelector("#tabla-sesiones tbody");
  cuerpoSesiones.replaceChildren();
  (datos.sesiones || []).forEach((sesion) => {
    const tr = document.createElement("tr");
    [sesion.sesion, fmtMs(sesion.inicio_ms), fmtMs(sesion.fin_ms), sesion.verdad_terreno,
      sesion.predicho || "—", fmtNum(sesion.evaluaciones, 0), sesion.celda || "—",
      sesion.notas || "—"].forEach((valor) => {
      const td = document.createElement("td");
      td.textContent = String(valor);
      tr.appendChild(td);
    });
    cuerpoSesiones.appendChild(tr);
  });
  if (!(datos.sesiones || []).length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 8;
    td.className = "text-slate-400";
    td.textContent = "Sin sesiones de validación registradas.";
    tr.appendChild(td);
    cuerpoSesiones.appendChild(tr);
  }

  // F3: sesión activa, ventanas marcables y registro del session logger.
  pintarSesionActiva(datos);
  pintarVentanas(datos);
  pintarRegistroSesiones(datos);
}

/* ------------------- validación F3: sesión + ventanas -------------------- */
function pintarSesionActiva(datos) {
  const nodo = document.getElementById("validacion-sesion-activa");
  nodo.replaceChildren();
  const activa = datos.sesion_activa;
  if (activa) {
    nodo.appendChild(el("span", "badge-vivo", "ACTIVA"));
    nodo.appendChild(document.createTextNode(
      ` ${activa.sesion} · inicio ${fmtMs(activa.inicio_ms)}`,
    ));
    if (activa.nota) nodo.appendChild(document.createTextNode(` · ${activa.nota}`));
  } else {
    nodo.appendChild(el("span", "badge-stale", "SIN SESIÓN ACTIVA"));
    nodo.appendChild(document.createTextNode(
      " — las marcas se guardan como «manual» (inicia una sesión con tools\\session_logger.py).",
    ));
  }

  const enlace = document.getElementById("validacion-export");
  if (enlace) {
    const sesion = activa ? activa.sesion : null;
    enlace.href = sesion
      ? `/api/validacion/export?sesion=${encodeURIComponent(sesion)}`
      : "/api/validacion/export";
    enlace.textContent = sesion ? `CSV validación (${sesion})` : "CSV validación (todas)";
  }
}

function guardarMarcaValidacion(ventana, hubo, nota) {
  const sesion = (estado.validacionActiva && estado.validacionActiva.sesion)
    || ventana.sesion || "manual";
  fetch("/api/validacion", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({
      sesion,
      inicio_ms: Number(ventana.inicio_ms),
      fin_ms: Number(ventana.fin_ms),
      hubo_movimiento_real: hubo,
      nota: (nota || "").trim() || null,
    }),
  }).then((respuesta) => {
    if (!respuesta.ok) throw new Error(`HTTP ${respuesta.status}`);
    return respuesta.json();
  }).then(() => cargarValidacion())
    .catch((error) => console.warn("Validación POST:", error.message));
}

function pintarVentanas(datos) {
  const cuerpo = document.querySelector("#tabla-ventanas tbody");
  cuerpo.replaceChildren();
  const ventanas = (datos.ventanas || []).slice().reverse(); // más reciente arriba
  const info = document.getElementById("validacion-ventanas-info");
  if (info) {
    info.textContent = `${ventanas.length} ventana(s) con datos reales · bucket `
      + `${fmtNum((datos.ventana_ms || 0) / 1000, 0)} s`;
  }
  if (!ventanas.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 6;
    td.className = "text-slate-400";
    td.textContent = "Sin evaluaciones reales en los últimos 10 min.";
    tr.appendChild(td);
    cuerpo.appendChild(tr);
    return;
  }
  ventanas.forEach((ventana) => {
    const tr = document.createElement("tr");

    const tdVentana = document.createElement("td");
    tdVentana.textContent = `${fmtHora(ventana.inicio_ms)} – ${fmtHora(ventana.fin_ms)}`;
    tr.appendChild(tdVentana);

    const tdEval = document.createElement("td");
    tdEval.textContent = `${fmtNum(ventana.n_evaluaciones, 0)} `
      + `(${fmtNum(ventana.n_motion, 0)} motion / ${fmtNum(ventana.n_idle, 0)} idle)`;
    tr.appendChild(tdEval);

    const tdPredicho = document.createElement("td");
    tdPredicho.textContent = ventana.predicho || "—";
    tr.appendChild(tdPredicho);

    const tdVerdad = document.createElement("td");
    const selector = document.createElement("select");
    selector.className = "bg-slate-900 border border-slate-700 rounded px-1 py-0.5 text-slate-100";
    [["sin-dato", "sin-dato"], ["si", "sí, hubo"], ["no", "no hubo"]].forEach(([valor, texto]) => {
      const opcion = el("option", null, texto);
      opcion.value = valor;
      selector.appendChild(opcion);
    });
    selector.value = ventana.hubo_movimiento_real || "sin-dato";
    tdVerdad.appendChild(selector);
    tr.appendChild(tdVerdad);

    const tdNota = document.createElement("td");
    const entradaNota = document.createElement("input");
    entradaNota.type = "text";
    entradaNota.maxLength = 200;
    entradaNota.placeholder = "nota (opcional)";
    entradaNota.value = ventana.nota || "";
    entradaNota.className = "bg-slate-900 border border-slate-700 rounded px-1 py-0.5 "
      + "text-slate-100 w-44";
    tdNota.appendChild(entradaNota);
    tr.appendChild(tdNota);

    const tdAccion = document.createElement("td");
    const boton = el("button", "rounded bg-blue-700 hover:bg-blue-600 px-2 py-0.5 text-xs", "guardar");
    boton.addEventListener("click", () => {
      guardarMarcaValidacion(ventana, selector.value, entradaNota.value);
    });
    tdAccion.appendChild(boton);
    tr.appendChild(tdAccion);

    cuerpo.appendChild(tr);
  });
}

function pintarRegistroSesiones(datos) {
  const cuerpo = document.querySelector("#tabla-registro-sesiones tbody");
  cuerpo.replaceChildren();
  const filas = datos.registro_sesiones || [];
  if (!filas.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 6;
    td.className = "text-slate-400";
    td.textContent = "Sin sesiones registradas (usa tools\\session_logger.py start --id S1).";
    tr.appendChild(td);
    cuerpo.appendChild(tr);
    return;
  }
  filas.forEach((fila) => {
    const tr = document.createElement("tr");
    const tdSesion = document.createElement("td");
    tdSesion.textContent = fila.sesion;
    tr.appendChild(tdSesion);

    const tdEstado = document.createElement("td");
    tdEstado.appendChild(el(
      "span",
      fila.estado === "activa" ? "badge-vivo" : "badge-stale",
      fila.estado === "activa" ? "ACTIVA" : "cerrada",
    ));
    tr.appendChild(tdEstado);

    [fmtMs(fila.inicio_ms), fmtMs(fila.fin_ms),
      (fila.fin_ms === null || fila.fin_ms === undefined)
        ? "—"
        : `${fmtNum((Number(fila.fin_ms) - Number(fila.inicio_ms)) / 1000, 1)} s`,
      fila.nota || "—"].forEach((valor) => {
      const td = document.createElement("td");
      td.textContent = String(valor);
      tr.appendChild(td);
    });
    cuerpo.appendChild(tr);
  });
}

function cargarValidacion() {
  obtener("/api/validacion")
    .then(pintarValidacion)
    .catch((error) => console.warn("Validación:", error.message));
}

/* -------------------------------- galería -------------------------------- */
// SPEC §9.1: el grid pide la foto QVGA por id de BD; el servidor nunca expone la
// ruta del archivo y el dashboard nunca acepta uploads (el upload es el receptor
// LAN :8001 de la ingesta). Metadata SIEMPRE por textContent (cero innerHTML).
const RE_DEVICE = /^[0-9a-f]{16}$/;
// F25-d3 (SPEC §9.3): filtros de la búsqueda. ``galeriaCanal`` es excluyente
// (mqtt | http | null) y ``galeriaConIa`` pide solo imágenes ya descritas.
let galeriaCanal = null;
let galeriaConIa = false;
let galeriaDebounce = null;

function msDeFecha(valor, finDelDia) {
  if (!valor) return null;
  const sufijo = finDelDia ? "T23:59:59.999" : "T00:00:00";
  const ms = new Date(`${valor}${sufijo}`).getTime();
  return Number.isFinite(ms) ? ms : null;
}

function parametrosGaleria() {
  const device = (document.getElementById("galeria-device").value || "").trim().toLowerCase();
  const params = new URLSearchParams();
  if (device) params.set("device_id", device);
  const desde = msDeFecha(document.getElementById("galeria-desde").value, false);
  const hasta = msDeFecha(document.getElementById("galeria-hasta").value, true);
  if (desde !== null) params.set("desde", String(desde));
  if (hasta !== null) params.set("hasta", String(hasta));
  const texto = (document.getElementById("galeria-q").value || "").trim();
  if (texto) params.set("q", texto);
  if (galeriaCanal) params.set("canal", galeriaCanal);
  if (galeriaConIa) params.set("con_ia", "1");
  // F26-a2 (SPEC §16.5/§16.6): pide también las columnas decision_* (opt-in;
  // si la BD aún no tiene F26, el servidor devuelve la metadata de siempre).
  params.set("decision", "1");
  params.set("limite", "200");
  return { device, params };
}

// Etiquetas del worker de visión: coma-separadas ("a, b, c"); se pintan como chips.
function chipsEtiquetas(etiquetas) {
  const caja = el("div", "flex flex-wrap gap-1 pt-0.5");
  String(etiquetas || "").split(/[,;]/).forEach((parte) => {
    const etiqueta = parte.trim();
    if (etiqueta) caja.appendChild(el("span", "chip-etiqueta", etiqueta));
  });
  return caja.childElementCount ? caja : null;
}

// Descripción IA bajo la foto. CERO innerHTML: nodos + textContent (AGENTS XSS).
function bloqueDescripcion(fila) {
  const caja = el("div", "pt-1 space-y-1");
  const estadoVision = String(fila.vision_estado || "pendiente");
  const descripcion = (fila.descripcion === null || fila.descripcion === undefined)
    ? "" : String(fila.descripcion).trim();
  if (estadoVision === "pendiente" || estadoVision === "procesando") {
    // Placeholder honesto: la IA todavía no ha descrito esta imagen.
    caja.appendChild(el("p", "text-[0.7rem] text-slate-500 italic", "descripción pendiente"));
  } else if (descripcion) {
    caja.appendChild(el("p", "text-[0.7rem] text-slate-200", descripcion));
  } else if (estadoVision === "error") {
    caja.appendChild(el("p", "text-[0.7rem] text-slate-500 italic",
      "descripción no disponible (error de visión)"));
  } else if (estadoVision === "omitido") {
    caja.appendChild(el("p", "text-[0.7rem] text-slate-500 italic", "descripción omitida"));
  } else if (estadoVision === "hecho") {
    caja.appendChild(el("p", "text-[0.7rem] text-slate-500 italic", "descripción vacía"));
  }
  const chips = chipsEtiquetas(fila.etiquetas);
  if (chips) caja.appendChild(chips);
  return caja;
}

// F26-a2 (SPEC §16.5): decisión de aula de la imagen, solo si la BD F26 la trae
// (``decision=1``). Sin el campo, la tarjeta queda EXACTAMENTE como antes.
function bloqueDecisionAula(fila) {
  const etiqueta = (fila.decision_etiqueta === null || fila.decision_etiqueta === undefined)
    ? "" : String(fila.decision_etiqueta).trim();
  if (!etiqueta) return null;
  const caja = el("div", "pt-1 space-y-1");
  const confianza = (fila.decision_confianza === null || fila.decision_confianza === undefined)
    ? "" : ` (conf ${fmtNum(fila.decision_confianza, 2)})`;
  const actividad = fila.decision_actividad
    ? ` · actividad ${String(fila.decision_actividad)}` : "";
  caja.appendChild(el("p", "text-[0.7rem] text-slate-200",
    `Decisión de aula: ${etiqueta}${confianza}${actividad}`));
  // badge derivado (SPEC §16.5): decision_cubre < 0.7, NO un estado nuevo.
  const cubre = (fila.decision_cubre === null || fila.decision_cubre === undefined)
    ? null : Number(fila.decision_cubre);
  if (cubre !== null && Number.isFinite(cubre) && cubre < 0.7) {
    const linea = el("div", "flex flex-wrap gap-1 pt-0.5");
    const badge = el("span", "rounded px-1.5 py-0.5 text-[0.62rem] font-semibold",
      "REVISIÓN_HUMANA");
    badge.style.background = "#78350f"; // ámbar, mismo tono que el panel /salud
    badge.style.color = "#fcd34d";
    linea.appendChild(badge);
    caja.appendChild(linea);
  }
  return caja;
}

function tarjetaImagen(fila) {
  const caja = el("div", "tarjeta p-2 space-y-2");
  const id = Number(fila.id);
  const purgada = Number(fila.imagen_purgada) === 1;
  if (purgada) {
    // La purga borró el archivo: placeholder, sin pedir la foto (sería un 404).
    caja.appendChild(el(
      "div",
      "w-full aspect-[4/3] rounded border border-slate-800 bg-slate-900 text-slate-500 "
        + "flex items-center justify-center text-xs",
      "imagen purgada",
    ));
  } else if (Number.isFinite(id)) {
    const img = document.createElement("img");
    img.src = `/api/imagenes/${id}/foto`;
    img.loading = "lazy";
    img.alt = `Evidencia de imagen ${id}`;
    img.className = "w-full rounded border border-slate-800 bg-slate-900 aspect-[4/3] object-contain";
    img.addEventListener("error", () => {
      // Archivo ausente en disco: se sustituye por texto (sin innerHTML).
      img.replaceWith(el(
        "div",
        "w-full aspect-[4/3] rounded border border-slate-800 bg-slate-900 text-slate-500 "
          + "flex items-center justify-center text-xs",
        "no disponible",
      ));
    });
    caja.appendChild(img);
  }

  const meta = el("div", "text-[0.7rem] text-slate-300 space-y-0.5");
  meta.appendChild(el("div", "font-medium", fila.device_id));
  meta.appendChild(el("div", "text-slate-400", fmtMs(fila.ts_ms)));
  const dimensiones = `${fmtNum(fila.ancho, 0)}×${fmtNum(fila.alto, 0)} px · `
    + `${fmtNum((Number(fila.tamano_bytes) || 0) / 1024, 1)} KB`;
  meta.appendChild(el("div", "text-slate-500", dimensiones));

  const badges = el("div", "flex flex-wrap gap-1 pt-1");
  if (fila.ia_persona !== null && fila.ia_persona !== undefined) {
    const etiqueta = Number(fila.ia_persona) === 1 ? "persona" : "sin persona";
    const score = (fila.ia_score === null || fila.ia_score === undefined)
      ? "" : ` ${fmtNum(fila.ia_score, 2)}`;
    badges.appendChild(el("span", "badge-ia", `IA: ${etiqueta}${score}`));
  }
  if (purgada) badges.appendChild(el("span", "badge-purgada", "purgada"));
  // Origen SIEMPRE visible (regla 10: lo sintético nunca se presenta como real).
  badges.appendChild(el(
    "span",
    fila.origen === "sintetico" ? "badge-simulado" : "badge-real",
    fila.origen === "sintetico" ? "SIMULADO" : "real",
  ));
  // Canal de transporte (SPEC §9.3) y estado de la IA de visión.
  const canal = String(fila.canal || "http");
  badges.appendChild(el("span", "badge-canal", `canal: ${canal}`));
  const estadoVision = String(fila.vision_estado || "pendiente");
  badges.appendChild(el("span", "badge-vision", `visión: ${estadoVision}`));
  meta.appendChild(badges);
  caja.appendChild(meta);
  caja.appendChild(bloqueDescripcion(fila));
  const decision = bloqueDecisionAula(fila);
  if (decision) caja.appendChild(decision);
  return caja;
}

function pintarGaleria(datos) {
  const grid = document.getElementById("galeria-grid");
  const info = document.getElementById("galeria-info");
  grid.replaceChildren();
  const filas = (datos && datos.filas) || [];
  info.textContent = `${filas.length} imagen(es) · límite ${fmtNum(datos && datos.limite, 0)}`;
  if (!filas.length) {
    grid.appendChild(el("p", "text-sm text-slate-400", "Sin imágenes para el filtro actual…"));
    return;
  }
  filas.forEach((fila) => grid.appendChild(tarjetaImagen(fila)));
}

function cargarGaleria() {
  const { device, params } = parametrosGaleria();
  const info = document.getElementById("galeria-info");
  if (device && !RE_DEVICE.test(device)) {
    info.textContent = "device_id debe ser 16 hex minúscula";
    return;
  }
  info.textContent = "cargando…";
  obtener(`/api/imagenes?${params.toString()}`)
    .then(pintarGaleria)
    .catch((error) => {
      // Sesión caducada: el middleware responde 401 → al login (SPEC §9.2).
      if (String(error.message).includes("HTTP 401")) {
        window.location.href = "/login";
        return;
      }
      info.textContent = "no se pudo cargar la galería";
      console.warn("Galería:", error.message);
    });
}

// Chips de filtro (canal excluyente + con_ia) y debounce de la búsqueda.
function pintarChipsGaleria() {
  const mqtt = document.getElementById("galeria-canal-mqtt");
  const http = document.getElementById("galeria-canal-http");
  const conIa = document.getElementById("galeria-con-ia");
  if (mqtt) mqtt.setAttribute("aria-pressed", galeriaCanal === "mqtt" ? "true" : "false");
  if (http) http.setAttribute("aria-pressed", galeriaCanal === "http" ? "true" : "false");
  if (conIa) conIa.setAttribute("aria-pressed", galeriaConIa ? "true" : "false");
}

function alternarCanalGaleria(canal) {
  galeriaCanal = galeriaCanal === canal ? null : canal;
  pintarChipsGaleria();
  cargarGaleria();
}

function alternarConIaGaleria() {
  galeriaConIa = !galeriaConIa;
  pintarChipsGaleria();
  cargarGaleria();
}

function programarBusquedaGaleria() {
  // Debounce ~300 ms (SPEC §9.3): no se recarga en cada tecla.
  if (galeriaDebounce !== null) clearTimeout(galeriaDebounce);
  galeriaDebounce = window.setTimeout(() => {
    galeriaDebounce = null;
    cargarGaleria();
  }, 300);
}

/* -------------------------------- sesión --------------------------------- */
// SPEC §9.2: POST /logout borra la sesión en la BD y expira la cookie; después
// se navega al login (cualquier GET protegido responderá 401).
function cerrarSesion() {
  const boton = document.getElementById("btn-salir");
  if (boton) {
    boton.disabled = true;
    boton.textContent = "saliendo…";
  }
  fetch("/logout", { method: "POST", headers: { Accept: "application/json" } })
    .then(() => {
      window.location.href = "/login";
    })
    .catch((error) => {
      console.warn("Logout:", error.message);
      window.location.href = "/login";
    });
}

/* ------------------------------- arranque -------------------------------- */
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("#pestanas button").forEach((boton) => {
    boton.addEventListener("click", () => activarPestana(boton.dataset.tab));
  });
  document.getElementById("pagina-prev").addEventListener("click", () => {
    if (estado.pagina > 0) {
      estado.pagina -= 1;
      pintarTablaEvaluaciones();
    }
  });
  document.getElementById("pagina-next").addEventListener("click", () => {
    const totalPaginas = Math.ceil(estado.evaluaciones.length / estado.paginaTam);
    if (estado.pagina + 1 < totalPaginas) {
      estado.pagina += 1;
      pintarTablaEvaluaciones();
    }
  });
  document.getElementById("toggle-alertas").addEventListener("change", (evento) => {
    estado.alertas = Boolean(evento.target.checked);
    if (estado.alertas) {
      try {
        if (!contextoAudio) contextoAudio = new (window.AudioContext || window.webkitAudioContext)();
        contextoAudio.resume();
      } catch (error) { /* sin audio */ }
      beep();
    }
  });

  document.getElementById("galeria-recargar").addEventListener("click", cargarGaleria);
  document.getElementById("galeria-device").addEventListener("keydown", (evento) => {
    if (evento.key === "Enter") cargarGaleria();
  });
  // F25-d3: búsqueda con debounce + chips de canal y "con descripción IA".
  document.getElementById("galeria-q").addEventListener("input", programarBusquedaGaleria);
  document.getElementById("galeria-q").addEventListener("keydown", (evento) => {
    if (evento.key === "Enter") cargarGaleria();
  });
  document.getElementById("galeria-canal-mqtt").addEventListener("click", () => {
    alternarCanalGaleria("mqtt");
  });
  document.getElementById("galeria-canal-http").addEventListener("click", () => {
    alternarCanalGaleria("http");
  });
  document.getElementById("galeria-con-ia").addEventListener("click", alternarConIaGaleria);
  document.getElementById("btn-salir").addEventListener("click", cerrarSesion);

  cargarTodo();
  conectarWS();
  // Refresco periódico del snapshot: mantiene expirados los badges en_vivo/stale
  // y sirve de respaldo si el WS se cae (el WS sigue siendo la vía principal).
  setInterval(cargarTodo, 15000);
});