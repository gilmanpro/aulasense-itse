"""Casos BORDE de ``/api/imagenes`` (SPEC §9.1/§9.3, F25-d3) que faltaban en
``test_dashboard.py`` (revisa el 2026-11-15):

- filtros COMBINADOS ``q`` + ``canal`` + ``con_ia`` en una sola consulta (la
  combinacion exacta del formulario de la galeria);
- ``canal``/``vision_estado`` normalizados (mayusculas y espacios: " MQTT "
  filtra por mqtt) y cadena en blanco = SIN filtro (no 422 silencioso);
- 422 por tipos: ``con_ia``, ``limite`` y ``desde`` no enteros (la coercion de
  FastAPI, nunca 500);
- ``con_ia`` con cualquier valor no-cero activa el filtro (contrato del int);
- busqueda con BACKSLASH literal (``q=\\``): el escape de LIKE debe cubrir los
  TRES caracteres (``%``, ``_`` y ``\\``), si el backslash no se escapa el patron
  muere y no casa ni la fila con backslash;
- truncado de ``q`` al TOPE_BUSQUEDA (200 chars) con termino mayor;
- precedencia ``q`` vs alias ``texto`` (q gana si trae texto; con q en blanco
  gana texto).

Reutiliza fixtures y helper de insercion de ``test_dashboard``.
"""

from __future__ import annotations

from test_dashboard import _insertar_imagen, galeria  # noqa: F401  (fixture + helper)


# --- 1. Filtros combinados de la UI --------------------------------------------


def test_filtros_q_canal_con_ia_combinados(galeria):
    """La consulta real de la galeria: texto + canal mqtt + solo descritas."""
    cliente, datos = galeria
    objetivo = _insertar_imagen(
        datos, ts_ms=1_700_000_500_000, canal="mqtt", vision_estado="hecho",
        descripcion="persona junto a la ventana", etiquetas="persona, ventana",
    )
    # Mismo texto y canal pero SIN describir -> fuera por con_ia.
    _insertar_imagen(
        datos, ts_ms=1_700_000_600_000, canal="mqtt", vision_estado="pendiente",
        descripcion="persona pendiente",
    )
    # Texto y con_ia OK pero por HTTP -> fuera por canal.
    _insertar_imagen(
        datos, ts_ms=1_700_000_700_000, canal="http", vision_estado="hecho",
        descripcion="persona por http",
    )
    # Coincide canal+con_ia pero no el texto -> fuera por q.
    _insertar_imagen(
        datos, ts_ms=1_700_000_800_000, canal="mqtt", vision_estado="hecho",
        descripcion="silla apilada",
    )

    cuerpo = cliente.get(
        "/api/imagenes",
        params={"q": "persona", "canal": "mqtt", "con_ia": 1},
    ).json()
    assert [fila["id"] for fila in cuerpo["filas"]] == [objetivo]
    # Eco exacto de los filtros aplicados.
    assert cuerpo["q"] == "persona" and cuerpo["canal"] == "mqtt"
    assert cuerpo["con_ia"] is True


def test_canal_normalizado_mayusculas_y_espacios(galeria):
    """``canal=" MQTT "`` no es 422 ni se ignora: se normaliza y filtra."""
    cliente, datos = galeria
    mqtt = _insertar_imagen(datos, ts_ms=1_700_000_500_000, canal="mqtt")
    _insertar_imagen(datos, ts_ms=1_700_000_600_000, canal="http")
    cuerpo = cliente.get("/api/imagenes", params={"canal": " MQTT "}).json()
    assert cuerpo["canal"] == "mqtt"
    assert [fila["id"] for fila in cuerpo["filas"]] == [mqtt]


def test_canal_en_blanco_no_filtra(galeria):
    """Cadena en blanco = filtro ausente (el endpoint lo documenta): devuelve
    TODOS los canales y el eco de ``canal`` es null."""
    cliente, datos = galeria
    _insertar_imagen(datos, ts_ms=1_700_000_500_000, canal="mqtt")
    _insertar_imagen(datos, ts_ms=1_700_000_600_000, canal="http")
    cuerpo = cliente.get("/api/imagenes", params={"canal": "   "}).json()
    assert cuerpo["canal"] is None
    assert len(cuerpo["filas"]) == 2


def test_vision_estado_normalizado_mayusculas(galeria):
    """``vision_estado=" HeChO "`` se canonicaliza y filtra (mismo criterio que
    canal)."""
    cliente, datos = galeria
    hecho = _insertar_imagen(
        datos, ts_ms=1_700_000_500_000, vision_estado="hecho", descripcion="x"
    )
    _insertar_imagen(datos, ts_ms=1_700_000_600_000, vision_estado="pendiente")
    cuerpo = cliente.get("/api/imagenes", params={"vision_estado": " HeChO "}).json()
    assert cuerpo["vision_estado"] == "hecho"
    assert [fila["id"] for fila in cuerpo["filas"]] == [hecho]


