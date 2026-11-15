#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Runner autonomo de sesiones F4 S2/S3 (AulaSense).

Se auto-ejecuta al LOGON de Windows (entrada HKCU\\...\\Run ``AulasenseS2Runner``)
y graba SOLAS, sin operador, las sesiones S2 y S3 de la franja de actividad
(local 10:50-12:10) del dia siguiente a su instalacion, reutilizando el
mecanismo probado de S1:

  * grabacion pasiva >=22 min: ``tools/f4_sesion_pasiva.py`` (copia literal del
    sampler S1) corriendo DENTRO de WSL ``debian-aulasense`` con el venv del
    server, sondeando el dashboard por login autenticado (credenciales leidas
    de claves/.env, NUNCA impresas). Ground truth honesto:
    ``pasivo_foto_corroborada`` (cada foto real = motion confirmado; huecos =
    sin-dato, JAMAS idle inventado — reglas 10/11 AGENTS).
  * sesiones abiertas/cerradas en la tabla ``sesiones`` via
    ``tools/session_logger.py`` (API CLI existente, misma que S1).
  * QC PIL estricto de las fotos nuevas de la BD (ids > maximo previo) con el
    venv ``/home/aulasense/qc_pil`` -> ``.tmp/s2_qc_fotos.md`` (criterio
    rotas=0).
  * S3 en cola a las 11:20 (o al terminar S2 si ya es mas tarde) SOLO si caben
    >=22 min antes de 12:10; si no caben, ``.tmp/s3_pendiente.md`` (honesto).
  * al terminar (exito o fallo definitivo) BORRA su propia entrada del registro
    (auto-limpieza, no corre eterno).

Limites de reintentos (orden del orquestador): maximo 1 reintento al dia
siguiente; si la ventana falla, se escribe ``.tmp/s2_runner_fallo.md`` con la
hora real y se sale. Si el PC estuvo apagado/sin logon hasta despues de la
ventana, se escribe ``.tmp/s2_runner_falta_logon.md`` (la entrada Run solo
corre al logon).

PROHIBIDO por diseno: este script NO importa ni llama ningun modelo de IA ni
usa claves sk-sp-/credits — solo HTTP local al dashboard y ficheros del repo.

Modo prueba: variable de entorno ``AULASENSE_TEST=1`` fuerza ventana simulada
de 3 min hacia ``dataset/sesion_test/`` (NO toca sesion_2/3), hace QC real
sobre las ultimas 3 fotos existentes y SIMULA la auto-limpieza (la registra en
el log sin borrar nada).

Uso manual:
    C:\\Python315\\python.exe tools\\sesion_autonoma.py --bg       # despegado (Run key)
    C:\\Python315\\python.exe tools\\sesion_autonoma.py --fg       # en primer plano
    set AULASENSE_TEST=1 && C:\\Python315\\python.exe tools\\sesion_autonoma.py --fg
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# --- ubicaciones y constantes del plan ----------------------------------------
RAIZ = Path(__file__).resolve().parents[1]              # aulasense/
TMP = RAIZ / ".tmp"
DATASET = RAIZ / "dataset"
ESTADO = TMP / "s2_runner_state.json"
LOG_FALLO = TMP / "s2_runner_fallo.md"
LOG_FALTA = TMP / "s2_runner_falta_logon.md"
LOG_S3_PEND = TMP / "s3_pendiente.md"
QC_MD = TMP / "s2_qc_fotos.md"

DISTRO = "debian-aulasense"
USUARIO_WSL = "aulasense"
PY_VENV = "/home/aulasense/venvs/aulasense/bin/python"      # sampler + session_logger
PY_QC = "/home/aulasense/qc_pil/bin/python"                 # helpers maxid/probe/qc (PIL)
SL_WSL = "/home/aulasense/aulasense/tools/session_logger.py"
DB_WSL = "/home/aulasense/aulasense-datos/aulasense.db"
SAMPLER = ""   # rutas WSL resueltas en rutas_wsl()
HELPER = ""

# Ventana de actividad (hora LOCAL del PC, igual que S1: 10:54-12:12)
INICIO_VENTANA = dt.time(10, 50)
FIN_VENTANA = dt.time(12, 10)
LIMITE_TARDE = dt.time(12, 15)      # despertar despues de esto = falta logon
DURACION_S = 22 * 60                # >=22 min continuos (S1 uso 1320)
INICIO_S3 = dt.time(11, 20)         # cola de la segunda sesion
INTERVALO_SONDEO = 20               # igual que S1
MAX_DIAS = 2                        # dia objetivo + 1 reintento como maximo

