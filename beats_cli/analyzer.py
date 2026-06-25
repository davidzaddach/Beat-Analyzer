"""Audio analysis using Essentia - BPM, key, and optional ML-based genre/mood detection."""

from __future__ import annotations

import json
import logging
import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"
os.environ["ABSL_MIN_LOG_LEVEL"] = "3"
warnings.filterwarnings("ignore", category=DeprecationWarning)
logging.getLogger("essentia").setLevel(logging.ERROR)
logging.getLogger("tensorflow").setLevel(logging.ERROR)
logging.getLogger("absl").setLevel(logging.ERROR)

# Essentia nur in ``analyze*`` / ``_load_audio`` importieren — sonst lädt die GUI
# beim Import von ``analyze_with_subprocess_timeout`` SDL/Native mit.

KEY_SHORT_MAP = {
    "C": "C",
    "C#": "Cs",
    "Db": "Db",
    "D": "D",
    "D#": "Ds",
    "Eb": "Eb",
    "E": "E",
    "F": "F",
    "F#": "Fs",
    "Gb": "Gb",
    "G": "G",
    "G#": "Gs",
    "Ab": "Ab",
    "A": "A",
    "A#": "As",
    "Bb": "Bb",
    "B": "B",
}

SCALE_SHORT_MAP = {
    "major": "maj",
    "minor": "min",
}


@dataclass
class AnalysisResult:
    bpm: int
    key: str
    scale: str
    key_strength: float
    genre_tags: list[str] = field(default_factory=list)
    mood_tags: list[str] = field(default_factory=list)
    instrument_tags: list[str] = field(default_factory=list)
    is_instrumental: bool = False
    is_vocal: bool = False
    is_acoustic: bool = False
    is_aggressive: bool = False
    is_speech: bool = False
    music_confidence: float = 1.0

    @property
    def key_short(self) -> str:
        """Short key notation for filenames, e.g. 'Cmaj', 'Amin'."""
        note = KEY_SHORT_MAP.get(self.key, self.key)
        sc = SCALE_SHORT_MAP.get(self.scale, self.scale)
        return f"{note}{sc}"

    @property
    def key_full(self) -> str:
        """Full key notation for display, e.g. 'C major', 'A minor'."""
        return f"{self.key} {self.scale}"


_ML_MODELS_DIR = Path.home() / ".beats-cli" / "models"

_MODELS_BASE = "https://essentia.upf.edu/models"
_DISCOGS_EFFNET_URL = f"{_MODELS_BASE}/feature-extractors/discogs-effnet/discogs-effnet-bs64-1.pb"
_GENRE_MODEL_URL = f"{_MODELS_BASE}/classification-heads/genre_discogs400/genre_discogs400-discogs-effnet-1.pb"
_GENRE_META_URL = f"{_MODELS_BASE}/classification-heads/genre_discogs400/genre_discogs400-discogs-effnet-1.json"
_MOOD_THEME_URL = f"{_MODELS_BASE}/classification-heads/mtg_jamendo_moodtheme/mtg_jamendo_moodtheme-discogs-effnet-1.pb"
_MOOD_THEME_META_URL = f"{_MODELS_BASE}/classification-heads/mtg_jamendo_moodtheme/mtg_jamendo_moodtheme-discogs-effnet-1.json"
_VOICE_INSTRUMENTAL_URL = f"{_MODELS_BASE}/classification-heads/voice_instrumental/voice_instrumental-discogs-effnet-1.pb"
_INSTRUMENT_URL = f"{_MODELS_BASE}/classification-heads/mtg_jamendo_instrument/mtg_jamendo_instrument-discogs-effnet-1.pb"
_INSTRUMENT_META_URL = f"{_MODELS_BASE}/classification-heads/mtg_jamendo_instrument/mtg_jamendo_instrument-discogs-effnet-1.json"
_ACOUSTIC_URL = f"{_MODELS_BASE}/classification-heads/mood_acoustic/mood_acoustic-discogs-effnet-1.pb"
_AGGRESSIVE_URL = f"{_MODELS_BASE}/classification-heads/mood_aggressive/mood_aggressive-discogs-effnet-1.pb"


def _coerce_tag_string_list(items) -> list[str]:
    """Plain str list for pickle/UI (no numpy types); drop empty / placeholder."""
    if items is None:
        return []
    if isinstance(items, (str, bytes)):
        s = (
            items.decode("utf-8", errors="replace")
            if isinstance(items, bytes)
            else items
        ).strip()
        return [s] if s and s != "-" else []
    out: list[str] = []
    seen: set[str] = set()
    try:
        for x in items:
            if x is None:
                continue
            s = str(x).strip()
            if not s or s == "-":
                continue
            if s in seen:
                continue
            seen.add(s)
            out.append(s)
    except TypeError:
        return []
    return out


