#!/usr/bin/env python
"""lee_serial.py — monitor serial portable para AulaSense (sin pyserial en requirements).

Lee continuamente un puerto COM de Windows usando ctypes (CreateFileW/ReadFile),
sin dependencias externas. Ctrl+C corta limpio.

Uso:
  C:\\Python315\\python.exe tools\\lee_serial.py COM6
  C:\\Python315\\python.exe tools\\lee_serial.py COM6 --baud 115200 --out .tmp\\f1_serial.log
  C:\\Python315\\python.exe tools\\lee_serial.py COM6 --segundos 30     # lectura acotada

Fallback oficial si el puerto está ocupado o algo falla (ESP-IDF 5.5.6, SPEC §4):
  eim run "idf.py -p COM6 monitor" v5.5.6

Notas:
- Abre el puerto en modo EXCLUSIVO (share 0): cierra antes `idf.py monitor` u otro terminal.
- DTR/RTS se dejan desactivados a propósito: activarlos puede resetear el ESP32 por la
  secuencia de esptool y sacarlo de la app en ejecución.
"""

from __future__ import annotations

import argparse
import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import IO

ROOT = Path(__file__).resolve().parents[1]  # aulasense/

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
PURGE_TXCLEAR = 0x0004
PURGE_RXCLEAR = 0x0008


class DCB(ctypes.Structure):
    """Estructura DCB de Win32 (configuración del puerto serie)."""

    _fields_ = [
        ("DCBlength", wintypes.DWORD),
        ("BaudRate", wintypes.DWORD),
        ("fBinary", wintypes.DWORD, 1),
        ("fParity", wintypes.DWORD, 1),
        ("fOutxCtsFlow", wintypes.DWORD, 1),
        ("fOutxDsrFlow", wintypes.DWORD, 1),
        ("fDtrControl", wintypes.DWORD, 2),
        ("fDsrSensitivity", wintypes.DWORD, 1),
        ("fTXContinueOnXoff", wintypes.DWORD, 1),
        ("fOutX", wintypes.DWORD, 1),
        ("fInX", wintypes.DWORD, 1),
        ("fErrorChar", wintypes.DWORD, 1),
        ("fNull", wintypes.DWORD, 1),
        ("fRtsControl", wintypes.DWORD, 2),
        ("fAbortOnError", wintypes.DWORD, 1),
        ("fDummy2", wintypes.DWORD, 17),
        ("wReserved", wintypes.WORD),
        ("XonLim", wintypes.WORD),
        ("XoffLim", wintypes.WORD),
        ("ByteSize", wintypes.BYTE),
        ("Parity", wintypes.BYTE),
        ("StopBits", wintypes.BYTE),
        ("XonChar", ctypes.c_char),
        ("XoffChar", ctypes.c_char),
        ("ErrorChar", ctypes.c_char),
        ("EofChar", ctypes.c_char),
        ("EvtChar", ctypes.c_char),
        ("wReserved1", wintypes.WORD),
    ]


class COMMTIMEOUTS(ctypes.Structure):
    """Timeouts de lectura/escritura del puerto serie."""

    _fields_ = [
        ("ReadIntervalTimeout", wintypes.DWORD),
        ("ReadTotalTimeoutMultiplier", wintypes.DWORD),
        ("ReadTotalTimeoutConstant", wintypes.DWORD),
        ("WriteTotalTimeoutMultiplier", wintypes.DWORD),
        ("WriteTotalTimeoutConstant", wintypes.DWORD),
    ]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if sys.platform == "win32" else None

if kernel32 is not None:
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.ReadFile.argtypes = (
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    )
    kernel32.ReadFile.restype = wintypes.BOOL
    kernel32.SetCommState.argtypes = (wintypes.HANDLE, ctypes.POINTER(DCB))
    kernel32.SetCommState.restype = wintypes.BOOL
    kernel32.SetCommTimeouts.argtypes = (wintypes.HANDLE, ctypes.POINTER(COMMTIMEOUTS))
    kernel32.SetCommTimeouts.restype = wintypes.BOOL
    kernel32.SetupComm.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD)
    kernel32.SetupComm.restype = wintypes.BOOL
    kernel32.PurgeComm.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.PurgeComm.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL


def resolver_puerto(puerto: str) -> str:
    """Normaliza 'COM6' -> '\\\\.\\COM6' (prefijo necesario también para COM>=10)."""
    nombre = puerto.strip()
    if not nombre:
        raise ValueError("puerto vacío")
    if nombre.startswith("\\\\.\\"):
        return nombre
    if not nombre.upper().startswith("COM") or not nombre[3:].isdigit():
        raise ValueError(f"puerto inválido: {puerto!r} (formato esperado COMx)")
    return "\\\\.\\" + nombre