RUN_KEY = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALOR = "AulasenseS2Runner"
LOCK = TMP / "s2_runner.lock"          # un solo velador a la vez (logon + arranque manual)
LOCK_STALE_S = 180

# bases del dashboard a sondear (orden de preferencia)
BASES = ("http://127.0.0.1:8100", "http://127.0.0.1:8000")
SSE_BASE = "http://172.26.159.208:8100"


def rutas_wsl() -> None:
    """Fija las rutas WSL (/mnt/c/...) de los scripts del repo una vez."""
    global SAMPLER, HELPER
    SAMPLER = a_wsl(RAIZ / "tools" / "f4_sesion_pasiva.py")
    HELPER = a_wsl(RAIZ / "tools" / "f4_runner_wsl.py")


def a_wsl(p: Path | str) -> str:
    """C:\\Users\\... -> /mnt/c/Users/... (solo lectura de rutas, sin espacios)."""
    s = str(p)
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", s)
    if not m:
        return s
    return f"/mnt/{m.group(1).lower()}/" + m.group(2).replace("\\", "/")


def log(msg: str) -> None:
    print(f"[runner {dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def anexar(md: Path, lineas: list[str]) -> None:
    md.parent.mkdir(parents=True, exist_ok=True)
    with open(md, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lineas) + "\n")


def wsl(args: list[str], capturar: bool = True, timeout: float | None = None) -> subprocess.CompletedProcess:
    cmd = ["wsl.exe", "-d", DISTRO, "-u", USUARIO_WSL, "--", *args]
    return subprocess.run(
        cmd, capture_output=capturar, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )


def hora_local() -> dt.datetime:
    return dt.datetime.now()


# --- estado y fichas de evidencia ----------------------------------------------


