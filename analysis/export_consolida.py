"""export_consolida.py — dataset consolidado de AulaSense (SPEC §10.1, fase F24-b1/f2).

Genera ``analysis/dataset/consolidado.csv``: **una fila por EVENTO** de
``eventos`` con joins por VENTANA temporal al resto de fuentes. SOLO filas con
``origen='real'`` (AGENTS.md regla 10: lo sintetico jamas se mezcla).

Joins (LEFT-semantics: un evento sin datos sale con null/0, nunca se pierde):
- telemetria mas cercana a +-30 s;
- imagen por FK ``imagenes.evento_id`` (tiene_imagen/ia_persona/ia_score/imagen_purgada);
- Open-Meteo mas cercano a +-1 h;
- ``config_eventos`` en +-60 s: ``n_provision`` (count) y ``hora_provision``
  (1 si hubo CFG_GUARDADA/AP_FALLBACK);
- ``validacion_sesiones`` cuyo intervalo contiene ``ts_ms`` (verdad_terreno);
- ``sesion_id`` de ``eventos`` SOLO si la columna existe (hoy no existe -> null).

Derivados de ``ts_ms`` (hora local del PC):
- ``hora``: entero 0-23 (feature del modelo);
- ``dia_semana``: entero 0-6 con lunes=0;
- ``franja_horaria``: etiqueta categorial "00-05" | "06-11" | "12-17" | "18-23".

**Regla de validez temporal (F26-h3, 2026-11-17):** el CLI aplica por defecto la venta
``[2026-01-01T00:00:00Z, ahora+1h]`` sobre ``eventos.ts_ms`` y sobre el join de
``imagenes`` por FK. Filas documentadas fuera de venta (NO se editan en la BD — regla 10
AGENTS):
- imagen ``id=56``: ts_ms = 3.589.525.831.468 (epoca ~año 2083). Fire-test del receptor
  :8001 con epoca absurda por autocorreccion de reloj; queda excluida del join por la venta.
- familia con desfase +2963 s del 2026-11-17 (autocorreccion de reloj WSL post-reinicio):
  dentro de ``ahora+1h`` al momento del export, se CONSERVA (se documenta, no se borra);
  el analisis la lee por orden de ``id`` (monotono del nodo), no por ``ts_ms``.
Conteo de filtrados por la regla queda en ``ExportadorConsolidado.filtrados_por_venta``
y se imprime en el resumen JSON del CLI. El parametro de clase ``filtro_ts`` queda OFF
por defecto (API historica intacta para tests/otros llamantes).

Uso:
  server\\.venv\\Scripts\\python.exe analysis/export_consolida.py
  server\\.venv\\Scripts\\python.exe analysis/export_consolida.py --db ruta.db --out ruta.csv
  # --sin-filtro-ts desactiva la venta F26-h3 (export "crudo" con origen=real unicamente)

Reproducible: mismo SQLite -> mismo CSV (orden ``ts_ms, id``, sin marcas de
generacion; unico campo dependiente del reloj: el techo ``ahora+1h`` de la venta, que
solo afecta a filas futuras — ninguna en el dataset actual). Nunca escribe en el .db.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]  # aulasense/
DB_DEFECTO = ROOT / "server" / "datos" / "aulasense.db"
OUT_DEFECTO = ROOT / "analysis" / "dataset" / "consolidado.csv"

# Columnas EXACTAS de SPEC §10.1 (el orden importa: lo valida smoke_consolida).
COLUMNAS: tuple[str, ...] = (
    "evento_id",
    "device_id",
    "ts_ms",
    "tipo",
    "score",
    "duracion_ms",
    "hora",
    "dia_semana",
    "franja_horaria",
    "detection_avg_us",
    "runtime_load_percent",
    "free_memory_kb",
    "csi_accepted_pps",
    "csi_occupancy",
    "wifi_rssi_dbm",
    "die_temp_c",
    "tiene_imagen",
    "ia_persona",
    "ia_score",
    "imagen_purgada",
    "temperatura_c",
    "humedad_pct",
    "precipitacion_mm",
    "viento_kmh",
    "n_provision",
    "hora_provision",
    "verdad_terreno",
    "sesion_id",
    "origen",
)

# Ventanas temporales de los joins (SPEC §10.1).
VENTANA_TELEMETRIA_MS = 30_000
VENTANA_CLIMA_MS = 3_600_000
VENTANA_CONFIG_MS = 60_000
TIPOS_PROVISION = ("CFG_GUARDADA", "AP_FALLBACK")

# Venta de validez temporal del CLI (F26-h3; ver docstring). Techo = ahora + 1 h.
TS_VENTA_MIN_MS = 1_767_225_600_000          # 2026-01-01T00:00:00Z
TS_VENTA_MARGEN_FUTURO_MS = 3_600_000        # +1 h de tolerancia de reloj

_CAMPOS_TELEMETRIA = (
    "detection_avg_us",
    "runtime_load_percent",
    "free_memory_kb",
    "csi_accepted_pps",
    "csi_occupancy",
    "wifi_rssi_dbm",
    "die_temp_c",
)
_CAMPOS_CLIMA = ("temperatura_c", "humedad_pct", "precipitacion_mm", "viento_kmh")


def _franja_horaria(hora: int) -> str:
    """Bloques de 6 h: 00-05 / 06-11 / 12-17 / 18-23."""
    inicio = (hora // 6) * 6
    return f"{inicio:02d}-{inicio + 5:02d}"


class ExportadorConsolidado:
    """Lee SQLite (solo lectura) y escribe el consolidado por evento.

    ``filtro_ts``: venta opcional ``(min_ms, max_ms)`` (F26-h3). OFF por defecto
    (API historica); el CLI la enciende con [2026-01-01, ahora+1h]. Cuando esta
    activa, las filas fuera de venta NO se exportan (y NO se editan en la BD) y
    quedan listadas en ``self.filtrados_por_venta`` con su conteo."""

    def __init__(self, db_path: str | Path,
                 filtro_ts: tuple[int, int] | None = None) -> None:
        self.db_path = Path(db_path)
        self.filtro_ts = filtro_ts
        self.filtrados_por_venta: dict[str, list[list[int]]] = {}
        self._conn = self._conectar_lectura(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._tiene_eventos = self._tabla_existe("eventos")
        self._tiene_telemetria = self._tabla_existe("telemetria")
        self._tiene_imagenes = self._tabla_existe("imagenes")
        self._tiene_clima = self._tabla_existe("fuentes_externas")
        self._tiene_config = self._tabla_existe("config_eventos")
        self._tiene_validacion = self._tabla_existe("validacion_sesiones")
        self._eventos_tiene_sesion = self._tiene_eventos and self._columna_existe(
            "eventos", "sesion_id"
        )

    def cerrar(self) -> None:
        self._conn.close()

    # -- introspeccion tolerante a DB parciales -----------------------------

    @staticmethod
    def _conectar_lectura(db_path: Path) -> sqlite3.Connection:
        """Abre la DB en modo lectura (``mode=ro``) con fallback ``query_only``."""
        uri = "file:" + quote(db_path.as_posix(), safe="/:") + "?mode=ro"
        try:
            return sqlite3.connect(uri, uri=True, timeout=10.0)
        except sqlite3.OperationalError:
            con = sqlite3.connect(str(db_path), timeout=10.0)
            con.execute("PRAGMA query_only=ON")
            return con

    def _tabla_existe(self, nombre: str) -> bool:
        fila = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (nombre,)
        ).fetchone()
        return fila is not None

    def _columna_existe(self, tabla: str, columna: str) -> bool:
        filas = self._conn.execute(f"PRAGMA table_info({tabla})").fetchall()
        return any(fila["name"] == columna for fila in filas)

    # -- joins --------------------------------------------------------------

    def _telemetria_cercana(self, ts_ms: int) -> dict[str, Any]:
        if not self._tiene_telemetria:
            return {}
        fila = self._conn.execute(
            f"SELECT {', '.join(_CAMPOS_TELEMETRIA)} FROM telemetria "
            "WHERE origen = 'real' AND ts_ms BETWEEN ? AND ? "
            "ORDER BY ABS(ts_ms - ?), id LIMIT 1",
            (
                ts_ms - VENTANA_TELEMETRIA_MS,
                ts_ms + VENTANA_TELEMETRIA_MS,
                ts_ms,
            ),
        ).fetchone()
        return dict(fila) if fila is not None else {}

    def _imagen_de_evento(self, evento_id: int) -> dict[str, Any]:
        if not self._tiene_imagenes:
            return {}
        venta = ""
        params: tuple = (evento_id,)
        if self.filtro_ts is not None:
            # Venta F26-h3: una imagen con ts fuera de epoca no es evidencia de la
            # fila aunque el FK la apunte (p. ej. id 56, epoca ~2083, FIRE-TEST).
            venta = " AND ts_ms BETWEEN ? AND ?"
            params = (evento_id, *self.filtro_ts)
        fila = self._conn.execute(
            "SELECT ia_persona, ia_score, imagen_purgada FROM imagenes "
            "WHERE evento_id = ? AND origen = 'real'" + venta + " ORDER BY id LIMIT 1",
            params,
        ).fetchone()
        return dict(fila) if fila is not None else {}

    def _clima_cercano(self, ts_ms: int) -> dict[str, Any]:
        if not self._tiene_clima:
            return {}
        fila = self._conn.execute(
            f"SELECT {', '.join(_CAMPOS_CLIMA)} FROM fuentes_externas "
            "WHERE fuente = 'openmeteo' AND ts_ms BETWEEN ? AND ? "
            "ORDER BY ABS(ts_ms - ?), id LIMIT 1",
            (ts_ms - VENTANA_CLIMA_MS, ts_ms + VENTANA_CLIMA_MS, ts_ms),
        ).fetchone()
        return dict(fila) if fila is not None else {}

    def _provision(self, ts_ms: int) -> tuple[int, int]:
        if not self._tiene_config:
            return 0, 0
        marcadores = ", ".join("?" for _ in TIPOS_PROVISION)
        fila = self._conn.execute(
            "SELECT COUNT(*) AS n, "
            "COALESCE(MAX(CASE WHEN tipo IN (" + marcadores + ") THEN 1 ELSE 0 END), 0) AS flag "
            "FROM config_eventos WHERE origen = 'real' AND ts_ms BETWEEN ? AND ?",
            (*TIPOS_PROVISION, ts_ms - VENTANA_CONFIG_MS, ts_ms + VENTANA_CONFIG_MS),
        ).fetchone()
        return int(fila["n"] or 0), int(fila["flag"] or 0)

    def _validacion(self, ts_ms: int) -> str | None:
        if not self._tiene_validacion:
            return None
        fila = self._conn.execute(
            "SELECT verdad_terreno FROM validacion_sesiones "
            "WHERE inicio_ms <= ? AND fin_ms >= ? ORDER BY id DESC LIMIT 1",
            (ts_ms, ts_ms),
        ).fetchone()
        return None if fila is None else fila["verdad_terreno"]

    # -- fila consolidada ---------------------------------------------------

    def _fila(self, evento: sqlite3.Row) -> dict[str, Any]:
        evento_id = int(evento["id"])
        device_id = evento["device_id"]
        ts_ms = int(evento["ts_ms"])
        momento = datetime.fromtimestamp(ts_ms / 1000)
        telemetria = self._telemetria_cercana(ts_ms)
        imagen = self._imagen_de_evento(evento_id)
        clima = self._clima_cercano(ts_ms)
        n_provision, hora_provision = self._provision(ts_ms)

        fila: dict[str, Any] = {
            "evento_id": evento_id,
            "device_id": device_id,
            "ts_ms": ts_ms,
            "tipo": evento["tipo"],
            "score": evento["score"],
            "duracion_ms": evento["duracion_ms"],
            "hora": momento.hour,
            "dia_semana": momento.weekday(),
            "franja_horaria": _franja_horaria(momento.hour),
            "tiene_imagen": 1 if imagen else 0,
            "ia_persona": imagen.get("ia_persona"),
            "ia_score": imagen.get("ia_score"),
            "imagen_purgada": int(imagen.get("imagen_purgada") or 0),
            "n_provision": n_provision,
            "hora_provision": hora_provision,
            "verdad_terreno": self._validacion(ts_ms),
            "sesion_id": evento["sesion_id"] if self._eventos_tiene_sesion else None,
            "origen": "real",
        }
        for campo in _CAMPOS_TELEMETRIA:
            fila[campo] = telemetria.get(campo)
        for campo in _CAMPOS_CLIMA:
            fila[campo] = clima.get(campo)
        return fila

    def _eventos(self) -> list[sqlite3.Row]:
        if not self._tiene_eventos:
            return []
        sesion = "sesion_id" if self._eventos_tiene_sesion else "NULL AS sesion_id"
        venta = ""
        params: tuple = ()
        if self.filtro_ts is not None:
            venta = " AND ts_ms BETWEEN ? AND ?"
            params = tuple(self.filtro_ts)
        return self._conn.execute(
            "SELECT id, device_id, ts_ms, tipo, score, duracion_ms, "
            + sesion
            + " FROM eventos WHERE origen = 'real'" + venta + " ORDER BY ts_ms, id",
            params,
        ).fetchall()

    def _contabilizar_filtrados(self) -> None:
        """Regla F26-h3 CON conteo: filas `origen=real` fuera de la venta (id, ts_ms),
        por tabla, para documentarlas SIN tocarlas (regla 10)."""
        if self.filtro_ts is not None and self._tiene_eventos:
            lo, hi = self.filtro_ts
            cuenta: dict[str, list[list[int]]] = {}
            for tabla in ("eventos", "imagenes"):
                if tabla == "imagenes" and not self._tiene_imagenes:
                    continue
                cuenta[tabla] = [[int(i), int(t)] for i, t in self._conn.execute(
                    f"SELECT id, ts_ms FROM {tabla} "
                    "WHERE origen = 'real' AND (ts_ms < ? OR ts_ms > ?) ORDER BY id",
                    (lo, hi),
                ).fetchall()]
            self.filtrados_por_venta = cuenta

    def exportar(self, out_path: str | Path) -> int:
        """Escribe el CSV y devuelve el numero de filas (0 si no hay eventos)."""
        destino = Path(out_path)
        destino.parent.mkdir(parents=True, exist_ok=True)
        self._contabilizar_filtrados()
        filas = [self._fila(evento) for evento in self._eventos()]
        with open(destino, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(COLUMNAS), extrasaction="ignore")
            writer.writeheader()
            for fila in filas:
                writer.writerow(fila)
        return len(filas)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Genera analysis/dataset/consolidado.csv (una fila por evento, SOLO real)"
    )
    ap.add_argument(
        "--db",
        default=None,
        help="ruta de la SQLite (default: env AULASENSE_DB o server/datos/aulasense.db)",
    )
    ap.add_argument("--out", default=str(OUT_DEFECTO), help="CSV de salida")
    ap.add_argument(
        "--sin-filtro-ts",
        action="store_true",
        help="desactiva la venta de validez temporal F26-h3 "
        "(por defecto: ts fuera de [2026-01-01T00:00Z, ahora+1h] se DOCUMENTA+FILTRA, "
        "nunca se edita la BD)",
    )
    args = ap.parse_args(argv)

    db_path = Path(args.db or os.getenv("AULASENSE_DB") or DB_DEFECTO)
    if not db_path.exists():
        print(f"[consolida] ERROR: DB inexistente: {db_path}", file=sys.stderr)
        return 1
    filtro = None
    if not args.sin_filtro_ts:
        techo = int(datetime.now().timestamp() * 1000) + TS_VENTA_MARGEN_FUTURO_MS
        filtro = (TS_VENTA_MIN_MS, techo)
    exportador = ExportadorConsolidado(db_path, filtro_ts=filtro)
    try:
        n = exportador.exportar(args.out)
    finally:
        exportador.cerrar()
    resumen = {
        "filas_exportadas": n,
        "venta_ts_ms": filtro,
        "filtrados_por_venta": {
            tabla: {"n": len(pares), "ids": [i for i, _ in pares]}
            for tabla, pares in exportador.filtrados_por_venta.items()
        },
        "salida": str(args.out),
    }
    print(f"[consolida] {n} eventos reales -> {args.out}")
    print(json.dumps(resumen, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())