def _clean_genre_label(label: str) -> str:
    """Convert Discogs label format to readable genre.

    'Electronic---House' -> 'House'
    'Hip Hop---Boom Bap' -> 'Boom Bap'
    'Pop' -> 'Pop'
    """
    if "---" in label:
        return label.split("---", 1)[1]
    return label


def _download_model(url: str) -> Path:
    """Download a model file if not already cached."""
    filename = url.rsplit("/", 1)[-1]
    dest = _ML_MODELS_DIR / filename
    if dest.exists():
        return dest
    _ML_MODELS_DIR.mkdir(parents=True, exist_ok=True)
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "beats-cli/0.1"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        dest.write_bytes(resp.read())
    return dest


def _load_audio(path: Path, sample_rate: int = 44100) -> np.ndarray:
    import sys

    if sys.platform == "darwin":
        from .darwin_bundle import preload_sdl2_library_macos

        preload_sdl2_library_macos()

    from essentia.standard import MonoLoader

    loader = MonoLoader(filename=str(path), sampleRate=sample_rate)
    return loader()


def analyze_bpm_key_array(
    audio: np.ndarray, sample_rate: int = 44100
) -> AnalysisResult:
    """BPM/Tonart aus bereits geladenem Mono-Signal (kein erneutes Datei-Lesen)."""
    from essentia.standard import FrameGenerator, KeyExtractor, RhythmExtractor2013

    rhythm_extractor = RhythmExtractor2013(method="multifeature")
    bpm, ticks, confidence, _estimates, _intervals = rhythm_extractor(audio)

    key_extractor = KeyExtractor(profileType="edma")
    key, scale, strength = key_extractor(audio)

    is_speech = False
    music_conf = 1.0

    frames = list(FrameGenerator(audio, frameSize=2048, hopSize=1024))
    if frames:
        energies = [float(np.sum(f ** 2)) for f in frames]
        mean_energy = np.mean(energies)

        if mean_energy < 1e-8:
            is_speech = True
            music_conf = 0.0
        else:
            silent = sum(1 for e in energies if e < 0.0001)
            silent_ratio = silent / len(frames)
            energy_cv = float(np.std(energies) / mean_energy)
            duration = len(audio) / float(sample_rate)

            if silent_ratio > 0.6 and energy_cv > 2.0:
                is_speech = True
                music_conf = round(1.0 - silent_ratio, 3)
            elif confidence < 0:
                is_speech = True
                music_conf = 0.0
            elif confidence < 0.5 and len(ticks) < 10:
                is_speech = True
                music_conf = round(float(confidence), 3)
            elif duration < 5.0 and confidence < 1.0 and len(ticks) < 15:
                is_speech = True
                music_conf = round(float(confidence), 3)

    return AnalysisResult(
        bpm=round(bpm),
        key=key,
        scale=scale,
        key_strength=round(float(strength), 3),
        is_speech=is_speech,
        music_confidence=music_conf,
    )


def analyze_bpm_key(audio_path: Path) -> AnalysisResult:
    """Analyze BPM and musical key using standard Essentia algorithms."""
    audio = _load_audio(audio_path, sample_rate=44100)
    return analyze_bpm_key_array(audio, sample_rate=44100)


def _check_tf_available() -> bool:
    """True wenn die Essentia-Binary die Discogs-Effnet- und 2D-TF-Predict-Algorithmen enthält.

    Manche ``essentia-tensorflow``-Wheels exposen nur ``TensorflowInput*`` — dann kein Genre/Mood.
    """
    try:
        import essentia.standard as es

        return hasattr(es, "TensorflowPredictEffnetDiscogs") and hasattr(
            es, "TensorflowPredict2D"
        )
    except Exception:
        return False


def _essentia_mel_chain_available() -> bool:
    """TensorflowInputMusiCNN + FrameGenerator (liefert Mel-Bänder für EffNet-Discogs)."""
    try:
        import essentia.standard as es

        return hasattr(es, "TensorflowInputMusiCNN") and hasattr(es, "FrameGenerator")
    except Exception:
        return False


def _try_import_tensorflow():
    """Liefert das tensorflow-Modul oder None (Apple Silicon: ``pip install tensorflow``)."""
    try:
        import tensorflow as tf

        return tf
    except Exception:
        return None


def tf_ml_fallback_ready() -> bool:
    """Workaround: macOS-PyPI-Wheels ohne TensorflowPredict*, aber mit Mel-Extraktion + pip-TensorFlow."""
    if _check_tf_available():
        return False
    return _essentia_mel_chain_available() and _try_import_tensorflow() is not None


def ml_genre_pipeline_available() -> bool:
    """True, wenn Genre/Mood/Instrument-ML laufen kann (Essentia-Predict oder TF-Fallback)."""
    return _check_tf_available() or tf_ml_fallback_ready()


