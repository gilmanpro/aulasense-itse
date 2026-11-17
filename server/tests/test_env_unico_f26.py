# -*- coding: utf-8 -*-
"""test_env_unico_f26.py — F26-b: el .env UNICO de la raiz es la fuente canonica.

Cubre (SPEC 16.8 / D-F26-6):
- `tools/consolidar_env.py --unificar` en arbol temporal: migra byte-a-byte, gana la
  primera fuente, archiva fuentes, y NO imprime valores (canario de secreto en stdout).
- Loaders seams (`server/vision/cliente.py::valor_env`, `server/dashboard/auth.py::
  valor_env/cargar_env`): la raiz manda sobre `claves/`; `claves/` solo lectura
  retrocompatible (ultimo lugar); el entorno del proceso sigue ganando a todo.
- El .env real del repo define las variables nuevas de las secciones F26 (solo se
  comprueban NOMBRES; jamas valores).

Regla de secretos: los valores de prueba son literales falsos `secreto-de-prueba-*`.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[1]
RAIZ = SERVER.parent  # aulasense/
sys.path.insert(0, str(SERVER))
sys.path.insert(0, str(SERVER / "dashboard"))
sys.path.insert(0, str(SERVER / "vision"))

from dashboard import auth  # noqa: E402
from vision import cliente  # noqa: E402


def _cargar_consolidar():
    ruta = RAIZ / "tools" / "consolidar_env.py"
    espec = importlib.util.spec_from_file_location("consolidar_env_f26", ruta)
    mod = importlib.util.module_from_spec(espec)
    espec.loader.exec_module(mod)
    return mod


CANARIO = "secreto-de-prueba-CANARIO-0123456789abcdef"


def _apuntar_bases(mod, tmp_path: Path):
    """Redirige BASE/FUENTES/DESTINOS del script a un arbol temporal aislado."""
    base = tmp_path / "repo"
    (base / "claves").mkdir(parents=True, exist_ok=True)
    mod.BASE = base
    mod.FUENTES = [
        ("RAIZ", base / ".env"),
        ("CLAVES", base / "claves" / ".env"),
        ("WSL", base / "claves" / ".env-wsl-nuevo"),
    ]
    mod.DESTINO_MASTER = base / "claves" / ".env.master"
    mod.FUENTE_MAESTRO = base / "claves" / ".env.master"
    mod.DESTINO_UNICO = base / ".env"
    return base


def _parsear(ruta: Path) -> dict[str, str]:
    datos = {}
    for raw in ruta.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if s and not s.startswith("#") and "=" in s:
            k, v = s.split("=", 1)
            datos[k.strip()] = v
    return datos


# ------------------------------------------------------------------------------
# 1) consolidar_env --unificar: migracion byte-a-byte, sin impresion, archivism
# ------------------------------------------------------------------------------
def test_unificar_migra_sin_imprimir_valores(tmp_path, capsys, monkeypatch):
    mod = _cargar_consolidar()
    base = _apuntar_bases(mod, tmp_path)
    (base / ".env").write_text(
        f"MQTT_HOST=broker.example\nMQTT_PASS={CANARIO}\n", encoding="utf-8")
    (base / "claves" / ".env").write_text(
        "AULASENSE_ADMIN_USER=admin\nAULASENSE_ADMIN_PASSWORD=otro-secreto-prueba-xyz\n"
        "MQTT_PASS=NO-GANA-fuente-posterior\n", encoding="utf-8")
    (base / "claves" / ".env-wsl-nuevo").write_text(
        "WSL_NEW_USER=aulasense\nWSL_NEW_PASS=secreto-wsl-prueba\n", encoding="utf-8")

    rc = mod.main(["--unificar", "--archivar"])
    out = capsys.readouterr().out

    assert rc == 0
    # byte-a-byte, gana la PRIMERA fuente:
    dst = _parsear(base / ".env")
    assert dst["MQTT_PASS"] == CANARIO
    assert dst["AULASENSE_ADMIN_PASSWORD"] == "otro-secreto-prueba-xyz"
    assert dst["AULASENSE_PROVISION_AP_SSID"] == "AULASENSE-CFG"
    # secciones nuevas del layout SPEC 16.8 presentes:
    for k in ("OPENROUTER_API_KEY", "AULASENSE_CLEF_MAX_DIA", "AULASENSE_OTA_CHECK_MIN",
              "AULASENSE_SALUD_HEAP_WARN_KB", "WSL_NEW_PASS"):
        assert k in dst, k
    # NINGUN valor migrado aparece en stdout (solo nombres/conteos):
    for valor in (CANARIO, "otro-secreto-prueba-xyz", "secreto-wsl-prueba",
                  "NO-GANA-fuente-posterior"):
        assert valor not in out
    # fuentes archivadas:
    assert (base / "claves" / ".env.bak-f26").is_file()
    assert (base / "claves" / ".env-wsl-nuevo.bak-f26").is_file()
    assert not (base / "claves" / ".env").exists()
    assert not (base / "claves" / ".env-wsl-nuevo").exists()


def test_unificar_dry_run_no_escribe(tmp_path, capsys):
    mod = _cargar_consolidar()
    base = _apuntar_bases(mod, tmp_path)
    original = "MQTT_HOST=broker.example\nMQTT_PASS=secreto-dry-prueba\n"
    (base / ".env").write_text(original, encoding="utf-8")
    rc = mod.main(["--unificar", "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert (base / ".env").read_text(encoding="utf-8") == original  # intacto
    assert "secreto-dry-prueba" not in out
    assert not (base / "claves" / "env-raiz.bak-f26").exists()


# ------------------------------------------------------------------------------
# 2) Loaders seams: raiz canonica; claves/ solo lectura retro; entorno manda
# ------------------------------------------------------------------------------
@pytest.mark.parametrize("loader", ("cliente", "auth"), ids=("vision.cliente", "dashboard.auth"))
def test_raiz_gana_sobre_claves(tmp_path, monkeypatch, loader):
    raiz = tmp_path / "env_raiz"
    raiz.write_text("DASHSCOPE_API_KEY=sk-raiz-prueba\nAULASENSE_ADMIN_PASSWORD=pass-raiz\n", encoding="utf-8")
    claves = tmp_path / "claves" / ".env"
    claves.parent.mkdir(parents=True)
    claves.write_text("DASHSCOPE_API_KEY=sk-claves-prueba\nAULASENSE_ADMIN_PASSWORD=pass-claves\nSOLO_CLAVES=retro-ok\n", encoding="utf-8")
    monkeypatch.setenv("AULASENSE_ENV_RAIZ", str(raiz))
    monkeypatch.setenv("AULASENSE_CLAVES_ENV", str(claves))
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("AULASENSE_ADMIN_PASSWORD", raising=False)

    valor = (cliente if loader == "cliente" else auth).valor_env
    assert valor("DASHSCOPE_API_KEY") == "sk-raiz-prueba"      # raiz canonica gana
    assert valor("AULASENSE_ADMIN_PASSWORD") == "pass-raiz"
    assert valor("SOLO_CLAVES") == "retro-ok"                  # lectura retro funciona
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-proceso-prueba")
    assert valor("DASHSCOPE_API_KEY") == "sk-proceso-prueba"   # el proceso manda a todo


def test_cargar_env_fusion_raiz_gana(tmp_path, monkeypatch):
    raiz = tmp_path / "env_raiz"
    raiz.write_text("AULASENSE_ADMIN_USER=admin-raiz\nX_COMPARTE=x-desde-raiz\n", encoding="utf-8")
    claves = tmp_path / "claves" / ".env"
    claves.parent.mkdir(parents=True)
    claves.write_text("AULASENSE_ADMIN_USER=admin-viejo-claves\nX_CLAVE_VIEJA=1\n", encoding="utf-8")
    monkeypatch.setenv("AULASENSE_ENV_RAIZ", str(raiz))
    monkeypatch.setenv("AULASENSE_CLAVES_ENV", str(claves))
    for k in ("AULASENSE_ADMIN_USER", "X_COMPARTE", "X_CLAVE_VIEJA"):
        monkeypatch.delenv(k, raising=False)
    fusion = auth.cargar_env(exportar=False)
    assert fusion["AULASENSE_ADMIN_USER"] == "admin-raiz"  # raiz gana en la fusion
    assert fusion["X_COMPARTE"] == "x-desde-raiz"
    assert fusion["X_CLAVE_VIEJA"] == "1"                  # y claves sigue visible


def test_escribir_password_va_a_la_raiz(tmp_path, monkeypatch):
    raiz = tmp_path / "env_raiz"
    raiz.write_text("MQTT_HOST=h\n", encoding="utf-8")
    claves = tmp_path / "claves" / ".env"
    claves.parent.mkdir(parents=True)
    claves.write_text("AULASENSE_ADMIN_PASSWORD=vieja\n", encoding="utf-8")
    monkeypatch.setenv("AULASENSE_ENV_RAIZ", str(raiz))
    monkeypatch.setenv("AULASENSE_CLAVES_ENV", str(claves))
    ruta = auth.escribir_password("secreto-nuevo-prueba-000", "admin")
    assert ruta == raiz
    assert "AULASENSE_ADMIN_PASSWORD=secreto-nuevo-prueba-000" in raiz.read_text(encoding="utf-8")
    assert "secreto-nuevo-prueba-000" not in claves.read_text(encoding="utf-8")


# ------------------------------------------------------------------------------
# 3) El .env real del repo (solo NOMBRES: nunca leer valores en la evidencia)
# ------------------------------------------------------------------------------
def test_env_real_define_las_claves_nuevas_f26():
    env = RAIZ / ".env"
    if not env.is_file():
        pytest.skip("el .env real es gitignored; puede faltar en checkouts limpios")
    nombres = set(_parsear(env))
    esperadas = {
        "AULASENSE_PROVISION_AP_SSID", "AULASENSE_PROVISION_AP_PASS",
        "OPENROUTER_API_KEY",
        "AULASENSE_CLEF_MAX_DIA", "AULASENSE_CLEF_MAX_HORA",
        "AULASENSE_CLEF_MODELO_FLASH", "AULASENSE_CLEF_MODELO",
        "AULASENSE_CLEF_UMBRAL_ESCALADO", "AULASENSE_CLEF_NOUL_CUBRE_MIN",
        "AULASENSE_SALUD_TELEMETRIA_MAX_MIN", "AULASENSE_SALUD_STALE_CRIT_MIN",
        "AULASENSE_SALUD_DISCO_WARN_PCT", "AULASENSE_SALUD_RSSI_WARN_DBM",
        "AULASENSE_SALUD_HEAP_WARN_KB",
        "AULASENSE_OTA_MANIFEST_URL", "AULASENSE_OTA_CHECK_MIN",
        "AULASENSE_VISION_MAX_DIA", "AULASENSE_VISION_MODO",
        "AULASENSE_ADMIN_PASSWORD", "WSL_NEW_PASS",
    }
    faltan = esperadas - nombres
    assert not faltan, f"variables F26 ausentes en el .env unico: {sorted(faltan)}"


def test_env_real_resuelve_variables_nuevas_por_loader(tmp_path, monkeypatch):
    """SOLO claves de umbrales NO-secretos: el loader debe resolverlas del .env UNICO
    real. Se anula el aislamiento de conftest apuntando el seam a la raiz vera; los
    valores leidos aqui son literales publicos de SPEC 16.8 (nunca credenciales)."""
    env = RAIZ / ".env"
    if not env.is_file():
        pytest.skip("el .env real es gitignored; puede faltar en checkouts limpios")
    monkeypatch.setenv("AULASENSE_ENV_RAIZ", str(env))
    for k in ("AULASENSE_CLEF_MAX_DIA", "AULASENSE_SALUD_HEAP_WARN_KB",
              "AULASENSE_OTA_CHECK_MIN"):
        monkeypatch.delenv(k, raising=False)
    assert cliente.valor_env("AULASENSE_CLEF_MAX_DIA") == "120"
    assert cliente._entero_env("AULASENSE_SALUD_HEAP_WARN_KB", 0) == 120
    assert cliente._entero_env("AULASENSE_OTA_CHECK_MIN", 99) == 0
    assert auth.valor_env("AULASENSE_CLEF_MODELO") == "cloudflare/clef"
