"""Beats Analyzer – pywebview GUI with native drag & drop."""

from __future__ import annotations

import os

# Before any native code (e.g. Essentia → SDL) loads: headless SDL drivers reduce
# modal "Fatal error" dialogs that can deadlock with the Cocoa event loop.
for _sdl_k, _sdl_v in (
    ("SDL_VIDEODRIVER", "dummy"),
    ("SDL_AUDIODRIVER", "dummy"),
):
    os.environ.setdefault(_sdl_k, _sdl_v)

import mimetypes
import subprocess
import threading
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# pywebview darf nicht auf Modul-Ebene importiert werden: multiprocessing.spawn
# lädt dieses Modul im Analyse-Kindprozess erneut — Cocoa/WebKit dort friert oft ein.


class _AudioHandler(BaseHTTPRequestHandler):
    """Serves local audio files for in-app playback."""

    def do_GET(self):
        path = urllib.parse.unquote(self.path.lstrip("/"))
        fp = Path(path)
        if not fp.is_file():
            self.send_error(404)
            return
        mime = mimetypes.guess_type(fp.name)[0] or "application/octet-stream"
        size = fp.stat().st_size

        range_header = self.headers.get("Range")
        if range_header:
            start, end = 0, size - 1
            r = range_header.replace("bytes=", "").split("-")
            start = int(r[0]) if r[0] else 0
            end = int(r[1]) if r[1] else size - 1
            length = end - start + 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(length))
        else:
            start, length = 0, size
            self.send_response(200)
            self.send_header("Content-Length", str(size))

        self.send_header("Content-Type", mime)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        with open(fp, "rb") as f:
            f.seek(start)
            self.wfile.write(f.read(length))

    def log_message(self, *args):
        pass


def _start_audio_server() -> int:
    server = HTTPServer(("127.0.0.1", 0), _AudioHandler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return port

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".aiff"}

def _gui_html_path() -> Path:
    import sys

    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "beats_cli" / "webgui.html"
    return Path(__file__).resolve().with_name("webgui.html")


def _load_gui_html() -> str:
    return _gui_html_path().read_text(encoding="utf-8")


HTML = _load_gui_html()



def _norm_audio_path_key(p: str | Path) -> str:
    """Canonical dict key for ``_results`` / lookups (matches get_file_info paths)."""
    try:
        return str(Path(p).expanduser().resolve())
    except (OSError, RuntimeError):
        return str(Path(p).expanduser())


def _comma_join_tags(xs) -> str:
    """Join tag list for the table payload; never raises (subprocess/pickle edge cases)."""
    if xs is None:
        return ""
    if isinstance(xs, (str, bytes)):
        s = (
            xs.decode("utf-8", errors="replace")
            if isinstance(xs, bytes)
            else xs
        ).strip()
        return s
    try:
        parts = [str(x) for x in xs]
    except (TypeError, ValueError):
        return ""
    return ", ".join(parts) if parts else ""


def _read_existing_tags(audio_path: Path) -> dict:
    """Read existing BPM, Key, Genre, Mood, Instruments from an audio file."""
    from .tagger import read_existing_tags

    return read_existing_tags(audio_path)


def _tags_with_disk_genre_mood_flag(tags: dict) -> dict:
    """GUI-Flag: ob Genre/Mood/Instrumente aus der Datei gelesen wurden (nicht nur später aus Analyse)."""
    g = (tags.get("genre") or "").strip()
    m = (tags.get("mood") or "").strip()
    i = (tags.get("instruments") or "").strip()
    tags["hadGenreMoodInstrOnDisk"] = bool(g or m or i)
    return tags


_PREFS_FILE = Path.home() / ".beats-analyzer-prefs.json"


def _cli_launch_folder_resolved() -> str | None:
    """Ordner aus ``python -m beats_cli <pfad>`` (z. B. .command mit Drag & Drop)."""
    import sys

    if len(sys.argv) < 2:
        return None
    raw = sys.argv[1]
    if not raw or (isinstance(raw, str) and raw.startswith("-")):
        return None
    p = Path(raw).expanduser()
    try:
        p = p.resolve()
    except (OSError, RuntimeError):
        return None
    if p.is_dir():
        return str(p)
    if p.is_file():
        try:
            return str(p.parent.resolve())
        except (OSError, RuntimeError):
            return str(p.parent)
    return None