def _suppress_essentia_logging() -> None:
    """Suppress Essentia's internal INFO logging for TF model loads."""
    try:
        import essentia

        essentia.log.infoActive = False
        essentia.log.warningActive = False
    except Exception:
        pass


_model_cache: dict[str, object] = {}
_meta_cache: dict[str, list[str]] = {}
_tf_session_cache: dict[str, tuple[object, object]] = {}


def _tf_graph_session(tf_mod, pb_path: Path) -> tuple[object, object]:
    """Ein Graph + compat.v1.Session pro Modellpfad (wiederverwendbar)."""
    key = str(pb_path.resolve())
    if key in _tf_session_cache:
        return _tf_session_cache[key]
    graph = tf_mod.Graph()
    with graph.as_default():
        gd = tf_mod.compat.v1.GraphDef()
        gd.ParseFromString(pb_path.read_bytes())
        tf_mod.compat.v1.import_graph_def(gd, name="")
    sess = tf_mod.compat.v1.Session(graph=graph)
    _tf_session_cache[key] = (graph, sess)
    return graph, sess


def _tf_get_tensor(graph, *candidates: str):
    for base in candidates:
        for name in (base, f"{base}:0"):
            try:
                return graph.get_tensor_by_name(name)
            except (KeyError, ValueError):
                continue
    raise KeyError(f"Keiner der Tensor-Namen: {candidates}")


def _mel_spectrogram_frames_effnet(audio_16k: np.ndarray) -> np.ndarray:
    """Melierte Log-Bänder wie in TensorflowPredictEffnetDiscogs (512/256 @ 16 kHz)."""
    import essentia.standard as es

    audio_16k = np.asarray(audio_16k, dtype=np.float32).ravel()
    mel = es.TensorflowInputMusiCNN()
    rows: list[np.ndarray] = []
    for frame in es.FrameGenerator(
        audio_16k, frameSize=512, hopSize=256, startFromZero=False
    ):
        rows.append(np.asarray(mel(frame), dtype=np.float32))
    if not rows:
        return np.zeros((0, 96), dtype=np.float32)
    return np.vstack(rows)


def _mel_frames_to_effnet_patches(
    mel: np.ndarray, patch_size: int = 128, patch_hop: int = 62
) -> np.ndarray:
    """(n_mel_frames, 96) -> (n_patches, patch_size, 96), lastPatchMode=discard."""
    if mel.size == 0:
        return np.zeros((0, patch_size, 96), dtype=np.float32)
    if mel.shape[1] != 96:
        raise ValueError(f"Erwarte 96 Mel-Bänder, got {mel.shape[1]}")
    patches: list[np.ndarray] = []
    t = 0
    while t + patch_size <= len(mel):
        patches.append(mel[t : t + patch_size])
        t += patch_hop
    if not patches:
        return np.zeros((0, patch_size, 96), dtype=np.float32)
    return np.stack(patches, axis=0)


def _tf_run_effnet_discogs(
    tf_mod, pb_path: Path, patches: np.ndarray, *, batch_size: int = 64
) -> np.ndarray:
    """Frozen EffNet: Input (N,128,96) -> Embeddings (N, D). Modell bs64."""
    if patches.size == 0:
        return np.zeros((0, 1280), dtype=np.float32)
    graph, sess = _tf_graph_session(tf_mod, pb_path)
    in_t = _tf_get_tensor(
        graph,
        "serving_default_melspectrogram",
        "import/serving_default_melspectrogram",
    )
    out_t = _tf_get_tensor(graph, "PartitionedCall:1")
    n = len(patches)
    pad = (batch_size - (n % batch_size)) % batch_size
    if pad:
        z = np.zeros((pad, 128, 96), dtype=np.float32)
        feed_patches = np.concatenate([patches, z], axis=0)
    else:
        feed_patches = patches
    outs: list[np.ndarray] = []
    for i in range(0, len(feed_patches), batch_size):
        # Graph erwartet (batch, 128, 96), nicht (batch, 1, 128, 96).
        batch = feed_patches[i : i + batch_size].reshape(batch_size, 128, 96)
        emb = sess.run(out_t, feed_dict={in_t: batch})
        emb = np.asarray(emb, dtype=np.float32)
        if emb.ndim == 1:
            emb = emb.reshape(batch_size, -1)
        outs.append(emb)
    stacked = np.vstack(outs)[:n]
    return stacked


