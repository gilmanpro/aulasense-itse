"use strict";

/* ============================================================================
 * AulaSense — panel /salud (F26-a2, SPEC §16.7).
 *
 * Lee GET /api/salud (detrás del login por cookie) y pinta el semáforo cada 30 s.
 * REGLA XSS (AGENTS.md): este archivo no inyecta HTML dinámico en ningún punto.
 * Todo dato del servidor se pinta con createElement + textContent.
 * REGLA HONESTA: 'sin-dato' se pinta SIEMPRE gris con el literal «sin dato»
 * (nunca rojo): la fuente F26 puede no estar desplegada y eso no es una caída.
 * ========================================================================== */

const COLORES = {
  verde: "#2ecc71",
  warn: "#f1c40f",
  critico: "#e74c3c",
  info: "#3498db",
  "sin-dato": "#7f8c8d",
};

const ETIQUETAS_ESTADO = {
  verde: "verde",
  warn: "aviso",
  critico: "crítico",
  info: "info",
  "sin-dato": "sin dato",
};

const INTERVALO_REFRESCO_MS = 30000;

const panel = {
  temporizador: null,
  cargando: false,
};

/* ------------------------------ utilidades ------------------------------- */
function byId(id) {
  return document.getElementById(id);
}

// Nodo con textContent (nunca HTML inyectado): único constructor de la UI.
function el(tag, clase, texto) {
  const nodo = document.createElement(tag);
  if (clase) nodo.className = clase;
  if (texto !== undefined && texto !== null) nodo.textContent = String(texto);
  return nodo;
}

function vacio(valor) {
  return valor === null || valor === undefined || valor === "";
}

function numero(valor) {
  if (vacio(valor) || typeof valor === "boolean") return null;
  const n = Number(valor);
  return Number.isFinite(n) ? n : null;
}

function fmtNum(valor, decimales = 1) {
  const n = numero(valor);
  return n === null ? "—" : n.toFixed(decimales);
}

function fmtFechaHora(ms) {
  const n = numero(ms);
  if (n === null) return "—";
  const fecha = new Date(n);
  return Number.isNaN(fecha.getTime()) ? "—" : fecha.toLocaleString();
}

function hace(ms) {
  const n = numero(ms);
  if (n === null) return "—";
  const delta = Date.now() - n;
  if (!Number.isFinite(delta)) return "—";
  if (delta < 0) return "en el futuro";
  const segundos = Math.round(delta / 1000);
  if (segundos < 60) return `hace ${segundos} s`;
  const minutos = Math.round(segundos / 60);
  if (minutos < 60) return `hace ${minutos} min`;
  const horas = Math.round(minutos / 60);
  if (horas < 48) return `hace ${horas} h`;
  return `hace ${Math.round(horas / 24)} días`;
}

// Duración legible en s/min/h (los checks de frescura llegan en ms).
function fmtDuracion(ms) {
  const n = numero(ms);
  if (n === null || n < 0) return "—";
  const segundos = Math.round(n / 1000);
  if (segundos < 90) return `${segundos} s`;
  const minutos = Math.round(segundos / 60);
  if (minutos < 90) return `${minutos} min`;
  const horas = Math.floor(minutos / 60);
  const resto = minutos % 60;
  return resto ? `${horas} h ${resto} min` : `${horas} h`;
}

function fmtBytes(valor) {
  const n = numero(valor);
  if (n === null) return "—";
  const gb = n / (1024 ** 3);
  if (gb >= 1) return `${gb.toFixed(2)} GB`;
  return `${(n / (1024 ** 2)).toFixed(1)} MB`;
}

function fmtSiNo(valor) {
  if (valor === null || valor === undefined) return "—";
  if (typeof valor === "boolean") return valor ? "sí" : "no";
  if (typeof valor === "number") {
    if (valor === 1) return "sí";
    if (valor === 0) return "no";
    return String(valor);
  }
  const texto = String(valor).trim().toLowerCase();
  if (["true", "1", "yes", "si", "sí", "on"].includes(texto)) return "sí";
  if (["false", "0", "no", "off"].includes(texto)) return "no";
  return String(valor);
}

// Normaliza el estado del servidor a una clave conocida (clase CSS segura).
function estadoSeguro(valor) {
  const estado = String(valor || "sin-dato");
  return Object.prototype.hasOwnProperty.call(COLORES, estado) ? estado : "sin-dato";
}

function bloqueSinDato(mensaje) {
  return el("div", "caja-sindato", mensaje || "sin dato");
}

