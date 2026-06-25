"""Write analysis results into audio file ID3/metadata tags using mutagen."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.id3 import COMM, TBPM, TCON, TKEY, TPE1, TCOM, TXXX, WXXX, ID3
from mutagen.mp3 import MP3
from mutagen.wave import WAVE

from .analyzer import AnalysisResult

SUPPORTED_ID3_EXTENSIONS = {".mp3"}
SUPPORTED_WAVE_EXTENSIONS = {".wav", ".wave"}
SUPPORTED_VORBIS_EXTENSIONS = {".flac", ".ogg", ".opus"}
SUPPORTED_MP4_EXTENSIONS = {".m4a", ".mp4", ".aac"}


def _id3_text_join(frame) -> str:
    """Join human-readable text from a mutagen ID3 frame (TCON, TBPM, TKEY, TXXX, …)."""
    if frame is None:
        return ""
    text = getattr(frame, "text", None)
    if text is None:
        return ""
    if isinstance(text, (list, tuple)):
        parts: list[str] = []
        for x in text:
            if x is None:
                continue
            if isinstance(x, bytes):
                parts.append(x.decode("utf-8", errors="replace"))
            else:
                parts.append(str(x))
        return ", ".join(parts)
    if isinstance(text, bytes):
        return text.decode("utf-8", errors="replace")
    return str(text)


def _norm_txxx_desc(d) -> str:
    """Normalize TXXX description for comparison (mutagen may use str or bytes)."""
    if d is None:
        return ""
    if isinstance(d, bytes):
        return d.decode("latin-1", errors="replace").strip().lower()
    return str(d).strip().lower()


def _apply_beats_comment_segments(tags: dict[str, str], blob: str) -> None:
    """Fill empty genre/mood/instruments from ``_build_comment``-style ``|``-separated text."""
    if not (blob or "").strip():
        return
    for part in blob.split("|"):
        p = part.strip()
        if p.startswith("Genre:") and not (tags.get("genre") or "").strip():
            tags["genre"] = p[len("Genre:") :].strip()
        elif p.startswith("Mood:") and not (tags.get("mood") or "").strip():
            tags["mood"] = p[len("Mood:") :].strip()
        elif p.startswith("Instruments:") and not (tags.get("instruments") or "").strip():
            tags["instruments"] = p[len("Instruments:") :].strip()


def _gather_embedded_notes(t, ext: str) -> str:
    """Concatenate user-visible comment fields that may carry our Genre/Mood/Instruments line."""
    ext = ext.lower()
    try:
        if ext in {".mp3", ".wav", ".wave"} and hasattr(t, "getall"):
            return " ".join(_id3_text_join(c) for c in t.getall("COMM"))
        if ext in {".flac", ".ogg", ".opus"} and hasattr(t, "get"):
            parts = t.get("COMMENT") or []
            return " ".join(str(x) for x in parts)
        if ext in {".m4a", ".mp4", ".aac"} and hasattr(t, "get"):
            parts = t.get("\xa9cmt") or []
            return " ".join(str(x) for x in parts)
    except Exception:
        pass
    return ""


def _read_easy_tags_for_wav_aiff(audio_path: Path, tags: dict[str, str]) -> None:
    """Populate list columns from mutagen easy tags (WAV/AIFF INFO etc.)."""
    ext = audio_path.suffix.lower()
    if ext not in {".wav", ".aiff", ".aif", ".aifc"}:
        return
    try:
        easy = MutagenFile(str(audio_path), easy=True)
        if easy is None or easy.tags is None:
            return
        et = easy.tags

        def first(key: str) -> str:
            v = et.get(key)
            if v is None:
                return ""
            if isinstance(v, list):
                return str(v[0]) if v else ""
            return str(v)

        if not (tags.get("bpm") or "").strip():
            tb = first("bpm")
            if tb:
                tags["bpm"] = tb
        if not (tags.get("key") or "").strip():
            tk = first("key")
            if tk:
                tags["key"] = tk
        if not (tags.get("genre") or "").strip():
            tags["genre"] = first("genre")
        if not (tags.get("mood") or "").strip():
            tags["mood"] = first("mood")
        if not (tags.get("instruments") or "").strip():
            tags["instruments"] = first("instrument") or first("instruments")
    except Exception:
        pass


def _read_id3_into_tags(t, tags: dict[str, str]) -> None:
    """Fülle dict aus ID3-Frames (MP3 und WAV mit RIFF-id3-Chunk)."""
    if t.get("TBPM"):
        tags["bpm"] = _id3_text_join(t["TBPM"])
    if t.get("TKEY"):
        tags["key"] = _id3_text_join(t["TKEY"])
    if t.get("TCON"):
        tags["genre"] = _id3_text_join(t["TCON"])
    if not (tags["genre"] or "").strip():
        for fr in t.getall("TCON"):
            s = _id3_text_join(fr)
            if s.strip():
                tags["genre"] = s
                break
    for txxx in t.getall("TXXX"):
        desc = _norm_txxx_desc(getattr(txxx, "desc", ""))
        if desc == "mood":
            tags["mood"] = _id3_text_join(txxx)
        elif desc == "instruments":
            tags["instruments"] = _id3_text_join(txxx)


def read_existing_tags(audio_path: Path) -> dict[str, str]:
    """Read BPM, key, genre, mood, instruments as strings (same keys as legacy GUI helper)."""
    tags: dict[str, str] = {
        "bpm": "",
        "key": "",
        "genre": "",
        "mood": "",
        "instruments": "",
    }
    try:
        audio = MutagenFile(str(audio_path), easy=False)
        if audio is None:
            return tags

        ext = audio_path.suffix.lower()
        t = audio.tags
        wav_like = ext in SUPPORTED_WAVE_EXTENSIONS
        aiff_like = ext in {".aiff", ".aif", ".aifc"}

        if t is None:
            if wav_like or aiff_like:
                _read_easy_tags_for_wav_aiff(audio_path, tags)
            return tags

        if ext == ".mp3" or wav_like:
            _read_id3_into_tags(t, tags)

        elif ext in {".flac", ".ogg", ".opus"}:
            tags["bpm"] = t.get("BPM", [""])[0] if t.get("BPM") else ""
            tags["key"] = t.get("KEY", [""])[0] if t.get("KEY") else ""
            tags["genre"] = t.get("GENRE", [""])[0] if t.get("GENRE") else ""
            tags["mood"] = t.get("MOOD", [""])[0] if t.get("MOOD") else ""
            tags["instruments"] = (
                t.get("INSTRUMENTS", [""])[0] if t.get("INSTRUMENTS") else ""
            )

        elif ext in {".m4a", ".mp4", ".aac"}:
            if t.get("tmpo"):
                tags["bpm"] = str(t["tmpo"][0])
            if t.get("\xa9gen"):
                tags["genre"] = t["\xa9gen"][0]
            if not (tags["genre"] or "").strip():
                itg = t.get("----:com.apple.iTunes:GENRE")
                if itg:
                    tags["genre"] = bytes(itg[0]).decode(
                        "utf-8", errors="ignore"
                    )
            key_tag = t.get("----:com.apple.iTunes:KEY")
            if key_tag:
                tags["key"] = bytes(key_tag[0]).decode("utf-8", errors="ignore")
            mood_tag = t.get("----:com.apple.iTunes:MOOD")
            if mood_tag:
                tags["mood"] = bytes(mood_tag[0]).decode("utf-8", errors="ignore")
            inst_tag = t.get("----:com.apple.iTunes:INSTRUMENTS")
            if inst_tag:
                tags["instruments"] = bytes(inst_tag[0]).decode(
                    "utf-8", errors="ignore"
                )
        elif aiff_like:
            _read_easy_tags_for_wav_aiff(audio_path, tags)
        else:
            _read_easy_tags_for_wav_aiff(audio_path, tags)

        notes = _gather_embedded_notes(t, ext) if t is not None else ""
        if notes.strip():
            _apply_beats_comment_segments(tags, notes)
    except Exception:
        pass
    for k in ("genre", "mood", "instruments"):
        if tags[k].strip() == "-":
            tags[k] = ""
    return tags


def _disk_genre_as_list(s: str) -> list[str]:
    """Single ID3/Vorbis genre field — do not split on commas (e.g. 'Rock, AOR')."""
    s = (s or "").strip()
    if not s or s == "-":
        return []
    return [s]


def _disk_csv_as_list(s: str) -> list[str]:
    """Mood/instruments we store as comma-separated — round-trip split."""
    if not (s or "").strip():
        return []
    parts = [x.strip() for x in str(s).split(",")]
    return _clean_tag_list(parts)


def _parse_key_disk(s: str) -> tuple[str, str]:
    """Split stored key string (e.g. ``C major``) into note + scale."""
    s = (s or "").strip()
    if not s:
        return "", ""
    parts = s.split(None, 1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def analysis_result_from_file(audio_path: Path) -> AnalysisResult:
    """Build ``AnalysisResult`` from tags on disk (no Essentia run).

    Used when the GUI shows files (session restore / folder load) but the row was
    never analyzed — so Save/Rename still has an object to merge and write.
    """
    disk = read_existing_tags(audio_path)
    bpm = 0
    try:
        raw_bpm = (disk.get("bpm") or "").strip()
        if raw_bpm:
            bpm = int(float(raw_bpm))
    except ValueError:
        bpm = 0
    key, scale = _parse_key_disk(disk.get("key", ""))
    g = _disk_genre_as_list(disk.get("genre", ""))
    m = _disk_csv_as_list(disk.get("mood", ""))
    i = _disk_csv_as_list(disk.get("instruments", ""))
    return AnalysisResult(
        bpm=bpm,
        key=key,
        scale=scale,
        key_strength=0.0,
        genre_tags=g,
        mood_tags=m,
        instrument_tags=i,
    )


def _norm_tag_fold(s: str) -> str:
    return str(s).strip().lower()


def _merge_tag_lists_disk_first(
    disk_list: list[str],
    ml_list: list[str],
    *,
    max_items: int | None = None,
) -> list[str]:
    """Vorhandene Datei-Tags behalten, ML-/Analyse-Tags hinten anhängen (ohne Duplikate)."""
    seen: set[str] = set()
    out: list[str] = []
    for bucket in (disk_list, ml_list):
        for x in bucket:
            if x is None:
                continue
            s = str(x).strip()
            if not s or s == "-":
                continue
            k = _norm_tag_fold(s)
            if k in seen:
                continue
            seen.add(k)
            out.append(s)
            if max_items is not None and len(out) >= max_items:
                return out
    return out


def merge_result_with_disk(audio_path: Path, result: AnalysisResult) -> AnalysisResult:
    """Genre/Mood/Instrumente: bestehende Datei-Tags + Analyse kombinieren.

    BPM/Key kommen immer aus der Analyse (``result``). Für die drei Text-Spalten
    bleiben Werte von der Platte erhalten; neue ML-Tags werden ergänzt statt
    die alten zu ersetzen, wenn beides vorhanden ist.
    """
    disk = read_existing_tags(audio_path)
    dg = _disk_genre_as_list(disk.get("genre", ""))
    dm = _disk_csv_as_list(disk.get("mood", ""))
    di = _disk_csv_as_list(disk.get("instruments", ""))
    rg = _clean_tag_list(result.genre_tags)
    rm = _clean_tag_list(result.mood_tags)
    ri = _clean_tag_list(result.instrument_tags)

    g = _merge_tag_lists_disk_first(dg, rg, max_items=8)
    m = _merge_tag_lists_disk_first(dm, rm, max_items=24)
    i = _merge_tag_lists_disk_first(di, ri, max_items=24)
    return replace(result, genre_tags=g, mood_tags=m, instrument_tags=i)


def write_tags(audio_path: Path, result: AnalysisResult, *, meta: dict | None = None) -> bool:
    """Write analysis results as metadata tags into the audio file.

    Returns True if tags were written, False if format is unsupported.
    """
    result = merge_result_with_disk(audio_path, result)
    ext = audio_path.suffix.lower()

    if ext in SUPPORTED_ID3_EXTENSIONS:
        return _write_id3_tags(audio_path, result, meta=meta)
    elif ext in SUPPORTED_WAVE_EXTENSIONS:
        return _write_wave_tags(audio_path, result, meta=meta)
    elif ext in SUPPORTED_VORBIS_EXTENSIONS:
        return _write_vorbis_tags(audio_path, result, meta=meta)
    elif ext in SUPPORTED_MP4_EXTENSIONS:
        return _write_mp4_tags(audio_path, result, meta=meta)

    return False


def write_finder_comment(file_path: Path, result: AnalysisResult) -> None:
    """Write a macOS Finder comment (visible in Finder -> Get Info -> Comments).

    Call this on the final file path (after any copy/rename).
    """
    if sys.platform != "darwin":
        return
    _write_finder_comment(file_path, merge_result_with_disk(file_path, result))


def _write_finder_comment(audio_path: Path, result: AnalysisResult) -> None:
    """Write a macOS Finder comment (visible in Finder list view Comments column)."""
    import subprocess

    comment = _build_comment(result)
    escaped = comment.replace("\\", "\\\\").replace('"', '\\"')
    posix = str(audio_path.resolve())
    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                f'tell application "Finder" to set comment of '
                f'(POSIX file "{posix}" as alias) to "{escaped}"',
            ],
            capture_output=True,
            timeout=30,
        )
    except Exception:
        pass


def _clean_tag_list(tags: list[str] | None) -> list[str]:
    """Non-empty, non-placeholder tag strings (avoids writing empty TCON / TXXX)."""
    if tags is None:
        return []
    if isinstance(tags, (str, bytes)):
        s = (
            tags.decode("utf-8", errors="replace")
            if isinstance(tags, bytes)
            else tags
        ).strip()
        return [s] if s and s != "-" else []
    out: list[str] = []
    try:
        for t in tags:
            if t is None:
                continue
            s = str(t).strip()
            if s and s != "-":
                out.append(s)
    except TypeError:
        return []
    return out


def _genre_write_value(genres: list[str]) -> str:
    """Ein Genre-Feld für TCON/©gen/GENRE (mehrere Labels mit Komma)."""
    g = _clean_tag_list(genres)
    if not g:
        return ""
    return ", ".join(g[:8])


def _has_values(tags: list[str]) -> bool:
    """True if there is at least one real tag string (not ``['']`` or only ``'-'``)."""
    return bool(_clean_tag_list(tags))


def _build_comment(result: AnalysisResult) -> str:
    """Build a human-readable comment string for Finder/iTunes display."""
    parts = [
        f"BPM: {result.bpm}",
        f"Key: {result.key_full}",
    ]
    if _has_values(result.genre_tags):
        parts.append(f"Genre: {', '.join(_clean_tag_list(result.genre_tags))}")
    if _has_values(result.mood_tags):
        parts.append(f"Mood: {', '.join(_clean_tag_list(result.mood_tags))}")
    if _has_values(result.instrument_tags):
        parts.append(f"Instruments: {', '.join(_clean_tag_list(result.instrument_tags))}")
    if result.is_instrumental:
        parts.append("Instrumental")
    elif result.is_vocal:
        parts.append("Vocal")
    if result.is_acoustic:
        parts.append("Acoustic")
    if result.is_aggressive:
        parts.append("Aggressive")
    return " | ".join(parts)


def _apply_id3_frames(tags: ID3, result: AnalysisResult, *, meta: dict | None = None) -> None:
    """Gemeinsame ID3v2-Frames für MP3 und WAV (RIFF id3-Chunk)."""
    tags.delall("TBPM")
    tags.add(TBPM(encoding=3, text=[str(result.bpm)]))
    tags.delall("TKEY")
    tags.add(TKEY(encoding=3, text=[result.key_full]))

    genres = _clean_tag_list(result.genre_tags)
    if genres:
        tags.delall("TCON")
        tags.add(TCON(encoding=3, text=[_genre_write_value(result.genre_tags)]))

    def _txxx_keep_specs() -> list[tuple[int, str, list]]:
        have_m = _has_values(result.mood_tags)
        have_i = _has_values(result.instrument_tags)
        out: list[tuple[int, str, list]] = []
        for f in tags.getall("TXXX"):
            dn = _norm_txxx_desc(getattr(f, "desc", ""))
            if dn == "mood" and have_m:
                continue
            if dn == "instruments" and have_i:
                continue
            enc = getattr(f, "encoding", 3) or 3
            desc_raw = getattr(f, "desc", "") or ""
            if isinstance(desc_raw, bytes):
                desc_s = desc_raw.decode("latin-1", errors="replace")
            else:
                desc_s = str(desc_raw)
            tx = f.text
            if tx is None:
                text_list: list = []
            elif isinstance(tx, str):
                text_list = [tx]
            elif isinstance(tx, (list, tuple)):
                text_list = list(tx)
            else:
                text_list = [tx]
            out.append((int(enc), desc_s, text_list))
        return out

    txxx_specs = _txxx_keep_specs()
    tags.delall("TXXX")
    for enc, desc_s, text_list in txxx_specs:
        tags.add(TXXX(encoding=enc, desc=desc_s, text=text_list))
    moods = _clean_tag_list(result.mood_tags)
    if moods:
        tags.add(TXXX(encoding=3, desc="mood", text=[", ".join(moods)]))
    inst = _clean_tag_list(result.instrument_tags)
    if inst:
        tags.add(
            TXXX(
                encoding=3,
                desc="instruments",
                text=[", ".join(inst)],
            )
        )

    if meta:
        if meta.get("artist"):
            tags.add(TPE1(encoding=3, text=[meta["artist"]]))
        if meta.get("composer"):
            tags.add(TCOM(encoding=3, text=[meta["composer"]]))
        if meta.get("instagram"):
            url = meta["instagram"]
            if not url.startswith("http"):
                url = "https://" + url
            tags.add(WXXX(encoding=3, desc="Instagram", url=url))

    tags.add(COMM(encoding=3, lang="eng", desc="", text=[_build_comment(result)]))


def _write_id3_tags(audio_path: Path, result: AnalysisResult, *, meta: dict | None = None) -> bool:
    """Write ID3v2 tags to MP3 files."""
    audio = MP3(str(audio_path))

    if audio.tags is None:
        audio.add_tags()

    tags: ID3 = audio.tags  # type: ignore[assignment]
    _apply_id3_frames(tags, result, meta=meta)
    audio.save()
    return True


def _write_wave_tags(audio_path: Path, result: AnalysisResult, *, meta: dict | None = None) -> bool:
    """ID3v2 in RIFF/WAVE id3-Chunk (u. a. von mutagen, viele Player lesen das)."""
    audio = WAVE(str(audio_path))
    if audio.tags is None:
        audio.add_tags()
    tags: ID3 = audio.tags  # type: ignore[assignment]
    _apply_id3_frames(tags, result, meta=meta)
    audio.save()
    return True


def _write_vorbis_tags(audio_path: Path, result: AnalysisResult, *, meta: dict | None = None) -> bool:
    """Write Vorbis comments to FLAC/OGG files."""
    audio = MutagenFile(str(audio_path))
    if audio is None or audio.tags is None:
        return False

    audio.tags["BPM"] = [str(result.bpm)]
    audio.tags["KEY"] = [result.key_full]
    audio.tags["COMMENT"] = [_build_comment(result)]

    genres = _clean_tag_list(result.genre_tags)
    if genres:
        audio.tags["GENRE"] = [_genre_write_value(result.genre_tags)]

    moods = _clean_tag_list(result.mood_tags)
    if moods:
        audio.tags["MOOD"] = [", ".join(moods)]

    inst = _clean_tag_list(result.instrument_tags)
    if inst:
        audio.tags["INSTRUMENTS"] = [", ".join(inst)]

    if meta:
        if meta.get("artist"):
            audio.tags["ARTIST"] = [meta["artist"]]
        if meta.get("composer"):
            audio.tags["COMPOSER"] = [meta["composer"]]
        if meta.get("instagram"):
            url = meta["instagram"]
            if not url.startswith("http"):
                url = "https://" + url
            audio.tags["CONTACT"] = [url]

    audio.save()
    return True


def _write_mp4_tags(audio_path: Path, result: AnalysisResult, *, meta: dict | None = None) -> bool:
    """Write MP4/M4A tags."""
    audio = MutagenFile(str(audio_path))
    if audio is None:
        return False

    if audio.tags is None:
        audio.add_tags()

    audio.tags["tmpo"] = [result.bpm]
    audio.tags["----:com.apple.iTunes:KEY"] = [result.key_full.encode("utf-8")]
    audio.tags["\xa9cmt"] = [_build_comment(result)]

    genres = _clean_tag_list(result.genre_tags)
    if genres:
        audio.tags["\xa9gen"] = [_genre_write_value(result.genre_tags)]

    moods = _clean_tag_list(result.mood_tags)
    if moods:
        audio.tags["----:com.apple.iTunes:MOOD"] = [
            ", ".join(moods).encode("utf-8")
        ]

    inst = _clean_tag_list(result.instrument_tags)
    if inst:
        audio.tags["----:com.apple.iTunes:INSTRUMENTS"] = [
            ", ".join(inst).encode("utf-8")
        ]

    if meta:
        if meta.get("artist"):
            audio.tags["\xa9ART"] = [meta["artist"]]
        if meta.get("composer"):
            audio.tags["\xa9wrt"] = [meta["composer"]]
        if meta.get("instagram"):
            url = meta["instagram"]
            if not url.startswith("http"):
                url = "https://" + url
            audio.tags["----:com.apple.iTunes:URL"] = [url.encode("utf-8")]

    audio.save()
    return True