def _tf_run_classifier_2d(
    tf_mod,
    pb_path: Path,
    embeddings: np.ndarray,
    input_candidates: tuple[str, ...],
    output_candidates: tuple[str, ...],
    *,
    batch_size: int = 64,
) -> np.ndarray:
    """Kopf-Graph: (N, D) Sequenz -> (N, n_classes)."""
    if embeddings.size == 0:
        return np.zeros((0, 1), dtype=np.float32)
    graph, sess = _tf_graph_session(tf_mod, pb_path)
    in_t = _tf_get_tensor(graph, *input_candidates)
    out_t = _tf_get_tensor(graph, *output_candidates)
    n, d = embeddings.shape
    pad = (batch_size - (n % batch_size)) % batch_size
    if pad:
        feed_e = np.vstack([embeddings, np.zeros((pad, d), dtype=np.float32)])
    else:
        feed_e = embeddings
    outs: list[np.ndarray] = []
    for i in range(0, len(feed_e), batch_size):
        chunk = feed_e[i : i + batch_size]
        # Typische EffNet-Köpfe: Placeholder (batch, D), nicht 4D.
        bat = chunk.reshape(batch_size, d)
        pr = sess.run(out_t, feed_dict={in_t: bat})
        pr = np.asarray(pr, dtype=np.float32)
        if pr.ndim == 1:
            pr = pr.reshape(batch_size, -1)
        outs.append(pr)
    return np.vstack(outs)[:n]


def _analyze_genre_mood_tf_fallback(
    audio_path: Path,
    result: AnalysisResult,
    *,
    audio_16k: np.ndarray | None = None,
) -> AnalysisResult:
    """Genre/Mood/Instrumente ohne TensorflowPredict* — Mel via Essentia, Graphen via pip-TensorFlow."""
    tf_mod = _try_import_tensorflow()
    if tf_mod is None or not _essentia_mel_chain_available():
        return result

    _suppress_essentia_logging()

    if audio_16k is None:
        audio_16k = _load_audio(audio_path, sample_rate=16000)

    log = logging.getLogger(__name__)
    try:
        mel = _mel_spectrogram_frames_effnet(audio_16k)
        patches = _mel_frames_to_effnet_patches(mel)
        eff_path = _download_model(_DISCOGS_EFFNET_URL)
        embeddings = _tf_run_effnet_discogs(tf_mod, eff_path, patches)
    except Exception as exc:
        log.warning("ML TensorFlow-Fallback (EffNet): %s", exc)
        return result

    if embeddings.size == 0 or embeddings.shape[0] == 0:
        return result

    def _head(
        url: str,
        in_c: tuple[str, ...],
        out_c: tuple[str, ...],
    ) -> np.ndarray:
        p = _download_model(url)
        return _tf_run_classifier_2d(tf_mod, p, embeddings, in_c, out_c)

    # Genre (top-5 Schwellen wie Essentia-Pfad)
    try:
        genre_preds = _head(
            _GENRE_MODEL_URL,
            ("serving_default_model_Placeholder",),
            ("PartitionedCall:0",),
        )
        avg_preds = np.ravel(np.mean(genre_preds, axis=0))
        genre_classes = _get_classes(_GENRE_META_URL)
        if genre_classes:
            ng = min(len(avg_preds), len(genre_classes))
            ap = avg_preds[:ng]
            top_indices = np.argsort(ap)[-5:][::-1]
            seen: set[str] = set()
            for i in top_indices:
                if ap[i] < 0.08:
                    break
                label = str(_clean_genre_label(genre_classes[i])).strip()
                if not label or label == "-":
                    continue
                if label not in seen:
                    seen.add(label)
                    result.genre_tags.append(label)
                if len(result.genre_tags) >= 3:
                    break
            if not result.genre_tags:
                j = int(np.argmax(ap))
                lab = str(_clean_genre_label(genre_classes[j])).strip()
                if lab and lab != "-":
                    result.genre_tags.append(lab)
    except Exception as exc:
        log.warning("ML TensorFlow-Fallback (Genre): %s", exc)

    try:
        mood_preds = _head(
            _MOOD_THEME_URL, ("model/Placeholder",), ("model/Sigmoid",)
        )
        avg_mood = np.ravel(np.mean(mood_preds, axis=0))
        mood_classes = _get_classes(_MOOD_THEME_META_URL)
        if mood_classes:
            nm = min(len(avg_mood), len(mood_classes))
            am = avg_mood[:nm]
            top_mood_indices = np.argsort(am)[-8:][::-1]
            result.mood_tags = [
                str(mood_classes[i]).strip()
                for i in top_mood_indices
                if am[i] > 0.03 and str(mood_classes[i]).strip()
            ]
            if not result.mood_tags:
                for i in np.argsort(am)[-3:][::-1]:
                    s = str(mood_classes[int(i)]).strip()
                    if s and s not in result.mood_tags:
                        result.mood_tags.append(s)
    except Exception as exc:
        log.warning("ML TensorFlow-Fallback (Mood): %s", exc)

    try:
        voice_preds = _head(
            _VOICE_INSTRUMENTAL_URL,
            ("model/Placeholder",),
            ("model/Softmax",),
        )
        avg_voice = np.mean(voice_preds, axis=0)
        if len(avg_voice) >= 2 and avg_voice[0] > 0.7:
            result.is_instrumental = True
        elif len(avg_voice) >= 2 and avg_voice[1] > 0.7:
            result.is_vocal = True
    except Exception as exc:
        log.warning("ML TensorFlow-Fallback (Voice/Inst): %s", exc)

    try:
        inst_preds = _head(
            _INSTRUMENT_URL, ("model/Placeholder",), ("model/Sigmoid",)
        )
        avg_inst = np.ravel(np.mean(inst_preds, axis=0))
        inst_classes = _get_classes(_INSTRUMENT_META_URL)
        if inst_classes:
            ni = min(len(avg_inst), len(inst_classes))
            ai = avg_inst[:ni]
            top_inst_indices = np.argsort(ai)[-5:][::-1]
            result.instrument_tags = [
                str(inst_classes[i]).strip()
                for i in top_inst_indices
                if ai[i] > 0.06 and str(inst_classes[i]).strip()
            ]
            if not result.instrument_tags:
                for i in np.argsort(ai)[-4:][::-1]:
                    s = str(inst_classes[int(i)]).strip()
                    if s and s not in result.instrument_tags:
                        result.instrument_tags.append(s)
    except Exception as exc:
        log.warning("ML TensorFlow-Fallback (Instrumente): %s", exc)

    try:
        acoustic_preds = _head(
            _ACOUSTIC_URL, ("model/Placeholder",), ("model/Softmax",)
        )
        avg_acoustic = np.mean(acoustic_preds, axis=0)
        if len(avg_acoustic) >= 2 and avg_acoustic[0] > 0.7:
            result.is_acoustic = True
    except Exception as exc:
        log.warning("ML TensorFlow-Fallback (Acoustic): %s", exc)

    try:
        aggressive_preds = _head(
            _AGGRESSIVE_URL, ("model/Placeholder",), ("model/Softmax",)
        )
        avg_aggressive = np.mean(aggressive_preds, axis=0)
        if len(avg_aggressive) >= 2 and avg_aggressive[0] > 0.7:
            result.is_aggressive = True
    except Exception as exc:
        log.warning("ML TensorFlow-Fallback (Aggressive): %s", exc)

    return result