def _read_prefs_dict() -> dict:
    import json

    try:
        data = json.loads(_PREFS_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_prefs_dict(prefs: dict) -> None:
    import json

    try:
        _PREFS_FILE.write_text(json.dumps(prefs, ensure_ascii=False))
    except Exception:
        pass


def _evaluate_js_on_main(window, js: str) -> None:
    """Ruft ``evaluate_js`` immer in einem kurzen Daemon-Thread auf (fire-and-forget).

    Synchrones ``evaluate_js`` aus dem Analyse-Worker oder aus der Subprozess-Poll-Schleife
    blockiert oft lange oder deadlocked mit WebKit — dann läuft kein Timeout mehr und die
    Statuszeile bleibt auf „seit 0s“. Asynchron bleibt die Schleife reaktiv; der WebKit-Thread
    serialisiert JS weiterhin intern.

    **Nicht** ``NSOperationQueue.mainQueue()`` mit blockierendem Callback: leicht Deadlock.
    """
    if window is None:
        return

    def run() -> None:
        try:
            window.evaluate_js(js)
        except Exception:
            try:
                window.run_js(js)
            except Exception:
                pass

    threading.Thread(target=run, daemon=True).start()


def _coerce_use_ml_flag(v: object) -> bool:
    """Wert aus pywebview/JS — vermeidet ``bool('false') is True`` bei String-Booleans."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return False


def _ml_essentia_tensorflow_installed() -> bool:
    """True nur wenn Paket *und* die Genre/Mood-Pipeline (TensorflowPredict*) in der Binary stecken.

    Viele PyPI-Wheels von ``essentia-tensorflow`` enthalten nur ``TensorflowInput*`` — dann
    schlägt die ML-Kette still fehl (keine Genre/Mood/Instrumente trotz Häkchen).
    """
    try:
        import importlib.metadata as im

        im.version("essentia-tensorflow")
    except im.PackageNotFoundError:
        return False
    from .analyzer import _check_tf_available

    return _check_tf_available()


def _ml_essentia_tensorflow_package_only() -> bool:
    """Paket gemeldet, aber ohne Predict-Algorithmen (kaputtes/leeres Wheel)."""
    try:
        import importlib.metadata as im

        im.version("essentia-tensorflow")
    except im.PackageNotFoundError:
        return False
    return not _ml_essentia_tensorflow_installed()


class Api:
    """Python backend exposed to JavaScript via pywebview."""

    def __init__(self, window_ref: list) -> None:
        self._window_ref = window_ref
        self._results: dict[str, object] = {}  # path -> AnalysisResult
        self._drop_queue: list[dict] = []
        self._cancel = False

    def _ensure_result_for_path(self, path_key: str):
        """Lazy ``AnalysisResult`` from file tags if the row was never analyzed."""
        if path_key in self._results:
            return self._results[path_key]
        fp = Path(path_key)
        if not fp.is_file():
            return None
        from .tagger import analysis_result_from_file

        r = analysis_result_from_file(fp)
        self._results[path_key] = r
        return r

    def get_theme(self) -> str:
        t = _read_prefs_dict().get("theme", "")
        return t if isinstance(t, str) else ""

    def set_theme(self, theme: str) -> None:
        prefs = _read_prefs_dict()
        prefs["theme"] = theme
        _write_prefs_dict(prefs)

    def get_session(self) -> dict[str, list[str]]:
        """Ordner und Dateipfade der letzten Session (nur noch vorhandene Pfade)."""
        sess = _read_prefs_dict().get("session")
        if not isinstance(sess, dict):
            return {"folders": [], "files": []}
        raw_folders = sess.get("folders", [])
        raw_files = sess.get("files", [])
        folders_out: list[str] = []
        if isinstance(raw_folders, list):
            for f in raw_folders:
                if not isinstance(f, str):
                    continue
                p = Path(f)
                if p.is_dir():
                    folders_out.append(str(p.resolve()))
        files_out: list[str] = []
        seen: set[str] = set()
        if isinstance(raw_files, list):
            for p in raw_files:
                if not isinstance(p, str):
                    continue
                fp = Path(p)
                if fp.is_file():
                    k = str(fp.resolve())
                    if k not in seen:
                        seen.add(k)
                        files_out.append(k)
        launch = _cli_launch_folder_resolved()
        if launch and launch not in folders_out:
            folders_out.insert(0, launch)
        return {"folders": folders_out, "files": files_out}

    def save_session(self, folders_json: str, files_json: str) -> None:
        import json

        try:
            folders = json.loads(folders_json)
            files = json.loads(files_json)
        except Exception:
            return
        if not isinstance(folders, list):
            folders = []
        if not isinstance(files, list):
            files = []
        prefs = _read_prefs_dict()
        prefs["session"] = {"folders": folders, "files": files}
        _write_prefs_dict(prefs)


    @property
    def _window(self):
        return self._window_ref[0]

    def _js(self, js: str) -> None:
        try:
            _evaluate_js_on_main(self._window, js)
        except Exception:
            pass

    def get_pending_drops(self) -> list[dict]:
        if self._drop_queue:
            print(f"[DnD] get_pending_drops returning {len(self._drop_queue)} items", flush=True)
        drops = list(self._drop_queue)
        self._drop_queue.clear()
        return drops

    def pick_folder(self) -> str | None:
        import webview

        # Do not wrap in _run_on_main_sync: pywebview already runs the panel on a
        # suitable thread; blocking dispatch to main deadlocks the JS↔Python bridge.
        result = self._window.create_file_dialog(
            webview.FileDialog.FOLDER, directory="~"
        )
        if result and len(result) > 0:
            return str(result[0])
        return None

    def pick_files(self) -> list[str] | None:
        import webview

        ext_filter = (
            "Audio Files (*.mp3;*.wav;*.flac;*.ogg;*.m4a;*.aac;*.opus;*.aiff)"
        )
        result = self._window.create_file_dialog(
            webview.FileDialog.OPEN, directory="~",
            allow_multiple=True, file_types=(ext_filter,),
        )
        if result and len(result) > 0:
            return [str(p) for p in result]
        return None

    def get_file_info(self, file_paths_json: str) -> list[dict]:
        """Read tags for individual files."""
        import json

        paths = json.loads(file_paths_json)
        result = []
        for i, p in enumerate(paths):
            fp = Path(p)
            if fp.is_file() and fp.suffix.lower() in AUDIO_EXTENSIONS:
                tags = _tags_with_disk_genre_mood_flag(_read_existing_tags(fp))
                tags["name"] = fp.name
                tags["folder"] = str(fp.parent)
                tags["path"] = str(fp.resolve())
                tags["index"] = i
                result.append(tags)
        return result

    def list_files(self, folder: str) -> list[dict]:
        p = Path(folder)
        files = sorted(
            f for f in p.iterdir()
            if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS
        )
        result = []
        for i, f in enumerate(files):
            tags = _tags_with_disk_genre_mood_flag(_read_existing_tags(f))
            tags["name"] = f.name
            tags["folder"] = folder
            tags["path"] = str(f.resolve())
            tags["index"] = i
            result.append(tags)
        return result

    def remove_tags(self, file_paths_json: str) -> None:
        import json
        file_paths = json.loads(file_paths_json)
        threading.Thread(
            target=self._remove_tags, args=(file_paths,), daemon=True
        ).start()

    def _remove_tags(self, file_paths: list[str]) -> None:
        from mutagen import File as MutagenFile

        total = len(file_paths)
        removed = 0
        for i, p in enumerate(file_paths):
            fp = Path(p)
            self._js(
                f'setStatus("[{i+1}/{total}] Entferne Tags: {fp.name}")'
            )
            try:
                audio = MutagenFile(str(fp), easy=False)
                if audio is not None and audio.tags is not None:
                    audio.tags.clear()
                    audio.save()
                self._clear_finder_comment(fp)
                self._results.pop(_norm_audio_path_key(p), None)
                removed += 1
            except Exception:
                pass
            pct = round((i + 1) / total * 100)
            self._js(f"setProgress({pct})")

        self._js(f'setStatus("Tags entfernt – {removed} Dateien")')
        self._js(
            "document.getElementById('removeTagsBtn').disabled = false"
        )

    @staticmethod
    def _clear_finder_comment(fp: Path) -> None:
        import subprocess
        import sys
        if sys.platform != "darwin":
            return
        posix = str(fp.resolve())
        try:
            subprocess.run(
                ["osascript", "-e",
                 f'tell application "Finder" to set comment of '
                 f'(POSIX file "{posix}" as alias) to ""'],
                capture_output=True, timeout=30,
            )
        except Exception:
            pass

    def cancel_analysis(self) -> None:
        self._cancel = True

    def start_analysis(self, items_json: str, use_ml: object) -> None:
        import json

        use_ml = _coerce_use_ml_flag(use_ml)

        blocked = getattr(self, "_analysis_blocked_reason", None)
        if blocked:
            self._js(f"setStatus({json.dumps(blocked)})")
            self._js("analysisComplete()")
            return

        self._cancel = False
        items = json.loads(items_json)
        # daemon=False: multiprocessing/subprocess children from a daemon thread
        # can hang on macOS; a normal thread is allowed to spawn workers.
        thread = threading.Thread(
            target=self._run_analysis, args=(items, use_ml), daemon=False
        )
        thread.start()

    def _analyze_batch_with_join_timeout(
        self, audio_files: list[Path], timeout_sec: float, use_ml: bool
    ):
        """Ein Kindprozess für alle Pfade — ML-Modelle nur einmal laden."""
        import json
        import time

        from .analyzer import analyze_batch_with_subprocess_timeout

        start = time.monotonic()
        n = len(audio_files)

        def on_wait_tick() -> None:
            elapsed = int(time.monotonic() - start)
            hint = (
                " — Erster ML-Lauf: TensorFlow + Modelle können 1–3 Min. brauchen; "
                "weitere Dateien in diesem Lauf sind danach deutlich schneller."
                if use_ml
                else " — Essentia im Kindprozess; erster Start oft 30–90 s."
            )
            _df = "Datei" if n == 1 else "Dateien"
            self._js(
                "setStatus("
                + json.dumps(
                    f"Analyse aktiv… {n} {_df}, seit {elapsed}s{hint}"
                )
                + ")"
            )

        return analyze_batch_with_subprocess_timeout(
            audio_files,
            use_ml,
            timeout_sec=timeout_sec,
            on_wait_tick=on_wait_tick,
            wait_tick_interval=3.0,
            cancel_check=lambda: self._cancel,
        )

    def _run_analysis(self, items: list[dict], use_ml: bool) -> None:
        import base64
        import json

        from .analyzer import (
            AnalysisSubprocessCancelled,
            _check_tf_available,
            ml_genre_pipeline_available,
            tf_ml_fallback_ready,
        )
        from .tagger import _clean_tag_list, merge_result_with_disk

        if use_ml and not ml_genre_pipeline_available():
            if _ml_essentia_tensorflow_package_only():
                _ml_msg = (
                    "essentia-tensorflow ohne TensorflowPredict* — für Genre/Mood/Instrumente zusätzlich "
                    "in derselben Python-Umgebung ``pip install tensorflow`` installieren "
                    "(TensorFlow-Fallback), oder vollständiges Essentia (Conda-Forge / essentia.upf.edu). "
                    "App neu starten. Bis dahin nur BPM/Tonart."
                )
            else:
                _ml_msg = (
                    "ML-Paket essentia-tensorflow fehlt — im Ordner beats-cli: uv sync, dann App neu starten. "
                    "Vorher werden nur BPM und Tonart gefüllt."
                )
            self._js("setStatus(" + json.dumps(_ml_msg) + ")")
            use_ml = False
        elif use_ml and tf_ml_fallback_ready() and not _check_tf_available():
            self._js(
                "setStatus("
                + json.dumps(
                    "ML: TensorFlow-Fallback (pip-Paket tensorflow) — erste Analyse lädt ggf. große Modelle …"
                )
                + ")"
            )

        total = len(items)
        if total == 0:
            self._js('setStatus("Keine Dateien ausgewählt.")')
            self._js("analysisComplete()")
            return

        work: list[tuple[str, int, Path]] = []
        for item in items:
            path_key = _norm_audio_path_key(item["path"])
            work.append((path_key, int(item["idx"]), Path(path_key)))

        audio_files = [w[2] for w in work]

        if self._cancel:
            self._js('setStatus("Abgebrochen")')
            self._js("analysisComplete()")
            return

        first_name = audio_files[0].name
        if use_ml:
            self._js(
                f'setStatus("[1/{total}] {first_name} — ein Analyse-Prozess für alle Dateien (ML einmal laden)…")'
            )
        else:
            self._js(
                f'setStatus("[1/{total}] {first_name} — Analyse startet '
                f'(Essentia lädt im Hintergrund, erste Datei oft 30–90 s)…")'
            )

        timeout_total = 0.0
        for p in audio_files:
            sz = p.stat().st_size if p.is_file() else 0
            base = 240.0 if not use_ml else 540.0
            timeout_total += min(1200.0, base + max(0, sz) / (512 * 1024))
        timeout_total = min(7200.0, max(timeout_total, 180.0))

        try:
            batch_rows = self._analyze_batch_with_join_timeout(
                audio_files, timeout_sec=timeout_total, use_ml=use_ml
            )
        except AnalysisSubprocessCancelled:
            self._js('setStatus("Abgebrochen")')
            self._js("analysisComplete()")
            return

        for i, ((path_key, row_idx, audio_file), row) in enumerate(
            zip(work, batch_rows)
        ):
            if self._cancel:
                self._js(
                    f'setStatus("Abgebrochen – {i} von {total} Dateien verarbeitet")'
                )
                self._js("analysisComplete()")
                return

            kind, payload = row
            try:
                if kind != "ok":
                    raise RuntimeError(str(payload))
                result = payload
                try:
                    from concurrent.futures import ThreadPoolExecutor
                    from concurrent.futures import TimeoutError as FTimeout

                    with ThreadPoolExecutor(max_workers=1) as pool:
                        fut = pool.submit(merge_result_with_disk, audio_file, result)
                        try:
                            result = fut.result(timeout=45.0)
                        except FTimeout:
                            pass
                except Exception:
                    pass
                result.genre_tags = _clean_tag_list(result.genre_tags)
                result.mood_tags = _clean_tag_list(result.mood_tags)
                result.instrument_tags = _clean_tag_list(result.instrument_tags)
                self._results[path_key] = result
                data = [
                    audio_file.name,
                    str(result.bpm) if result.bpm else "",
                    result.key_full if result.key else "",
                    _comma_join_tags(result.genre_tags),
                    _comma_join_tags(result.mood_tags),
                    _comma_join_tags(result.instrument_tags),
                ]
                is_speech = "true" if result.is_speech else "false"
                b64 = base64.standard_b64encode(
                    json.dumps(data, ensure_ascii=False).encode("utf-8")
                ).decode("ascii")
                self._js(
                    f"__beatsApplyRow({row_idx},{json.dumps(b64)},false,{is_speech})"
                )
                if (
                    use_ml
                    and i == 0
                    and not (
                        result.genre_tags
                        or result.mood_tags
                        or result.instrument_tags
                    )
                ):
                    self._js(
                        "setStatus("
                        + json.dumps(
                            "ML aktiv, aber keine Genre/Mood/Instrumente — "
                            "Oft fehlen TensorflowPredict* in der Essentia-Wheel (nur Input-Layer): "
                            "Vollbuild/Conda-Forge prüfen oder essentia.upf.edu."
                        )
                        + ")"
                    )
            except Exception as e:
                err_data = [audio_file.name, "", "", "", str(e), ""]
                eb64 = base64.standard_b64encode(
                    json.dumps(err_data, ensure_ascii=False).encode("utf-8")
                ).decode("ascii")
                self._js(
                    f"__beatsApplyRow({row_idx},{json.dumps(eb64)},true,false)"
                )

            pct = round((i + 1) / total * 100)
            self._js(f"setProgress({pct})")

        if use_ml:
            self._js(
                "setStatus("
                + json.dumps(f"Fertig – {total} Dateien analysiert.")
                + ")"
            )
        else:
            self._js(
                "setStatus("
                + json.dumps(
                    f"Fertig – {total} Dateien (nur BPM/Key). "
                    "Genre, Mood und Instrumente: „ML-Modelle“ ankreuzen und erneut analysieren."
                )
                + ")"
            )
        self._js("analysisComplete()")

    def _apply_edits(self, edits: dict) -> dict[str, str]:
        """Apply user edits from the UI to stored AnalysisResult objects.

        Returns a mapping of old_path -> new_path for any filename renames.
        """
        renames: dict[str, str] = {}
        for path, vals in edits.items():
            pk = _norm_audio_path_key(path)
            fp = Path(pk)
            if not fp.is_file():
                fp = Path(path)
            if "filename" in vals and vals["filename"] and vals["filename"] != fp.name:
                    new_path = fp.parent / vals["filename"]
                    try:
                        fp.rename(new_path)
                        new_key = _norm_audio_path_key(new_path)
                        if pk in self._results:
                            self._results[new_key] = self._results.pop(pk)
                        renames[path] = str(new_path)
                    except Exception:
                        pass

            actual_path = renames.get(path, path)
            apk = _norm_audio_path_key(actual_path)
            result = self._ensure_result_for_path(apk)
            if not result:
                continue
            if "bpm" in vals and vals["bpm"]:
                try:
                    result.bpm = int(vals["bpm"])
                except ValueError:
                    pass
            if "key" in vals and vals["key"]:
                parts = vals["key"].strip().split()
                if len(parts) >= 2:
                    result.key = parts[0]
                    result.scale = " ".join(parts[1:])
                elif len(parts) == 1:
                    result.key = parts[0]
            if "genre" in vals:
                g_raw = vals["genre"].strip()
                if g_raw == "-":
                    result.genre_tags = []
                elif g_raw:
                    new_g = [
                        g.strip()
                        for g in g_raw.split(",")
                        if g.strip() and g.strip() != "-"
                    ]
                    if new_g:
                        result.genre_tags = new_g
            if "mood" in vals:
                m_raw = vals["mood"].strip()
                if m_raw == "-":
                    result.mood_tags = []
                elif m_raw:
                    new_m = [
                        m.strip()
                        for m in m_raw.split(",")
                        if m.strip() and m.strip() != "-"
                    ]
                    if new_m:
                        result.mood_tags = new_m
            if "instruments" in vals:
                i_raw = vals["instruments"].strip()
                if i_raw == "-":
                    result.instrument_tags = []
                elif i_raw:
                    new_i = [
                        t.strip()
                        for t in i_raw.split(",")
                        if t.strip() and t.strip() != "-"
                    ]
                    if new_i:
                        result.instrument_tags = new_i
        return renames

    def save_tags(self, file_paths_json: str, edits_json: str = "{}", meta_json: str = "{}") -> None:
        import json
        file_paths = json.loads(file_paths_json)
        edits = json.loads(edits_json)
        meta = json.loads(meta_json)
        renames = self._apply_edits(edits)
        file_paths = [renames.get(p, p) for p in file_paths]
        threading.Thread(
            target=self._save_tags, args=(file_paths, meta), daemon=True
        ).start()

    def _save_tags(self, file_paths: list[str], meta: dict | None = None) -> None:
        from .tagger import write_finder_comment, write_tags

        total = len(file_paths)
        saved = 0
        for i, p in enumerate(file_paths):
            pk = _norm_audio_path_key(p)
            result = self._ensure_result_for_path(pk)
            if not result:
                continue
            fp = Path(pk)
            self._js(
                f'setStatus("[{i+1}/{total}] Tags: {fp.name}")'
            )
            try:
                write_tags(fp, result, meta=meta)
                write_finder_comment(fp, result)
                saved += 1
            except Exception:
                pass
            pct = round((i + 1) / total * 100)
            self._js(f"setProgress({pct})")

        self._js(f'setStatus("Tags gespeichert – {saved} Dateien")')
        self._js(
            "document.getElementById('saveTagsBtn').disabled = false"
        )

    def rename_files(self, file_paths_json: str, edits_json: str = "{}") -> None:
        import json
        file_paths = json.loads(file_paths_json)
        edits = json.loads(edits_json)
        renames = self._apply_edits(edits)
        file_paths = [renames.get(p, p) for p in file_paths]
        threading.Thread(
            target=self._rename_files, args=(file_paths,), daemon=True
        ).start()

    def _rename_files(self, file_paths: list[str]) -> None:
        from .renamer import rename_file

        total = len(file_paths)
        renamed = 0
        for i, p in enumerate(file_paths):
            pk = _norm_audio_path_key(p)
            result = self._ensure_result_for_path(pk)
            if not result:
                continue
            fp = Path(pk)
            self._js(
                f'setStatus("[{i+1}/{total}] Rename: {fp.name}")'
            )
            try:
                new_path = rename_file(fp, result)
                if new_path != fp:
                    nk = _norm_audio_path_key(new_path)
                    self._results[nk] = self._results.pop(pk)
                    escaped_old = fp.name.replace("'", "\\'")
                    escaped_new = new_path.name.replace("'", "\\'")
                    self._js(
                        f"updateFilename('{escaped_old}', '{escaped_new}')"
                    )
                    renamed += 1
            except Exception:
                pass
            pct = round((i + 1) / total * 100)
            self._js(f"setProgress({pct})")

        self._js(f'setStatus("Umbenannt – {renamed} Dateien")')
        self._js(
            "document.getElementById('renameBtn').disabled = false"
        )

    def save_all(self, file_paths_json: str, edits_json: str = "{}", meta_json: str = "{}") -> None:
        import json
        file_paths = json.loads(file_paths_json)
        edits = json.loads(edits_json)
        meta = json.loads(meta_json)
        renames = self._apply_edits(edits)
        file_paths = [renames.get(p, p) for p in file_paths]
        threading.Thread(
            target=self._save_all, args=(file_paths, meta), daemon=True
        ).start()

    def _save_all(self, file_paths: list[str], meta: dict | None = None) -> None:
        from .renamer import rename_file
        from .tagger import write_finder_comment, write_tags

        total = len(file_paths)
        saved = 0
        renamed = 0

        for i, p in enumerate(file_paths):
            pk = _norm_audio_path_key(p)
            result = self._ensure_result_for_path(pk)
            if not result:
                continue
            fp = Path(pk)
            self._js(
                f'setStatus("[{i+1}/{total}] {fp.name}")'
            )
            try:
                write_tags(fp, result, meta=meta)
                saved += 1
            except Exception:
                pass
            try:
                new_path = rename_file(fp, result)
                if new_path != fp:
                    nk = _norm_audio_path_key(new_path)
                    self._results[nk] = self._results.pop(pk)
                    escaped_old = fp.name.replace("'", "\\'")
                    escaped_new = new_path.name.replace("'", "\\'")
                    self._js(
                        f"updateFilename('{escaped_old}', '{escaped_new}')"
                    )
                    write_finder_comment(new_path, result)
                    renamed += 1
                else:
                    write_finder_comment(fp, result)
            except Exception:
                pass
            pct = round((i + 1) / total * 100)
            self._js(f"setProgress({pct})")

        self._js(
            f'setStatus("Gespeichert – {saved} Tags, {renamed} umbenannt")'
        )
        self._js(
            "document.getElementById('saveAllBtn').disabled = false;"
            "document.getElementById('saveTagsBtn').disabled = false;"
            "document.getElementById('renameBtn').disabled = false"
        )


def _darwin_extend_dyld_for_sdl2() -> None:
    """Prepend likely lib dirs so Essentia can dlopen libSDL2 (bundle / Conda / Homebrew)."""
    from .darwin_bundle import extend_dyld_fallback_for_sdl2

    extend_dyld_fallback_for_sdl2()


def _darwin_sdl2_available() -> bool:
    """True if libSDL2 can be dlopen'd (Essentia needs it on macOS)."""
    import ctypes
    import sys

    from .darwin_bundle import bundled_sdl2_dylib_path, essentia_wheel_sdl2_path

    if sys.platform != "darwin":
        return True
    mode = getattr(ctypes, "RTLD_GLOBAL", 8)
    _ew = essentia_wheel_sdl2_path()
    if _ew and os.path.isfile(_ew):
        try:
            ctypes.CDLL(_ew, mode=mode)
            return True
        except OSError:
            pass
    _bd = bundled_sdl2_dylib_path()
    if _bd and os.path.isfile(_bd):
        try:
            ctypes.CDLL(_bd, mode=mode)
            return True
        except OSError:
            pass
    for d in os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "").split(":"):
        d = d.strip()
        if not d or not os.path.isdir(d):
            continue
        for name in ("libSDL2-2.0.0.dylib", "libSDL2.dylib"):
            fp = os.path.join(d, name)
            if os.path.isfile(fp):
                try:
                    ctypes.CDLL(fp, mode=mode)
                    return True
                except OSError:
                    break
    for soname in ("libSDL2-2.0.0.dylib", "libSDL2.dylib"):
        try:
            ctypes.CDLL(soname)
            return True
        except OSError:
            continue
    return False


