import os
import sys
import platform
from pathlib import Path
import requests

# === TUS CREDENCIALES (proporcionadas) ===
BOT_TOKEN = "8411154396:AAHj__Ua7uyFwG97YvTg2_KbPnZiHKyAODo"
CHAT_ID = "6623014135"

# Puedes cambiar esta ruta si tu Escritorio está en otro lugar
def ruta_escritorio():
    home = Path.home()
    # En Windows y macOS suele ser ~/Desktop; en español a veces es ~/Escritorio
    candidatos = [
        home / "Desktop",
        home / "Escritorio",
    ]
    for p in candidatos:
        if p.exists():
            return p
    # Si no existe, usa el home como último recurso
    return home

DESKTOP_DIR = ruta_escritorio()

def enviar_documento(bot_token: str, chat_id: str, ruta: Path):
    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    with ruta.open("rb") as f:
        files = {"document": (ruta.name, f)}
        data = {"chat_id": chat_id, "caption": f"Archivo: {ruta.name}"}
        r = requests.post(url, data=data, files=files, timeout=60)
    r.raise_for_status()
    return r.json()

def main():
    if not DESKTOP_DIR.exists():
        print(f"No encontré la carpeta del Escritorio: {DESKTOP_DIR}")
        sys.exit(1)

    txts = sorted([p for p in DESKTOP_DIR.glob("*.txt") if p.is_file()])
    if not txts:
        print("No se encontraron archivos .txt en el Escritorio.")
        return

    print(f"Se enviarán {len(txts)} archivo(s) desde: {DESKTOP_DIR}\n")
    enviados = 0
    fallidos = 0

    for ruta in txts:
        try:
            resp = enviar_documento(BOT_TOKEN, CHAT_ID, ruta)
            ok = resp.get("ok", False)
            if ok:
                enviados += 1
                print(f"✅ Enviado: {ruta.name}")
            else:
                fallidos += 1
                print(f"⚠️ Falló: {ruta.name} -> {resp}")
        except Exception as e:
            fallidos += 1
            print(f"❌ Error enviando {ruta.name}: {e}")

    print(f"\nResumen: {enviados} enviados, {fallidos} fallidos.")

if __name__ == "__main__":
    main()
