"""Isolated analysis worker: ``python -u -m beats_cli.analyze_child <path> <0|1> <out.pkl>``.

Loaded as ``__main__`` in a subprocess — keeps imports minimal until ``main()`` runs.
"""

from __future__ import annotations


def main() -> int:
    import pickle
    import sys
    from pathlib import Path

    if len(sys.argv) != 4:
        return 2
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