function metrica(titulo, valor) {
  const caja = el("div", "metrica");
  caja.appendChild(el("div", "k", titulo));
  caja.appendChild(el("div", "v", valor));
  return caja;
}

// Barra simple (div de ancho %). El color es una constante local, no un dato.
function barra(pct, color) {
  const fondo = el("div", "barra");
  const relleno = el("div");
  const n = numero(pct);
  relleno.style.width = `${Math.max(0, Math.min(100, n === null ? 0 : n))}%`;
  relleno.style.backgroundColor = color || COLORES.info;
  fondo.appendChild(relleno);
  return fondo;
}

function colorBarra(pct, warnPct, criticoPct) {
  const n = numero(pct);
  if (n === null) return COLORES.info;
  if (n >= criticoPct) return COLORES.critico;
  if (n >= warnPct) return COLORES.warn;
  return COLORES.info;
}

function textoCompacto(valor) {
  if (vacio(valor)) return "—";
  if (Array.isArray(valor)) return `${valor.length} elemento(s)`;
  if (typeof valor === "object") {
    const partes = Object.keys(valor).map((clave) => `${clave}: ${String(valor[clave])}`);
    return partes.length ? partes.join(" · ") : "—";
  }
  return String(valor);
}

/* --------------------------- semáforo de checks --------------------------- */
function resumenCola(conteos) {
  if (!conteos || typeof conteos !== "object") return "—";
  const pares = [
    ["pendiente", "pend"],
    ["procesando", "proc"],
    ["hecho", "hechos"],
    ["error", "error"],
    ["omitido", "omit"],
  ];
  const partes = pares
    .filter(([clave]) => conteos[clave] !== undefined && conteos[clave] !== null)
    .map(([clave, corto]) => `${corto} ${fmtNum(conteos[clave], 0)}`);
  return partes.length ? partes.join(" · ") : "—";
}

// Valor legible por check: ms→s/min, KB, dBm, %, conteos...
function valorLegible(check) {
  const valor = check.valor;
  if (vacio(valor) && valor !== 0) return "—";
  switch (String(check.id || "")) {
    case "ingesta":
    case "telemetria": {
      const ms = numero(valor);
      return ms === null ? "—" : `${fmtDuracion(ms)} de antigüedad`;
    }
    case "mqtt":
      return fmtSiNo(valor);
    case "heap_interno": {
      const kb = numero(valor);
      return kb === null ? "—" : `${kb.toFixed(0)} KB libres`;
    }
    case "rssi": {
      const dbm = numero(valor);
      return dbm === null ? "—" : `${Math.round(dbm)} dBm`;
    }
    case "cola_vision":
    case "cola_decision":
      return resumenCola(valor);
    case "consumo_vision":
    case "consumo_clef": {
      if (!valor || typeof valor !== "object") return "—";
      return `día ${fmtNum(valor.dia, 0)} · hora ${fmtNum(valor.hora, 0)}`;
    }
    case "disco": {
      const pct = numero(valor);
      return pct === null ? "—" : `${pct.toFixed(1)} % usado`;
    }
    case "tuneles":
      return Array.isArray(valor) ? `${valor.length} túnel(es)` : "—";
    case "ota":
      return String(valor);
    default:
      return textoCompacto(valor);
  }
}

function filaCheck(check, otaEnCurso) {
  const id = String(check.id || "");
  let estado = estadoSeguro(check.estado);
  // Anti-falsa-alarma OTA (SPEC §16.7): con OTA en curso la pausa de telemetría
  // es esperada → se pinta info. Un 'sin-dato' se queda gris (sin dato es sin dato).
  if (otaEnCurso && id === "telemetria" && estado !== "sin-dato") estado = "info";

  const fila = el("div", "fila");
  const punto = el("span", "dot");
  punto.style.backgroundColor = COLORES[estado];
  punto.setAttribute("aria-hidden", "true");
  fila.appendChild(punto);
  fila.appendChild(el("span", "nom", check.etiqueta || id || "check"));

  const textoEstado = el("span", "est", ETIQUETAS_ESTADO[estado] || estado);
  textoEstado.style.color = COLORES[estado];
  fila.appendChild(textoEstado);
  fila.appendChild(el("span", "val", valorLegible(check)));

  if (check.detalle) fila.appendChild(el("span", "det", check.detalle));
  return fila;
}

