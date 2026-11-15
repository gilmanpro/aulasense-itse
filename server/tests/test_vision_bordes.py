"""Casos BORDE del worker de visión (SPEC §7.3, F25-d2); BUG-PROD-VISION-LOCK.

**Historia (2026-11-15):** el worker NO tenía claim entre ``seleccionar_pendientes``
y el marcado a ``procesando``, y ``liberar_procesando`` devolvía a ``pendiente``
las filas que OTRA pasada tenía EN VUELO al inicio de cada pasada. Test original
(orquestado con eventos de hilo y API falsa que bloquea al worker A) DEMOSTRABA
el doble consumo: la misma imagen se describía 2x y ``vision_uso`` acumulaba dos
filas ``hecho`` para el mismo ``imagen_id`` (violación de la regla 16).

**Fix aplicado (mismo fichero de producción, ``server/vision/cliente.py``):**

1. CLAIM atómico por fila — ``reclamar_imagen``::

       UPDATE imagenes SET vision_estado='procesando', vision_ts_ms=?
        WHERE id=? AND vision_estado IN ('pendiente','error')

   ``rowcount == 0`` -> ``continue`` sin llamar a la API (solo una pasada gana).
2. ``liberar_procesando`` solo libera claims STALE (heartbeat sellado en
   ``vision_ts_ms``, columna F25 existente: sin columnas nuevas).

**Estos tests son ahora REGRESIÓN de exactly-once.** El caso concurrente conserva
su nodeid original (``test_doble_pasada_concurrente_quema_doble_llamada_sin_claim``)
para no romper referencias externas, pero su aserción está INVERTIDA: dos pasadas
concurrentes -> 1 llamada por imagen, 1 fila ``hecho`` por ``imagen_id``.

Convención: reusa el escenario aislado y los helpers de ``test_vision.py``.
"""

from __future__ import annotations

import sqlite3
import threading

import requests

from test_vision import (  # fixture autouse + helpers compartidos
    CLAVE_PRUEBA,
    RespuestaFake,
    _config,
    _crear_db,
    _cuerpo,
    _filas_uso,
    _fila_imagen,
    _imagen,
    cliente,
    entorno_vision_aislado,  # noqa: F401  (autouse: aísla env, topes y _dormir)
)


