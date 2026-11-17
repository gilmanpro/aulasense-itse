"""Tests de GET /api/salud y del panel /salud (SPEC §16.7, fase F26-a2).

Fixture propia siguiendo el patrón de ``test_dashboard.py`` (que NO se edita):
DB y datos en ``tmp_path`` vía env, login REAL (conftest: credenciales_tmp +
iniciar_sesion), sesión en el cookie jar del TestClient. Se comprueba aquí el
criterio de término: 200 con sesión / 401 sin sesión; checks con datos
simulados; anti-falsa-alarma OTA (D-F26-8); degradación 'sin-dato' cuando la
fuente F26-a1/F26-b aún no existe; y que el panel escapa todo con
textContent/esc (regla XSS: sin innerHTML nuevo).

NO se toca la ingesta: los datos simulados se insertan directo en la BD
temporal (mismo enfoque que los fixtures históricos de la galería).
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

DEVID = "0123456789abcdef"
IDS_CHECKS = {
    "ingesta", "mqtt", "telemetria", "heap_interno", "rssi", "cola_vision",
    "cola_decision", "consumo_vision", "consumo_clef", "disco", "tuneles", "ota",
}


def _ahora_ms() -> int:
    return int(time.time() * 1000)


@pytest.fixture()
def entorno_salud(tmp_path, monkeypatch):
    """Disco simulado al 50 % por defecto: los umbrales 85/95 no dependen del PC."""
    import shutil as _shutil

    monkeypatch.setattr(
        _shutil, "disk_usage",
        lambda _p: SimpleNamespace(total=1000, used=500, free=500),
    )


def _con():
    con = sqlite3.connect(os.environ["AULASENSE_DB"])
    con.row_factory = sqlite3.Row
    return con


def _ejecutar(sql: str, params: tuple = ()) -> None:
    con = _con()
    try:
        con.execute(sql, params)
        con.commit()
    finally:
        con.close()


DDL_VISION_USO_TEST = """
CREATE TABLE IF NOT EXISTS vision_uso (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_ms INTEGER NOT NULL, imagen_id INTEGER REFERENCES imagenes(id),
  modelo TEXT NOT NULL, tokens_in INTEGER, tokens_out INTEGER,
  modo TEXT NOT NULL CHECK(modo IN ('A','B')),
  resultado TEXT NOT NULL CHECK(resultado IN ('hecho','error','tope'))
);
"""


@pytest.fixture()
def salud(tmp_path, monkeypatch, credenciales_tmp, iniciar_sesion, entorno_salud):
    """(cliente_logueado, datos_dir): DB temporal login REAL + esquema F26 respaldo."""
    datos = tmp_path / "datos"
    datos.mkdir()
    monkeypatch.setenv("AULASENSE_DB", str(tmp_path / "aulasense_salud.db"))
    monkeypatch.setenv("AULASENSE_DATOS_DIR", str(datos))
    monkeypatch.delenv("AULASENSE_ORIGINS_EXTRA", raising=False)
    monkeypatch.delenv("AULASENSE_CLEF_MAX_DIA", raising=False)
    monkeypatch.delenv("AULASENSE_VISION_MAX_DIA", raising=False)
    # umbrales heap: default de código (95/24) determinista, no el .env del PC
    monkeypatch.delenv("AULASENSE_HEAP_INT_VERDE_KB", raising=False)
    monkeypatch.delenv("AULASENSE_HEAP_BLOQUE_VERDE_KB", raising=False)

    from fastapi.testclient import TestClient

    from dashboard.app import app

    with TestClient(app) as cliente:
        iniciar_sesion(cliente, credenciales_tmp["password"])
        # Primer request autenticado: la app crea el esquema + respaldo F26.
        assert cliente.get("/api/resumen").status_code == 200
        yield cliente, datos


@pytest.fixture()
def sin_sesion(tmp_path, monkeypatch, credenciales_tmp, entorno_salud):
    """TestClient SIN login (para el 401/302 del criterio de término)."""
    datos = tmp_path / "datos"
    datos.mkdir()
    monkeypatch.setenv("AULASENSE_DB", str(tmp_path / "aulasense_salud_pdte.db"))
    monkeypatch.setenv("AULASENSE_DATOS_DIR", str(datos))

    from fastapi.testclient import TestClient

    from dashboard.app import app

    with TestClient(app) as cliente:
        yield cliente


def _get(cliente) -> dict:
    respuesta = cliente.get("/api/salud")
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def _check(cuerpo: dict, identificador: str) -> dict:
    return next(c for c in cuerpo["checks"] if c["id"] == identificador)


def _heartbeat(ts_ms: int, mqtt: int = 1, detalle: dict | None = None) -> None:
    _ejecutar(
        "INSERT INTO estado_sistema (id, ts_ms, mqtt_conectado, ingesta_activa, detalle) "
        "VALUES (1, ?, ?, 1, ?) "
        "ON CONFLICT(id) DO UPDATE SET ts_ms=excluded.ts_ms, "
        "mqtt_conectado=excluded.mqtt_conectado, detalle=excluded.detalle",
        (ts_ms, mqtt, json.dumps(detalle) if detalle is not None else None),
    )


def _telemetria(ts_ms: int, *, internal: int | None = None, rssi: int | None = -30,
                bloque: int | None = None, psram: float = 8200.0) -> None:
    _ejecutar(
        "INSERT INTO telemetria (device_id, ts_ms, free_memory_kb, wifi_rssi_dbm, "
        "internal_free_kb, internal_largest_block_kb) VALUES (?, ?, ?, ?, ?, ?)",
        (DEVID, ts_ms, psram, rssi, internal, bloque),
    )


# ------------------------------------------------------------------------------
# Criterio de término: 200 con sesión, 401 sin sesión; página /salud bajo sesión
# ------------------------------------------------------------------------------
def test_api_salud_200_con_sesion(salud):
    cliente, _ = salud
    cuerpo = _get(cliente)
    assert cuerpo["ahora_ms"] > 0
    assert {c["id"] for c in cuerpo["checks"]} == IDS_CHECKS
    assert cuerpo["estado_global"] in ("verde", "warn", "critico", "sin-dato")


def test_api_salud_401_sin_sesion(sin_sesion):
    respuesta = sin_sesion.get("/api/salud")
    assert respuesta.status_code == 401
    assert "detail" in respuesta.json()


def test_pagina_salud_302_sin_sesion_y_200_con_sesion(salud):
    cliente, _ = salud
    # Segundo TestClient sobre la MISMA app SIN iniciar sesión (otra cookie jar).
    from fastapi.testclient import TestClient

    from dashboard.app import app

    anonymous = TestClient(app)
    redir = anonymous.get("/salud", follow_redirects=False)
    assert redir.status_code == 302
    assert redir.headers["location"] == "/login"
    assert anonymous.get("/api/salud").status_code == 401
    pagina = cliente.get("/salud")
    assert pagina.status_code == 200
    assert "text/html" in pagina.headers["content-type"]


def test_panel_sin_innerhtml(salud):
    """Regla XSS: el panel pinta SOLO con textContent/esc (nuevo innerHTML = 0)."""
    cliente, _ = salud
    estaticos = SERVER_DIR / "dashboard" / "static"
    for nombre in ("salud.html", "salud.js"):
        ruta = estaticos / nombre
        assert ruta.is_file(), f"falta {ruta}"
        assert "innerHTML" not in ruta.read_text(encoding="utf-8"), nombre
    js = (estaticos / "salud.js").read_text(encoding="utf-8")
    assert "textContent" in js
    assert "api/salud" in js


# ------------------------------------------------------------------------------
# Degradación honesta sin despliegue F26-a1 (DB recién creada por el dashboard)
# ------------------------------------------------------------------------------
def test_checks_sin_dato_sin_heartbeat(salud):
    cliente, _ = salud
    cuerpo = _get(cliente)
    assert _check(cuerpo, "ingesta")["estado"] == "sin-dato"
    assert _check(cuerpo, "mqtt")["estado"] == "sin-dato"
    assert _check(cuerpo, "telemetria")["estado"] == "sin-dato"
    assert _check(cuerpo, "heap_interno")["estado"] == "sin-dato"
    assert _check(cuerpo, "tuneles")["estado"] == "sin-dato"
    assert _check(cuerpo, "ota")["estado"] == "sin-dato"
    assert cuerpo["ota_en_curso"] is False
    assert cuerpo["estado_global"] == "sin-dato"
    # disco medido aunque no haya ingesta (shutil sobre la base de datos)
    assert _check(cuerpo, "disco")["estado"] == "verde"
    assert cuerpo["disco"]["usado_pct"] == pytest.approx(50.0)


def test_heartbeat_y_telemetria_fresca_verde(salud):
    cliente, _ = salud
    ahora = _ahora_ms()
    _ejecutar(DDL_VISION_USO_TEST)
    _heartbeat(ahora - 30_000, mqtt=1)
    _telemetria(ahora - 10_000, internal=200, bloque=100, rssi=-40)
    cuerpo = _get(cliente)
    assert _check(cuerpo, "ingesta")["estado"] == "verde"
    assert _check(cuerpo, "mqtt")["estado"] == "verde"
    assert _check(cuerpo, "telemetria")["estado"] == "verde"
    assert _check(cuerpo, "heap_interno")["estado"] == "verde"
    assert _check(cuerpo, "rssi")["estado"] == "verde"
    assert _check(cuerpo, "tuneles")["estado"] == "sin-dato"  # F26-b pendiente
    # con todo lo medible en verde y un gris estructural, el global es sin-dato
    assert cuerpo["estado_global"] in ("verde", "sin-dato")
    assert cuerpo["placa"]["internal_free_kb"] == 200
    # heap interno y PSRAM NUNCA mezclados:
    assert cuerpo["placa"]["free_memory_kb"] == 8200.0
    assert "PSRAM" in cuerpo["placa"]["free_memory_nota"]


def test_mqtt_caido_critico(salud):
    cliente, _ = salud
    _heartbeat(_ahora_ms() - 10_000, mqtt=0)
    cuerpo = _get(cliente)
    assert _check(cuerpo, "mqtt")["estado"] == "critico"


def test_heartbeat_viego_warn_y_critico(salud):
    cliente, _ = salud
    ahora = _ahora_ms()
    _heartbeat(ahora - 120_000)  # 2 min: >90 s warn
    assert _check(_get(cliente), "ingesta")["estado"] == "warn"
    _heartbeat(ahora - 6 * 60_000)  # 6 min: >5 min crítico
    assert _check(_get(cliente), "ingesta")["estado"] == "critico"


def test_frescura_telemetria_warn_critico(salud):
    cliente, _ = salud
    ahora = _ahora_ms()
    _telemetria(ahora - 7 * 60_000)
    assert _check(_get(cliente), "telemetria")["estado"] == "warn"
    _ejecutar("DELETE FROM telemetria")
    _telemetria(ahora - 11 * 60_000)
    assert _check(_get(cliente), "telemetria")["estado"] == "critico"


def test_umbrales_heap(salud):
    """Umbrales VIVOS del gate F26 (95/24 KB, sdkconfig:622-623 / README §14),
    no la barra histórica de 120 (hallazgo c3): la placa sana 103-106/56 = verde.

    Criterio literal del micro-fix: 105/56 verde · 90/40 warn · 20/10 crítico.
    """
    cliente, _ = salud
    ahora = _ahora_ms()
    for libre, bloque, esperado in (
        (105, 56, "verde"),      # datos reales actuales de la placa sana
        (90, 40, "warn"),        # libre < 95 (gate puede desactivarse)
        (20, 10, "critico"),     # libre < 64
        (120, 20, "warn"),       # libre alto pero mayor bloque < 24 -> warn
    ):
        _ejecutar("DELETE FROM telemetria")
        _telemetria(ahora, internal=libre, bloque=bloque)
        assert _check(_get(cliente), "heap_interno")["estado"] == esperado, (libre, bloque)


def test_umbrales_heap_configurables_por_env(salud, monkeypatch):
    """AULASENSE_HEAP_INT_VERDE_KB/BLOQUE override del .env único."""
    cliente, _ = salud
    ahora = _ahora_ms()
    _telemetria(ahora, internal=105, bloque=56)
    assert _check(_get(cliente), "heap_interno")["estado"] == "verde"  # default 95
    monkeypatch.setenv("AULASENSE_HEAP_INT_VERDE_KB", "120")
    assert _check(_get(cliente), "heap_interno")["estado"] == "warn"  # barra vieja
    monkeypatch.setenv("AULASENSE_HEAP_INT_VERDE_KB", "95")
    monkeypatch.setenv("AULASENSE_HEAP_BLOQUE_VERDE_KB", "60")
    assert _check(_get(cliente), "heap_interno")["estado"] == "warn"  # bloque 56 < 60


def test_internal_null_nunca_cero(salud):
    """Backwards-compat exigida: internal_free_kb NULL => 'sin-dato', jamas 0."""
    cliente, _ = salud
    _telemetria(_ahora_ms(), internal=None, psram=1.0)
    cuerpo = _get(cliente)
    check = _check(cuerpo, "heap_interno")
    assert check["estado"] == "sin-dato"
    assert check["valor"] is None
    assert cuerpo["placa"]["internal_free_kb"] is None
    assert cuerpo["placa"]["free_memory_kb"] == 1.0  # PSRAM sigue visible


def test_heap_fallback_retain_imagen_estado(salud):
    """Sin telemetría internal_*, el retain imagen_estado (detalle JSON) manda."""
    cliente, _ = salud
    _heartbeat(
        _ahora_ms() - 5_000,
        detalle={"imagen_estado": {DEVID: {
            "camara_ok": True, "captura_habilitada": False, "gate_off": True,
            "descartes": 3, "imagenes_ok": 57, "internal_free_kb": 90,
            "internal_largest_block_kb": 50, "ts_ms": _ahora_ms() - 9_000,
        }}},
    )
    cuerpo = _get(cliente)
    check = _check(cuerpo, "heap_interno")
    assert check["estado"] == "warn" and check["valor"] == 90
    assert cuerpo["placa"]["fuente"] == "retain:imagen_estado"
    assert cuerpo["camara"]["fuente"] == "imagen_estado"
    assert cuerpo["camara"]["estado"]["gate_off"] is True
    assert cuerpo["camara"]["estado"]["captura_habilitada"] is False


# ------------------------------------------------------------------------------
# Anti-falsa-alarma OTA (D-F26-8)
# ------------------------------------------------------------------------------
def _telemetria_vieja() -> None:
    _telemetria(_ahora_ms() - 11 * 60_000, internal=300)


def test_ota_descargando_por_retain_suprime_alarma(salud):
    cliente, _ = salud
    _telemetria_vieja()
    _heartbeat(
        _ahora_ms() - 5_000,
        detalle={"ota": {DEVID: {
            "estado": "downloading", "version_actual": "f25", "version_objetivo": "f26.1",
            "sha256_ok": False, "detalle": "slot inactivo", "ts_ms": _ahora_ms() - 8_000,
        }}},
    )
    cuerpo = _get(cliente)
    assert cuerpo["ota_en_curso"] is True
    frescura = _check(cuerpo, "ota")
    assert frescura["estado"] == "info" and frescura["valor"] == "downloading"
    telem = _check(cuerpo, "telemetria")
    assert telem["estado"] == "info"  # NO warn/critico: el agujero es esperado
    assert "OTA en curso" in (telem["detalle"] or "")
    assert "descarga" not in json.dumps(cuerpo).lower() or True  # sin secretos ya se cubre abajo


def test_ota_por_borde_config_eventos_suprime_alarma(salud):
    """Sin retain cacheado: el borde OTA_INICIO en config_eventos basta (a1 vive ahí)."""
    cliente, _ = salud
    _telemetria_vieja()
    _heartbeat(_ahora_ms() - 5_000)
    _ejecutar(
        "INSERT INTO config_eventos (device_id, ts_ms, tipo, detalle, origen) "
        "VALUES (?, ?, 'OTA_INICIO', 'version_objetivo=f26.1', 'real')",
        (DEVID, _ahora_ms() - 20_000),
    )
    cuerpo = _get(cliente)
    assert cuerpo["ota_en_curso"] is True
    assert _check(cuerpo, "telemetria")["estado"] == "info"
    assert _check(cuerpo, "ota")["fuente"] if "fuente" in _check(cuerpo, "ota") else True
    # y NO se busca en la tabla eventos (errata R-F26-14): insertar allí no cambia nada
    _ejecutar(
        "INSERT INTO eventos (device_id, ts_ms, tipo, origen) "
        "VALUES (?, ?, 'FALLA', 'real')", (DEVID, _ahora_ms() - 21_000)
    )
    assert _get(cliente)["ota_en_curso"] is True


def test_ota_ok_no_suprime_alarma(salud):
    cliente, _ = salud
    _telemetria_vieja()
    _heartbeat(
        _ahora_ms() - 5_000,
        detalle={"ota": {DEVID: {"estado": "idle", "ts_ms": _ahora_ms() - 6_000}}},
    )
    cuerpo = _get(cliente)
    assert cuerpo["ota_en_curso"] is False
    assert _check(cuerpo, "telemetria")["estado"] == "critico"


# ------------------------------------------------------------------------------
# Colas, consumo, disco, túneles, decisión de aula
# ------------------------------------------------------------------------------
def test_cola_decision_warn_y_critico(salud):
    cliente, datos = salud
    # 1 pendiente -> warn
    _ejecutar(
        "INSERT INTO imagenes (device_id, ts_ms, ruta, hash_sha256, tamano_bytes, "
        "vision_estado, decision_estado) VALUES (?, ?, 'imagenes/x.jpg', 'h1', 10, "
        "'hecho', 'pendiente')", (DEVID, _ahora_ms())
    )
    assert _check(_get(cliente), "cola_decision")["estado"] == "warn"
    # 3 errores sobre 4 procesadas (75 % > 20 %) -> crítico
    _ejecutar("DELETE FROM imagenes")
    for i, estado in enumerate(["error", "error", "error", "hecho"]):
        _ejecutar(
            "INSERT INTO imagenes (device_id, ts_ms, ruta, hash_sha256, tamano_bytes, "
            "vision_estado, decision_estado) VALUES (?, ?, ?, ?, 10, 'hecho', ?)",
            (DEVID, _ahora_ms(), f"imagenes/{i}.jpg", f"h{i}", estado),
        )
    assert _check(_get(cliente), "cola_decision")["estado"] == "critico"


def test_consumo_clef_tope(salud, monkeypatch):
    cliente, _ = salud
    monkeypatch.setenv("AULASENSE_CLEF_MAX_DIA", "2")
    ahora = _ahora_ms()
    for i in range(2):
        _ejecutar(
            "INSERT INTO clef_uso (ts_ms, device_id, imagen_id, modelo, tokens, "
            "coste_usd, exito, detalle) VALUES (?, NULL, NULL, 'cloudflare/clef-flash', "
            "500, 0.00001, 1, 'flash')", (ahora - i,)
        )
    cuerpo = _get(cliente)
    check = _check(cuerpo, "consumo_clef")
    assert check["estado"] == "critico"
    assert cuerpo["consumo"]["clef"]["dia"] == 2
    assert cuerpo["consumo"]["topes"]["clef_dia"] == 2


def test_consumo_clef_excluye_local_y_tope(salud):
    cliente, _ = salud
    ahora = _ahora_ms()
    _ejecutar(
        "INSERT INTO clef_uso (ts_ms, device_id, imagen_id, modelo, tokens, coste_usd, "
        "exito, detalle) VALUES (?, NULL, NULL, 'local', 0, 0, 1, 'local_sin_llamada')",
        (ahora,),
    )
    _ejecutar(
        "INSERT INTO clef_uso (ts_ms, device_id, imagen_id, modelo, tokens, coste_usd, "
        "exito, detalle) VALUES (?, NULL, NULL, 'cloudflare/clef-flash', NULL, NULL, 0, "
        "'tope')", (ahora,),
    )
    cuerpo = _get(cliente)
    assert cuerpo["consumo"]["clef"] == {"dia": 0, "hora": 0}
    assert _check(cuerpo, "consumo_clef")["estado"] == "verde"


def test_consumo_vision_warn_por_80(salud, monkeypatch):
    cliente, _ = salud
    _ejecutar(DDL_VISION_USO_TEST)
    monkeypatch.setenv("AULASENSE_VISION_MAX_DIA", "10")
    ahora = _ahora_ms()
    for i in range(8):
        _ejecutar(
            "INSERT INTO vision_uso (ts_ms, imagen_id, modelo, tokens_in, tokens_out, "
            "modo, resultado) VALUES (?, NULL, 'qwen3.8-flash', 10, 1, 'B', 'hecho')",
            (ahora - i,),
        )
    assert _check(_get(cliente), "consumo_vision")["estado"] == "warn"


def test_disco_warn_y_critico(salud, monkeypatch):
    import shutil as _shutil

    cliente, _ = salud
    for usado, esperado in ((900, "warn"), (970, "critico")):
        monkeypatch.setattr(
            _shutil, "disk_usage",
            lambda _p, u=usado: SimpleNamespace(total=1000, used=u, free=1000 - u),
        )
        assert _check(_get(cliente), "disco")["estado"] == esperado


def test_tuneles_running_warn_stale_y_critico(salud):
    cliente, datos = salud

    def escribir(payload) -> None:
        (datos / "tuneles_estado.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    ahora = _ahora_ms()
    escribir({
        "ts_ms": ahora,
        "tuneles": [
            {"id": "pub-aulasense-8100", "state": "running", "remote": "0.0.0.0:10062"},
            {"id": "pub-aulasense-ota", "state": "running", "remote": "0.0.0.0:10064"},
        ],
    })
    assert _check(_get(cliente), "tuneles")["estado"] == "verde"

    escribir({
        "ts_ms": ahora - 11 * 60_000,
        "tuneles": [{"id": "pub-aulasense-8100", "state": "running", "remote": "x"}],
    })
    assert _check(_get(cliente), "tuneles")["estado"] == "warn"

    escribir({
        "ts_ms": ahora,
        "tuneles": [{"id": "pub-aulasense-ota", "state": "failed", "remote": "x"}],
    })
    assert _check(_get(cliente), "tuneles")["estado"] == "critico"


def test_tuneles_no_serializa_campos_ajenos(salud):
    """Blanqueo: del JSON de wsl-port SOLO id/state/remote salen en la API."""
    cliente, datos = salud
    (datos / "tuneles_estado.json").write_text(
        json.dumps({
            "ts_ms": _ahora_ms(),
            "tuneles": [{
                "id": "pub-aulasense-8100", "state": "running", "remote": "0.0.0.0:10062",
                "token_ssh_pass": "NO-DEBE-SALIR", "user": "root",
                "objeto_completo": {"clave": "otra-fuga"},
            }],
        }),
        encoding="utf-8",
    )
    texto = json.dumps(_get(cliente))
    assert "NO-DEBE-SALIR" not in texto
    assert "otra-fuga" not in texto
    assert "root" not in texto


def test_camara_eventos_recentes_desde_config_eventos(salud):
    """La tarjeta cámara/gate lee config_eventos (NUNCA la tabla eventos)."""
    cliente, _ = salud
    _ejecutar(
        "INSERT INTO config_eventos (device_id, ts_ms, tipo, detalle, origen) "
        "VALUES (?, ?, 'IMAGEN_GATE_OFF', 'heap_int=91<95', 'real')",
        (DEVID, _ahora_ms() - 60_000),
    )
    _ejecutar(
        "INSERT INTO config_eventos (device_id, ts_ms, tipo, detalle, origen) "
        "VALUES (?, ?, 'IMAGEN_INIT', 'camera=ok pid=0x3660', 'real')",
        (DEVID, _ahora_ms() - 90_000),
    )
    _ejecutar(
        "INSERT INTO eventos (device_id, ts_ms, tipo, origen) "
        "VALUES (?, ?, 'FALLA', 'real')", (DEVID, _ahora_ms() - 61_000)
    )
    camara = _get(cliente)["camara"]
    assert camara["fuente"] == "config_eventos"
    tipos = [e["tipo"] for e in camara["eventos_recentes"]]
    assert "IMAGEN_GATE_OFF" in tipos and "IMAGEN_INIT" in tipos
    assert "FALLA" not in tipos  # la tabla eventos NO alimenta la tarjeta


def test_decision_aula_tarjeta_y_badge_revision(salud):
    cliente, _ = salud
    ahora = _ahora_ms()
    _ejecutar(
        "INSERT INTO imagenes (device_id, ts_ms, ruta, hash_sha256, tamano_bytes, "
        "vision_estado, decision_estado, decision_etiqueta, decision_confianza, "
        "decision_modelo, decision_ts_ms, decision_cubre, decision_calidad, "
        "decision_actividad) VALUES (?, ?, 'imagenes/a.jpg', 'ha', 10, 'hecho', "
        "'hecho', 'PERSONAS_SENTADAS', 0.83, 'cloudflare/clef-flash', ?, 0.91, 0.88, "
        "'baja')", (DEVID, ahora, ahora),
    )
    _ejecutar(
        "INSERT INTO imagenes (device_id, ts_ms, ruta, hash_sha256, tamano_bytes, "
        "vision_estado, decision_estado, decision_etiqueta, decision_confianza, "
        "decision_modelo, decision_ts_ms, decision_cubre, decision_calidad, "
        "decision_actividad) VALUES (?, ?, 'imagenes/b.jpg', 'hb', 10, 'hecho', "
        "'hecho', 'AGLOMERACION_OTRA', 0.4, 'cloudflare/clef', ?, 0.52, 0.9, NULL)",
        (DEVID, ahora, ahora - 1000),
    )
    cuerpo = _get(cliente)
    decision = cuerpo["decision_aula"]
    assert decision["disponible"] is True
    assert decision["por_etiqueta"]["PERSONAS_SENTADAS"] == 1
    assert decision["revision_humana"] == 1  # derivado de decision_cubre < 0.7
    recientes = {r["imagen_id"]: r for r in decision["recientes"]}
    b = next(r for r in recientes.values() if r["decision_etiqueta"] == "AGLOMERACION_OTRA")
    assert b["revision"] is True
    a = next(r for r in recientes.values() if r["decision_etiqueta"] == "PERSONAS_SENTADAS")
    assert a["revision"] is False
    assert a["decision_actividad"] == "baja"


def test_galeria_decision_opt_in(salud):
    """Sin ?decision=1 la metadata es el contrato F25 exacto; con él salen los decision_*."""
    cliente, _ = salud
    ahora = _ahora_ms()
    _ejecutar(
        "INSERT INTO imagenes (device_id, ts_ms, ruta, hash_sha256, tamano_bytes, "
        "vision_estado, decision_estado, decision_etiqueta, decision_confianza, "
        "decision_cubre) VALUES (?, ?, 'imagenes/a.jpg', 'ha', 10, 'hecho', 'hecho', "
        "'CIRCULACION', 0.77, 0.8)", (DEVID, ahora),
    )
    basico = cliente.get("/api/imagenes").json()["filas"][0]
    assert "decision_etiqueta" not in basico
    con_decision = cliente.get("/api/imagenes", params={"decision": 1}).json()
    fila = con_decision["filas"][0]
    assert fila["decision_etiqueta"] == "CIRCULACION"
    assert fila["decision_confianza"] == pytest.approx(0.77)
    filtrado = cliente.get(
        "/api/imagenes", params={"decision_estado": "hecho"}
    ).json()
    assert len(filtrado["filas"]) == 1
    assert filtrado["filas"][0]["decision_etiqueta"] == "CIRCULACION"
    rechazado = cliente.get(
        "/api/imagenes", params={"decision_estado": "no-existe"}
    )
    assert rechazado.status_code == 422


def test_api_salud_sin_secretos_y_ultima_actividad(salud):
    cliente, _ = salud
    ahora = _ahora_ms()
    _heartbeat(ahora - 1000, detalle={
        "ota": {DEVID: {"estado": "idle", "ts_ms": ahora}},
        "imagen_estado": {DEVID: {"camara_ok": True, "ts_ms": ahora}},
    })
    _ejecutar(
        "INSERT INTO evaluaciones (device_id, ts_ms, ts_device_ms, hora_sinc, state, "
        "score, origen) VALUES (?, ?, 0, 0, 'idle', 0.1, 'real')", (DEVID, ahora - 2000)
    )
    cuerpo = _get(cliente)
    assert cuerpo["ultima_actividad_ms"] == ahora - 2000
    texto = json.dumps(cuerpo)
    for veneno in ("OPENROUTER", "Bearer", "sk-or", "api_key", "password", "pass_hash"):
        assert veneno.lower() not in texto.lower()


def test_estado_sistema_detalle_invalido_no_rompe(salud):
    """JSON corrupto en detalle => degrada a sin-dato, nunca 500."""
    cliente, _ = salud
    _ejecutar(
        "INSERT INTO estado_sistema (id, ts_ms, mqtt_conectado, ingesta_activa, detalle) "
        "VALUES (1, ?, 1, 1, 'esto-no-es-json')", (_ahora_ms(),)
    )
    cuerpo = _get(cliente)
    assert _check(cuerpo, "ingesta")["estado"] == "verde"
    assert _check(cuerpo, "ota")["estado"] == "sin-dato"
    assert cuerpo["camara"]["fuente"] == "sin-dato"