def _get_model(url: str, cls_name: str, **kwargs):
    """Load a TF model once and cache it for reuse."""
    if url not in _model_cache:
        from essentia.standard import TensorflowPredictEffnetDiscogs, TensorflowPredict2D

        model_path = _download_model(url)
        cls = TensorflowPredictEffnetDiscogs if cls_name == "effnet" else TensorflowPredict2D
        _model_cache[url] = cls(graphFilename=str(model_path), **kwargs)
    return _model_cache[url]


def _get_classes(meta_url: str) -> list[str]:
    """Load class labels once and cache them."""
    if meta_url not in _meta_cache:
        meta_path = _download_model(meta_url)
        with open(meta_path) as f:
            _meta_cache[meta_url] = json.load(f).get("classes", [])
    return _meta_cache[meta_url]


def analyze_genre_mood(
    audio_path: Path,
    result: AnalysisResult,
    *,
    audio_16k: np.ndarray | None = None,
) -> AnalysisResult:
    """Enrich an AnalysisResult with ML-based genre, mood, instrument, and character predictions.

    Requires essentia-tensorflow to be installed (pip install beats-cli[ml]).
    Models are downloaded automatically on first use (~200 MB total).
    All models are cached after first load for fast batch processing.

    Optional ``audio_16k`` avoids a second full decode when already resampled from 44.1 kHz.
    """
    if not _check_tf_available():
        if tf_ml_fallback_ready():
            return _analyze_genre_mood_tf_fallback(
                audio_path, result, audio_16k=audio_16k
            )
        return result

    _suppress_essentia_logging()

    if audio_16k is None:
        audio_16k = _load_audio(audio_path, sample_rate=16000)

    embedding_model = _get_model(
        _DISCOGS_EFFNET_URL, "effnet", output="PartitionedCall:1"
    )
    embeddings = embedding_model(audio_16k)

    # Genre classification (top-3 from Discogs 400 labels)
    genre_model = _get_model(
        _GENRE_MODEL_URL, "2d",
        input="serving_default_model_Placeholder",
        output="PartitionedCall:0",
    )
    genre_preds = genre_model(embeddings)
    avg_preds = np.ravel(np.mean(genre_preds, axis=0))

    genre_classes = _get_classes(_GENRE_META_URL)
    if genre_classes:
        ng = min(len(avg_preds), len(genre_classes))
        ap = avg_preds[:ng]
        top_indices = np.argsort(ap)[-5:][::-1]
        seen: set[str] = set()
        for i in top_indices:
            if ap[i] < 0.08:
                break
            label = str(_clean_genre_label(genre_classes[i])).strip()
            if not label or label == "-":
                continue
            if label not in seen:
                seen.add(label)
                result.genre_tags.append(label)
            if len(result.genre_tags) >= 3:
                break
        if not result.genre_tags:
            j = int(np.argmax(ap))
            lab = str(_clean_genre_label(genre_classes[j])).strip()
            if lab and lab != "-":
                result.genre_tags.append(lab)

    # Mood/theme classification (56 labels from MTG-Jamendo)
    mood_model = _get_model(_MOOD_THEME_URL, "2d")
    mood_preds = mood_model(embeddings)
    avg_mood = np.ravel(np.mean(mood_preds, axis=0))

    mood_classes = _get_classes(_MOOD_THEME_META_URL)
    if mood_classes:
        nm = min(len(avg_mood), len(mood_classes))
        am = avg_mood[:nm]
        top_mood_indices = np.argsort(am)[-8:][::-1]
        result.mood_tags = [
            str(mood_classes[i]).strip()
            for i in top_mood_indices
            if am[i] > 0.03 and str(mood_classes[i]).strip()
        ]
        if not result.mood_tags:
            for i in np.argsort(am)[-3:][::-1]:
                s = str(mood_classes[int(i)]).strip()
                if s and s not in result.mood_tags:
                    result.mood_tags.append(s)

    # Voice/instrumental detection
    voice_model = _get_model(_VOICE_INSTRUMENTAL_URL, "2d", output="model/Softmax")
    voice_preds = voice_model(embeddings)
    avg_voice = np.mean(voice_preds, axis=0)
    if len(avg_voice) >= 2 and avg_voice[0] > 0.7:
        result.is_instrumental = True
    elif len(avg_voice) >= 2 and avg_voice[1] > 0.7:
        result.is_vocal = True

    # Instrument detection (40 labels from MTG-Jamendo)
    inst_model = _get_model(_INSTRUMENT_URL, "2d")
    inst_preds = inst_model(embeddings)
    avg_inst = np.ravel(np.mean(inst_preds, axis=0))

    inst_classes = _get_classes(_INSTRUMENT_META_URL)
    if inst_classes:
        ni = min(len(avg_inst), len(inst_classes))
        ai = avg_inst[:ni]
        top_inst_indices = np.argsort(ai)[-5:][::-1]
        result.instrument_tags = [
            str(inst_classes[i]).strip()
            for i in top_inst_indices
            if ai[i] > 0.06 and str(inst_classes[i]).strip()
        ]
        if not result.instrument_tags:
            for i in np.argsort(ai)[-4:][::-1]:
                s = str(inst_classes[int(i)]).strip()
                if s and s not in result.instrument_tags:
                    result.instrument_tags.append(s)

    # Acoustic/Electronic detection
    acoustic_model = _get_model(_ACOUSTIC_URL, "2d", output="model/Softmax")
    acoustic_preds = acoustic_model(embeddings)
    avg_acoustic = np.mean(acoustic_preds, axis=0)
    if len(avg_acoustic) >= 2 and avg_acoustic[0] > 0.7:
        result.is_acoustic = True

    # Mood Aggressive detection
    aggressive_model = _get_model(_AGGRESSIVE_URL, "2d", output="model/Softmax")
    aggressive_preds = aggressive_model(embeddings)
    avg_aggressive = np.mean(aggressive_preds, axis=0)
    if len(avg_aggressive) >= 2 and avg_aggressive[0] > 0.7:
        result.is_aggressive = True

    return result