def cargar_estado() -> dict:
    if ESTADO.exists():
        try:
            return json.loads(ESTADO.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            log("estado ilegible, se regenera")
    hoy = hora_local().date()
    return {"instalado": hoy.isoformat(), "objetivo": (hoy + dt.timedelta(days=1)).isoformat()}


def guardar_estado(est: dict) -> None:
    TMP.mkdir(exist_ok=True)
    ESTADO.write_text(json.dumps(est, ensure_ascii=False, indent=1), encoding="utf-8")


def sesion2_existente() -> bool:
    return bool(glob.glob(str(DATASET / "sesion_2" / "registro*")))


# --- auto-limpieza del registro -------------------------------------------------


def limpiar_run(simular: bool) -> None:
    """Borra la entrada Run propia si apunta a este script (no corre eterno)."""
    q = subprocess.run(["reg", "query", RUN_KEY, "/v", RUN_VALOR],
                       capture_output=True, text=True)
    if q.returncode != 0:
        log("limpiar_run: la entrada no existe (nada que borrar)")
        return
    apunta_aqui = "sesion_autonoma.py" in (q.stdout or "")
    if not apunta_aqui:
        log("AVISO: la entrada Run no apunta a este script; NO se toca")
        return
    if simular:
        log(f"[SIMULADO] reg delete {RUN_KEY} /v {RUN_VALOR} /f  (no ejecutado en test)")
        return
    d = subprocess.run(["reg", "delete", RUN_KEY, "/v", RUN_VALOR, "/f"],
                       capture_output=True, text=True)
    log(f"auto-limpieza Run: rc={d.returncode} {(d.stdout or d.stderr or '').strip()[:120]}")


# --- operaciones contra WSL -----------------------------------------------------


def maxid_fotos() -> int:
    r = wsl([PY_QC, HELPER, "maxid"], timeout=60)
    try:
        return int((r.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        log(f"maxid fallo: rc={r.returncode} {r.stdout!r} {r.stderr!r}")
        return 0


def elegir_base() -> str | None:
    for base in BASES:
        r = wsl([PY_QC, HELPER, "probe", "--base", base], timeout=45)
        ok = r.returncode == 0
        log(f"probe {base}: {'OK' if ok else 'NO'}")
        if ok:
            return base
    return None


def sesiones_activas_residuales() -> list[str]:
    r = wsl([PY_VENV, SL_WSL, "--db", DB_WSL, "list", "--json", "--limite", "50"], timeout=60)
    try:
        filas = json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        return []
    return [f["sesion"] for f in filas if f.get("estado") == "activa"]


def cerrar_residuos() -> None:
    for nom in sesiones_activas_residuales():
        log(f"cerrando sesion residual activa: {nom}")
        wsl([PY_VENV, SL_WSL, "--db", DB_WSL, "stop", "--id", nom,
             "--nota", "cerrada por runner autonomo (residuo de una corrida anterior)"], timeout=60)


def sesion_logger(accion: str, nom: str, nota: str) -> bool:
    r = wsl([PY_VENV, SL_WSL, "--db", DB_WSL, accion, "--id", nom, "--nota", nota], timeout=60)
    ok = r.returncode == 0
    detalle = (r.stdout or r.stderr or "").strip().splitlines()
    log(f"session_logger {accion} {nom}: {'OK' if ok else 'FALLO'} {detalle[-1] if detalle else ''}")
    return ok


def correr_sampler(nom: str, base: str, dur_s: int, jsonl: Path, csv: Path) -> bool:
    cmd = [
        PY_VENV, SAMPLER,
        "--sesion", nom,
        "--base", base,
        "--sse-base", SSE_BASE,
        "--duracion-s", str(dur_s),
        "--intervalo-s", str(INTERVALO_SONDEO),
        "--salida", a_wsl(jsonl),
        "--csv-gt", a_wsl(csv),
    ]
    log(f"grabando {nom} ({dur_s} s) via {base} -> {jsonl}")
    t0 = time.monotonic()
    proc = subprocess.Popen(["wsl.exe", "-d", DISTRO, "-u", USUARIO_WSL, "--", *cmd])
    while proc.poll() is None:      # latido durante la corrida larga
        latido()
        time.sleep(30)
    ok = proc.returncode == 0 and jsonl.exists()
    log(f"{nom}: rc={proc.returncode} en {time.monotonic() - t0:.0f} s -> {'OK' if ok else 'FALLO'}")
    return ok


def qc_fotos(desde: int, sesion: str, hasta: int | None = None) -> None:
    cmd = [PY_QC, HELPER, "qc", "--desde", str(desde), "--sesion", sesion, "--salida", a_wsl(QC_MD)]
    if hasta is not None:
        cmd += ["--hasta", str(hasta)]
    r = wsl(cmd, timeout=300)
    log(f"QC {sesion}: rc={r.returncode} (informe -> {QC_MD})")


# --- planificacion de la ventana -------------------------------------------------


def inicio_de(dia: dt.date, a_hora: dt.time) -> dt.datetime:
    return dt.datetime.combine(dia, a_hora)


def ultimo_arranque_valido(dia: dt.date) -> dt.datetime:
    """Ultima hora de arranque para terminar dentro de la ventana."""
    return inicio_de(dia, FIN_VENTANA) - dt.timedelta(seconds=DURACION_S)


def dormir_hasta(target: dt.datetime) -> None:
    while True:
        resta = (target - hora_local()).total_seconds()
        if resta <= 0:
            return
        latido()
        time.sleep(min(60.0, resta))


def lock_vivo() -> bool:
    """Otro runner escribio el fichero de latido hace <180 s."""
    try:
        return time.time() - LOCK.stat().st_mtime < LOCK_STALE_S
    except OSError:
        return False


def latido() -> None:
    try:
        TMP.mkdir(exist_ok=True)
        LOCK.write_text(f"{os.getpid()} {hora_local().isoformat()}\n", encoding="utf-8")
    except OSError:
        pass


def grabar_dia(dia: dt.date, test: bool) -> bool:
    """Intenta S2 (+S3 si cabe) en el dia dado. True si S2 quedo grabada.

    En modo test ignora la ventana real: usa ``dataset/sesion_test/``,
    duracion de 3 min y nombre S2TEST (no contamina sesion_2/validacion S2).
    """
    if test:
        base = elegir_base()
        if base is None:
            log("TEST: dashboard no alcanzable -> fallo")
            return False
    else:
        ahora = hora_local()
        if ahora < inicio_de(dia, INICIO_VENTANA):
            log(f"durmiendo hasta {inicio_de(dia, INICIO_VENTANA)}")
            dormir_hasta(inicio_de(dia, INICIO_VENTANA))
        if hora_local() > inicio_de(dia, LIMITE_TARDE):
            return False
        if sesion2_existente():
            log("registro de sesion_2 ya existe (grabado en otra via): nada que hacer")
            return True
        if hora_local() > ultimo_arranque_valido(dia):
            anexar(LOG_FALLO, [
                f"## Ventana insuficiente para S2 — {hora_local():%Y-%m-%d %H:%M:%S}",
                f"- Desperto dentro de la ventana pero despues de {ultimo_arranque_valido(dia):%H:%M}"
                f" (ultima hora que permite 22 min antes de {FIN_VENTANA}).",
            ])
            return False
        base = elegir_base()
        if base is None:
            log("dashboard no alcanzable al abrir la ventana; probe cada 60 s mientras quepa S2")
            while hora_local() <= ultimo_arranque_valido(dia):
                latido()
                time.sleep(60)
                base = elegir_base()
                if base:
                    break
        if base is None:
            log("dashboard NO alcanzable -> fallo de la ventana")
            anexar(LOG_FALLO, [f"## Dashboard inalcanzable — {hora_local():%Y-%m-%d %H:%M:%S}",
                               f"- dia {dia}: probe fallo en {' y '.join(BASES)} hasta "
                               f"{ultimo_arranque_valido(dia):%H:%M}"])
            return False

    cerrar_residuos()

    if test:
        nom, carpeta, dur = f"S2TEST{hora_local():%H%M}", DATASET / "sesion_test", 180
        csv = TMP / "s2_runner_selftest_validacion.csv"
    else:
        nom, carpeta, dur = "S2", DATASET / "sesion_2", DURACION_S
        csv = RAIZ / "docs" / "validacion_sesion2.csv"
    jsonl = carpeta / f"registro_{nom}.jsonl"

    nota = (f"F4 autonoma {nom} (runner sesion_autonoma.py, GT "
            f"pasivo_foto_corroborada, sin operador)")
    if not sesion_logger("start", nom, nota):
        log("no se pudo abrir la sesion en la tabla sesiones")
        return False
    desde = maxid_fotos()
    ok = correr_sampler(nom, base, dur, jsonl, csv)
    if ok and jsonl.exists():
        resumen = parsear_resumen(jsonl)
        if resumen is None:
            log(f"{nom}: registro NO parseable (falta resumen) -> se trata como fallo")
            ok = False
    sesion_logger("stop", nom, nota + "; resultado " + ("OK" if ok else "FALLO"))
    if ok:
        hasta = maxid_fotos()
        log(f"QC {nom}: ids {desde + 1}..{hasta}")
        qc_fotos(desde, nom + (" (test)" if test else ""), hasta)
    else:
        anexar(LOG_FALLO, [f"## Fallo grabacion {nom} — {hora_local():%Y-%m-%d %H:%M:%S}",
                           f"- base: {base}; duracion pedida: {dur} s",
                           "- ver log completo: .tmp/s2_runner.log"])
        return False

    # --- S3: segunda corrida en cola (11:20) si caben >=22 min antes de 12:10 ---
    if test:
        log("[TEST] S3 no se graba en la prueba en seco (la cola se evalua en produccion)")
        return True
    inicio_s3 = max(hora_local(), inicio_de(dia, INICIO_S3))
    fin_s3 = inicio_s3 + dt.timedelta(seconds=DURACION_S)
    if fin_s3 > inicio_de(dia, FIN_VENTANA):
        anexar(LOG_S3_PEND, [
            f"# S3 pendiente — {hora_local():%Y-%m-%d %H:%M:%S}",
            "",
            f"- S2 grabada OK; para S3 no alcanzaba la ventana: arranque mas temprano "
            f"viable {inicio_s3:%H:%M} + {DURACION_S // 60} min terminaria {fin_s3:%H:%M} "
            f"(ventana cierra {FIN_VENTANA}).",
            "- El runner se retira tras S2 (max 1 dia objetivo); decidir con el orquestador.",
        ])
        log("S3 no cabe en la ventana -> .tmp/s3_pendiente.md")
        return True
    if inicio_s3 > hora_local():
        dormir_hasta(inicio_s3)
    nom3, jsonl3 = "S3", DATASET / "sesion_3" / "registro_S3.jsonl"
    csv3 = RAIZ / "docs" / "validacion_sesion3.csv"
    if not sesion_logger("start", nom3, nota.replace(nom, nom3)):
        return True
    desde3 = maxid_fotos()
    ok3 = correr_sampler(nom3, base, DURACION_S, jsonl3, csv3)
    if ok3 and parsear_resumen(jsonl3) is None:
        ok3 = False
    sesion_logger("stop", nom3, nota.replace(nom, nom3) + "; resultado " + ("OK" if ok3 else "FALLO"))
    if ok3:
        qc_fotos(desde3, nom3, maxid_fotos())
    else:
        anexar(LOG_S3_PEND, [f"# S3 fallida — {hora_local():%Y-%m-%d %H:%M:%S}",
                             "- el sampler devolvio fallo en la segunda corrida; ver .tmp/s2_runner.log"])
    return True


def parsear_resumen(jsonl: Path) -> dict | None:
    """Ultima linea {'tipo':'resumen'} del registro (prueba de parseabilidad)."""
    try:
        resumen = None
        with open(jsonl, encoding="utf-8") as fh:
            for linea in fh:
                obj = json.loads(linea)  # cada linea debe ser JSON valido
                if obj.get("tipo") == "resumen":
                    resumen = obj
        return resumen
    except (OSError, json.JSONDecodeError) as exc:
        log(f"parsear_resumen {jsonl.name}: {exc}")
        return None


# --- ciclo principal --------------------------------------------------------------


def producir(test: bool) -> int:
    rutas_wsl()
    if not test:
        if lock_vivo():
            log("otro runner esta vivo (lock reciente) -> este proceso sale sin duplicar")
            return 0
        latido()
    if not test and sesion2_existente():
        log("dataset/sesion_2/registro* ya existe -> exit 0 (y auto-limpieza)")
        limpiar_run(simular=test)
        return 0

    est = cargar_estado()
    guardar_estado(est)  # dejar constancia del dia objetivo desde el primer latido
    objetivo = dt.date.fromisoformat(est["objetivo"])
    if test:
        log("MODO TEST (AULASENSE_TEST=1): ventana simulada, salida a dataset/sesion_test/")
        ok = grabar_dia(objetivo, test=True)
        if not ok:
            anexar(LOG_FALLO, [f"## Fallo TEST — {hora_local():%Y-%m-%d %H:%M:%S}"])
            return 1
        log("TEST OK")
        return 0

    for intento in range(1, MAX_DIAS + 1):
        dia = objetivo + dt.timedelta(days=intento - 1)
        ahora = hora_local()
        if ahora > inicio_de(dia, LIMITE_TARDE):
            # el runner arranco (logon) demasiado tarde para la ventana de ese dia
            anexar(LOG_FALTA, [
                f"## Arranque tardio — intento {intento} — {ahora:%Y-%m-%d %H:%M:%S}",
                "",
                f"- La entrada HKCU Run solo corre al LOGON: no hubo sesion de Windows "
                f"dentro de la ventana {INICIO_VENTANA}-{FIN_VENTANA} del {dia}.",
                f"- Hora real de arranque del runner: {ahora:%Y-%m-%d %H:%M:%S}.",
                "- " + ("Reintento manana (max 1)." if intento < MAX_DIAS else "Sin reintentos."),
            ])
            log(f"arranque tardio el dia {dia} (falta logon en la franja)")
            continue
        if ahora < inicio_de(dia, INICIO_VENTANA):
            log(f"esperando la ventana del {dia} (despierto para {inicio_de(dia, INICIO_VENTANA)})")
        if grabar_dia(dia, test=False):
            est["completado"] = hora_local().isoformat()
            guardar_estado(est)
            log("sesiones grabadas -> auto-limpieza de la entrada Run")
            limpiar_run(simular=False)
            return 0
        # fallo dentro de la ventana: grabar_dia ya anoto LOG_FALLO
        if intento < MAX_DIAS:
            log(f"intento {intento} fallo; max 1 reintento: manana {dia + dt.timedelta(days=1)}")
            guardar_estado({**est, "ultimo_fallo": hora_local().isoformat()})
    anexar(LOG_FALLO, [
        f"# Ventana fallida — {hora_local():%Y-%m-%d %H:%M:%S}",
        "",
        f"- Agotado el maximo de {MAX_DIAS} dias (objetivo {objetivo} + 1 reintento).",
        "- S2/S3 NO quedaron grabadas por esta via; reportar al orquestador.",
        "- Detalle por intento en este fichero y en .tmp/s2_runner.log.",
    ])
    limpiar_run(simular=False)  # no correr eterno tras el ultimo reintento
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Runner autonomo F4 S2/S3 (sin IA, sin placas)")
    modo = ap.add_mutually_exclusive_group(required=True)
    modo.add_argument("--bg", action="store_true",
                      help="re-lanza el trabajo despegado (un proceso por logon) y sale ya")
    modo.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    modo.add_argument("--fg", action="store_true", help="corre en primer plano")
    args = ap.parse_args(argv)

    test = os.environ.get("AULASENSE_TEST", "") == "1"

    if args.bg:
        if not test and lock_vivo():
            print("[runner] ya hay un velador vivo; no se duplica")
            return 0
        TMP.mkdir(exist_ok=True)
        logf = open(TMP / "s2_runner.log", "a", encoding="utf-8")
        logf.write(f"\n==== arranque despegado {dt.datetime.now():%F %T} ====\n")
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--child"],
            stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            creationflags=flags, cwd=str(RAIZ), env={**os.environ, "AULASENSE_TEST": "1" if test else "0"},
        )
        print("[runner] trabajo despegado (hijo); este proceso ya termino")
        return 0

    return producir(test)


if __name__ == "__main__":
    sys.exit(main())