# --- 2. 422 por tipos en los parametros enteros --------------------------------


def test_enteros_invalidos_422(galeria):
    """``con_ia``/``limite``/``desde`` deben ser enteros: la validacion de
    FastAPI responde 422 (nunca 500 ni coercion laxa)."""
    cliente, _datos = galeria
    assert cliente.get("/api/imagenes", params={"con_ia": "si"}).status_code == 422
    assert cliente.get("/api/imagenes", params={"limite": "abc"}).status_code == 422
    assert cliente.get("/api/imagenes", params={"desde": "ayer"}).status_code == 422
    assert cliente.get("/api/imagenes", params={"hasta": "1700.5"}).status_code == 422


def test_con_ia_cualquier_no_cero_activa_el_filtro(galeria):
    """Contrato del flag: ``con_ia != 0`` (incluso 2 o -1) activa el filtro;
    solo 0 (o ausente) lo desactiva."""
    cliente, datos = galeria
    hecho = _insertar_imagen(
        datos, ts_ms=1_700_000_500_000, vision_estado="hecho", descripcion="mesa"
    )
    _insertar_imagen(datos, ts_ms=1_700_000_600_000, vision_estado="pendiente")
    for valor in (2, -1):
        cuerpo = cliente.get("/api/imagenes", params={"con_ia": valor}).json()
        assert cuerpo["con_ia"] is True, valor
        assert [fila["id"] for fila in cuerpo["filas"]] == [hecho], valor


# --- 3. Escape de LIKE: backslash literal ---------------------------------------


def test_busqueda_backslash_literal(galeria):
    """El tercer caracter escapado (§9.3) es ``\\``: buscar un backslash debe
    casar SOLO la fila que lo contiene literal. Si el escape faltara, patron
    ``%\\%`` se traga el comodin y NO casa nada (o casa de mas)."""
    cliente, datos = galeria
    con_barra = _insertar_imagen(
        datos,
        ts_ms=1_700_000_500_000,
        descripcion="rótulo con C:\\ventana\\proyector",
        vision_estado="hecho",
    )
    _insertar_imagen(
        datos,
        ts_ms=1_700_000_600_000,
        descripcion="rotulo sin barra alguna",
        vision_estado="hecho",
    )
    filas = cliente.get("/api/imagenes", params={"q": "\\"}).json()["filas"]
    assert [fila["id"] for fila in filas] == [con_barra]


# --- 4. Tope de longitud de busqueda ---------------------------------------------


def test_busqueda_larga_se_trunca_al_tope(galeria):
    """``q`` mayor que TOPE_BUSQUEDA (200) se recorta: el eco trae exactamente
    los primeros 200 caracteres y la busqueda sigue funcionando (no 422/500)."""
    from dashboard.app import TOPE_BUSQUEDA

    cliente, datos = galeria
    termino_largo = "x" * (TOPE_BUSQUEDA + 50)
    dentro = _insertar_imagen(
        datos,
        ts_ms=1_700_000_500_000,
        descripcion="fondo " + "x" * (TOPE_BUSQUEDA + 50) + " fin",
        vision_estado="hecho",
    )
    cuerpo = cliente.get("/api/imagenes", params={"q": termino_largo}).json()
    assert cuerpo["q"] == "x" * TOPE_BUSQUEDA  # truncado al eco
    assert [fila["id"] for fila in cuerpo["filas"]] == [dentro]  # sigue casando


# --- 5. Precedencia q vs texto ----------------------------------------------------


def test_q_gana_sobre_texto_y_texto_usado_si_q_vacio(galeria):
    """Con ambos terminos, gana ``q`` (si trae texto). Con ``q`` en blanco, gana
    el alias ``texto`` (§9.3: un unico termino, nunca se combinan)."""
    cliente, datos = galeria
    persona = _insertar_imagen(
        datos, ts_ms=1_700_000_500_000, descripcion="persona caminando",
        vision_estado="hecho",
    )
    mobiliario = _insertar_imagen(
        datos, ts_ms=1_700_000_600_000, descripcion="silla y mesa",
        etiquetas="mobiliario", vision_estado="hecho",
    )

    ambos = cliente.get(
        "/api/imagenes", params={"q": "persona", "texto": "mobiliario"}
    ).json()
    assert ambos["q"] == "persona"
    assert [fila["id"] for fila in ambos["filas"]] == [persona]

    solo_alias = cliente.get(
        "/api/imagenes", params={"q": "   ", "texto": "mobiliario"}
    ).json()
    assert solo_alias["q"] == "mobiliario"
    assert [fila["id"] for fila in solo_alias["filas"]] == [mobiliario]