def analyze(audio_path: Path, use_ml: bool = True) -> AnalysisResult:
    """Full analysis pipeline: BPM, key, and optionally genre/mood via ML models."""
    audio_44 = _load_audio(audio_path, sample_rate=44100)
    result = analyze_bpm_key_array(audio_44, sample_rate=44100)
    if use_ml:
        from essentia.standard import Resample

        resampler = Resample(
            inputSampleRate=44100, outputSampleRate=16000, quality=1
        )
        audio_16k = np.asarray(resampler(audio_44), dtype=np.float32)
        result = analyze_genre_mood(
            audio_path, result, audio_16k=audio_16k
        )
    if result.is_speech:
        result.bpm = 0
        result.key = ""
        result.scale = ""
        result.key_strength = 0.0
        result.instrument_tags = []
    result.genre_tags = _coerce_tag_string_list(result.genre_tags)
    result.mood_tags = _coerce_tag_string_list(result.mood_tags)
    result.instrument_tags = _coerce_tag_string_list(result.instrument_tags)
    return result


class AnalysisSubprocessCancelled(Exception):
    """Abbruch während ein Analyse-Kindprozess noch lief (Batch)."""


def analyze_with_subprocess_timeout(
    audio_path: Path,
    use_ml: bool,
    timeout_sec: float,
    *,
    on_wait_tick: object | None = None,
    wait_tick_interval: float = 2.5,
) -> AnalysisResult:
    """Run analysis in a fresh subprocess.

    Der Kindprozess läuft als ``python -c "…"`` und setzt ``sys.path`` aus
    ``BEATS_CLI_ROOT`` *bevor* ``import beats_cli`` — kein ``-m``, daher kein
    vorzeitiges Laden aus site-packages.

    On macOS, ``subprocess`` can use ``posix_spawn`` when ``close_fds=False``,
    avoiding ``fork`` from a WebKit-loaded GUI process (a common freeze source).
    Stdout/stderr are discarded so pipes cannot deadlock.

    ``on_wait_tick`` wird optional alle ``wait_tick_interval`` Sekunden aufgerufen,
    solange der Kindprozess noch läuft (GUI-Feedback beim ersten ML-/Modell-Laden).
    """
    import os
    import pickle
    import subprocess
    import sys
    import tempfile
    import time
    from pathlib import Path

    out_fd, out_path = tempfile.mkstemp(suffix=".pkl")
    os.close(out_fd)
    _tree_root = Path(__file__).resolve().parent.parent
    _pp = str(_tree_root)

    env = os.environ.copy()
    env.setdefault("PYTHONWARNINGS", "ignore")
    env["BEATS_CLI_ROOT"] = _pp
    env["BEATS_AUDIO"] = str(audio_path.resolve())
    env["BEATS_USE_ML"] = "1" if use_ml else "0"
    env["BEATS_OUT"] = str(out_path)
    if env.get("PYTHONPATH"):
        env["PYTHONPATH"] = f"{_pp}{os.pathsep}{env['PYTHONPATH']}"
    else:
        env["PYTHONPATH"] = _pp

    # macOS: Kindprozess — Essentia-.dylibs vor Resources/lib (build_app.sh), sonst doppeltes SDL2.
    if sys.platform == "darwin":
        try:
            from .darwin_bundle import macos_dyld_sdl_prefix

            _sdl_prefix = macos_dyld_sdl_prefix()
            if _sdl_prefix:
                sep = os.pathsep
                for key in ("DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH"):
                    prev = env.get(key, "")
                    env[key] = (
                        f"{_sdl_prefix}{sep}{prev}" if prev.strip() else _sdl_prefix
                    )
        except Exception:
            pass

    _child_code = """import os, pickle, sys
from pathlib import Path
_r = os.environ["BEATS_CLI_ROOT"]
if _r not in sys.path:
    sys.path.insert(0, _r)
_audio = Path(os.environ["BEATS_AUDIO"])
_use_ml = os.environ["BEATS_USE_ML"] == "1"
_out = Path(os.environ["BEATS_OUT"])
try:
    from beats_cli.analyzer import analyze
    r = analyze(_audio, use_ml=_use_ml)
    _out.write_bytes(pickle.dumps(("ok", r)))
except Exception as _e:
    try:
        _out.write_bytes(pickle.dumps(("err", str(_e))))
    except OSError:
        pass
"""
    cmd = [sys.executable, "-u", "-c", _child_code]

    popen_kw: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": env,
        "cwd": _pp,
    }
    if sys.platform == "darwin":
        popen_kw["close_fds"] = False

    proc = subprocess.Popen(cmd, **popen_kw)
    if on_wait_tick is not None:
        try:
            on_wait_tick()
        except Exception:
            pass
    deadline = time.monotonic() + float(timeout_sec)
    timed_out = False
    next_tick = time.monotonic() + float(wait_tick_interval)
    while proc.poll() is None:
        now = time.monotonic()
        if now >= deadline:
            timed_out = True
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
            break
        if on_wait_tick is not None and now >= next_tick:
            try:
                on_wait_tick()
            except Exception:
                pass
            next_tick = now + float(wait_tick_interval)
        time.sleep(0.05)

    try:
        if timed_out:
            raise TimeoutError(
                f"Timeout ({int(timeout_sec)}s) — Analyse-Prozess wurde beendet "
                "(SDL2/Audio hängt?)."
            ) from None

        outp = Path(out_path)
        if not outp.is_file() or outp.stat().st_size == 0:
            raise RuntimeError(
                f"Analyse ohne Ergebnis (Exit {proc.returncode}). "
                "SDL2 installiert? Siehe Terminal-Hinweis beim Start."
            )

        kind, payload = pickle.loads(outp.read_bytes())
        if kind == "err":
            raise RuntimeError(payload)
        return payload
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


