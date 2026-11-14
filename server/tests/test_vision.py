"""Tests del worker de descripcion IA de imagenes (SPEC §7.3, fase F25-d2).

Cubren ``server/vision/cliente.py`` + ``tools/vision_describe.py``. La API de
vision SIEMPRE se mockea (monkeypatch de ``requests.post``) y los sleeps del
backoff tambien (``cliente._dormir``): ningun test llama a la red ni espera.

DB temporal propia (``tmp_path``) con DDL inline que replica SPEC §8.2 (base
F24 de ``imagenes`` + columnas ``vision_*`` + unique index de hash + tabla
``vision_uso``). El DDL canonico vive en ``server/ingesta/esquema.py`` (F25-d1)
y este worker NO lo aplica: si la DB no esta migrada debe fallar honestamente.

Regla de secretos: las claves de prueba son literales falsos (``sk-prueba-*`` /
``sk-sp-prueba-*``); se afirma que NUNCA aparecen en la salida del CLI.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import signal
import sqlite3
import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]  # aulasense/

# El paquete ``server.vision`` se importa como modulo de primer nivel (patron
# conftest: el proyecto no es paquete instalable).
_SERVER = ROOT / "server"
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))

from vision import cliente  # noqa: E402  (tras ajustar sys.path)


def _cargar(nombre: str, ruta: Path):
    """Carga un modulo por ruta (tools/ no es paquete instalable)."""
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nombre] = modulo
    spec.loader.exec_module(modulo)
    return modulo


vision_describe = _cargar("vision_describe_tool", ROOT / "tools" / "vision_describe.py")

CLAVE_PRUEBA = "sk-prueba-f25d2-no-real"
CLAVE_TP_PRUEBA = "sk-sp-prueba-f25d2-no-real"

# --- DDL inline (replica de SPEC §8.1/§8.2; NO se toca esquema.py) -----------
DDL_BASE = """
CREATE TABLE imagenes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL,
  ts_ms INTEGER NOT NULL,
  ruta TEXT NOT NULL,
  hash_sha256 TEXT NOT NULL,
  tamano_bytes INTEGER NOT NULL,
  ancho INTEGER,
  alto INTEGER,
  evento_id INTEGER,
  ia_persona INTEGER,
  ia_score REAL,
  imagen_purgada INTEGER NOT NULL DEFAULT 0,
  origen TEXT NOT NULL DEFAULT 'real',
  recibido_en TEXT NOT NULL DEFAULT '2026-11-14 00:00:00'
);
CREATE TABLE evaluaciones (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL,
  ts_ms INTEGER NOT NULL,
  state TEXT NOT NULL,
  score REAL NOT NULL
);
"""

DDL_COLUMNAS_F25 = """
ALTER TABLE imagenes ADD COLUMN canal TEXT NOT NULL DEFAULT 'http';
ALTER TABLE imagenes ADD COLUMN descripcion TEXT;
ALTER TABLE imagenes ADD COLUMN etiquetas TEXT;
ALTER TABLE imagenes ADD COLUMN vision_estado TEXT NOT NULL DEFAULT 'pendiente';
ALTER TABLE imagenes ADD COLUMN vision_modelo TEXT;
ALTER TABLE imagenes ADD COLUMN vision_ts_ms INTEGER;
ALTER TABLE imagenes ADD COLUMN vision_tokens INTEGER;
"""

DDL_VISION_USO = """
CREATE TABLE vision_uso (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_ms INTEGER NOT NULL,
  imagen_id INTEGER REFERENCES imagenes(id),
  modelo TEXT NOT NULL,
  tokens_in INTEGER,
  tokens_out INTEGER,
  modo TEXT NOT NULL CHECK(modo IN ('A','B')),
  resultado TEXT NOT NULL CHECK(resultado IN ('hecho','error','tope'))
);
CREATE INDEX idx_vision_uso_ts ON vision_uso(ts_ms);
"""

COLUMNAS_SNAPSHOT = (
    "id",
    "device_id",
    "ts_ms",
    "ruta",
    "hash_sha256",
    "tamano_bytes",
    "ancho",
    "alto",
    "evento_id",
    "ia_persona",
    "ia_score",
    "imagen_purgada",
    "origen",
    "recibido_en",
    "canal",
    "descripcion",
    "etiquetas",
    "vision_estado",
    "vision_modelo",
    "vision_ts_ms",
    "vision_tokens",
)


# ------------------------------------------------------------------------------
# Fixtures y helpers
# ------------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def entorno_vision_aislado(tmp_path, monkeypatch):
    """Aisla bandera de parada, auth.json, topes y sleeps (ni red ni esperas)."""
    monkeypatch.setenv("AULASENSE_VISION_STOP_FILE", str(tmp_path / "vision_stop.flag"))
    monkeypatch.setenv(
        "AULASENSE_VISION_AUTH_JSON", str(tmp_path / "auth_inexistente.json")
    )
    for nombre in (
        "DASHSCOPE_API_KEY",
        "AULASENSE_VISION_MODELO",
        "AULASENSE_VISION_MAX_DIA",
        "AULASENSE_VISION_MAX_HORA",
        "AULASENSE_VISION_MODO",
        "AULASENSE_DB",
        "AULASENSE_DATOS_DIR",
    ):
        monkeypatch.delenv(nombre, raising=False)
    monkeypatch.setattr(cliente, "_dormir", lambda _s: None)
    cliente.reiniciar_parada()
    yield
    cliente.reiniciar_parada()


@pytest.fixture()
def dormidos(monkeypatch) -> list[float]:
    """Lista de esperas del backoff (mock de sleep: cero espera real)."""
    esperas: list[float] = []
    monkeypatch.setattr(cliente, "_dormir", lambda segundos: esperas.append(float(segundos)))
    return esperas


def _crear_db(tmp_path: Path, *, migrada: bool = True, hash_unico: bool = True) -> Path:
    """DB temporal: base F24 + (opcional) columnas F25 + unique hash + uso."""
    db = tmp_path / "aulasense.db"
    con = sqlite3.connect(db)
    try:
        con.executescript(DDL_BASE)
        if migrada:
            con.executescript(DDL_COLUMNAS_F25)
        if hash_unico:
            con.execute(
                "CREATE UNIQUE INDEX idx_img_hash_unico ON imagenes(hash_sha256)"
            )
        if migrada:
            con.executescript(DDL_VISION_USO)
        con.commit()
    finally:
        con.close()
    return db


def _imagen(
    db: Path,
    *,
    device: str = "aabbccddeeff",
    hash_sha256: str = "hash-1",
    ruta: str | None = None,
    purgada: int = 0,
    estado: str | None = None,
    ts_ms: int = 1_700_000_000_000,
) -> int:
    """Inserta una fila de ``imagenes`` y crea el archivo JPEG en datos/."""
    datos_dir = db.parent
    ruta = ruta or f"imagenes/{device}/{hash_sha256}.jpg"
    archivo = datos_dir / ruta
    archivo.parent.mkdir(parents=True, exist_ok=True)
    archivo.write_bytes(b"\xff\xd8\xff\xe0" + hash_sha256.encode() + b"\xff\xd9")

    con = sqlite3.connect(db)
    try:
        columnas = {fila[1] for fila in con.execute("PRAGMA table_info(imagenes)")}
        if "vision_estado" in columnas and estado is not None:
            cursor = con.execute(
                "INSERT INTO imagenes (device_id, ts_ms, ruta, hash_sha256, tamano_bytes, "
                "ancho, alto, imagen_purgada, vision_estado) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    device,
                    ts_ms,
                    ruta,
                    hash_sha256,
                    archivo.stat().st_size,
                    320,
                    240,
                    purgada,
                    estado,
                ),
            )
        else:
            cursor = con.execute(
                "INSERT INTO imagenes (device_id, ts_ms, ruta, hash_sha256, tamano_bytes, "
                "ancho, alto, imagen_purgada) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    device,
                    ts_ms,
                    ruta,
                    hash_sha256,
                    archivo.stat().st_size,
                    320,
                    240,
                    purgada,
                ),
            )
        con.commit()
        return int(cursor.lastrowid)
    finally:
        con.close()


def _sembrar_uso(
    db: Path,
    cantidad: int,
    *,
    ts_ms: int | None = None,
    resultado: str = "hecho",
    modo: str = "B",
    modelo: str = "qwen3.8-flash",
) -> None:
    """Filas previas en ``vision_uso`` (contador persistente)."""
    cuando = cliente.ahora_ms() if ts_ms is None else ts_ms
    con = sqlite3.connect(db)
    try:
        con.executemany(
            "INSERT INTO vision_uso (ts_ms, imagen_id, modelo, tokens_in, tokens_out, modo, "
            "resultado) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(cuando, None, modelo, 300, 100, modo, resultado)] * cantidad,
        )
        con.commit()
    finally:
        con.close()


def _filas_uso(db: Path) -> list[tuple]:
    con = sqlite3.connect(db)
    try:
        return con.execute(
            "SELECT imagen_id, modelo, tokens_in, tokens_out, modo, resultado "
            "FROM vision_uso ORDER BY id"
        ).fetchall()
    finally:
        con.close()


def _fila_imagen(db: Path, imagen_id: int) -> dict:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        fila = con.execute(
            f"SELECT {', '.join(COLUMNAS_SNAPSHOT)} FROM imagenes WHERE id = ?",
            (imagen_id,),
        ).fetchone()
        assert fila is not None
        return dict(fila)
    finally:
        con.close()


def _evaluaciones(db: Path) -> list[tuple]:
    """Snapshot de ``evaluaciones`` (el worker NUNCA debe tocarla)."""
    con = sqlite3.connect(db)
    try:
        return con.execute("SELECT * FROM evaluaciones").fetchall()
    finally:
        con.close()


def _cuerpo(
    texto: str = "Una persona camina por el aula.\nETIQUETAS: persona, aula, movimiento",
    tokens_in: int = 380,
    tokens_out: int = 120,
) -> dict:
    return {
        "choices": [{"message": {"content": texto}}],
        "usage": {
            "prompt_tokens": tokens_in,
            "completion_tokens": tokens_out,
            "total_tokens": tokens_in + tokens_out,
        },
    }


class RespuestaFake:
    """Respuesta HTTP minima (status + json + headers)."""

    def __init__(self, status: int = 200, cuerpo: dict | None = None, headers=None):
        self.status_code = status
        self._cuerpo = cuerpo
        self.headers = headers or {}
        self.text = ""

    def json(self) -> dict:
        if self._cuerpo is None:
            raise ValueError("respuesta sin JSON (test)")
        return self._cuerpo


def _instalar_api(monkeypatch, respuestas: list) -> list[dict]:
    """Mock de ``requests.post``: devuelve la secuencia y registra las llamadas."""
    llamadas: list[dict] = []
    cola = list(respuestas)

    def _post(url, headers=None, json=None, timeout=None):  # noqa: A002 (firma requests)
        llamadas.append(
            {"url": url, "headers": headers, "payload": json, "timeout": timeout}
        )
        respuesta = cola.pop(0) if len(cola) > 1 else cola[0]
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta

    monkeypatch.setattr(requests, "post", _post)
    return llamadas


def _config(db: Path, **cambios):
    cfg = cliente.construir_config(db=str(db), datos=str(db.parent))
    return dataclasses.replace(cfg, **cambios) if cambios else cfg


# ------------------------------------------------------------------------------
# Resolucion de la key (nunca se imprime)
# ------------------------------------------------------------------------------
def test_clave_env_tiene_precedencia_y_no_se_imprime(tmp_path, monkeypatch):
    """env DASHSCOPE_API_KEY gana a claves/.env; solo se publica tipo/longitud."""
    claves_env = tmp_path / "claves" / ".env"
    claves_env.parent.mkdir(parents=True, exist_ok=True)
    claves_env.write_text(f"DASHSCOPE_API_KEY={CLAVE_TP_PRUEBA}\n", encoding="utf-8")
    monkeypatch.setenv("DASHSCOPE_API_KEY", CLAVE_PRUEBA)

    clave = cliente.resolver_clave(
        env_path=tmp_path / "raiz.env", claves_path=claves_env
    )

    assert clave.tipo == "pay_as_you_go"
    assert clave.origen == "env"
    assert clave.base_url == cliente.BASE_PAYG
    assert CLAVE_PRUEBA not in clave.descripcion_segura
    assert "longitud=" in clave.descripcion_segura


def test_clave_claves_env_sk_sp_usa_base_token_plan(tmp_path, monkeypatch):
    """key sk-sp- desde claves/.env -> clasificada Token Plan y base TP."""
    claves_env = tmp_path / "claves" / ".env"
    claves_env.parent.mkdir(parents=True, exist_ok=True)
    claves_env.write_text(f"DASHSCOPE_API_KEY={CLAVE_TP_PRUEBA}\n", encoding="utf-8")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    clave = cliente.resolver_clave(
        env_path=tmp_path / "raiz.env", claves_path=claves_env
    )

    assert clave.tipo == "token_plan"
    assert clave.origen == "claves/.env"
    assert clave.base_url == cliente.BASE_TOKEN_PLAN


def test_clave_desde_auth_json_proveedor_token_plan(tmp_path, monkeypatch):
    """Sin env ni claves: se lee auth.json (provider alibaba-token-plan)."""
    auth = tmp_path / "auth.json"
    auth.write_text(
        '{"alibaba-token-plan": {"type": "api", "key": "' + CLAVE_TP_PRUEBA + '"}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("AULASENSE_VISION_AUTH_JSON", str(auth))
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    clave = cliente.resolver_clave(
        env_path=tmp_path / "raiz.env", claves_path=tmp_path / "claves" / ".env"
    )

    assert clave.tipo == "token_plan"
    assert clave.origen == "auth.json:alibaba-token-plan"
    assert clave.valor == CLAVE_TP_PRUEBA  # se usa, pero no se imprime
    assert CLAVE_TP_PRUEBA not in clave.descripcion_segura


# ------------------------------------------------------------------------------
# Guarda ToS del Token Plan y idle honesto
# ------------------------------------------------------------------------------
def test_loop_rechaza_sk_sp_con_exit_code(tmp_path, monkeypatch, capsys):
    """Modo A daemon con sk-sp-: RECHAZO (exit != 0), log ToS y cero llamadas."""
    db = _crear_db(tmp_path)
    imagen_id = _imagen(db)
    monkeypatch.setenv("DASHSCOPE_API_KEY", CLAVE_TP_PRUEBA)
    llamadas = _instalar_api(monkeypatch, [RespuestaFake(200, _cuerpo())])

    codigo = vision_describe.main(["--loop", "--db", str(db)])
    salida = capsys.readouterr()

    assert codigo != 0
    assert "ToS" in salida.err
    assert "sk-sp-" in salida.err  # cita el prefijo, no la key
    assert CLAVE_TP_PRUEBA not in salida.err + salida.out
    assert llamadas == []
    assert _fila_imagen(db, imagen_id)["vision_estado"] == "pendiente"
    assert _filas_uso(db) == []


def test_loop_sin_clave_es_idle_honesto(tmp_path, capsys, monkeypatch):
    """Key vacia: el daemon NO arranca, exit 0 y el sistema sigue sin IA (regla 13)."""
    db = _crear_db(tmp_path)
    imagen_id = _imagen(db)

    def _no_llamar(*_args, **_kwargs):
        raise AssertionError("sin key no debe haber llamadas a la API")

    monkeypatch.setattr(requests, "post", _no_llamar)

    codigo = vision_describe.main(["--loop", "--db", str(db)])
    salida = capsys.readouterr()

    assert codigo == 0
    assert "IDLE" in salida.err
    assert _fila_imagen(db, imagen_id)["vision_estado"] == "pendiente"


# ------------------------------------------------------------------------------
# Esquema: DB sin migrar -> error honesto
# ------------------------------------------------------------------------------
def test_once_modo_b_admite_key_token_plan_y_usa_base_tp(tmp_path, monkeypatch):
    """Modo B (default) con sk-sp-: permitido por D-F25-5 y usa la base TP."""
    db = _crear_db(tmp_path)
    imagen_id = _imagen(db)
    monkeypatch.setenv("DASHSCOPE_API_KEY", CLAVE_TP_PRUEBA)
    llamadas = _instalar_api(monkeypatch, [RespuestaFake(200, _cuerpo())])

    codigo = vision_describe.main(["--once", "--db", str(db)])

    assert codigo == 0
    assert llamadas[0]["url"] == cliente.BASE_TOKEN_PLAN + "/chat/completions"
    assert _fila_imagen(db, imagen_id)["vision_estado"] == "hecho"
    assert _filas_uso(db)[0][4] == "B"  # registrado como modo B


def test_cli_stop_escribe_bandera_en_tmp(tmp_path, capsys):
    """``--stop`` escribe la bandera (ruta .tmp/ configurada) y sale con 0."""
    db = _crear_db(tmp_path)
    ruta_bandera = _config(db).bandera_stop

    codigo = vision_describe.main(["--stop", "--db", str(db)])
    salida = capsys.readouterr()

    assert codigo == 0
    assert ruta_bandera.exists()
    assert "bandera de parada" in salida.out


def test_db_sin_migrar_loguea_error_y_sale_con_codigo(tmp_path, monkeypatch, capsys):
    """DB F24 sin columnas F25: exit 3 y mensaje 'DB sin migrar' (sin DDL propio)."""
    db = _crear_db(tmp_path, migrada=False, hash_unico=False)
    _imagen(db)
    monkeypatch.setenv("DASHSCOPE_API_KEY", CLAVE_PRUEBA)

    codigo = vision_describe.main(["--once", "--db", str(db)])
    salida = capsys.readouterr()

    assert codigo == 3
    assert "DB sin migrar (corre la ingesta F25-d1)" in salida.err
    con = sqlite3.connect(db)
    try:
        columnas = {fila[1] for fila in con.execute("PRAGMA table_info(imagenes)")}
    finally:
        con.close()
    assert "vision_estado" not in columnas  # el worker NO hace DDL


# ------------------------------------------------------------------------------
# Dedupe por hash
# ------------------------------------------------------------------------------
def test_dedupe_hash_una_imagen_nunca_dos_veces(tmp_path, monkeypatch):
    """Dos filas con el mismo hash: 1 sola llamada; la copia queda 'omitido'."""
    db = _crear_db(tmp_path, hash_unico=False)  # DB heredada sin unique index
    primera = _imagen(db, hash_sha256="hash-repetido", ruta="imagenes/a/x.jpg")
    copia = _imagen(
        db, device="112233445566", hash_sha256="hash-repetido", ruta="imagenes/b/x.jpg"
    )
    monkeypatch.setenv("DASHSCOPE_API_KEY", CLAVE_PRUEBA)
    llamadas = _instalar_api(monkeypatch, [RespuestaFake(200, _cuerpo())])

    codigo = vision_describe.main(["--once", "--db", str(db), "--limit", "5"])

    assert codigo == 0
    assert len(llamadas) == 1
    assert _fila_imagen(db, primera)["vision_estado"] == "hecho"
    assert _fila_imagen(db, copia)["vision_estado"] == "omitido"
    assert [f[5] for f in _filas_uso(db)] == ["hecho"]


# ------------------------------------------------------------------------------
# Rate limits persistentes (dia/hora)
# ------------------------------------------------------------------------------
def test_tope_dia_120_persistente_entre_invocaciones(tmp_path, monkeypatch, capsys):
    """Tope diario (120) con contador persistente: se para sin quemar Credits."""
    db = _crear_db(tmp_path)
    primera = _imagen(db, hash_sha256="h1", ruta="imagenes/a/1.jpg")
    segunda = _imagen(db, hash_sha256="h2", ruta="imagenes/a/2.jpg")
    # Fuera de la ventana de 1 h (dentro de la de 24 h): prueba el tope DIARIO.
    _sembrar_uso(db, 119, ts_ms=cliente.ahora_ms() - 2 * 60 * 60 * 1000)
    monkeypatch.setenv("DASHSCOPE_API_KEY", CLAVE_PRUEBA)
    llamadas = _instalar_api(monkeypatch, [RespuestaFake(200, _cuerpo())])

    codigo = vision_describe.main(["--once", "--db", str(db), "--limit", "5"])
    salida = capsys.readouterr()

    assert codigo == 0
    assert len(llamadas) == 1  # solo la que cabia antes del tope
    assert "TOPE dia" in salida.err
    assert _fila_imagen(db, primera)["vision_estado"] == "hecho"
    assert _fila_imagen(db, segunda)["vision_estado"] == "pendiente"

    # Segunda invocacion (reabre la DB): el contador persiste -> 0 llamadas.
    codigo2 = vision_describe.main(["--once", "--db", str(db), "--limit", "5"])
    salida2 = capsys.readouterr()

    assert codigo2 == 0
    assert len(llamadas) == 1
    assert "TOPE dia" in salida2.err
    resultados = [f[5] for f in _filas_uso(db)]
    assert resultados.count("tope") >= 2
    assert resultados.count("hecho") == 120

    # Defaults conservadores de SPEC §7.3/D-F25-6.
    cfg = cliente.construir_config(db=str(db))
    assert cfg.max_dia == 120
    assert cfg.max_hora == 20


def test_tope_hora_20_persistente(tmp_path, monkeypatch, capsys):
    """Tope por hora (20): con 19 consumidas solo cabe 1 llamada mas."""
    db = _crear_db(tmp_path)
    primera = _imagen(db, hash_sha256="h1", ruta="imagenes/a/1.jpg")
    segunda = _imagen(db, hash_sha256="h2", ruta="imagenes/a/2.jpg")
    _sembrar_uso(db, 19)
    monkeypatch.setenv("DASHSCOPE_API_KEY", CLAVE_PRUEBA)
    llamadas = _instalar_api(monkeypatch, [RespuestaFake(200, _cuerpo())])

    codigo = vision_describe.main(["--once", "--db", str(db), "--limit", "5"])
    salida = capsys.readouterr()

    assert codigo == 0
    assert len(llamadas) == 1
    assert "TOPE hora" in salida.err
    assert _fila_imagen(db, primera)["vision_estado"] == "hecho"
    assert _fila_imagen(db, segunda)["vision_estado"] == "pendiente"


# ------------------------------------------------------------------------------
# Backoff 429 (Retry-After) y cadena de modelos con fallback
# ------------------------------------------------------------------------------
def test_429_respeta_retry_after_y_backoff_max_2(tmp_path, monkeypatch, dormidos):
    """429: 1a espera = Retry-After (11 s), 2a = 30 s; luego 'error' sin bucle."""
    db = _crear_db(tmp_path)
    imagen_id = _imagen(db)
    cfg = _config(db, fallbacks=())  # cadena de un solo modelo
    clave = cliente.Clave(CLAVE_PRUEBA, "pay_as_you_go", "test")
    _instalar_api(
        monkeypatch,
        [
            RespuestaFake(429, _cuerpo(), headers={"Retry-After": "11"}),
            RespuestaFake(429, _cuerpo()),
            RespuestaFake(429, _cuerpo()),
        ],
    )

    resumen = cliente.ejecutar_pasada(cfg, clave)

    assert resumen.errores == 1
    assert dormidos == [11.0, 30.0]  # Retry-After respetado; backoff max 2
    assert _fila_imagen(db, imagen_id)["vision_estado"] == "error"
    assert [f[5] for f in _filas_uso(db)] == ["error"]


def test_cadena_500_fallback_a_qwen36_flash(tmp_path, monkeypatch, dormidos, capsys):
    """HTTP 500 agota qwen3.8-flash (10 s/30 s) y cae a qwen3.6-flash."""
    db = _crear_db(tmp_path)
    imagen_id = _imagen(db)
    monkeypatch.setenv("DASHSCOPE_API_KEY", CLAVE_PRUEBA)
    llamadas = _instalar_api(
        monkeypatch,
        [
            RespuestaFake(500),
            RespuestaFake(500),
            RespuestaFake(500),
            RespuestaFake(200, _cuerpo()),
        ],
    )

    codigo = vision_describe.main(["--once", "--db", str(db)])
    capsys.readouterr()

    assert codigo == 0
    modelos = [c["payload"]["model"] for c in llamadas]
    assert modelos == [
        "qwen3.8-flash",
        "qwen3.8-flash",
        "qwen3.8-flash",
        "qwen3.6-flash",
    ]
    assert dormidos == [10.0, 30.0]
    fila = _fila_imagen(db, imagen_id)
    assert fila["vision_estado"] == "hecho"
    assert fila["vision_modelo"] == "qwen3.6-flash"
    assert llamadas[0]["url"] == cliente.BASE_PAYG + "/chat/completions"
    # La clave va en la cabecera del request (uso legitimo) pero NUNCA en logs.
    assert llamadas[0]["headers"]["Authorization"] == f"Bearer {CLAVE_PRUEBA}"


# ------------------------------------------------------------------------------
# Escritura BD: UPDATE solo de columnas vision_*/descripcion/etiquetas
# ------------------------------------------------------------------------------
def test_update_solo_columnas_vision_y_otras_filas_intactas(tmp_path, monkeypatch):
    """Procesar una imagen no toca otras filas ni columnas ajenas (ni evaluaciones)."""
    db = _crear_db(tmp_path)
    objetivo = _imagen(db, hash_sha256="h-objetivo", ruta="imagenes/a/o.jpg")
    intocable = _imagen(
        db,
        device="112233445566",
        hash_sha256="h-intocable",
        ruta="imagenes/b/i.jpg",
        estado="hecho",
    )
    con = sqlite3.connect(db)
    try:
        con.execute(
            "UPDATE imagenes SET descripcion = 'ya descrita', etiquetas = 'x, y', "
            "vision_modelo = 'qwen3.8-flash', vision_ts_ms = 123, vision_tokens = 77 "
            "WHERE id = ?",
            (intocable,),
        )
        con.execute(
            "INSERT INTO evaluaciones (device_id, ts_ms, state, score) "
            "VALUES ('aabbccddeeff', 1, 'motion', 0.9)"
        )
        con.commit()
    finally:
        con.close()

    antes_objetivo = _fila_imagen(db, objetivo)
    antes_intocable = _fila_imagen(db, intocable)
    antes_eval = _evaluaciones(db)

    monkeypatch.setenv("DASHSCOPE_API_KEY", CLAVE_PRUEBA)
    _instalar_api(monkeypatch, [RespuestaFake(200, _cuerpo())])
    assert vision_describe.main(["--once", "--db", str(db)]) == 0

    despues_objetivo = _fila_imagen(db, objetivo)
    despues_intocable = _fila_imagen(db, intocable)
    despues_eval = _evaluaciones(db)

    # Filas contiguas y evaluaciones: byte a byte iguales.
    assert despues_intocable == antes_intocable
    assert despues_eval == antes_eval
    # Columnas NO vision de la fila procesada intactas.
    for columna in (
        "device_id",
        "ts_ms",
        "ruta",
        "hash_sha256",
        "tamano_bytes",
        "ancho",
        "alto",
        "evento_id",
        "ia_persona",
        "ia_score",
        "imagen_purgada",
        "origen",
        "recibido_en",
        "canal",
    ):
        assert despues_objetivo[columna] == antes_objetivo[columna]
    # Y las de vision SÍ cambian (solo ellas).
    assert despues_objetivo["vision_estado"] == "hecho"
    assert despues_objetivo["descripcion"] == "Una persona camina por el aula."
    assert despues_objetivo["etiquetas"] == "persona, aula, movimiento"
    assert despues_objetivo["vision_tokens"] == 500
    assert despues_objetivo["vision_ts_ms"] and despues_objetivo["vision_ts_ms"] > 0


# ------------------------------------------------------------------------------
# Error persistente -> 'error' reintetable (sin bucle)
# ------------------------------------------------------------------------------
def test_error_persistente_marca_error_y_se_reintenta_despues(
    tmp_path, monkeypatch, dormidos
):
    """Cadena agotada -> 'error'; una pasada posterior con API sana -> 'hecho'."""
    db = _crear_db(tmp_path)
    imagen_id = _imagen(db)
    cfg = _config(db, fallbacks=())
    clave = cliente.Clave(CLAVE_PRUEBA, "pay_as_you_go", "test")
    _instalar_api(monkeypatch, [RespuestaFake(500)])

    resumen = cliente.ejecutar_pasada(cfg, clave)

    assert resumen.errores == 1
    assert _fila_imagen(db, imagen_id)["vision_estado"] == "error"
    assert len(dormidos) == 2  # 10 s + 30 s, y NADA de bucle infinito

    _instalar_api(monkeypatch, [RespuestaFake(200, _cuerpo())])
    resumen2 = cliente.ejecutar_pasada(cfg, clave)

    assert resumen2.hechas == 1
    assert _fila_imagen(db, imagen_id)["vision_estado"] == "hecho"


# ------------------------------------------------------------------------------
# Salida acotada (max_tokens 200) + uso real registrado (modo B)
# ------------------------------------------------------------------------------
def test_salida_acotada_y_usage_real_modo_b(tmp_path, monkeypatch):
    """El payload lleva max_tokens=200 y la imagen inline; vision_uso guarda usage."""
    db = _crear_db(tmp_path)
    imagen_id = _imagen(db)
    monkeypatch.setenv("DASHSCOPE_API_KEY", CLAVE_PRUEBA)
    llamadas = _instalar_api(monkeypatch, [RespuestaFake(200, _cuerpo())])

    assert vision_describe.main(["--once", "--db", str(db)]) == 0

    payload = llamadas[0]["payload"]
    assert payload["max_tokens"] == 200
    assert payload["model"] == "qwen3.8-flash"
    contenido = payload["messages"][0]["content"]
    assert contenido[0]["type"] == "image_url"
    assert contenido[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "españ" in contenido[1]["text"].lower()
    assert "ETIQUETAS" in contenido[1]["text"]

    fila = _fila_imagen(db, imagen_id)
    assert fila["descripcion"] == "Una persona camina por el aula."
    assert fila["etiquetas"] == "persona, aula, movimiento"
    assert fila["vision_modelo"] == "qwen3.8-flash"
    assert fila["vision_tokens"] == 500  # 380 + 120 (usage REAL)
    uso = _filas_uso(db)
    assert len(uso) == 1
    assert uso[0] == (imagen_id, "qwen3.8-flash", 380, 120, "B", "hecho")


# ------------------------------------------------------------------------------
# Interrumpibilidad: SIGTERM y --stop
# ------------------------------------------------------------------------------
def test_sigterm_termina_la_imagen_en_curso_y_sale_limpio(tmp_path, monkeypatch, capsys):
    """SIGTERM durante la 1a llamada: esa imagen se guarda y la pasada sale limpia."""
    db = _crear_db(tmp_path)
    primera = _imagen(db, hash_sha256="h1", ruta="imagenes/a/1.jpg")
    segunda = _imagen(db, hash_sha256="h2", ruta="imagenes/a/2.jpg")
    monkeypatch.setenv("DASHSCOPE_API_KEY", CLAVE_PRUEBA)

    llamadas: list[int] = []

    def _post(url, headers=None, json=None, timeout=None):  # noqa: A002
        llamadas.append(1)
        cliente._manejar_senal(signal.SIGTERM, None)  # simula SIGTERM en plena llamada
        return RespuestaFake(200, _cuerpo())

    monkeypatch.setattr(requests, "post", _post)

    codigo = vision_describe.main(["--once", "--db", str(db), "--limit", "5"])
    salida = capsys.readouterr()

    assert codigo == 0
    assert len(llamadas) == 1
    assert _fila_imagen(db, primera)["vision_estado"] == "hecho"
    assert _fila_imagen(db, segunda)["vision_estado"] == "pendiente"
    assert "parada solicitada" in salida.err


def test_bandera_stop_corta_la_pasada_sin_llamadas(tmp_path, monkeypatch, capsys):
    """--stop deja bandera en .tmp/: la pasada la consume y sale limpia."""
    db = _crear_db(tmp_path)
    imagen_id = _imagen(db)
    cfg = _config(db)
    ruta_bandera = cliente.escribir_bandera(cfg)
    assert ruta_bandera.exists()
    monkeypatch.setenv("DASHSCOPE_API_KEY", CLAVE_PRUEBA)

    def _no_llamar(*_args, **_kwargs):
        raise AssertionError("con la bandera de parada no debe haber llamadas")

    monkeypatch.setattr(requests, "post", _no_llamar)

    codigo = vision_describe.main(["--once", "--db", str(db)])
    salida = capsys.readouterr()

    assert codigo == 0
    assert "parada solicitada" in salida.err
    assert not ruta_bandera.exists()  # bandera consumida
    assert _fila_imagen(db, imagen_id)["vision_estado"] == "pendiente"


# ------------------------------------------------------------------------------
# Modo B agente-mediado: vision_pending / vision_apply
# ------------------------------------------------------------------------------
def test_vision_pending_emite_json_sin_llamar_api(tmp_path, monkeypatch, capsys):
    """vision_pending: JSON con id/ruta/hash/modelo/prompt y cero llamadas."""
    db = _crear_db(tmp_path)
    imagen_id = _imagen(db)
    _sembrar_uso(db, 19)  # 19 de 20 por hora -> sugiere como mucho 1

    def _no_llamar(*_args, **_kwargs):
        raise AssertionError("vision_pending NO llama a la API")

    monkeypatch.setattr(requests, "post", _no_llamar)

    codigo = vision_describe.main(["vision_pending", "--db", str(db), "--limit", "5"])
    salida = capsys.readouterr()

    assert codigo == 0
    datos = json.loads(salida.out)  # stdout limpio (JSON)
    assert datos["modo"] == "B"
    assert datos["modelo"] == "qwen3.8-flash"
    assert datos["topes"] == {
        "max_dia": 120,
        "max_hora": 20,
        "usadas_dia": 19,
        "usadas_hora": 19,
    }
    assert len(datos["imagenes"]) == 1  # respeta el cupo restante
    imagen = datos["imagenes"][0]
    assert imagen["id"] == imagen_id
    assert imagen["hash_sha256"] == "hash-1"
    assert "ETIQUETAS" in imagen["prompt"]
    assert Path(imagen["ruta"]).is_file()
    assert "sk-" not in salida.out  # nunca aparece una key


def test_vision_pending_con_tope_devuelve_lista_vacia(tmp_path, capsys):
    """Con el tope diario alcanzado, vision_pending no sugiere imagenes."""
    db = _crear_db(tmp_path)
    _imagen(db)
    _sembrar_uso(db, 120)

    codigo = vision_describe.main(["vision_pending", "--db", str(db)])
    salida = capsys.readouterr()

    assert codigo == 0
    datos = json.loads(salida.out)
    assert datos["imagenes"] == []
    assert datos["motivo"] == "tope"
    assert "TOPE dia" in salida.err


def test_vision_apply_escribe_resultados_y_registra_usage(tmp_path, monkeypatch):
    """vision_apply: escribe descripcion/etiquetas/estado y vision_uso modo B."""
    db = _crear_db(tmp_path)
    primera = _imagen(db, hash_sha256="h1", ruta="imagenes/a/1.jpg")
    segunda = _imagen(db, hash_sha256="h2", ruta="imagenes/a/2.jpg")
    resultados = tmp_path / "vision_resultados.json"
    resultados.write_text(
        '{"resultados": ['
        f'{{"imagen_id": {primera}, "descripcion": "Un aula vacia con sillas.", '
        '"etiquetas": "aula, sillas, vacio", "modelo": "qwen3.8-flash", '
        '"tokens_in": 380, "tokens_out": 120}, '
        f'{{"imagen_id": {segunda}, "descripcion": "Una persona de espaldas.", '
        '"etiquetas": "persona", "tokens_in": 300, "tokens_out": 90}]}',
        encoding="utf-8",
    )

    codigo = vision_describe.main(["vision_apply", str(resultados), "--db", str(db)])

    assert codigo == 0
    fila1 = _fila_imagen(db, primera)
    fila2 = _fila_imagen(db, segunda)
    assert fila1["vision_estado"] == "hecho"
    assert fila1["descripcion"] == "Un aula vacia con sillas."
    assert fila1["etiquetas"] == "aula, sillas, vacio"
    assert fila1["vision_tokens"] == 500
    assert fila2["vision_estado"] == "hecho"
    assert fila2["vision_modelo"] == "qwen3.8-flash"  # default de la config
    uso = _filas_uso(db)
    assert len(uso) == 2
    assert uso[0] == (primera, "qwen3.8-flash", 380, 120, "B", "hecho")
    assert uso[1] == (segunda, "qwen3.8-flash", 300, 90, "B", "hecho")


def test_vision_apply_respeta_tope(tmp_path, capsys):
    """vision_apply con tope alcanzado: registra 'tope' y no toca las filas."""
    db = _crear_db(tmp_path)
    imagen_id = _imagen(db)
    _sembrar_uso(db, 120)
    resultados = tmp_path / "vision_resultados.json"
    resultados.write_text(
        '{"resultados": [{"imagen_id": %d, "descripcion": "x", "etiquetas": "y"}]}'
        % imagen_id,
        encoding="utf-8",
    )

    codigo = vision_describe.main(["vision_apply", str(resultados), "--db", str(db)])
    salida = capsys.readouterr()

    assert codigo == 0
    assert "TOPE dia" in salida.err
    assert _fila_imagen(db, imagen_id)["vision_estado"] == "pendiente"
    assert [f[5] for f in _filas_uso(db)][-1] == "tope"