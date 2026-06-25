"""Pfade innerhalb einer gebündelten macOS-.app (minimal, keine schweren Imports)."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _path_bases() -> list[Path]:
    """Startpunkte zum Hochlaufen der Verzeichnishierarchie."""
    bases: list[Path] = []
    try:
        bases.append(Path(sys.executable).resolve())
    except OSError:
        pass
    try:
        if sys.argv and sys.argv[0]:
            a0 = Path(sys.argv[0])
            if a0.is_file():
                rp = a0.resolve()
                if rp not in bases:
                    bases.append(rp)
    except OSError:
        pass
    try:
        rf = Path(__file__).resolve()
        if rf not in bases:
            bases.append(rf)
    except OSError:
        pass
    return bases


def macos_app_bundle_root() -> Path | None:
    """Oberstes ``…/*.app``, falls erkennbar (nicht alle Pfade liefern ``.suffix == '.app'`` zuverlässig)."""
    if sys.platform != "darwin":
        return None
    libdir = bundled_sdl2_lib_dir()
    if libdir:
        try:
            # …/App.app/Contents/Resources/lib → drei Ebenen hoch = .app
            p = Path(libdir).resolve()
            app_candidate = p.parent.parent.parent
            if app_candidate.suffix == ".app" and app_candidate.is_dir():
                return app_candidate
        except (OSError, ValueError):
            pass
    try:
        for base in _path_bases():
            for parent in base.parents:
                if parent.suffix == ".app" and parent.is_dir():
                    return parent
    except (OSError, ValueError):
        pass
    return None


def bundled_sdl2_lib_dir() -> str | None:
    """``…/Contents/Resources/lib`` mit von ``build_app.sh`` kopierter SDL2.

    Erkennung über den Ordnernamen ``Resources`` + ``lib/libSDL2*.dylib`` — robuster
    als nur ``*.app`` in den Elternpfaden (Translocation, Hilfsstarter, …).
    """
    if sys.platform != "darwin":
        return None
    try:
        for base in _path_bases():
            for d in base.parents:
                if d.name != "Resources":
                    continue
                libdir = d / "lib"
                if (libdir / "libSDL2-2.0.0.dylib").is_file():
                    return str(libdir)
                if (libdir / "libSDL2.dylib").is_file():
                    return str(libdir)
    except (OSError, ValueError):
        pass
    return None


def bundled_sdl2_dylib_path() -> str | None:
    d = bundled_sdl2_lib_dir()
    if not d:
        return None
    for name in ("libSDL2-2.0.0.dylib", "libSDL2.dylib"):
        p = Path(d) / name
        if p.is_file():
            return str(p)
    return None


def extend_dyld_fallback_for_sdl2() -> None:
    """Vor Essentia/SDL: typische lib-Verzeichnisse in DYLD_FALLBACK_LIBRARY_PATH.

    Gleiche Logik wie in der GUI — auch für ``beats-cli analyze`` und Analyse-Subprozesse,
    die nicht über ``webgui.main()`` laufen.
    """
    if sys.platform != "darwin":
        return
    dirs: list[str] = []
    ew = essentia_wheel_sdl2_path()
    if ew:
        _d = str(Path(ew).parent)
        if os.path.isdir(_d):
            dirs.append(_d)
    bundled = bundled_sdl2_lib_dir()
    if bundled:
        dirs.append(bundled)
    conda = os.environ.get("CONDA_PREFIX")
    if conda:
        clib = os.path.join(conda, "lib")
        if os.path.isdir(clib):
            dirs.append(clib)
    home_lib = os.path.expanduser("~/lib")
    if os.path.isdir(home_lib) and home_lib not in dirs:
        dirs.append(home_lib)
    for p in ("/opt/homebrew/lib", "/usr/local/lib"):
        if os.path.isdir(p) and p not in dirs:
            dirs.append(p)
    if not dirs:
        return
    extra = ":".join(dirs)
    prev = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = extra if not prev else f"{extra}:{prev}"


def _darwin_process_is_translated() -> bool:
    """True, wenn dieser Prozess unter Rosetta läuft (Apple Silicon + x86_64-Python)."""
    import subprocess

    try:
        p = subprocess.run(
            ["sysctl", "-n", "sysctl.proc_translated"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return p.returncode == 0 and p.stdout.strip() == "1"
    except (OSError, subprocess.TimeoutExpired):
        return False


def sdl2_macos_python_arch_mismatch_hint() -> str | None:
    """Homebrew liefert auf Apple Silicon arm64-Dylibs; Rosetta-Python braucht x86_64-SDL."""
    if sys.platform != "darwin" or not _darwin_process_is_translated():
        return None
    return (
        "Dieses Python läuft unter Rosetta (x86_64); Homebrew/SDL2 ist arm64. "
        "Auf Apple Silicon explizit arm64-Python für uv nutzen, z. B. "
        "`cd beats-cli && rm -rf .venv && uv python install cpython-3.11.15-macos-aarch64-none && "
        "uv venv --python cpython-3.11.15-macos-aarch64-none && uv sync` "
        "(nur „3.11“ kann sonst wieder ein gecachtes x86_64-Python wählen)."
    )


def macos_dyld_sdl_prefix() -> str | None:
    """Reihenfolge für DYLD_*: Essentia-.dylibs, dann gebündeltes ``Resources/lib``."""
    parts: list[str] = []
    ew = essentia_wheel_sdl2_path()
    if ew:
        d = str(Path(ew).parent)
        if os.path.isdir(d) and d not in parts:
            parts.append(d)
    bd = bundled_sdl2_lib_dir()
    if bd and bd not in parts:
        parts.append(bd)
    if not parts:
        return None
    return os.pathsep.join(parts)


def essentia_wheel_sdl2_path() -> str | None:
    """SDL2 neben dem Essentia-Wheel — vor Homebrew laden, sonst zwei libSDL2 → SDL/ObjC-Fehler."""
    if sys.platform != "darwin":
        return None
    try:
        import sysconfig

        platlib = Path(sysconfig.get_path("platlib"))
        for name in ("libSDL2-2.0.0.dylib", "libSDL2.dylib"):
            p = platlib / "essentia" / ".dylibs" / name
            if p.is_file():
                return str(p)
    except (OSError, ValueError):
        pass
    return None


def _sdl2_dylib_candidates_macos() -> list[str]:
    seen: set[str] = set()
    out: list[str] = []

    def _add(path: str | None) -> None:
        if path and os.path.isfile(path) and path not in seen:
            seen.add(path)
            out.append(path)

    # Wheel-.dylibs zuerst: gleiche libSDL wie die Essentia-Extension; sonst + Resources/lib
    # (build_app.sh) zwei Exemplare → SDL/ObjC „Failed loading SDL2“.
    _add(essentia_wheel_sdl2_path())
    _add(bundled_sdl2_dylib_path())
    conda = os.environ.get("CONDA_PREFIX")
    if conda:
        for name in ("libSDL2-2.0.0.dylib", "libSDL2.dylib"):
            _add(os.path.join(conda, "lib", name))
    _add(os.path.expanduser("~/lib/libSDL2-2.0.0.dylib"))
    _add("/opt/homebrew/lib/libSDL2-2.0.0.dylib")
    _add("/usr/local/lib/libSDL2-2.0.0.dylib")
    return out


def preload_sdl2_library_macos() -> None:
    """Lädt libSDL2 per absolutem Pfad mit RTLD_GLOBAL — vor Essentia (MonoLoader).

    Unter macOS ignoriert das System ``DYLD_FALLBACK_LIBRARY_PATH`` für viele Prozesse;
    ein explizites ``ctypes.CDLL`` ist zuverlässiger. Architektur muss zu Python passen.
    """
    import ctypes

    if sys.platform != "darwin":
        return
    extend_dyld_fallback_for_sdl2()
    mode = getattr(ctypes, "RTLD_GLOBAL", 8)
    last_err: OSError | None = None
    for path in _sdl2_dylib_candidates_macos():
        try:
            ctypes.CDLL(path, mode=mode)
            return
        except OSError as e:
            last_err = e
            continue
    arch = sdl2_macos_python_arch_mismatch_hint()
    if arch:
        raise RuntimeError(
            "SDL2 konnte nicht geladen werden (Architektur-Mismatch). " + arch
        ) from last_err
    extra = (
        "brew install sdl2; bei Conda: conda install -c conda-forge sdl2. "
        f"Letzter Fehler: {last_err}"
        if last_err
        else "brew install sdl2; bei Conda: conda install -c conda-forge sdl2."
    )
    raise RuntimeError("SDL2 konnte nicht geladen werden. " + extra) from last_err