def analyze_batch_with_subprocess_timeout(
    audio_paths: list[Path],
    use_ml: bool,
    timeout_sec: float,
    *,
    on_wait_tick: object | None = None,
    wait_tick_interval: float = 2.5,
    cancel_check: object | None = None,
) -> list[tuple[str, object]]:
    """Mehrere Dateien in *einem* Kindprozess — ML-Modelle nur einmal laden.

    Rückgabe: Liste von ``("ok", AnalysisResult)`` oder ``("err", str)`` pro Pfad.
    Leere ``audio_paths`` → leere Liste ohne Subprozess.
    """
    import os
    import pickle
    import subprocess
    import sys
    import tempfile
    import time
    from pathlib import Path as _Path

    if not audio_paths:
        return []

    out_fd, out_path = tempfile.mkstemp(suffix=".pkl")
    os.close(out_fd)
    batch_fd, batch_path = tempfile.mkstemp(suffix=".json")
    os.close(batch_fd)
    _Path(batch_path).write_text(
        __import__("json").dumps(
            [str(p.resolve()) for p in audio_paths], ensure_ascii=False
        ),
        encoding="utf-8",
    )

    _tree_root = _Path(__file__).resolve().parent.parent
    _pp = str(_tree_root)

    env = os.environ.copy()
    env.setdefault("PYTHONWARNINGS", "ignore")
    env["BEATS_CLI_ROOT"] = _pp
    env["BEATS_BATCH_JSON"] = str(batch_path)
    env["BEATS_USE_ML"] = "1" if use_ml else "0"
    env["BEATS_OUT"] = str(out_path)
    if env.get("PYTHONPATH"):
        env["PYTHONPATH"] = f"{_pp}{os.pathsep}{env['PYTHONPATH']}"
    else:
        env["PYTHONPATH"] = _pp

    if sys.platform == "darwin":
        try:
            from .darwin_bundle import macos_dyld_sdl_prefix

            _sdl_prefix = macos_dyld_sdl_prefix()
            if _sdl_prefix:
                sep = os.pathsep
                for key in ("DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH"):
                    prev = env.get(key, "")
                    env[key] = (
                        f"{_sdl_prefix}{sep}{prev}" if prev.strip() else _sdl_prefix
                    )
        except Exception:
            pass

    _child_code = """import json, os, pickle, sys
from pathlib import Path
_r = os.environ["BEATS_CLI_ROOT"]
if _r not in sys.path:
    sys.path.insert(0, _r)
_paths = json.loads(Path(os.environ["BEATS_BATCH_JSON"]).read_text(encoding="utf-8"))
_use_ml = os.environ["BEATS_USE_ML"] == "1"
_out = Path(os.environ["BEATS_OUT"])
_rows = []
try:
    from beats_cli.analyzer import analyze
    for _ps in _paths:
        _ap = Path(_ps)
        try:
            _one = analyze(_ap, use_ml=_use_ml)
            _rows.append(("ok", _one))
        except Exception as _e:
            _rows.append(("err", str(_e)))
    _out.write_bytes(pickle.dumps(_rows))
except Exception as _e:
    try:
        _out.write_bytes(pickle.dumps([("err", str(_e))]))
    except OSError:
        pass
"""
    cmd = [sys.executable, "-u", "-c", _child_code]

    popen_kw: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": env,
        "cwd": _pp,
    }
    if sys.platform == "darwin":
        popen_kw["close_fds"] = False

    proc = subprocess.Popen(cmd, **popen_kw)
    if on_wait_tick is not None:
        try:
            on_wait_tick()
        except Exception:
            pass
    deadline = time.monotonic() + float(timeout_sec)
    timed_out = False
    user_cancelled = False
    next_tick = time.monotonic() + float(wait_tick_interval)
    while proc.poll() is None:
        now = time.monotonic()
        if cancel_check is not None:
            try:
                if cancel_check():
                    user_cancelled = True
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        try:
                            proc.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            pass
                    break
            except Exception:
                pass
        if now >= deadline:
            timed_out = True
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
            break
        if on_wait_tick is not None and now >= next_tick:
            try:
                on_wait_tick()
            except Exception:
                pass
            next_tick = now + float(wait_tick_interval)
        time.sleep(0.05)

    try:
        if user_cancelled:
            raise AnalysisSubprocessCancelled(
                "Analyse abgebrochen."
            ) from None
        if timed_out:
            raise TimeoutError(
                f"Timeout ({int(timeout_sec)}s) — Batch-Analyse wurde beendet."
            ) from None

        outp = _Path(out_path)
        if not outp.is_file() or outp.stat().st_size == 0:
            raise RuntimeError(
                f"Analyse ohne Ergebnis (Exit {proc.returncode}). "
                "SDL2 installiert? Siehe Terminal-Hinweis beim Start."
            )

        rows = pickle.loads(outp.read_bytes())
        if len(rows) != len(audio_paths):
            raise RuntimeError(
                "Batch-Analyse lieferte eine unerwartete Ergebnisliste."
            )
        return rows
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass
        try:
            os.unlink(batch_path)
        except OSError:
            pass
