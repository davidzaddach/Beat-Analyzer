# beats-cli / Beat Analyzer

Audio-Analyse (BPM, Tonart, Genre, Stimmung, Instrumente) und ID3-Tags — GUI (`python -m beats_cli`) und CLI.

## Voraussetzungen

- Python **3.11–3.13** (siehe `requires-python` in `pyproject.toml`)
- **macOS** für die pywebview-GUI
- Für die Analyse: **SDL2** (z. B. `brew install sdl2` oder Conda `conda-forge::sdl2`), sonst meldet die App einen Hinweis

## Installation (Entwicklung)

Im Projektordner `beats-cli`:

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

**essentia-tensorflow** (Genre/Mood/Instrumente) ist **feste Abhängigkeit** — `pip install -e .` reicht. Ältere Anleitungen mit `.[ml]` sind weiterhin möglich (leeres Extra).

**Wichtig:** Entwicklung am Repo immer als **editable** installieren (`-e`), damit GUI und Analyse-Subprozess dieselbe Codebasis nutzen. Nur `pip install beats-cli` ohne `-e` kann zu einer veralteten Kopie in `site-packages` führen.

Mit **uv** (wie in `build_app.sh`):

```bash
uv sync
```

## Start

```bash
.venv/bin/python -m beats_cli
```

Nach Änderungen an Abhängigkeiten oder wenn etwas „hängen bleibt“: App beenden und neu starten.

## Arbeit auf mehreren Macs

Damit dieselbe Codebasis auf **jedem Rechner** zuverlässig läuft (pywebview-GUI inklusive):

1. **Ein Git-Remote nutzen**  
   Änderungen committen und pushen; auf dem anderen Mac nur noch **pullen** — keine parallelen Ordner-Kopien ohne Versionshistorie als „zweite Wahrheit“.

2. **`.venv` nicht übertragen**  
   Virtualenvs enthalten maschinenspezifische Pfade. Repo klonen bzw. aktualisieren, dann auf jedem Mac neu:  
   `uv sync`

3. **`uv.lock` ins Repository committen**  
   So sind die **Python-Paketversionen** auf allen Macs gleich; verbleibende Unterschiede betreffen vor allem **Systembibliotheken** (SDL2) und die **installierte Python-Minor-Version** innerhalb von `requires-python`.

4. **SDL2 auf jedem Mac installieren**  
   Siehe [Voraussetzungen](#voraussetzungen). Ohne SDL2 schlägt die Analyse fehl oder die App blockiert die Analyse mit einem Hinweis.

5. **Kurzer Check nach `git pull`**  
   `uv sync` → `uv run python -m beats_cli` → eine Testdatei analysieren (mit den Optionen, die du normal nutzt, z. B. mit ML).

6. **Optional: Bundle testen**  
   Wenn du `Beat Analyzer.app` verteilst: nach größeren Änderungen `./build_app.sh` auf dem **Ziel-Mac** einmal bauen oder die gebaute App dort kurz starten.

Die GUI-Analyse läuft absichtlich in einem **Subprozess** (siehe `analyzer.py` / `webgui.py`), damit Essentia und WebKit nicht im gleichen Prozess kollidieren — das erhöht die **Portabilität zwischen macOS-Versionen** gegenüber einer rein in-process-Analyse.

## macOS-App bündeln

Voraussetzung: `.venv` mit `uv sync` (siehe oben).

**SDL2:** Essentia braucht `libSDL2`. Beim Build wird sie aus Homebrew nach `Contents/Resources/lib` kopiert und zusätzlich neben die Essentia-Extension gelegt; `install_name_tool` setzt `LC_RPATH`, damit der Start aus dem Finder auch ohne wirksames `DYLD_LIBRARY_PATH` funktioniert (sonst meldet Essentia oft „Failed loading SDL2 library“). Auf dem Build-Mac zuerst **`brew install sdl2`**, sonst warnt `build_app.sh`.

```bash
brew install sdl2   # einmalig auf dem Rechner, der ./build_app.sh ausführt
./build_app.sh
./create_dmg.sh   # optional: DMG für Verteilung
```

Siehe `create_dmg.sh` für die Installation per Drag & Drop auf einem anderen Mac.
