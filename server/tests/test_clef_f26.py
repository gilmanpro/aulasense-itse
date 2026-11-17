"""Tests del worker de decision de aula Clef (SPEC §16.6, fase F26-a2).

Cubren ``server/vision/clef.py`` + ``tools/clef_decide.py``. La API alpha de
OpenRouter SIEMPRE se mockea (monkeypatch de ``requests.post``) y los sleeps del
backoff tambien (``clef._dormir``): NINGUN test llama a la red — las llamadas
reales corresponden a la fase F26-h2. Las claves de prueba son literales
falsos; se afirma que NUNCA aparecen en la salida del CLI.

El DDL de las tablas F26 se construye IMPORTANDO las constantes canónicas de
``server/ingesta/esquema.py`` (COLUMNAS_F25/F26, _DDL_CLEF_USO): el test replica
la migración que aplicaría la ingesta, sin divergencia posible.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]  # aulasense/
_SERVER = ROOT / "server"
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))

from ingesta import esquema  # noqa: E402  (constantes canónicas del DDL F26)
from vision import clef  # noqa: E402


def _cargar(nombre: str, ruta: Path):
    """Carga un modulo por ruta (tools/ no es paquete instalable)."""
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nombre] = modulo
    spec.loader.exec_module(modulo)
    return modulo


clef_decide = _cargar("clef_decide_tool", ROOT / "tools" / "clef_decide.py")

CLAVE_PRUEBA = "sk-or-PRUEBA-f26a2-no-real"
DEVID = "0123456789abcdef"

DDL_IMAGENES_BASE = """
CREATE TABLE imagenes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL,
  ts_ms INTEGER NOT NULL,
  ruta TEXT NOT NULL,
  hash_sha256 TEXT NOT NULL,
  tamano_bytes INTEGER NOT NULL,
  ancho INTEGER, alto INTEGER,
  evento_id INTEGER,
  ia_persona INTEGER, ia_score REAL,
  imagen_purgada INTEGER NOT NULL DEFAULT 0,
  origen TEXT NOT NULL DEFAULT 'real',
  recibido_en TEXT NOT NULL DEFAULT '2026-11-17 00:00:00'
);
CREATE TABLE vision_uso (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_ms INTEGER NOT NULL, imagen_id INTEGER REFERENCES imagenes(id),
  modelo TEXT NOT NULL, tokens_in INTEGER, tokens_out INTEGER,
  modo TEXT NOT NULL CHECK(modo IN ('A','B')),
  resultado TEXT NOT NULL CHECK(resultado IN ('hecho','error','tope'))
);
"""


def _crear_db(tmp_path: Path, *, con_decision: bool = True, hash_unico: bool = True) -> Path:
    """DB temporal con el esquema F25 + (opcional) F26 canónico (SPEC §16.5)."""
    db = tmp_path / "aulasense.db"
    con = sqlite3.connect(db)
    try:
        con.executescript(DDL_IMAGENES_BASE)
        for _nombre, ddl in esquema.COLUMNAS_F25_IMAGENES:
            con.execute(f"ALTER TABLE imagenes ADD COLUMN {ddl}")
        if con_decision:
            for _nombre, ddl in esquema.COLUMNAS_F26_IMAGENES:
                con.execute(f"ALTER TABLE imagenes ADD COLUMN {ddl}")
            con.executescript(esquema._DDL_CLEF_USO)
        if hash_unico:
            con.execute("CREATE UNIQUE INDEX idx_img_hash_unico ON imagenes(hash_sha256)")
        con.commit()
    finally:
        con.close()
    return db


def _cfg(tmp_path: Path, db: Path, **overrides) -> clef.ConfigClef:
    base = dict(
        db_path=db,
        datos_dir=tmp_path,
        env_path=tmp_path / "env_inexistente",
        claves_path=tmp_path / "claves_inexistente",
        modelo_flash=clef.MODELO_FLASH_DEFECTO,
        modelo_clef=clef.MODELO_CLEF_DEFECTO,
        umbral_escaldado=0.6,
        max_dia=120,
        max_hora=20,
        limite=10,
        stale_min=30,
        timeout_s=5.0,
        bandera_stop=tmp_path / "clef_stop.flag",
    )
    base.update(overrides)
    return clef.ConfigClef(**base)


def _jpeg_ok() -> bytes:
    """JPEG minimo: con Pillow real de 320x240; sin Pillow, marcadores SOI/EOI."""
    try:
        import io

        from PIL import Image
    except ImportError:  # pragma: no cover
        return b"\xff\xd8\xff\xe0prueba\xff\xd9"
    buffer = io.BytesIO()
    Image.new("RGB", (320, 240), (120, 120, 120)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _imagen(
    db: Path,
    datos: Path,
    *,
    hash_sha256: str = "hash-1",
    vision_estado: str = "hecho",
    decision_estado: str = "pendiente",
    descripcion: str | None = "Aula con varias personas sentadas mirando al frente.",
    etiquetas: str | None = "aula, personas sentadas",
    contenido: bytes | None = None,
    purgada: int = 0,
    ruta: str | None = None,
) -> int:
    """Inserta una fila de ``imagenes`` (con archivo JPEG en datos/) y su id."""
    ruta = ruta or f"imagenes/{DEVID}/{hash_sha256}.jpg"
    if contenido is None:
        contenido = _jpeg_ok()
    archivo = datos / ruta
    archivo.parent.mkdir(parents=True, exist_ok=True)
    archivo.write_bytes(contenido)
    con = sqlite3.connect(db)
    try:
        cursor = con.execute(
            "INSERT INTO imagenes (device_id, ts_ms, ruta, hash_sha256, tamano_bytes, "
            "ancho, alto, imagen_purgada, canal, descripcion, etiquetas, vision_estado, "
            "decision_estado) VALUES (?, ?, ?, ?, ?, 320, 240, ?, 'mqtt', ?, ?, ?, ?)",
            (
                DEVID, 1_700_000_000_000, ruta, hash_sha256, len(contenido),
                purgada, descripcion, etiquetas, vision_estado, decision_estado,
            ),
        )
        con.commit()
        return int(cursor.lastrowid)
    finally:
        con.close()


def _fila(db: Path, imagen_id: int) -> dict:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        return dict(con.execute("SELECT * FROM imagenes WHERE id = ?", (imagen_id,)).fetchone())
    finally:
        con.close()


# ------------------------------------------------------------------------------
# Mock de la API alpha
# ------------------------------------------------------------------------------
class FakeResp:
    def __init__(self, status: int = 200, cuerpo: dict | None = None, text: str = "",
                 headers: dict | None = None):
        self.status_code = status
        self._cuerpo = cuerpo
        self.text = text
        self.headers = headers or {}

    def json(self) -> dict:
        if self._cuerpo is None:
            raise ValueError("sin JSON")
        return self._cuerpo


def respuestas_clef(
    *,
    choice: str = "PERSONAS_SENTADAS",
    confidence: float = 0.91,
    cubre: float = 0.95,
    calidad: float = 0.9,
    score: float = 1.6,
    refusal: bool = False,
    tokens: int = 520,
    cost: float = 0.00001,
) -> dict:
    """Cuerpo de respuesta según primitivas.md (noul/choice/score + usage)."""
    if refusal:
        decision = {"type": "refusal"}
    else:
        decision = {
            "type": "choice",
            "choice": choice,
            "probabilities": {et: (confidence if et == choice else (1 - confidence) / 5)
                              for et in clef.ETIQUETAS_TAXONOMIA},
            "confidence": confidence,
        }
    return {
        "id": "resp-test",
        "model": "cloudflare/clef-flash",
        "answers": {
            "decision": decision,
            "noul_cubre": {"type": "noul", "noul": cubre},
            "noul_calidad": {"type": "noul", "noul": calidad},
            "score_actividad": {
                "type": "score",
                "score": score,
                "legend": {},
                "probabilities": {},
            },
        },
        "usage": {"input_tokens": tokens, "output_tokens": 0, "cost": cost},
    }


@pytest.fixture(autouse=True)
def entorno_clef_aislado(tmp_path, monkeypatch):
    """Sin red, sin esperas, sin bandera heredada y sin env real."""
    monkeypatch.setenv("AULASENSE_CLEF_STOP_FILE", str(tmp_path / "clef_stop.flag"))
    for nombre in (
        "OPENROUTER_API_KEY",
        "AULASENSE_CLEF_MAX_DIA",
        "AULASENSE_CLEF_MAX_HORA",
        "AULASENSE_CLEF_MODELO_FLASH",
        "AULASENSE_CLEF_MODELO",
        "AULASENSE_CLEF_UMBRAL_ESCALADO",
        "AULASENSE_CLEF_STALE_MIN",
    ):
        monkeypatch.delenv(nombre, raising=False)
    monkeypatch.setattr(clef, "_dormir", lambda _s: None)
    clef.reiniciar_parada()
    yield
    clef.reiniciar_parada()


class FakePost:
    """Registry de respuestas por modelo + captura de payloads/llamadas."""

    def __init__(self, *secuencias):
        self.secuencias = list(secuencias)  # listas por número de llamada global
        self.llamadas: list[dict] = []

    def __call__(self, url, headers=None, json=None, timeout=None, **kwargs):
        self.llamadas.append({"url": url, "headers": dict(headers or {}),
                              "payload": json, "timeout": timeout})
        idx = len(self.llamadas) - 1
        if idx < len(self.secuencias):
            item = self.secuencias[idx]
            if isinstance(item, Exception):
                raise item
            return item
        return self.secuencias[-1] if self.secuencias else FakeResp(500, text="agotado")


# ------------------------------------------------------------------------------
# Esquema exigido (sin migrar = error honesto)
# ------------------------------------------------------------------------------
def test_db_sin_migrar_f26_falla_honesto(tmp_path):
    db = _crear_db(tmp_path, con_decision=False)
    cfg = _cfg(tmp_path, db)
    with pytest.raises(clef.ErrorEsquema) as exc:
        clef.abrir_db(cfg)
    assert "F26" in str(exc.value)


def test_cli_exit_3_sin_esquema(tmp_path, monkeypatch, capsys):
    db = _crear_db(tmp_path, con_decision=False)
    monkeypatch.setenv("AULASENSE_DB", str(db))
    monkeypatch.setenv("OPENROUTER_API_KEY", CLAVE_PRUEBA)
    codigo = clef_decide.main(["--once"])
    assert codigo == 3


# ------------------------------------------------------------------------------
# Cascada
# ------------------------------------------------------------------------------
def test_flash_confianza_alta_sin_escalado(tmp_path, monkeypatch):
    db = _crear_db(tmp_path)
    cfg = _cfg(tmp_path, db)
    imagen = _imagen(db, tmp_path)
    post = FakePost(FakeResp(200, respuestas_clef(confidence=0.91, score=1.6)))
    monkeypatch.setattr(clef.requests, "post", post)

    resumen = clef.ejecutar_pasada(cfg, CLAVE_PRUEBA)
    assert resumen.llamadas == 1 and resumen.escaladas == 0
    assert post.llamadas[0]["payload"]["model"] == "cloudflare/clef-flash"
    fila = _fila(db, imagen)
    assert fila["decision_estado"] == "hecho"
    assert fila["decision_etiqueta"] == "PERSONAS_SENTADAS"
    assert fila["decision_confianza"] == pytest.approx(0.91)
    assert fila["decision_cubre"] == pytest.approx(0.95)
    assert fila["decision_calidad"] == pytest.approx(0.9)
    assert fila["decision_actividad"] == "media"  # score 1.6 -> nivel redondeado 2
    assert fila["decision_modelo"] == "cloudflare/clef-flash"
    assert fila["decision_tokens"] == 520
    assert fila["decision_ts_ms"] is not None
    # El state NO lleva la imagen en el nivel flash y usa descripcion+etiquetas.
    state = post.llamadas[0]["payload"]["state"]
    assert isinstance(state, dict) and "descripcion" in state
    assert "base64" not in json.dumps(post.llamadas[0]["payload"])
    # Auditoría en clef_uso (exito=1, sin 'tope').
    con = sqlite3.connect(db)
    filas = con.execute("SELECT modelo, tokens, coste_usd, exito, detalle FROM clef_uso").fetchall()
    con.close()
    assert len(filas) == 1
    assert filas[0] == ("cloudflare/clef-flash", 520, pytest.approx(0.00001), 1, "flash")


def test_score_actividad_mapa_niveles(tmp_path, monkeypatch):
    db = _crear_db(tmp_path)
    cfg = _cfg(tmp_path, db)
    for valor, esperado in ((0.2, "ninguna"), (1.4, "baja"), (2.5, "media"), (3.0, "alta")):
        imagen = _imagen(db, tmp_path, hash_sha256=f"h-score-{esperado}")
        post = FakePost(FakeResp(200, respuestas_clef(confidence=0.9, score=valor)))
        monkeypatch.setattr(clef.requests, "post", post)
        clef.ejecutar_pasada(cfg, CLAVE_PRUEBA)
        assert _fila(db, imagen)["decision_actividad"] == esperado


def test_conf_baja_escala_a_clef_con_imagen(tmp_path, monkeypatch):
    db = _crear_db(tmp_path)
    cfg = _cfg(tmp_path, db)
    imagen = _imagen(db, tmp_path)
    post = FakePost(
        FakeResp(200, respuestas_clef(confidence=0.31)),
        FakeResp(200, respuestas_clef(choice="CIRCULACION", confidence=0.87, score=2.1)),
    )
    monkeypatch.setattr(clef.requests, "post", post)

    resumen = clef.ejecutar_pasada(cfg, CLAVE_PRUEBA)
    assert resumen.llamadas == 2 and resumen.escaladas == 1 and resumen.hechas == 1
    payload2 = post.llamadas[1]["payload"]
    assert payload2["model"] == "cloudflare/clef"
    # La imagen viaja en el NIVEL SUPERIOR del state (primitivas.md:203-236).
    assert isinstance(payload2["state"], list)
    parte_img = payload2["state"][1]
    assert parte_img["type"] == "image_url"
    assert parte_img["image_url"]["url"].startswith("data:image/jpeg;base64,")
    fila = _fila(db, imagen)
    assert fila["decision_etiqueta"] == "CIRCULACION"
    assert fila["decision_modelo"] == "cloudflare/clef"
    assert fila["decision_confianza"] == pytest.approx(0.87)
    con = sqlite3.connect(db)
    detalle = con.execute("SELECT detalle FROM clef_uso ORDER BY id DESC LIMIT 1").fetchone()[0]
    con.close()
    assert detalle == "escalado"


def test_umbral_escalado_configurable(tmp_path, monkeypatch):
    db = _crear_db(tmp_path)
    cfg = _cfg(tmp_path, db, umbral_escaldado=0.95)
    _imagen(db, tmp_path)
    post = FakePost(FakeResp(200, respuestas_clef(confidence=0.91)))
    monkeypatch.setattr(clef.requests, "post", post)
    clef.ejecutar_pasada(cfg, CLAVE_PRUEBA)
    assert len(post.llamadas) == 2  # con umbral 0.95, 0.91 escala


def test_refusal_flash_escala_refusal_final_fail_closed(tmp_path, monkeypatch):
    db = _crear_db(tmp_path)
    cfg = _cfg(tmp_path, db)
    imagen = _imagen(db, tmp_path)
    post = FakePost(
        FakeResp(200, respuestas_clef(refusal=True)),
        FakeResp(200, respuestas_clef(refusal=True)),
    )
    monkeypatch.setattr(clef.requests, "post", post)

    resumen = clef.ejecutar_pasada(cfg, CLAVE_PRUEBA)
    assert resumen.errores == 1 and resumen.hechas == 0
    fila = _fila(db, imagen)
    assert fila["decision_estado"] == "error"  # refusal != "no": a revisión
    assert fila["decision_etiqueta"] is None
    con = sqlite3.connect(db)
    exito, detalle = con.execute(
        "SELECT exito, detalle FROM clef_uso ORDER BY id DESC LIMIT 1"
    ).fetchone()
    con.close()
    assert exito == 0 and detalle in ("refusal", "escalado_refusal")


def test_escalado_error_api_conserva_flash_con_auditoria(tmp_path, monkeypatch):
    db = _crear_db(tmp_path)
    cfg = _cfg(tmp_path, db)
    imagen = _imagen(db, tmp_path)
    post = FakePost(
        FakeResp(200, respuestas_clef(confidence=0.42, cubre=0.55)),
        FakeResp(500, text="boom"),  # agota backoff -> error
        FakeResp(500, text="boom"),
        FakeResp(500, text="boom"),
    )
    monkeypatch.setattr(clef.requests, "post", post)
    resumen = clef.ejecutar_pasada(cfg, CLAVE_PRUEBA)
    assert resumen.hechas == 1
    fila = _fila(db, imagen)
    # La decisión flash se conserva (baja confianza -> badge por cubre=0.55 < 0.7)
    assert fila["decision_estado"] == "hecho"
    assert fila["decision_etiqueta"] == "PERSONAS_SENTADAS"
    assert fila["decision_cubre"] == pytest.approx(0.55)
    con = sqlite3.connect(db)
    detalle = con.execute("SELECT detalle FROM clef_uso ORDER BY id DESC LIMIT 1").fetchone()[0]
    con.close()
    assert detalle == "escalado_fallo"


def test_flash_error_api_deja_error_reintetable(tmp_path, monkeypatch):
    db = _crear_db(tmp_path)
    cfg = _cfg(tmp_path, db)
    imagen = _imagen(db, tmp_path)
    post = FakePost(*([FakeResp(503, text="caido")] * 3))
    monkeypatch.setattr(clef.requests, "post", post)
    resumen = clef.ejecutar_pasada(cfg, CLAVE_PRUEBA)
    assert resumen.errores == 1
    assert _fila(db, imagen)["decision_estado"] == "error"


def test_choice_fuera_de_taxonomia_fail_closed(tmp_path, monkeypatch):
    db = _crear_db(tmp_path)
    cfg = _cfg(tmp_path, db)
    imagen = _imagen(db, tmp_path)
    post = FakePost(FakeResp(200, respuestas_clef(choice="GATO_EN_PIZARRA")))
    monkeypatch.setattr(clef.requests, "post", post)
    clef.ejecutar_pasada(cfg, CLAVE_PRUEBA)
    assert _fila(db, imagen)["decision_estado"] == "error"


# ------------------------------------------------------------------------------
# IMAGEN_DEGRADADA local (cero llamadas) y dedupe
# ------------------------------------------------------------------------------
def test_jpeg_degradada_local_sin_llamada(tmp_path, monkeypatch):
    db = _crear_db(tmp_path)
    cfg = _cfg(tmp_path, db)
    imagen = _imagen(
        db, tmp_path, hash_sha256="rota-1", contenido=b"\x00\x01\x02sin magic jpeg"
    )
    post = FakePost(FakeResp(200, respuestas_clef()))
    monkeypatch.setattr(clef.requests, "post", post)

    resumen = clef.ejecutar_pasada(cfg, CLAVE_PRUEBA)
    assert post.llamadas == []  # 0 llamadas de API
    assert resumen.degradadas == 1 and resumen.hechas == 1
    fila = _fila(db, imagen)
    assert fila["decision_estado"] == "hecho"
    assert fila["decision_etiqueta"] == "IMAGEN_DEGRADADA"
    assert fila["decision_modelo"] == "local"
    assert fila["decision_tokens"] == 0
    con = sqlite3.connect(db)
    modelo, exito, detalle = con.execute(
        "SELECT modelo, exito, detalle FROM clef_uso ORDER BY id DESC LIMIT 1"
    ).fetchone()
    dia, hora = clef.contar_uso(con, clef.ahora_ms())
    con.close()
    assert modelo == "local" and exito == 1 and detalle.startswith("local_sin_llamada")
    # y NO consume presupuesto (regla 16): los contadores lo ignoran
    assert (dia, hora) == (0, 0)


def test_dedupe_hash_ya_decidido_omitido(tmp_path, monkeypatch):
    db = _crear_db(tmp_path, hash_unico=False)  # DB heredada sin índice único
    cfg = _cfg(tmp_path, db)
    primera = _imagen(db, tmp_path, hash_sha256="dup-1")
    segunda = _imagen(db, tmp_path, hash_sha256="dup-1",
                      ruta="imagenes/dup-2.jpg")
    con = sqlite3.connect(db)
    con.execute(
        "UPDATE imagenes SET decision_estado = 'hecho', decision_etiqueta = "
        "'PERSONAS_SENTADAS' WHERE id = ?", (primera,)
    )
    con.commit()
    con.close()
    post = FakePost(FakeResp(200, respuestas_clef()))
    monkeypatch.setattr(clef.requests, "post", post)

    resumen = clef.ejecutar_pasada(cfg, CLAVE_PRUEBA)
    assert post.llamadas == []
    assert resumen.omitidas == 1
    assert _fila(db, segunda)["decision_estado"] == "omitido"


def test_dedupe_intra_pasada(tmp_path, monkeypatch):
    db = _crear_db(tmp_path, hash_unico=False)
    cfg = _cfg(tmp_path, db)
    a = _imagen(db, tmp_path, hash_sha256="gem", ruta="imagenes/a.jpg")
    b = _imagen(db, tmp_path, hash_sha256="gem", ruta="imagenes/b.jpg")
    post = FakePost(
        FakeResp(200, respuestas_clef()),
    )
    monkeypatch.setattr(clef.requests, "post", post)
    resumen = clef.ejecutar_pasada(cfg, CLAVE_PRUEBA)
    assert len(post.llamadas) == 1
    estados = {_fila(db, a)["decision_estado"], _fila(db, b)["decision_estado"]}
    assert estados == {"hecho", "omitido"}


# ------------------------------------------------------------------------------
# Filtros de la cola
# ------------------------------------------------------------------------------
def test_cola_solo_vision_hecho_decision_pendiente(tmp_path, monkeypatch):
    db = _crear_db(tmp_path)
    cfg = _cfg(tmp_path, db)
    sin_describir = _imagen(db, tmp_path, hash_sha256="v-pdte", vision_estado="pendiente")
    ya_decidida = _imagen(db, tmp_path, hash_sha256="v-hecha", decision_estado="hecho")
    purgada = _imagen(db, tmp_path, hash_sha256="v-purg", purgada=1)
    post = FakePost(FakeResp(200, respuestas_clef()))
    monkeypatch.setattr(clef.requests, "post", post)
    resumen = clef.ejecutar_pasada(cfg, CLAVE_PRUEBA)
    assert post.llamadas == []  # nada elegible
    assert resumen.procesadas == 0
    assert _fila(db, sin_describir)["decision_estado"] == "pendiente"
    assert _fila(db, ya_decidida)["decision_estado"] == "hecho"
    assert _fila(db, purgada)["decision_estado"] == "pendiente"


# ------------------------------------------------------------------------------
# Rate limits (clef_uso) — topes día/hora
# ------------------------------------------------------------------------------
def _sembrar_uso(db: Path, n: int, *, modelo: str = "cloudflare/clef-flash",
                 detalle: str | None = None, edad_ms: int = 0):
    con = sqlite3.connect(db)
    ahora = clef.ahora_ms() - edad_ms
    for i in range(n):
        con.execute(
            "INSERT INTO clef_uso (ts_ms, device_id, imagen_id, modelo, tokens, "
            "coste_usd, exito, detalle) VALUES (?, NULL, NULL, ?, NULL, NULL, 1, ?)",
            (ahora - i, modelo, detalle),
        )
    con.commit()
    con.close()


def test_tope_hora_detiene_sin_llamar(tmp_path, monkeypatch):
    db = _crear_db(tmp_path)
    cfg = _cfg(tmp_path, db, max_hora=2)
    for i in range(5):
        _imagen(db, tmp_path, hash_sha256=f"h{i}")
    _sembrar_uso(db, 2)
    post = FakePost(FakeResp(200, respuestas_clef()))
    monkeypatch.setattr(clef.requests, "post", post)
    resumen = clef.ejecutar_pasada(cfg, CLAVE_PRUEBA)
    assert resumen.tope == "hora" and post.llamadas == []
    # la fila 'tope' registrada no cuenta para el tope (no hubo llamada)
    con = sqlite3.connect(db)
    dia, hora = clef.contar_uso(con, clef.ahora_ms())
    con.close()
    assert (dia, hora) == (2, 2)


def test_tope_dia_detiene_y_no_cuenta_tope_rows(tmp_path, monkeypatch):
    db = _crear_db(tmp_path)
    cfg = _cfg(tmp_path, db, max_dia=3, max_hora=100)
    _imagen(db, tmp_path, hash_sha256="x")
    _sembrar_uso(db, 3, edad_ms=60_000)  # dentro de la ventana de día también
    post = FakePost(FakeResp(200, respuestas_clef()))
    monkeypatch.setattr(clef.requests, "post", post)
    resumen = clef.ejecutar_pasada(cfg, CLAVE_PRUEBA)
    assert resumen.tope == "dia" and post.llamadas == []


def test_a_mitad_de_pasada_se_para_por_tope(tmp_path, monkeypatch):
    db = _crear_db(tmp_path)
    cfg = _cfg(tmp_path, db, max_hora=1)
    ids = [_imagen(db, tmp_path, hash_sha256=f"m{i}") for i in range(3)]
    post = FakePost(FakeResp(200, respuestas_clef()))
    monkeypatch.setattr(clef.requests, "post", post)
    resumen = clef.ejecutar_pasada(cfg, CLAVE_PRUEBA)
    assert len(post.llamadas) == 1 and resumen.tope == "hora"
    # la segunda queda reclamada?? NO: el tope se comprueba ANTES del claim de
    # cada fila; las no procesadas siguen 'pendiente'
    assert _fila(db, ids[0])["decision_estado"] == "hecho"
    assert _fila(db, ids[1])["decision_estado"] == "pendiente"


# ------------------------------------------------------------------------------
# Backoff y errores HTTP
# ------------------------------------------------------------------------------
def test_429_respeta_retry_after(tmp_path, monkeypatch):
    db = _crear_db(tmp_path)
    cfg = _cfg(tmp_path, db)
    _imagen(db, tmp_path)
    esperas: list[float] = []
    monkeypatch.setattr(clef, "_dormir", lambda s: esperas.append(float(s)))
    post = FakePost(
        FakeResp(429, headers={"Retry-After": "7"}, text="calma"),
        FakeResp(200, respuestas_clef()),
    )
    monkeypatch.setattr(clef.requests, "post", post)
    resumen = clef.ejecutar_pasada(cfg, CLAVE_PRUEBA)
    assert esperas and esperas[0] == pytest.approx(7.0)
    assert resumen.hechas == 1


def test_401_fail_closed_sin_reintento(tmp_path, monkeypatch):
    db = _crear_db(tmp_path)
    cfg = _cfg(tmp_path, db)
    imagen = _imagen(db, tmp_path)
    esperas: list[float] = []
    monkeypatch.setattr(clef, "_dormir", lambda s: esperas.append(float(s)))
    post = FakePost(FakeResp(401, text="unauthorized"))
    monkeypatch.setattr(clef.requests, "post", post)
    resumen = clef.ejecutar_pasada(cfg, CLAVE_PRUEBA)
    assert len(post.llamadas) == 1  # 401 no se reintenta
    assert esperas == []
    assert _fila(db, imagen)["decision_estado"] == "error"


def test_red_exception_reintenta_y_falla_honesto(tmp_path, monkeypatch):
    db = _crear_db(tmp_path)
    cfg = _cfg(tmp_path, db)
    imagen = _imagen(db, tmp_path)
    boom = clef.requests.ConnectionError("sin red")
    post = FakePost(boom, boom, boom)
    monkeypatch.setattr(clef.requests, "post", post)
    resumen = clef.ejecutar_pasada(cfg, CLAVE_PRUEBA)
    assert len(post.llamadas) == 3  # 1 + 2 reintentos
    assert _fila(db, imagen)["decision_estado"] == "error"


# ------------------------------------------------------------------------------
# Claim atómico / stale
# ------------------------------------------------------------------------------
def test_claim_atomico_entre_dos_hilos(tmp_path):
    db = _crear_db(tmp_path)
    cfg = _cfg(tmp_path, db)
    imagen = _imagen(db, tmp_path)
    resultados: list[bool] = []
    barrera = threading.Barrier(2)
    lock = threading.Lock()

    def competir():
        con = clef.abrir_db(cfg)
        try:
            barrera.wait(timeout=5)
            ok = clef.reclamar_decision(con, imagen)
            with lock:
                resultados.append(ok)
        finally:
            con.close()

    hilos = [threading.Thread(target=competir) for _ in range(2)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(timeout=10)
    assert resultados.count(True) == 1
    assert resultados.count(False) == 1
    assert _fila(db, imagen)["decision_estado"] == "procesando"


def test_segunda_pasada_no_reprocesa_en_vuelo(tmp_path, monkeypatch):
    """Una fila 'procesando' con heartbeat fresco NO se libera ni se re-llama."""
    db = _crear_db(tmp_path)
    cfg = _cfg(tmp_path, db)
    imagen = _imagen(db, tmp_path)
    con = sqlite3.connect(db)
    con.execute(
        "UPDATE imagenes SET decision_estado = 'procesando', decision_ts_ms = ? "
        "WHERE id = ?", (clef.ahora_ms(), imagen)
    )
    con.commit()
    con.close()
    post = FakePost(FakeResp(200, respuestas_clef()))
    monkeypatch.setattr(clef.requests, "post", post)
    clef.ejecutar_pasada(cfg, CLAVE_PRUEBA)
    assert post.llamadas == []
    assert _fila(db, imagen)["decision_estado"] == "procesando"


def test_stale_se_devuelve_a_pendiente(tmp_path):
    db = _crear_db(tmp_path)
    con = sqlite3.connect(db)
    huérfana = _imagen(db, tmp_path, hash_sha256="stale")
    en_vuelo = _imagen(db, tmp_path, hash_sha256="vivo")
    ahora = clef.ahora_ms()
    con.execute(
        "UPDATE imagenes SET decision_estado='procesando', decision_ts_ms=? WHERE id=?",
        (ahora - 40 * 60_000, huérfana),
    )
    con.execute(
        "UPDATE imagenes SET decision_estado='procesando', decision_ts_ms=? WHERE id=?",
        (ahora - 60_000, en_vuelo),
    )
    con.commit()
    liberadas = clef.liberar_decision_stale(con, ahora=ahora)
    con.close()
    assert liberadas == 1
    assert _fila(db, huérfana)["decision_estado"] == "pendiente"
    assert _fila(db, en_vuelo)["decision_estado"] == "procesando"


def test_archivo_faltante_error_reintetable(tmp_path, monkeypatch):
    db = _crear_db(tmp_path)
    cfg = _cfg(tmp_path, db)
    imagen = _imagen(db, tmp_path, hash_sha256="fantasma")
    (tmp_path / f"imagenes/{DEVID}/fantasma.jpg").unlink()
    post = FakePost(FakeResp(200, respuestas_clef()))
    monkeypatch.setattr(clef.requests, "post", post)
    resumen = clef.ejecutar_pasada(cfg, CLAVE_PRUEBA)
    assert resumen.errores == 1 and post.llamadas == []
    assert _fila(db, imagen)["decision_estado"] == "error"


# ------------------------------------------------------------------------------
# Interrupción
# ------------------------------------------------------------------------------
def test_bandera_stop_detiene_y_se_consume(tmp_path, monkeypatch):
    db = _crear_db(tmp_path)
    cfg = _cfg(tmp_path, db)
    _imagen(db, tmp_path)
    clef.escribir_bandera(cfg)
    post = FakePost(FakeResp(200, respuestas_clef()))
    monkeypatch.setattr(clef.requests, "post", post)
    resumen = clef.ejecutar_pasada(cfg, CLAVE_PRUEBA)
    assert resumen.parada == "bandera" and post.llamadas == []
    assert not cfg.bandera_stop.exists()  # consumida


def test_cli_stop_escribe_bandera(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AULASENSE_CLEF_STOP_FILE", str(tmp_path / "sub" / "stop.flag"))
    codigo = clef_decide.main(["--stop"])
    assert codigo == 0
    assert (tmp_path / "sub" / "stop.flag").exists()


# ------------------------------------------------------------------------------
# Paso 0 (regla 16)
# ------------------------------------------------------------------------------
def test_paso0_mide_usage_y_no_imprime_key(tmp_path, monkeypatch, capsys):
    db = _crear_db(tmp_path)
    cfg = _cfg(tmp_path, db)
    salida = tmp_path / "paso0.json"
    post = FakePost(FakeResp(200, respuestas_clef(tokens=433, cost=0.000012)))
    monkeypatch.setattr(clef.requests, "post", post)
    resultado = clef.paso0(cfg, CLAVE_PRUEBA, salida=salida)
    assert resultado.ok and resultado.tokens == 433
    assert resultado.coste_usd == pytest.approx(0.000012)
    assert resultado.origen_state == "simulado"
    registro = json.loads(salida.read_text(encoding="utf-8"))
    assert CLAVE_PRUEBA not in salida.read_text(encoding="utf-8")
    assert registro["tokens"] == 433 and registro["origen_state"] == "simulado"
    # no escribe BD (es medición, no pasada): cola intacta
    assert not sqlite3.connect(db).execute("SELECT COUNT(*) FROM clef_uso").fetchone()[0]


def test_cli_paso0_salida_solo_metricas(tmp_path, monkeypatch, capsys):
    db = _crear_db(tmp_path)
    monkeypatch.setenv("AULASENSE_DB", str(db))
    monkeypatch.setenv("OPENROUTER_API_KEY", CLAVE_PRUEBA)
    post = FakePost(FakeResp(200, respuestas_clef(tokens=500, cost=0.00001)))
    monkeypatch.setattr(clef.requests, "post", post)
    salida = tmp_path / "paso0-cli.json"
    codigo = clef_decide.main(["--paso0", "--salida", str(salida)])
    assert codigo == 0
    texto = capsys.readouterr().out
    assert "500" in texto
    assert CLAVE_PRUEBA not in texto
    assert "sk-or" not in texto


def test_paso0_sin_key_error_config(tmp_path):
    db = _crear_db(tmp_path)
    cfg = _cfg(tmp_path, db)
    with pytest.raises(clef.ErrorConfiguracion):
        clef.paso0(cfg, "")


# ------------------------------------------------------------------------------
# CLI --once con key: cola, pendientes JSON
# ------------------------------------------------------------------------------
def test_cli_once_end_to_end_mockeado(tmp_path, monkeypatch, capsys):
    db = _crear_db(tmp_path)
    monkeypatch.setenv("AULASENSE_DB", str(db))
    monkeypatch.setenv("AULASENSE_DATOS_DIR", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", CLAVE_PRUEBA)
    _imagen(db, tmp_path, hash_sha256="e2e")
    post = FakePost(FakeResp(200, respuestas_clef()))
    monkeypatch.setattr(clef.requests, "post", post)
    assert clef_decide.main(["--once"]) == 0
    assert len(post.llamadas) == 1
    stderr = capsys.readouterr().err
    assert CLAVE_PRUEBA not in stderr and "sk-or" not in stderr
    # decision_pending sin key tampoco llama
    assert clef_decide.main(["decision_pending"]) == 0


def test_sin_key_idle_honesto(tmp_path, monkeypatch):
    db = _crear_db(tmp_path)
    cfg = _cfg(tmp_path, db)
    _imagen(db, tmp_path)
    post = FakePost(FakeResp(200, respuestas_clef()))
    monkeypatch.setattr(clef.requests, "post", post)
    resumen = clef.ejecutar_pasada(cfg, "")
    assert post.llamadas == []
    assert resumen.procesadas == 0  # idle honesto (regla 13)


# ------------------------------------------------------------------------------
# Request shape (primitivas.md)
# ------------------------------------------------------------------------------
def test_shape_del_request(tmp_path):
    cfg = _cfg(tmp_path, _crear_db(tmp_path))
    state = clef.construir_state_textual("desc", "et1, et2")
    payload = clef.construir_payload(cfg, state)
    assert payload["model"] == "cloudflare/clef-flash"
    preguntas = payload["questions"]
    assert set(preguntas) == {"decision", "noul_cubre", "noul_calidad", "score_actividad"}
    assert preguntas["decision"]["type"] == "choice"
    # taxonomia EXACTA SPEC §16.6: 6 opciones, sin "other"
    assert set(preguntas["decision"]["criteria"]) == {
        "SIN_PERSONAS_VISIBLES", "PERSONAS_SENTADAS", "CIRCULACION",
        "PRESENTACION_AL_FRENTE", "AGLOMERACION_OTRA", "IMAGEN_DEGRADADA",
    }
    assert preguntas["noul_cubre"]["type"] == "noul"
    assert preguntas["score_actividad"]["type"] == "score"
    assert len(preguntas["score_actividad"]["criteria"]) == 4
    # con imagen el state es list -> modelo clef
    state_img = clef.construir_state_con_imagen(state, "data:image/jpeg;base64,AA==")
    payload2 = clef.construir_payload(cfg, state_img)
    assert payload2["model"] == "cloudflare/clef"
    assert state_img[1]["type"] == "image_url"


def test_usage_alternativo_tokens_plano():
    cuerpo = respuestas_clef(tokens=99)
    cuerpo["usage"] = {"tokens": 99, "cost": 0.5}
    resultado = clef.parsear_respuesta(cuerpo, "cloudflare/clef-flash")
    assert resultado.tokens == 99 and resultado.coste_usd == pytest.approx(0.5)