function pintarChecks(datos) {
  const contenedor = byId("lista-checks");
  const checks = Array.isArray(datos.checks) ? datos.checks : [];
  contenedor.replaceChildren();
  if (!checks.length) {
    contenedor.appendChild(bloqueSinDato("sin datos de checks"));
    return;
  }
  checks.forEach((check) => {
    contenedor.appendChild(filaCheck(check, Boolean(datos.ota_en_curso)));
  });
}

/* ---------------------------- tarjeta cámara ------------------------------ */
function pintarCamara(camara) {
  const contenedor = byId("camara-contenido");
  contenedor.replaceChildren();
  const datos = camara || {};
  const fuente = String(datos.fuente || "sin-dato");

  const cabecera = el("div", "chips");
  cabecera.appendChild(el("span", "chip-fuente", `fuente: ${fuente}`));
  if (datos.device_id) cabecera.appendChild(el("span", "chip-neutro", datos.device_id));
  contenedor.appendChild(cabecera);

  const estado = datos.estado;
  if (!estado || typeof estado !== "object") {
    contenedor.appendChild(bloqueSinDato(
      "sin dato — no hay retain imagen_estado; si hay eventos (config_eventos) "
        + "se listan abajo",
    ));
  } else {
    const grid = el("div", "grid-metricas");
    grid.appendChild(metrica("cámara OK", fmtSiNo(estado.camara_ok)));
    grid.appendChild(metrica("captura habilitada", fmtSiNo(estado.captura_habilitada)));
    grid.appendChild(metrica("gate_off", fmtSiNo(estado.gate_off)));
    grid.appendChild(metrica("descartes", fmtNum(estado.descartes, 0)));
    grid.appendChild(metrica("imágenes OK", fmtNum(estado.imagenes_ok, 0)));
    grid.appendChild(metrica("heap interno (KB)", fmtNum(estado.internal_free_kb, 0)));
    grid.appendChild(metrica("largest block (KB)", fmtNum(estado.internal_largest_block_kb, 0)));
    if (!vacio(estado.build)) grid.appendChild(metrica("build", String(estado.build)));
    if (!vacio(estado.ts_ms)) grid.appendChild(metrica("estado (hora)", fmtFechaHora(estado.ts_ms)));
    contenedor.appendChild(grid);
    contenedor.appendChild(el(
      "p",
      "nota",
      "El gate_off lo decide el nodo por heap interno: con el gate activo la captura "
        + "se deshabilita y el CSI sigue detectando.",
    ));
  }

  const eventos = Array.isArray(datos.eventos_recentes) ? datos.eventos_recentes : [];
  contenedor.appendChild(el("h3", "subtitulo", "Eventos recientes (config_eventos)"));
  if (!eventos.length) {
    contenedor.appendChild(el("p", "vacio", "sin eventos de cámara registrados"));
    return;
  }
  eventos.forEach((evento) => {
    const item = el("div", "item");
    const linea = el("div", "item-linea");
    linea.appendChild(el("span", "fuerte", String(evento.tipo || "evento")));
    linea.appendChild(el("span", "mono muted", fmtFechaHora(evento.ts_ms)));
    item.appendChild(linea);

    const sub = el("div", "item-sub");
    if (evento.device_id) sub.appendChild(el("span", "mono", String(evento.device_id)));
    if (evento.detalle) sub.appendChild(el("span", null, String(evento.detalle)));
    if (sub.childElementCount) item.appendChild(sub);
    contenedor.appendChild(item);
  });
}