def _print_sdl2_install_hint() -> None:
    from .darwin_bundle import sdl2_macos_python_arch_mismatch_hint

    arch = sdl2_macos_python_arch_mismatch_hint()
    extra = f"  {arch}\n" if arch else ""
    print(
        "\n[beats-cli] SDL2 kann nicht geladen werden (Essentia braucht libSDL2).\n"
        + extra
        + "  Installieren, dann App neu starten:\n"
        "    Conda:    conda install -c conda-forge sdl2\n"
        "    Homebrew: brew install sdl2\n"
        "  (Ohne Paketmanager: https://github.com/libsdl-org/SDL/releases — "
        "dylib nach ~/lib oder in CONDA_PREFIX/lib legen.)\n",
        flush=True,
    )


def main() -> None:
    import sys

    import webview

    _darwin_extend_dyld_for_sdl2()
    sdl2_ok = True
    if sys.platform == "darwin":
        sdl2_ok = _darwin_sdl2_available()
        if not sdl2_ok:
            _print_sdl2_install_hint()

    # Limit BLAS/OpenMP threads — nested parallelism with GUI/WebKit can segfault.
    for _k in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ.setdefault(_k, "1")

    # Do not import Essentia in the GUI process — SDL/native init can freeze the
    # main window; analysis runs only in subprocess children (analyzer.py).

    audio_port = _start_audio_server()
    html = HTML.replace("__AUDIO_PORT__", str(audio_port))

    window_ref: list = [None]
    api = Api(window_ref)
    if sys.platform == "darwin" and not sdl2_ok:
        from .darwin_bundle import sdl2_macos_python_arch_mismatch_hint

        _arch = sdl2_macos_python_arch_mismatch_hint()
        api._analysis_blocked_reason = (
            (_arch + " ")
            if _arch
            else (
                "SDL2 fehlt — Analyse nicht möglich. "
                "Terminal: brew install sdl2 (oder conda install -c conda-forge sdl2), dann App neu starten. "
                "Bei der .app aus dem DMG: auf dem Build-Rechner „brew install sdl2“, dann ./build_app.sh erneut."
            )
        )
    else:
        api._analysis_blocked_reason = None

    window = webview.create_window(
        "Beat Analyzer",
        html=html,
        js_api=api,
        width=960,
        height=700,
        min_size=(700, 500),
        resizable=True,
    )
    window_ref[0] = window

    _swizzle_refs = []

    def _install_native_drop() -> None:
        """Swizzle WKWebView's drag methods via the ObjC runtime (ctypes)."""
        try:
            import ctypes

            import objc
            from Foundation import NSURL

            libobjc = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
            libobjc.objc_getClass.restype = ctypes.c_void_p
            libobjc.objc_getClass.argtypes = [ctypes.c_char_p]
            libobjc.sel_registerName.restype = ctypes.c_void_p
            libobjc.sel_registerName.argtypes = [ctypes.c_char_p]
            libobjc.class_getInstanceMethod.restype = ctypes.c_void_p
            libobjc.class_getInstanceMethod.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p,
            ]
            libobjc.method_getImplementation.restype = ctypes.c_void_p
            libobjc.method_getImplementation.argtypes = [ctypes.c_void_p]
            libobjc.method_setImplementation.restype = ctypes.c_void_p
            libobjc.method_setImplementation.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p,
            ]

            wk_cls = libobjc.objc_getClass(b"WKWebView")

            def _eval_bg(js: str) -> None:
                def _run() -> None:
                    try:
                        _evaluate_js_on_main(window, js)
                    except Exception as ex:
                        print(f"[DnD] JS: {ex}", flush=True)

                threading.Thread(target=_run, daemon=True).start()

            # --- draggingEntered: → accept file drops ---
            ENTERED_T = ctypes.CFUNCTYPE(
                ctypes.c_ulong,
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            )
            entered_sel = libobjc.sel_registerName(b"draggingEntered:")
            entered_m = libobjc.class_getInstanceMethod(wk_cls, entered_sel)
            orig_entered = ENTERED_T(libobjc.method_getImplementation(entered_m))

            def new_entered(self_p, sel_p, sender_p):
                try:
                    sender = objc.objc_object(c_void_p=ctypes.c_void_p(sender_p))
                    types = sender.draggingPasteboard().types()
                    if types and "public.file-url" in types:
                        _eval_bg(
                            "document.getElementById('folderSection')"
                            ".classList.add('drag-over')"
                        )
                        return 1  # NSDragOperationCopy
                except Exception:
                    pass
                return orig_entered(self_p, sel_p, sender_p)

            imp_entered = ENTERED_T(new_entered)
            libobjc.method_setImplementation(entered_m, imp_entered)
            _swizzle_refs.append(imp_entered)

            # --- draggingUpdated: → keep accepting the drop ---
            UPDATED_T = ctypes.CFUNCTYPE(
                ctypes.c_ulong,
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            )
            updated_sel = libobjc.sel_registerName(b"draggingUpdated:")
            updated_m = libobjc.class_getInstanceMethod(wk_cls, updated_sel)

            def new_updated(self_p, sel_p, sender_p):
                try:
                    sender = objc.objc_object(c_void_p=ctypes.c_void_p(sender_p))
                    types = sender.draggingPasteboard().types()
                    if types and "public.file-url" in types:
                        return 1  # NSDragOperationCopy
                except Exception:
                    pass
                return 0

            imp_updated = UPDATED_T(new_updated)
            if updated_m:
                libobjc.method_setImplementation(updated_m, imp_updated)
            _swizzle_refs.append(imp_updated)

            # --- draggingExited: → remove highlight ---
            EXITED_T = ctypes.CFUNCTYPE(
                None,
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            )
            exited_sel = libobjc.sel_registerName(b"draggingExited:")
            exited_m = libobjc.class_getInstanceMethod(wk_cls, exited_sel)

            def new_exited(self_p, sel_p, sender_p):
                _eval_bg(
                    "document.getElementById('folderSection')"
                    ".classList.remove('drag-over')"
                )

            imp_exited = EXITED_T(new_exited)
            if exited_m:
                libobjc.method_setImplementation(exited_m, imp_exited)
            _swizzle_refs.append(imp_exited)

            # --- prepareForDragOperation: → always accept ---
            PREPARE_T = ctypes.CFUNCTYPE(
                ctypes.c_bool,
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            )
            prepare_sel = libobjc.sel_registerName(b"prepareForDragOperation:")
            prepare_m = libobjc.class_getInstanceMethod(wk_cls, prepare_sel)

            def new_prepare(self_p, sel_p, sender_p):
                return True

            imp_prepare = PREPARE_T(new_prepare)
            if prepare_m:
                libobjc.method_setImplementation(prepare_m, imp_prepare)
            _swizzle_refs.append(imp_prepare)

            # --- performDragOperation: → extract paths ---
            PERFORM_T = ctypes.CFUNCTYPE(
                ctypes.c_bool,
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            )
            perform_sel = libobjc.sel_registerName(b"performDragOperation:")
            perform_m = libobjc.class_getInstanceMethod(wk_cls, perform_sel)
            orig_perform = PERFORM_T(libobjc.method_getImplementation(perform_m))

            def new_perform(self_p, sel_p, sender_p):
                print("[DnD] performDragOperation called", flush=True)
                try:
                    sender = objc.objc_object(c_void_p=ctypes.c_void_p(sender_p))
                    pboard = sender.draggingPasteboard()
                    urls = pboard.readObjectsForClasses_options_([NSURL], None)
                    print(f"[DnD] urls={urls}", flush=True)
                    if urls:
                        for url in urls:
                            if url.isFileReferenceURL():
                                url = url.filePathURL()
                            path = str(url.path())
                            print(f"[DnD] resolved path={path}", flush=True)
                            api._drop_queue.append({
                                "path": path,
                                "is_dir": Path(path).is_dir(),
                            })
                        print(f"[DnD] queued {len(urls)} items, queue size={len(api._drop_queue)}", flush=True)
                        _eval_bg(
                            "document.getElementById('folderSection')"
                            ".classList.remove('drag-over')"
                        )
                        return True
                except Exception as e:
                    print(f"[DnD] perform error: {e}", flush=True)
                return orig_perform(self_p, sel_p, sender_p)

            imp_perform = PERFORM_T(new_perform)
            libobjc.method_setImplementation(perform_m, imp_perform)
            _swizzle_refs.append(imp_perform)

            print("[DnD] WKWebView methods swizzled", flush=True)
        except Exception as e:
            print(f"[DnD] Swizzle failed: {e}", flush=True)

    window.events.loaded += _install_native_drop
    webview.start(debug=False)


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    main()
