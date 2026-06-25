"""CLI entry point for beats-cli audio analysis tool."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .analyzer import AnalysisResult, analyze
from .renamer import build_new_filename, copy_file, rename_file
from .tagger import write_finder_comment, write_tags

app = typer.Typer(
    name="beats-cli",
    help="Audio analysis CLI — detects BPM, key, genre, mood and writes ID3 tags.",
    no_args_is_help=True,
)
console = Console()

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".aiff"}


def _collect_audio_files(path: Path) -> list[Path]:
    if path.is_file():
        if path.suffix.lower() in AUDIO_EXTENSIONS:
            return [path]
        console.print(f"[yellow]Skipping non-audio file: {path.name}[/yellow]")
        return []

    files = sorted(
        f for f in path.rglob("*") if f.suffix.lower() in AUDIO_EXTENSIONS
    )
    return files


def _print_result(result: AnalysisResult) -> None:
    parts = [
        f"BPM: [bold cyan]{result.bpm}[/bold cyan]",
        f"Key: [bold green]{result.key_full}[/bold green] ({result.key_strength:.0%})",
    ]
    if result.genre_tags:
        parts.append(f"Genre: [bold magenta]{', '.join(result.genre_tags)}[/bold magenta]")
    if result.mood_tags:
        parts.append(f"Mood: [bold yellow]{', '.join(result.mood_tags)}[/bold yellow]")
    if result.instrument_tags:
        parts.append(f"Inst: [bold blue]{', '.join(result.instrument_tags)}[/bold blue]")
    if result.is_instrumental:
        parts.append("[dim]Instrumental[/dim]")
    elif result.is_vocal:
        parts.append("[dim]Vocal[/dim]")
    if result.is_acoustic:
        parts.append("[dim]Acoustic[/dim]")
    if result.is_aggressive:
        parts.append("[bold red]Aggressive[/bold red]")

    console.print("  " + "  |  ".join(parts))


def _print_summary(results: list[tuple[Path, AnalysisResult, Path]]) -> None:
    if not results:
        return

    table = Table(title="Analysis Summary", show_lines=True)
    table.add_column("Original", style="dim", max_width=35)
    table.add_column("Result", style="cyan", max_width=40)
    table.add_column("BPM", justify="center")
    table.add_column("Key", justify="center")
    table.add_column("Genre", max_width=30)
    table.add_column("Mood", max_width=20)

    for original, result, final_path in results:
        table.add_row(
            original.name,
            final_path.name if final_path != original else "—",
            str(result.bpm),
            result.key_full,
            ", ".join(result.genre_tags) if result.genre_tags else "-",
            ", ".join(result.mood_tags) if result.mood_tags else "-",
        )

    console.print()
    console.print(table)


@app.command()
def analyze_cmd(
    path: Path = typer.Argument(..., help="Audio file or directory to analyze"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Copy results to this directory (keeps originals untouched)"),
    write_id3: bool = typer.Option(True, "--id3/--no-id3", help="Write ID3 tags to files"),
    do_rename: bool = typer.Option(True, "--rename/--no-rename", help="Rename files with BPM and key"),
    ml: bool = typer.Option(False, "--ml", help="Use ML models for genre/mood (requires essentia-tensorflow)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would happen without making changes"),
):
    """Analyze audio files: detect BPM, key, genre, and mood."""
    if not path.exists():
        console.print(f"[red]Path not found: {path}[/red]")
        raise typer.Exit(1)

    files = _collect_audio_files(path)
    if not files:
        console.print("[yellow]No audio files found.[/yellow]")
        raise typer.Exit(1)

    if output and not dry_run:
        output.mkdir(parents=True, exist_ok=True)
        console.print(f"[dim]Output directory: {output}[/dim]")

    console.print(f"\n[bold]Scanning {len(files)} file(s)...[/bold]\n")

    results: list[tuple[Path, AnalysisResult, Path]] = []

    for i, audio_file in enumerate(files, 1):
        console.print(f"[dim][{i}/{len(files)}][/dim] [bold]{audio_file.name}[/bold]")

        try:
            result = analyze(audio_file, use_ml=ml)
            _print_result(result)

            final_path = audio_file

            if dry_run:
                if write_id3:
                    console.print("  [dim]Would write ID3 tags[/dim]")
                if do_rename:
                    new_name = build_new_filename(audio_file, result).name
                    if new_name != audio_file.name:
                        if output:
                            console.print(f"  [dim]Would copy → {output / new_name}[/dim]")
                        else:
                            console.print(f"  [dim]Would rename → {new_name}[/dim]")
            else:
                if output:
                    if write_id3:
                        tagged = write_tags(audio_file, result)
                        status = "written" if tagged else f"skipped ({audio_file.suffix})"
                        console.print(f"  [{'green' if tagged else 'yellow'}]ID3 tags {status}[/]")

                    if do_rename:
                        final_path = copy_file(audio_file, result, output)
                        console.print(f"  [green]Copied → {final_path.name}[/green]")
                    else:
                        import shutil
                        dest = output / audio_file.name
                        shutil.copy2(str(audio_file), str(dest))
                        final_path = dest
                        console.print(f"  [green]Copied → {dest.name}[/green]")
                else:
                    if write_id3:
                        tagged = write_tags(audio_file, result)
                        status = "written" if tagged else f"skipped ({audio_file.suffix})"
                        console.print(f"  [{'green' if tagged else 'yellow'}]ID3 tags {status}[/]")

                    if do_rename:
                        new_path = rename_file(audio_file, result)
                        if new_path != audio_file:
                            console.print(f"  [green]Renamed → {new_path.name}[/green]")
                            final_path = new_path

            if not dry_run:
                write_finder_comment(final_path, result)

            results.append((audio_file, result, final_path))
            console.print()

        except Exception as e:
            console.print(f"  [red]Error: {e}[/red]\n")

    _print_summary(results)

    action = "copied" if output else "processed"
    console.print(f"\n[bold green]Done![/bold green] {len(results)}/{len(files)} files {action}.\n")


if __name__ == "__main__":
    app()