/* ------------------------- tarjeta decisión de aula ----------------------- */
function pintarDecision(datosDecision) {
  const contenedor = byId("decision-contenido");
  contenedor.replaceChildren();
  const datos = datosDecision || {};
  if (!datos.disponible) {
    contenedor.appendChild(bloqueSinDato("sin datos (worker Clef sin ejecutar)"));
    return;
  }

  const resumen = el("div", "chips");
  resumen.appendChild(el("span", "chip-fuente",
    `${fmtNum(datos.total_imagenes, 0)} imagen(es) en cola de decisión`));
  if (numero(datos.revision_humana) !== null) {
    resumen.appendChild(el("span", "chip-neutro",
      `revisión humana: ${fmtNum(datos.revision_humana, 0)}`));
  }
  contenedor.appendChild(resumen);

  const porEstado = datos.por_estado || {};
  const chipsEstado = el("div", "chips");
  ["pendiente", "procesando", "hecho", "error", "omitido"].forEach((clave) => {
    if (porEstado[clave] !== undefined && porEstado[clave] !== null) {
      chipsEstado.appendChild(el("span", "chip-neutro",
        `${clave}: ${fmtNum(porEstado[clave], 0)}`));
    }
  });
  if (chipsEstado.childElementCount) contenedor.appendChild(chipsEstado);

  contenedor.appendChild(el("h3", "subtitulo", "Conteo por etiqueta"));
  const porEtiqueta = datos.por_etiqueta || {};
  const etiquetas = Object.keys(porEtiqueta).sort(
    (a, b) => (numero(porEtiqueta[b]) || 0) - (numero(porEtiqueta[a]) || 0),
  );
  if (!etiquetas.length) {
    contenedor.appendChild(el("p", "vacio", "sin etiquetas decididas"));
  } else {
    const chips = el("div", "chips");
    etiquetas.forEach((etiqueta) => {
      chips.appendChild(el("span", "chip-neutro",
        `${etiqueta}: ${fmtNum(porEtiqueta[etiqueta], 0)}`));
    });
    contenedor.appendChild(chips);
  }

  contenedor.appendChild(el("h3", "subtitulo", "Decisiones recientes"));
  const recientes = Array.isArray(datos.recientes) ? datos.recientes : [];
  if (!recientes.length) {
    contenedor.appendChild(el("p", "vacio", "sin decisiones registradas"));
    return;
  }
  recientes.forEach((fila) => {
    const item = el("div", "item");
    const linea = el("div", "item-linea");
    linea.appendChild(el("span", "fuerte", fila.decision_etiqueta || "sin etiqueta"));
    if (!vacio(fila.decision_confianza)) {
      linea.appendChild(el("span", "mono muted",
        `conf ${fmtNum(fila.decision_confianza, 2)}`));
    }
    if (fila.decision_actividad) {
      linea.appendChild(el("span", "muted", `actividad ${fila.decision_actividad}`));
    }
    // badge derivado (SPEC §16.5): decision_cubre < 0.7, NO un estado nuevo.
    if (fila.revision) linea.appendChild(el("span", "badge-revision", "REVISIÓN_HUMANA"));
    item.appendChild(linea);

    const sub = el("div", "item-sub");
    sub.appendChild(el("span", "mono", `imagen ${fmtNum(fila.imagen_id, 0)}`));
    if (!vacio(fila.decision_cubre)) {
      sub.appendChild(el("span", "muted", `cubre ${fmtNum(fila.decision_cubre, 2)}`));
    }
    if (fila.decision_modelo) sub.appendChild(el("span", "muted", fila.decision_modelo));
    if (!vacio(fila.ts_ms)) sub.appendChild(el("span", "muted", fmtFechaHora(fila.ts_ms)));
    item.appendChild(sub);
    contenedor.appendChild(item);
  });
}

/* ----------------------------- tarjeta placa ------------------------------ */
function pintarPlaca(placa) {
  const contenedor = byId("placa-contenido");
  contenedor.replaceChildren();
  const datos = placa || {};

  const cabecera = el("div", "chips");
  cabecera.appendChild(el("span", "chip-fuente", `fuente: ${String(datos.fuente || "sin-dato")}`));
  if (datos.device_id) cabecera.appendChild(el("span", "chip-neutro", datos.device_id));
  if (!vacio(datos.ts_ms)) cabecera.appendChild(el("span", "chip-neutro", fmtFechaHora(datos.ts_ms)));
  contenedor.appendChild(cabecera);

  // DOS filas distintas (errata histórica a evitar): el heap interno del chip no
  // es la memoria libre de diagnósticos (esta última incluye PSRAM).
  const grid = el("div", "grid-2");

  const heap = el("div", "bloque");
  heap.appendChild(el("h3", "subtitulo", "Heap interno (RAM del chip)"));
  heap.appendChild(el("p", "valor-grande", `${fmtNum(datos.internal_free_kb, 1)} KB libres`));
  heap.appendChild(el("p", "muted",
    `largest block: ${fmtNum(datos.internal_largest_block_kb, 1)} KB`));
  heap.appendChild(el("p", "nota",
    "Gate de captura F26 (vivo): se auto-deshabilita bajo 95 KB libres o bloque <24 KB "
      + "(re-evalúa en cada onset, sin latch); la OTA exige 96/48 KB post-quiesce."));
  grid.appendChild(heap);

  const memoria = el("div", "bloque");
  memoria.appendChild(el("h3", "subtitulo", "Memoria libre (diagnósticos, incluye PSRAM)"));
  memoria.appendChild(el("p", "valor-grande", `${fmtNum(datos.free_memory_kb, 1)} KB`));
  if (datos.free_memory_nota) {
    memoria.appendChild(el("p", "nota", String(datos.free_memory_nota)));
  }
  grid.appendChild(memoria);
  contenedor.appendChild(grid);

  const dbm = numero(datos.wifi_rssi_dbm);
  contenedor.appendChild(el(
    "p",
    "muted",
    `Wi-Fi RSSI: ${dbm === null ? "—" : `${Math.round(dbm)} dBm`}`,
  ));
}