def abrir_puerto(ruta: str, baud: int) -> int:
    """Abre el COM en exclusiva, 8N1, sin flow control y sin tocar DTR/RTS."""
    if kernel32 is None:
        raise OSError(0, "lee_serial solo funciona en Windows")
    handle = kernel32.CreateFileW(ruta, GENERIC_READ | GENERIC_WRITE, 0, None, OPEN_EXISTING, 0, None)
    if handle is None or handle == INVALID_HANDLE_VALUE:
        err = ctypes.get_last_error()
        raise OSError(err, f"no se pudo abrir {ruta}: {ctypes.FormatError(err)}")

    dcb = DCB()
    dcb.DCBlength = ctypes.sizeof(DCB)
    dcb.BaudRate = int(baud)
    dcb.fBinary = 1
    dcb.fParity = 0
    dcb.fOutxCtsFlow = 0
    dcb.fOutxDsrFlow = 0
    dcb.fDtrControl = 0  # DTR_CONTROL_DISABLE
    dcb.fDsrSensitivity = 0
    dcb.fTXContinueOnXoff = 1
    dcb.fOutX = 0
    dcb.fInX = 0
    dcb.fErrorChar = 0
    dcb.fNull = 0
    dcb.fRtsControl = 0  # RTS_CONTROL_DISABLE
    dcb.fAbortOnError = 0
    dcb.ByteSize = 8
    dcb.Parity = 0  # NOPARITY
    dcb.StopBits = 0  # ONESTOPBIT

    if not kernel32.SetCommState(handle, ctypes.byref(dcb)):
        err = ctypes.get_last_error()
        kernel32.CloseHandle(handle)
        raise OSError(err, f"SetCommState falló: {ctypes.FormatError(err)}")

    # Lectura "semi-bloqueante": devuelve al llegar datos o cada ~200 ms.
    timeouts = COMMTIMEOUTS(50, 0, 200, 0, 0)
    kernel32.SetCommTimeouts(handle, ctypes.byref(timeouts))
    kernel32.SetupComm(handle, 4096, 4096)
    kernel32.PurgeComm(handle, PURGE_TXCLEAR | PURGE_RXCLEAR)
    return handle


def leer(handle: int, salida: IO[bytes], segundos: float, archivo: IO[bytes] | None) -> int:
    """Bucle de lectura; devuelve los bytes copiados. Ctrl+C sale limpio."""
    buffer = ctypes.create_string_buffer(4096)
    leidos = wintypes.DWORD()
    total = 0
    inicio = time.monotonic()
    try:
        while True:
            if segundos > 0 and (time.monotonic() - inicio) >= segundos:
                break
            if not kernel32.ReadFile(handle, buffer, len(buffer), ctypes.byref(leidos), None):
                err = ctypes.get_last_error()
                # ERROR_OPERATION_ABORTED / ERROR_DEVICE_NOT_CONNECTED al desenchufar.
                print(f"\n[lee_serial] WARN: ReadFile falló ({ctypes.FormatError(err)})", file=sys.stderr)
                break
            if leidos.value:
                datos = buffer.raw[: leidos.value]
                salida.write(datos)
                salida.flush()
                if archivo is not None:
                    archivo.write(datos)
                    archivo.flush()
                total += len(datos)
    except KeyboardInterrupt:
        pass
    return total


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Monitor serial COM por ctypes (sin pyserial). Ctrl+C para salir.",
        epilog='Fallback oficial: eim run "idf.py -p COMx monitor" v5.5.6',
    )
    ap.add_argument("puerto", nargs="?", default="COM6", help="puerto serie (default COM6)")
    ap.add_argument("--baud", type=int, default=115200, help="baudios (default 115200)")
    ap.add_argument(
        "--segundos",
        type=float,
        default=0.0,
        help="lectura acotada en segundos (0 = continua hasta Ctrl+C)",
    )
    ap.add_argument("--out", default="", help="archivo de log opcional (además de stdout)")
    args = ap.parse_args(argv)

    try:
        ruta = resolver_puerto(args.puerto)
    except ValueError as exc:
        print(f"[lee_serial] ERROR: {exc}", file=sys.stderr)
        return 2

    archivo: IO[bytes] | None = None
    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = ROOT / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        archivo = open(out_path, "wb")
        print(f"[lee_serial] log: {out_path}", file=sys.stderr)

    try:
        handle = abrir_puerto(ruta, args.baud)
    except OSError as exc:
        if archivo is not None:
            archivo.close()
        print(f"[lee_serial] ERROR: {exc}", file=sys.stderr)
        print("[lee_serial] Si otro monitor ocupa el puerto, ciérralo o usa el fallback oficial:", file=sys.stderr)
        print(f'  eim run "idf.py -p {args.puerto} monitor" v5.5.6', file=sys.stderr)
        return 1

    print(f"[lee_serial] {ruta} @ {args.baud} 8N1 — Ctrl+C para salir", file=sys.stderr)
    total = 0
    try:
        total = leer(handle, sys.stdout.buffer, args.segundos, archivo)
    finally:
        kernel32.CloseHandle(handle)
        if archivo is not None:
            archivo.close()

    print(f"\n[lee_serial] {total} bytes leídos", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())