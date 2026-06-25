"""Rename or copy audio files to include BPM and key in the filename."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from .analyzer import AnalysisResult

_BPM_PATTERN = re.compile(r"[\s_\-]*\d{2,3}\s*bpm", re.IGNORECASE)


def _strip_existing_bpm(stem: str) -> str:
    """Remove existing BPM references from a filename stem.

    Handles: '104bpm', '104BPM', '104 bpm', '_104bpm', '-104bpm', ' 104bpm'
    """
    cleaned = _BPM_PATTERN.sub("", stem)
    cleaned = re.sub(r"[\s_\-]+$", "", cleaned)
    return cleaned or stem


def build_new_filename(original: Path, result: AnalysisResult) -> Path:
    """Build a new filename with BPM and key appended, removing any existing BPM.

    Example: biorap104bpm.mp3 -> biorap_104BPM_Dmaj.mp3
    """
    stem = _strip_existing_bpm(original.stem)
    ext = original.suffix
    suffix = f"_{result.bpm}BPM_{result.key_short}"

    if suffix in stem:
        return original

    return original.with_name(f"{stem}{suffix}{ext}")


def rename_file(original: Path, result: AnalysisResult) -> Path:
    """Rename a file in-place to include BPM and key. Returns the new path."""
    new_path = build_new_filename(original, result)
    if new_path == original:
        return original

    if new_path.exists():
        raise FileExistsError(f"Target file already exists: {new_path}")

    original.rename(new_path)
    return new_path


def copy_file(original: Path, result: AnalysisResult, output_dir: Path) -> Path:
    """Copy a file to output_dir with BPM and key in the filename.

    Keeps the original file untouched.
    """
    new_name = build_new_filename(original, result).name
    dest = output_dir / new_name

    if dest.exists():
        raise FileExistsError(f"Target file already exists: {dest}")

    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(original), str(dest))
    return dest