/* -------------------------- disco / consumo / túneles --------------------- */
function pintarDisco(disco) {
  const contenedor = byId("disco-contenido");
  contenedor.replaceChildren();
  const datos = disco || {};
  const pct = numero(datos.usado_pct);
  if (pct === null) {
    contenedor.appendChild(bloqueSinDato(
      datos.error ? `sin dato — ${String(datos.error)}` : "sin dato",
    ));
    if (datos.ruta) contenedor.appendChild(el("p", "nota", `ruta: ${String(datos.ruta)}`));
    return;
  }
  contenedor.appendChild(el("p", "valor-grande", `${fmtNum(pct, 1)} % usado`));
  contenedor.appendChild(barra(pct, colorBarra(pct, 85, 95)));
  contenedor.appendChild(el("p", "muted",
    `${fmtBytes(datos.libre_bytes)} libres de ${fmtBytes(datos.total_bytes)}`));
  if (datos.ruta) contenedor.appendChild(el("p", "nota", `ruta: ${String(datos.ruta)}`));
}

function bloqueConsumo(titulo, usado, topeDia, topeHora, nota) {
  const caja = el("div", "bloque");
  caja.appendChild(el("h3", "subtitulo", titulo));
  const dia = numero(usado && usado.dia);
  const hora = numero(usado && usado.hora);
  if (dia === null || hora === null) {
    caja.appendChild(bloqueSinDato("sin dato (tabla de auditoría ausente)"));
    return caja;
  }
  const tDia = numero(topeDia);
  const tHora = numero(topeHora);
  caja.appendChild(el("p", "valor-grande",
    `día ${dia} / ${tDia === null ? "—" : tDia}`));
  caja.appendChild(el("p", "muted",
    `hora ${hora} / ${tHora === null ? "—" : tHora}`));
  const pct = tDia !== null && tDia > 0 ? (100 * dia) / tDia : 0;
  caja.appendChild(barra(pct, colorBarra(pct, 80, 100)));
  caja.appendChild(el("p", "nota", `% del tope diario: ${fmtNum(pct, 1)} %`));
  if (nota) caja.appendChild(el("p", "nota", nota));
  return caja;
}

function pintarConsumo(consumo) {
  const contenedor = byId("consumo-contenido");
  contenedor.replaceChildren();
  const datos = consumo || {};
  const topes = datos.topes || {};
  contenedor.appendChild(bloqueConsumo(
    "Visión (Qwen) — llamadas",
    datos.vision,
    topes.vision_dia,
    topes.vision_hora,
    "«día» = ventana móvil de 24 h; topes del worker (rate limit obligatorio).",
  ));
  contenedor.appendChild(bloqueConsumo(
    "Clef (decisiones) — llamadas",
    datos.clef,
    topes.clef_dia,
    topes.clef_hora,
    "Cuenta solo las llamadas que pudieron consumir Credits (las locales no).",
  ));
}

function pintarTuneles(tuneles) {
  const contenedor = byId("tuneles-contenido");
  contenedor.replaceChildren();
  const datos = tuneles || {};
  const estado = estadoSeguro(datos.estado);

  const cabecera = el("div", "chips");
  cabecera.appendChild(el("span", `chip chip-${estado}`,
    ETIQUETAS_ESTADO[estado] || estado));
  if (!vacio(datos.edad_ms)) {
    cabecera.appendChild(el("span", "chip-neutro", `edad ${fmtDuracion(datos.edad_ms)}`));
  }
  contenedor.appendChild(cabecera);

  const lista = Array.isArray(datos.tuneles) ? datos.tuneles : [];
  if (estado === "sin-dato" || !lista.length) {
    contenedor.appendChild(bloqueSinDato(
      `sin dato${datos.motivo ? ` — ${String(datos.motivo)}` : ""}`,
    ));
    return;
  }
  lista.forEach((tunel) => {
    const item = el("div", "item");
    const linea = el("div", "item-linea");
    linea.appendChild(el("span", "fuerte", String(tunel.id || "túnel")));
    linea.appendChild(el("span", "muted", String(tunel.state || "sin state")));
    if (tunel.remote) linea.appendChild(el("span", "mono muted", String(tunel.remote)));
    item.appendChild(linea);
    contenedor.appendChild(item);
  });
  if (datos.motivo) contenedor.appendChild(el("p", "nota", String(datos.motivo)));
}

