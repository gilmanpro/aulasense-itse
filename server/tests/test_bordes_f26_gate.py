# -*- coding: utf-8 -*-
"""test_bordes_f26_gate.py — GATE-pruebas F26: casos borde de la superficie NUEVA.

Entrega del auditor de pruebas (no del frente dueño): fija los límites CANÓNICOS
donde la suite base medía con márgenes amplios o no medía:

(a) ``ota``/``aulasense_config``: ``detalle`` justo 120/121 caracteres (el límite
    que ya muerde en campo) y su semántica de CARACTERES (no bytes UTF-8).
(b) Claim de decisión Clef stale JUSTO al umbral (``<=`` en ``liberar_decision_stale``).
(c) sha256 con mayúsculas/espacios/salto de línea en el envelope ``image``
    (espejo del fail-closed del firmware: app_ota.c:304-315).
(d) ``/api/salud`` con ``estado_sistema.detalle`` JSON corrupto o de forma inválida
    y con ``tuneles_estado.json`` ilegible (la salida del verbo salud de
    tools/wsl_aulasense.ps1 es PowerShell: no la cubre pytest; SÍ se cubre la
    PARSE-CONSUMIDORA de su contrato en el dashboard).
(e) ``consolidar_env --unificar`` con .env destino preexistente que ya trae claves
    DUPLICADAS en el mismo archivo (dry y real).
(f) Cascada Clef: refusal de flash + escalado que FALLA (API error) — y baja
    confianza + escalado refusal (la auditoría conserva la causa diferenciada
    'escalado_fallo' / 'escalado_refusal'; GATE-F26-M1 corregido).
(g) ``publicar_comando_ota`` con device_id sintácticamente válido pero INEXISTENTE
    (contrato fire-and-forget D-F26-2: no hay BD en la firma).

Además: bordes exactos del heartbeat en ``/api/salud`` (90 s / 5 min) y del
derivado stale de telemetría en la ingesta (600_000 ms estricto, 85 % disco con
``>=``), y los símbolos F26 nuevos de ``gen_firmware_config``
(``AULASENSE_OTA_MANIFEST_URL``, escape Kconfig, puerto entero, reescritura del
.env con CRLF/duplicadas).

Anti-reloj real (lección familia ts/+2963 s, analysis/export_consolida.py:27):
NINGÚN test de este archivo llama a ``time.time`` — el reloj va INYECTADO
(``ahora=``/``now_ms=``) o MONOPATCHIADO (``dashboard.app._ahora_ms``).

Reutiliza fixtures/helpers canónicos por import directo (patrón del repo:
test_imagen_mqtt_bordes.py:29), sin duplicar setups ni tocar módulos ajenos.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]  # aulasense/
_SERVER = ROOT / "server"
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))

import esquema  # noqa: E402  (sys.path de conftest apunta a server/ingesta)
import ingesta_mqtt  # noqa: E402
from ingesta_mqtt import publicar_comando_ota  # noqa: E402
from vision import clef  # noqa: E402

# --- helpers canónicos de la suite F26 (import, no copia) --------------------
from test_clef_f26 import (  # noqa: E402
    CLAVE_PRUEBA,
    FakePost,
    FakeResp,
    _cfg,
    _crear_db,
    _fila,
    _imagen,
    entorno_clef_aislado,  # noqa: F401  (autouse: sin red, sin sleeps, sin env real)
    respuestas_clef,
)
from test_env_unico_f26 import (  # noqa: E402
    CANARIO,
    _apuntar_bases,
    _cargar_consolidar,
    _parsear,
)
from test_esquema_f26 import DEV, OTA_OK, PREFIJO, topic  # noqa: E402
from test_ingesta_f26 import (  # noqa: E402
    NOW,
    ClienteStub,
    consultar,
    writer,  # noqa: F401  (fixture: IngestaWriter con DB/CSV temporales)
)
from test_salud_f26 import (  # noqa: E402
    DEVID,
    _check,
    _ejecutar,
    _get,
    entorno_salud,  # noqa: F401  (dependencia de la fixture salud)
    salud,  # noqa: F401  (fixture: cliente logueado + esquema F26 en tmp)
)

FISS = 1_800_000_000_000  # reloj inyectado fijo para TODOS los bordes de /salud


# ==============================================================================
# (a) OTA detalle: límite canónico 120/121, medido en CARACTERES
# ==============================================================================


def test_ota_detalle_exactamente_120_aceptado_y_121_rechazado():
    """El borde VIVO del contrato (esquema.py:438): 120 entra, 121 no."""
    ok = dict(OTA_OK, detalle="x" * 120)
    res = esquema.validar(topic("ota"), ok)
    assert res["valido"] is True
    mal = dict(OTA_OK, detalle="x" * 121)
    res = esquema.validar(topic("ota"), mal)
    assert res["valido"] is False
    assert "supera 120" in res["error"]


def test_evento_config_ota_inicio_detalle_120_persiste_y_121_no(writer):
    """Mismo borde en el EVENTO (la ruta que ya muerde en campo, SPEC §16.4):
    120 chars se persiste; 121 se rechaza y NO deja fila."""
    res = writer.procesar(
        topic("aulasense_config"),
        {"timestamp_ms": 10, "evento": "OTA_INICIO", "detalle": "y" * 120},
        now_ms=NOW,
    )
    assert res["valido"] is True
    res = writer.procesar(
        topic("aulasense_config"),
        {"timestamp_ms": 11, "evento": "OTA_INICIO", "detalle": "z" * 121},
        now_ms=NOW,
    )
    assert res["valido"] is False
    filas = consultar(writer, "SELECT detalle FROM config_eventos WHERE tipo='OTA_INICIO'")
    assert len(filas) == 1 and len(filas[0][0]) == 120


def test_ota_detalle_se_mide_en_caracteres_no_en_bytes_utf8():
    """120 acentos = 240 bytes UTF-8: el servidor mide CARACTERES (len()), el
    firmware buffer en bytes → el server es el lado PERMISIVO del borde.
    Se fija la semántica actual (contract-anchor) para no sorprenderse al
    refactorizar a bytes."""
    acentos_120 = "á" * 120
    assert len(acentos_120.encode("utf-8")) == 240
    assert esquema.validar(topic("ota"), dict(OTA_OK, detalle=acentos_120))["valido"] is True
    assert esquema.validar(topic("ota"), dict(OTA_OK, detalle="á" * 121))["valido"] is False


# ==============================================================================
# (b) Claim de decisión Clef stale JUSTO al umbral (clef.py:475-480)
# ==============================================================================


def _marcar_procesando(db: Path, imagen_id: int, ts: int | None) -> None:
    con = sqlite3.connect(db)
    con.execute(
        "UPDATE imagenes SET decision_estado='procesando', decision_ts_ms=? WHERE id=?",
        (ts, imagen_id),
    )
    con.commit()
    con.close()


def test_claim_stale_umbral_exacto_se_libera_y_un_ms_antes_no(tmp_path):
    """``decision_ts_ms <= ahora - stale_ms`` (INCLUSIVE, clef.py:479): al
    minuto 30 clavado se libera; a 29:59.999 sigue en vuelo."""
    db = _crear_db(tmp_path)
    justo = _imagen(db, tmp_path, hash_sha256="borde-exacto")
    un_ms_antes = _imagen(db, tmp_path, hash_sha256="borde-vivo")
    _marcar_procesando(db, justo, FISS - 30 * 60_000)          # == umbral
    _marcar_procesando(db, un_ms_antes, FISS - 30 * 60_000 + 1)  # 1 ms dentro
    con = sqlite3.connect(db)
    try:
        liberadas = clef.liberar_decision_stale(con, ahora=FISS)
    finally:
        con.close()
    assert liberadas == 1
    assert _fila(db, justo)["decision_estado"] == "pendiente"
    assert _fila(db, un_ms_antes)["decision_estado"] == "procesando"


def test_claim_stale_ts_null_es_huerfana_y_umbral_custom(tmp_path):
    """ts NULL = claim desconocido → siempre se libera; y el umbral es
    configurable (``stale_ms``): con 1000 ms el borde se corre exacto."""
    db = _crear_db(tmp_path)
    nula = _imagen(db, tmp_path, hash_sha256="ts-null")
    justo_custom = _imagen(db, tmp_path, hash_sha256="custom-ok")
    vivo_custom = _imagen(db, tmp_path, hash_sha256="custom-vivo")
    _marcar_procesando(db, nula, None)
    _marcar_procesando(db, justo_custom, FISS - 1000)
    _marcar_procesando(db, vivo_custom, FISS - 999)
    con = sqlite3.connect(db)
    try:
        liberadas = clef.liberar_decision_stale(con, stale_ms=1000, ahora=FISS)
    finally:
        con.close()
    assert liberadas == 3 - 1
    assert _fila(db, nula)["decision_estado"] == "pendiente"
    assert _fila(db, justo_custom)["decision_estado"] == "pendiente"
    assert _fila(db, vivo_custom)["decision_estado"] == "procesando"


# ==============================================================================
# (c) sha256 con mayúsculas/espacios/newline en el envelope image
#     (espejo del fail-closed firmware: app_ota.c:304-315 pide HEX MINÚSCULA)
# ==============================================================================


def _image_payload(**mutado) -> dict:
    p = {
        "v": 1,
        "device_id": DEV,
        "ts_ms": 1_763_400_000_000,
        "motivo": "onset",
        "tamano": 10,
        "sha256": "a" * 64,
        "b64": "AAAA",
    }
    p.update(mutado)
    return p


@pytest.mark.parametrize(
    "sha,espera_valido,motivo",
    [
        ("a" * 64, True, "lowercase canónico entra (guarda contra falso rechazo)"),
        ("A" * 64, False, "MAYÚSCULAS: fuera del contrato lowercase"),
        ("aA" + "0" * 62, False, "mixto: fuera"),
        (" " + "a" * 63, False, "espacio inicial"),
        ("a" * 63 + " ", False, "espacio final"),
        ("a" * 32 + " " + "a" * 31, False, "espacio interior (64 chars no hex)"),
        ("a" * 64 + "\n", False, "newline final: fullmatch NO lo perdona"),
    ],
)
def test_image_sha256_formas_hostiles(sha, espera_valido, motivo):
    res = esquema.validar(topic("image"), _image_payload(sha256=sha))
    assert res["valido"] is espera_valido, motivo


# ==============================================================================
# (f) Clef: refusal + escalado que falla (combinaciones que la suite base no cruzaba)
# ==============================================================================


def test_clef_refusal_flash_y_escalado_error_api_falla_honesto(tmp_path, monkeypatch):
    """flash REFUSA + escalado agota backoff con 5xx → fail-closed 'error' y
    auditoría con exito=0 / detalle 'escalado_fallo' (clef.py:946-957, 1163-1181)."""
    db = _crear_db(tmp_path)
    cfg = _cfg(tmp_path, db)
    imagen = _imagen(db, tmp_path, hash_sha256="ref-err")
    post = FakePost(
        FakeResp(200, respuestas_clef(refusal=True)),
        *[FakeResp(503, text="caido")] * 3,  # 1 intento + 2 reintentos de backoff
    )
    monkeypatch.setattr(clef.requests, "post", post)
    resumen = clef.ejecutar_pasada(cfg, CLAVE_PRUEBA)
    assert len(post.llamadas) == 4 and resumen.escaladas == 1
    assert resumen.errores == 1 and resumen.hechas == 0
    assert _fila(db, imagen)["decision_estado"] == "error"
    con = sqlite3.connect(db)
    exito, detalle = con.execute(
        "SELECT exito, detalle FROM clef_uso ORDER BY id DESC LIMIT 1"
    ).fetchone()
    con.close()
    assert exito == 0 and detalle == "escalado_fallo"


def test_clef_baja_conf_escalado_refusal_conserva_flash_y_auditoria_refusal(
    tmp_path, monkeypatch
):
    """flash baja confianza + escalado REFUSA → se conserva la etiqueta flash
    (hecho) y la auditoría conserva la CAUSA: 'escalado_refusal' (antes
    clef.py:1150-1154 la aplastaba a 'escalado_fallo'; GATE-F26-M1, corregido)."""
    db = _crear_db(tmp_path)
    cfg = _cfg(tmp_path, db)
    imagen = _imagen(db, tmp_path, hash_sha256="conf-ref")
    post = FakePost(
        FakeResp(200, respuestas_clef(confidence=0.42)),
        FakeResp(200, respuestas_clef(refusal=True)),
    )
    monkeypatch.setattr(clef.requests, "post", post)
    resumen = clef.ejecutar_pasada(cfg, CLAVE_PRUEBA)
    fila = _fila(db, imagen)
    assert resumen.escaladas == 1 and resumen.hechas == 1
    assert fila["decision_estado"] == "hecho"
    assert fila["decision_etiqueta"] == "PERSONAS_SENTADAS"  # la del flash
    assert fila["decision_modelo"] == "cloudflare/clef-flash"
    con = sqlite3.connect(db)
    detalle = con.execute("SELECT detalle FROM clef_uso ORDER BY id DESC LIMIT 1").fetchone()[0]
    con.close()
    # la auditoría distingue refusal del escalado vs fallo de API del escalado:
    assert detalle == "escalado_refusal"


# ==============================================================================
# (g) publicar_comando_ota: device inexistente y formas inválidas de device_id
# ==============================================================================


def test_publicar_comando_ota_device_inexistente_se_publica_igual():
    """CONTRATO (D-F26-2, fire-and-forget): ``publicar_comando_ota`` NO recibe
    conexión BD (ingesta_mqtt.py:1711-1717) — valida SINTAXIS, no existencia.
    Un device válido-jamás-visto publica igual (el comando simply no encuentra
    suscriptor; QoS 0 sin retain: se evapora, inofensivo). Se fija aquí para
    que un refactor que añada allowlist DB sea un cambio de contrato CONSCIENTE."""
    cliente = ClienteStub()
    desconocido = "deadbeefdeadbeef"  # 16 hex válida, nunca registrada
    registro = publicar_comando_ota(cliente, desconocido, "check", ahora=NOW)
    assert registro["topic"] == f"{PREFIJO}/{desconocido}/aulasense_ota"
    assert json.loads(cliente.publicados[0]["payload"])["action"] == "check"
    assert len(cliente.publicados) == 1


@pytest.mark.parametrize(
    "device",
    [
        pytest.param("2E6F0C909E30673E", id="mayusculas"),
        pytest.param(" 2e6f0c909e30673e", id="espacio-inicial"),
        pytest.param("2e6f0c909e30673e ", id="espacio-final"),
        pytest.param("2e6f 0c909e30673e", id="espacio-interior"),
        pytest.param(None, id="None"),
        pytest.param("", id="vacio"),
    ],
)
def test_publicar_comando_ota_device_forms_invalidas_no_publican(device):
    """El regex es ``^[0-9a-f]{16}$`` estricto (ingesta_mqtt.py:188): mayúsculas y
    espacios NUNCA salen al broker (y el topic con espacio sería inválido)."""
    cliente = ClienteStub()
    with pytest.raises(ValueError):
        publicar_comando_ota(cliente, device, "check", ahora=NOW)
    assert cliente.publicados == []


# ==============================================================================
# (d) /api/salud: JSON corrupto/inválido + bordes exactos del heartbeat
# ==============================================================================


@pytest.fixture()
def reloj_fijo(monkeypatch):
    """Congela el reloj del dashboard: NINGÚN test de salud de este archivo
    depende del reloj real de la máquina (familia +2963 s)."""
    from dashboard import app as app_mod

    monkeypatch.setattr(app_mod, "_ahora_ms", lambda: FISS)
    return FISS


def _heartbeat_crudo(ts_ms: int, detalle: str | None = None, activa: int = 1, mqtt: int = 1) -> None:
    _ejecutar(
        "INSERT INTO estado_sistema (id, ts_ms, mqtt_conectado, ingesta_activa, detalle) "
        "VALUES (1, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET ts_ms=excluded.ts_ms, "
        "mqtt_conectado=excluded.mqtt_conectado, "
        "ingesta_activa=excluded.ingesta_activa, detalle=excluded.detalle",
        (ts_ms, mqtt, activa, detalle),
    )


@pytest.mark.parametrize(
    "ts_ms,activa,esperado",
    [
        (FISS - 89_999, 1, "verde"),
        (FISS - 90_000, 1, "verde"),      # borde exacto verde (<=, app.py:1536)
        (FISS - 90_001, 1, "warn"),       # 1 ms fuera
        (FISS - 300_000, 1, "warn"),      # 5 min clavados: <= CRITICO_MS sigue warn
        (FISS - 300_001, 1, "critico"),   # y un ms más, crítico
        (FISS, 0, "warn"),                # fresca pero ingesta_activa=0 NO es verde
    ],
)
def test_api_salud_heartbeat_bordes_exactos(salud, reloj_fijo, ts_ms, activa, esperado):
    cliente, _ = salud
    _heartbeat_crudo(ts_ms, activa=activa)
    cuerpo = _get(cliente)
    assert _check(cuerpo, "ingesta")["estado"] == esperado


@pytest.mark.parametrize(
    "veneno",
    [
        pytest.param("[1,2]", id="json-lista-toplevel"),
        pytest.param('{"ota": 5, "imagen_estado": [7]}', id="valores-no-dict"),
        pytest.param('{"ota": {"dev": "cadena"}}', id="payload-no-dict"),
        pytest.param('{"ota": null}', id="ota-null"),
        pytest.param('{"ota": {}}', id="ota-dict-vacio"),
    ],
)
def test_api_salud_detalle_json_valido_forma_invalida_degrada_sin_dato(salud, reloj_fijo, veneno):
    """La suite base probó 'esto-no-es-json' (ValueError). Aquí: JSON VÁLIDO de
    forma errónea — ``_leer_retains``/``_payload_retain_reciente`` deben filtrar
    por tipo y degradar a 'sin-dato' SIN 500 (app.py:1244-1273)."""
    cliente, _ = salud
    _heartbeat_crudo(FISS, detalle=veneno)
    cuerpo = _get(cliente)
    assert _check(cuerpo, "ota")["estado"] == "sin-dato"
    assert cuerpo["camara"]["fuente"] == "sin-dato"
    # el heartbeat sigue siendo la fuente de 'ingesta' (verde): los retains no lo tocan
    assert _check(cuerpo, "ingesta")["estado"] == "verde"


def test_api_salud_ota_retain_sin_ts_ms_siguen_los_checks(salud, reloj_fijo):
    """Retain ota SIN ``ts_ms`` (payload del borde config_eventos o placa vieja):
    ``int(None or 0)=0`` lo elige igual (app.py:1268) y el check sale 'info' con
    downloading — el panel NO debe 500 ni inventar frescura."""
    cliente, _ = salud
    _heartbeat_crudo(
        FISS,
        detalle=json.dumps({"ota": {DEVID: {"estado": "downloading", "detalle": "x"}}}),
    )
    cuerpo = _get(cliente)
    check_ota = _check(cuerpo, "ota")
    assert check_ota["estado"] == "info"
    assert check_ota["valor"] == "downloading"


def test_api_salud_tuneles_estado_json_ilegible_sin_dato(salud, reloj_fijo):
    """El verbo ``salud`` de tools/wsl_aulasense.ps1 escribe
    datos/tuneles_estado.json (PowerShell: fuera del barrido pytest). Su
    CONSUMIDOR sí se prueba: escritura cortada/JSON inválido → 'sin-dato' con
    motivo, nunca 500 (app.py:1383-1387)."""
    cliente, datos = salud
    (datos / "tuneles_estado.json").write_text('{"ts_ms": 1, "tuneles": [{"id":', encoding="utf-8")
    _heartbeat_crudo(FISS)
    cuerpo = _get(cliente)
    check = _check(cuerpo, "tuneles")
    assert check["estado"] == "sin-dato"
    assert "ilegible" in (check["detalle"] or "")


def test_api_salud_tuneles_dict_sin_lista_fail_closed(salud, reloj_fijo):
    """Forma dict SIN clave 'tuneles' → se trata como UN túnel desconocido con
    state=None → 'state != running' → CRÍTICO (fail-closed, app.py:1388-1391,1424).
    Se fija el comportamiento: un archivo medio-escrito no debe pintarse en verde."""
    cliente, datos = salud
    (datos / "tuneles_estado.json").write_text('{"otra": true}', encoding="utf-8")
    _heartbeat_crudo(FISS)
    cuerpo = _get(cliente)
    assert _check(cuerpo, "tuneles")["estado"] == "critico"


# ==============================================================================
# Ingesta heartbeat: bordes exactos de los derivados (reloj 100 % inyectado)
# ==============================================================================


def test_ingesta_stale_telemetria_borde_exacto_600000ms(writer):
    """``ahora - ultimo_ts > 600_000`` ESTRICTO (ingesta_mqtt.py:1336): a 10 min
    clavados NO deriva; un ms después, sí. La suite base probó con 601_000."""
    writer.procesar(
        topic("aulasense_temp"), {"timestamp_ms": 1, "die_temp_c": 45.0}, now_ms=NOW
    )
    res = writer.escribir_heartbeat(True, ahora=NOW + 600_000)
    assert "SISTEMA_TELEMETRIA_STALE" not in res["derivados"]
    res = writer.escribir_heartbeat(True, ahora=NOW + 600_001)
    assert "SISTEMA_TELEMETRIA_STALE" in res["derivados"]


def test_ingesta_disco_borde_exacto_85_por_ciento(writer, monkeypatch):
    from types import SimpleNamespace

    def _uso(pct: int):
        monkeypatch.setattr(
            ingesta_mqtt.shutil,
            "disk_usage",
            lambda ruta: SimpleNamespace(total=100, used=pct, free=100 - pct),
        )

    _uso(84)
    assert "SISTEMA_DISCO_WARN" not in writer.escribir_heartbeat(True, ahora=NOW)["derivados"]
    _uso(85)  # ``fraccion >= 0.85`` (ingesta_mqtt.py:1353): el 85 entra
    assert "SISTEMA_DISCO_WARN" in writer.escribir_heartbeat(True, ahora=NOW + 30_000)["derivados"]


# ==============================================================================
# (e) consolidar_env --unificar: destino preexistente con claves duplicadas
# ==============================================================================


DUP_ENV = (
    f"MQTT_PASS={CANARIO}-primera\n"
    f"MQTT_PASS={CANARIO}-segunda\n"
    "MQTT_HOST=broker.dup.test\n"
)


def test_unificar_destino_preexistente_con_duplicadas_mismo_archivo(tmp_path, capsys):
    """.env destino (que ES la primera fuente) con la MISMA clave dos veces:
    gana la primera (consolidar_env.py:166-167), el destino queda con UNA línea y
    la segunda se PIERDE. GATE-F26-M2 (MENOR, corregido): el duplicado
    intra-archivo ahora se ADVIERTE por nombre (jamás el valor)."""
    mod = _cargar_consolidar()
    base = _apuntar_bases(mod, tmp_path)
    (base / ".env").write_text(DUP_ENV, encoding="utf-8")
    rc = mod.main(["--unificar"])
    out = capsys.readouterr().out
    assert rc == 0
    dst = _parsear(base / ".env")
    assert dst["MQTT_PASS"] == f"{CANARIO}-primera"
    assert f"{CANARIO}-segunda" not in (base / ".env").read_text(encoding="utf-8")
    # NINGÚN valor sale por stdout (ni el que gana ni el descartado):
    assert CANARIO not in out
    # y el conteo de duplicadas NO ve el duplicado intra-archivo (semántica intacta),
    # pero SÍ hay aviso específico por nombre (sin valor):
    assert "duplicadas (gano 1ª fuente): 0" in out
    linea_warn = next(l for l in out.splitlines() if "WARN duplicadas intra-archivo" in l)
    assert "MQTT_PASS" in linea_warn
    assert CANARIO not in linea_warn


def test_unificar_dry_con_destino_duplicado_no_toca_nada(tmp_path, capsys):
    mod = _cargar_consolidar()
    base = _apuntar_bases(mod, tmp_path)
    (base / ".env").write_text(DUP_ENV, encoding="utf-8")
    antes = (base / ".env").read_bytes()
    rc = mod.main(["--unificar", "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert (base / ".env").read_bytes() == antes  # intacto byte a byte
    assert not (base / "claves" / "env-raiz.bak-f26").exists()
    assert CANARIO not in out
    assert "DRY-RUN" in out


# ==============================================================================
# gen_firmware_config: símbolos F26 nuevos + escapes + reescritura del .env
# ==============================================================================


gen_firmware_config = None  # se carga en tiempo de import igual que test_tools.py


def _cargar_gen_firmware_config():
    import importlib.util

    global gen_firmware_config
    if gen_firmware_config is None:
        espec = importlib.util.spec_from_file_location(
            "gen_firmware_config_gate", ROOT / "tools" / "gen_firmware_config.py"
        )
        assert espec is not None and espec.loader is not None
        gen_firmware_config = importlib.util.module_from_spec(espec)
        espec.loader.exec_module(gen_firmware_config)
    return gen_firmware_config


def test_gen_config_emite_ota_manifest_url_simbolo_vivo(tmp_path, capsys, monkeypatch):
    """F26 (SPEC §16.1/§16.9): el símbolo existe en el Kconfig real
    (Kconfig.projbuild:302) — el generador debe verterlo cuando está vivo."""
    gfc = _cargar_gen_firmware_config()
    monkeypatch.setattr(
        gfc, "simbolos_kconfig",
        lambda: {"AULASENSE_OTA_MANIFEST_URL", "AULASENSE_UPLOAD_TOKEN"},
    )
    env = tmp_path / ".env"
    env.write_text(
        "AULASENSE_OTA_MANIFEST_URL=http://vps.test:10064/manifest.json\n", encoding="utf-8"
    )
    salida = tmp_path / "sdkconfig.defaults.local"
    assert gfc.main(["--env", str(env), "--out", str(salida)]) == 0
    contenido = salida.read_text(encoding="utf-8")
    assert 'CONFIG_AULASENSE_OTA_MANIFEST_URL="http://vps.test:10064/manifest.json"' in contenido


def test_gen_config_ota_manifest_simbolo_muerto_queda_preparado(tmp_path, monkeypatch):
    gfc = _cargar_gen_firmware_config()
    monkeypatch.setattr(gfc, "simbolos_kconfig", lambda: {"AULASENSE_UPLOAD_TOKEN"})
    env = tmp_path / ".env"
    env.write_text("AULASENSE_OTA_MANIFEST_URL=http://x.test/m.json\n", encoding="utf-8")
    salida = tmp_path / "sdkconfig.defaults.local"
    assert gfc.main(["--env", str(env), "--out", str(salida)]) == 0
    contenido = salida.read_text(encoding="utf-8")
    assert "CONFIG_AULASENSE_OTA_MANIFEST_URL=" not in contenido
    assert "PREPARADOS" in contenido and "AULASENSE_OTA_MANIFEST_URL" in contenido


def test_gen_config_escapa_comillas_y_backslash_del_valor(tmp_path, monkeypatch):
    """Inyección de línea Kconfig: un valor con " o \\ debe salir ESCAPADO
    (escapar_kconfig, gen_firmware_config.py:75-77) y no poder abrir una línea
    CONFIG_ falsa."""
    gfc = _cargar_gen_firmware_config()
    monkeypatch.setattr(gfc, "simbolos_kconfig", lambda: {"ESPECTRE_WIFI_SSID"})
    env = tmp_path / ".env"
    # valor REAL del SSID-hostil: a"b\c  (comilla + backslash pueden abrir una
    # línea CONFIG_ falsa si el generador no escapa)
    env.write_text("""WIFI_SSID='a"b\\c'\n""", encoding="utf-8")
    salida = tmp_path / "sdkconfig.defaults.local"
    assert gfc.main(["--env", str(env), "--out", str(salida)]) == 0
    lineas = [l for l in salida.read_text(encoding="utf-8").splitlines()
              if l.startswith("CONFIG_")]
    assert lineas == ['CONFIG_ESPECTRE_WIFI_SSID="a\\"b\\\\c"']


def test_gen_config_upload_port_se_emite_entero_sin_comillas(tmp_path, monkeypatch):
    gfc = _cargar_gen_firmware_config()
    monkeypatch.setattr(gfc, "simbolos_kconfig", lambda: {"AULASENSE_UPLOAD_PORT"})
    env = tmp_path / ".env"
    env.write_text("AULASENSE_UPLOAD_PORT=8001\n", encoding="utf-8")
    salida = tmp_path / "sdkconfig.defaults.local"
    assert gfc.main(["--env", str(env), "--out", str(salida)]) == 0
    assert "CONFIG_AULASENSE_UPLOAD_PORT=8001" in salida.read_text(encoding="utf-8")


def test_escribir_env_preserva_crlf_y_reemplaza_in_situ(tmp_path):
    """gen_firmware_config.py:146-158: reemplazo byte-a-byte CON saltos
    originales (el .env real en Windows convive con CRLF)."""
    gfc = _cargar_gen_firmware_config()
    ruta = tmp_path / ".env"
    ruta.write_bytes(b"AULASENSE_UPLOAD_TOKEN=\r\nMQTT_HOST=h\r\n")
    token, generado = gfc.asegurar_token_upload(ruta, {"AULASENSE_UPLOAD_TOKEN": ""})
    assert generado is True and len(token) == 32
    datos = ruta.read_bytes()
    assert b"\r\n" in datos and b"\n" not in datos.replace(b"\r\n", b"")
    assert f"AULASENSE_UPLOAD_TOKEN={token}".encode() in datos  # reemplazo in situ
    assert b"MQTT_HOST=h\r\n" in datos                          # resto intacto
    # segunda pasada: NO regenera (estable)
    token2, generado2 = gfc.asegurar_token_upload(ruta, {"AULASENSE_UPLOAD_TOKEN": token})
    assert generado2 is False and token2 == token


def test_gen_config_token_duplicado_consistencia_env_vs_sdkconfig(tmp_path):
    """BUG-GATE-F26-1 (MEDIO, CORREGIDO): con ``AULASENSE_UPLOAD_TOKEN`` duplicada
    en el .env, ``escribir_env`` reemplaza TODAS las ocurrencias (semántica
    última-gana de dotenv) → el valor que resuelve ``dotenv_values`` es el MISMO
    que se vertió al sdkconfig y la segunda pasada ya no regenera el token."""
    gfc = _cargar_gen_firmware_config()
    import re as _re

    from dotenv import dotenv_values

    env = tmp_path / ".env"
    salida = tmp_path / "sdkconfig.defaults.local"
    env.write_text("AULASENSE_UPLOAD_TOKEN=\nAULASENSE_UPLOAD_TOKEN=\n", encoding="utf-8")

    def _vivos():
        return {"AULASENSE_UPLOAD_TOKEN"}

    gfc.simbolos_kconfig = _vivos
    assert gfc.main(["--env", str(env), "--out", str(salida)]) == 0
    primera = _re.search(
        r'CONFIG_AULASENSE_UPLOAD_TOKEN="([^"]+)"', salida.read_text(encoding="utf-8")
    ).group(1)
    assert dotenv_values(env)["AULASENSE_UPLOAD_TOKEN"] == primera
    # IDEMPOTENTE: la segunda pasada reutiliza el token (ambas líneas coinciden).
    assert gfc.main(["--env", str(env), "--out", str(salida)]) == 0
    segunda = _re.search(
        r'CONFIG_AULASENSE_UPLOAD_TOKEN="([^"]+)"', salida.read_text(encoding="utf-8")
    ).group(1)
    assert segunda == primera


def test_gen_config_main_sin_env_falla_1_y_dirige_a_consolidar(tmp_path, capsys):
    gfc = _cargar_gen_firmware_config()
    rc = gfc.main(["--env", str(tmp_path / "no-existe.env"), "--out", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert rc == 1
    assert "consolidar_env" in out  # el error manda al flujo canónico F26
