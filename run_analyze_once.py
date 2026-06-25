#!/usr/bin/env python3
"""Eine Analyse pro Prozess — liegt im Projektroot, damit ``import beats_cli`` immer
dieses Repository nutzt (nicht ein zufälliges Paket aus site-packages vor dem Pfad).

Wird von ``analyze_with_subprocess_timeout`` bevorzugt statt ``-m beats_cli.analyze_child``.
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 4:
        return 2
    root = Path(__file__).resolve().parent
    rp = str(root)
    if rp not in sys.path:
        sys.path.insert(0, rp)

    audio_path = Path(sys.argv[1])
    use_ml = sys.argv[2] == "1"
    out_path = Path(sys.argv[3])
    try:
        from beats_cli.analyzer import analyze

        r = analyze(audio_path, use_ml=use_ml)
        out_path.write_bytes(pickle.dumps(("ok", r)))
    except Exception as e:
        try:
            out_path.write_bytes(pickle.dumps(("err", str(e))))
        except OSError:
            return 1
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