/* ------------------------------- pie y estado ---------------------------- */
function pintarPie(datos) {
  byId("pie-nota").textContent = vacio(datos.nota) ? "" : String(datos.nota);
  const ms = datos.ultima_actividad_ms;
  byId("pie-actividad").textContent = vacio(ms)
    ? "última actividad: —"
    : `última actividad: ${fmtFechaHora(ms)} (${hace(ms)})`;
}

function pintarCabecera(datos) {
  const estado = estadoSeguro(datos.estado_global);
  const chip = byId("chip-global");
  chip.textContent = ETIQUETAS_ESTADO[estado] || estado;
  chip.className = `chip chip-${estado}`;
  byId("banner-ota").hidden = !datos.ota_en_curso;
}

function pintarTodoSalud(datos) {
  pintarCabecera(datos);
  pintarChecks(datos);
  pintarCamara(datos.camara);
  pintarDecision(datos.decision_aula);
  pintarPlaca(datos.placa);
  pintarDisco(datos.disco);
  pintarConsumo(datos.consumo);
  pintarTuneles(datos.tuneles);
  pintarPie(datos);
}

// Fetch fallido: se avisa en rojo y se LIMPIAN los paneles (nunca se dejan datos
// viejos pintados como frescos). «última lectura OK» conserva la hora real.
function pintarError(motivo) {
  const banner = byId("banner-error");
  banner.textContent = "sin datos (¿sesión caducada?)";
  banner.hidden = false;
  byId("banner-ota").hidden = true;

  const chip = byId("chip-global");
  chip.textContent = ETIQUETAS_ESTADO["sin-dato"];
  chip.className = "chip chip-sin-dato";

  ["lista-checks", "camara-contenido", "decision-contenido", "placa-contenido",
    "disco-contenido", "consumo-contenido", "tuneles-contenido"].forEach((id) => {
    const contenedor = byId(id);
    if (contenedor) contenedor.replaceChildren(bloqueSinDato("sin dato — falló la lectura"));
  });

  byId("pie-nota").textContent = "";
  byId("pie-actividad").textContent = "última actividad: —";
  if (motivo) console.warn("Salud:", motivo);
}

function marcarLecturaOk() {
  const ahora = new Date();
  byId("ultima-lectura").textContent = `última lectura OK: ${ahora.toLocaleString()}`;
}

/* -------------------------------- fetch 30 s ----------------------------- */
function programarRefresco() {
  if (panel.temporizador !== null) clearTimeout(panel.temporizador);
  panel.temporizador = window.setTimeout(() => {
    panel.temporizador = null;
    cargarSalud();
  }, INTERVALO_REFRESCO_MS);
}

async function cargarSalud() {
  if (panel.cargando) return;
  panel.cargando = true;
  if (panel.temporizador !== null) {
    clearTimeout(panel.temporizador);
    panel.temporizador = null;
  }

  const boton = byId("btn-actualizar");
  const textoBoton = boton.textContent;
  boton.disabled = true;
  boton.textContent = "Actualizando…";

  try {
    const respuesta = await fetch("/api/salud", {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (respuesta.status === 401) {
      // Sesión caducada (SPEC §9.2): al login.
      window.location.href = "/login";
      return;
    }
    if (!respuesta.ok) throw new Error(`HTTP ${respuesta.status}`);
    const datos = await respuesta.json();
    pintarTodoSalud(datos);
    byId("banner-error").hidden = true;
    marcarLecturaOk();
  } catch (error) {
    pintarError(error && error.message ? error.message : "error de red");
  } finally {
    panel.cargando = false;
    boton.disabled = false;
    boton.textContent = textoBoton;
    programarRefresco();
  }
}

/* ------------------------------- arranque -------------------------------- */
document.addEventListener("DOMContentLoaded", () => {
  byId("btn-actualizar").addEventListener("click", () => cargarSalud());
  cargarSalud();
});