def test_doble_pasada_concurrente_quema_doble_llamada_sin_claim(tmp_path, monkeypatch):
    """REGRESIÓN: dos pasadas concurrentes -> exactly-once (1 llamada por imagen).

    Nombre heredado del test que documentaba el bug (mismo escenario); la
    aserción está invertida porque el claim atómico ya está implementado.

    Secuencia, determinista (todo sincronizado por Events, sin sleeps):
    1. A reclama la 1a candidata (claim: ``procesando`` + ``vision_ts_ms``) y
       queda BLOQUEADA dentro de su llamada a la API.
    2. B arranca su pasada: ``liberar_procesando`` NO reabre el claim EN VUELO de
       A (su ``vision_ts_ms`` es reciente), así que solo reclama las otras N-1
       imágenes y las describe.
    3. Se libera a A: termina su imagen; sus reclamos de las N-1 restantes dan
       ``rowcount == 0`` (ya ``hecho``) -> no hay segunda llamada.

    Exigido: total de llamadas == filas totales, ningún ``imagen_id`` descrito dos
    veces y ninguna fila ``error`` (regla 16: cero Credits duplicados).
    """
    total_filas = 4
    db = _crear_db(tmp_path)
    ids = [
        _imagen(db, hash_sha256=f"hash-{i}", ruta=f"imagenes/a/{i}.jpg")
        for i in range(1, total_filas + 1)
    ]
    monkeypatch.setenv("DASHSCOPE_API_KEY", CLAVE_PRUEBA)
    cfg = _config(db, limite=total_filas)
    clave = cliente.Clave(CLAVE_PRUEBA, "pay_as_you_go", "test")

    en_vuelo_a = threading.Event()
    soltar_a = threading.Event()
    llamadas: list[str] = []
    llamadas_a: list[str] = []
    candado = threading.Lock()
    resumenes: list = []

    def _post(url, headers=None, json=None, timeout=None):  # noqa: A002
        modelo = str((json or {}).get("model"))
        with candado:
            llamadas.append(modelo)
            es_a = threading.current_thread().name == "worker-A"
            if es_a:
                llamadas_a.append(modelo)
            primera_de_a = es_a and len(llamadas_a) == 1
        if primera_de_a:
            # A tiene su fila reclamada; B corre su pasada completa mientras.
            en_vuelo_a.set()
            assert soltar_a.wait(10), "worker B no terminó su pasada (¿deadlock?)"
        return RespuestaFake(200, _cuerpo())

    monkeypatch.setattr(requests, "post", _post)

    hilo_a = threading.Thread(
        target=lambda: resumenes.append(cliente.ejecutar_pasada(cfg, clave)),
        name="worker-A",
    )
    hilo_a.start()
    assert en_vuelo_a.wait(10), "worker A no llegó a su llamada en vuelo"

    resumen_b = cliente.ejecutar_pasada(cfg, clave)  # 2a pasada, concurrente

    soltar_a.set()
    hilo_a.join(10)
    assert not hilo_a.is_alive()
    assert len(resumenes) == 1, "el hilo A no terminó su pasada"
    resumen_a = resumenes[0]

    # --- EXACTLY-ONCE: una sola llamada por imagen --------------------------
    assert len(llamadas) == total_filas, (
        f"{len(llamadas)} llamadas para {total_filas} filas: el doble indica que "
        "el claim atómico se perdió (BUG-PROD-VISION-LOCK, regla 16)"
    )
    hechas = [fila for fila in _filas_uso(db) if fila[5] == "hecho"]
    assert len(hechas) == total_filas, hechas
    assert len({fila[0] for fila in hechas}) == total_filas, "hay un id repetido"
    assert {fila[0] for fila in hechas} == set(ids)
    assert [fila[5] for fila in _filas_uso(db)] == ["hecho"] * total_filas

    # Reparto real del trabajo: A la que tenía en vuelo, B las demás.
    assert resumen_a.hechas == 1, resumen_a
    assert resumen_b.hechas == total_filas - 1, resumen_b
    assert resumen_a.hechas + resumen_b.hechas == total_filas
    for imagen_id in ids:
        assert _fila_imagen(db, imagen_id)["vision_estado"] == "hecho"


def test_liberar_procesando_solo_libera_claims_stale(tmp_path):
    """Fix BUG-PROD-VISION-LOCK: un claim EN VUELO no se reabre; uno stale sí.

    ``reclamar_imagen`` sella el instante del reclamo en ``vision_ts_ms`` (columna
    F25 existente, sin columnas nuevas); ``liberar_procesando`` solo devuelve a
    ``pendiente`` los claims más viejos que ``stale_min`` (default 30 min) y las
    filas heredadas con ``vision_ts_ms`` NULL (claim desconocido = huérfana).
    """
    db = _crear_db(tmp_path)
    en_vuelo = _imagen(db, hash_sha256="h-en-vuelo", ruta="imagenes/a/1.jpg")
    stale = _imagen(db, hash_sha256="h-stale", ruta="imagenes/a/2.jpg")
    heredada = _imagen(db, hash_sha256="h-heredada", ruta="imagenes/a/3.jpg")
    ahora = cliente.ahora_ms()

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        assert cliente.reclamar_imagen(con, en_vuelo, ahora=ahora) is True
        # Claim de hace 2 h (worker caído) y fila heredada sin heartbeat.
        con.execute(
            "UPDATE imagenes SET vision_estado = 'procesando', vision_ts_ms = ? "
            "WHERE id = ?",
            (ahora - 2 * 60 * 60 * 1000, stale),
        )
        con.execute(
            "UPDATE imagenes SET vision_estado = 'procesando', vision_ts_ms = NULL "
            "WHERE id = ?",
            (heredada,),
        )
        con.commit()

        assert cliente.liberar_procesando(con, ahora=ahora) == 2  # stale + heredada
        assert _fila_imagen(db, en_vuelo)["vision_estado"] == "procesando"
        assert _fila_imagen(db, stale)["vision_estado"] == "pendiente"
        assert _fila_imagen(db, heredada)["vision_estado"] == "pendiente"

        # El claim es exclusivo mientras está fresco: la 2a pasada no lo gana.
        assert cliente.reclamar_imagen(con, en_vuelo, ahora=ahora + 1_000) is False
        assert cliente.reclamar_imagen(con, stale, ahora=ahora + 1_000) is True
    finally:
        con.close()