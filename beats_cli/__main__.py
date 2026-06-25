"""Minimal entry for ``python -m beats_cli`` so multiprocessing spawn children
import almost nothing (see webgui — deferred pywebview import)."""

from __future__ import annotations


def main() -> None:
    import multiprocessing
    import sys
    from pathlib import Path

    # Ordner mit ``beats_cli/`` vorn auf den Pfad — gleiche Quelle wie Subprozess-Analyse.
    _rp = str(Path(__file__).resolve().parent.parent)
    if _rp not in sys.path:
        sys.path.insert(0, _rp)

    multiprocessing.freeze_support()
    from .webgui import main as run_gui

    run_gui()


if __name__ == "__main__":
    main()
