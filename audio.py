"""Audio file checks, tags (mutagen) and optional ffmpeg tools (spectrum, AIFF)."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import mutagen

_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def digest(path) -> str:
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def validate_audio(path, size: int = 0, minimum_mp3_kbps: int | None = None) -> None:
    """Raise ValueError unless `path` is a readable audio file of the expected size."""
    path = Path(path)
    if size and path.stat().st_size != size:
        raise ValueError('Dosya boyutu beklenen boyutla eşleşmiyor.')
    try:
        audio = mutagen.File(path)
    except mutagen.MutagenError:
        audio = None
    info = getattr(audio, 'info', None)
    if info is None:
        raise ValueError('Ses başlığı okunamadı.')
    if path.suffix.lower() == '.flac':
        # Streamed FLAC encoders leave total_samples=0, so mutagen reports length 0
        # for perfectly good files. A valid STREAMINFO block is enough here.
        if not getattr(info, 'sample_rate', 0) or not getattr(info, 'channels', 0):
            raise ValueError('FLAC başlığı geçersiz.')
        return
    if not getattr(info, 'length', 0) or info.length <= 0:
        raise ValueError('Ses süresi okunamadı.')
    if minimum_mp3_kbps and path.suffix.lower() == '.mp3':
        if getattr(info, 'bitrate', 0) < (minimum_mp3_kbps - 20) * 1000:
            raise ValueError(f'MP3 gerçek bitrate {round(info.bitrate / 1000)} kbps; {minimum_mp3_kbps} beklendi.')


def audio_metadata(path) -> dict:
    """Common tags for the track table; missing tags are not an error."""
    audio = mutagen.File(path, easy=True)
    if audio is None or not getattr(audio, 'info', None):
        return {}

    def first(name):
        value = audio.get(name, []) if getattr(audio, 'tags', None) is not None else []
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ''
        return str(value or '').strip()

    info = audio.info
    return {'length': float(getattr(info, 'length', 0) or 0), 'album': first('album'),
            'genre': first('genre'), 'tracknumber': first('tracknumber'),
            'bitrate': round(float(getattr(info, 'bitrate', 0) or 0) / 1000),
            'bit_depth': getattr(info, 'bits_per_sample', None),
            'sample_rate': getattr(info, 'sample_rate', None),
            'extension': Path(path).suffix[1:].lower()}


def write_track_number(path, position: int) -> bool:
    """Write only the playlist position tag; every other tag is preserved."""
    try:
        audio = mutagen.File(path, easy=True)
        if audio is None:
            return False
        if getattr(audio, 'tags', None) is None:
            audio.add_tags()
        try:
            audio['tracknumber'] = [str(position)]
        except (KeyError, TypeError, ValueError):
            # AIFF / WAV carry raw ID3 tags without the "easy" mapping.
            from mutagen.id3 import TRCK
            audio.tags.setall('TRCK', [TRCK(encoding=3, text=str(position))])
        audio.save()
        return read_track_number(path) == str(position)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, mutagen.MutagenError):
        return False


def read_track_number(path) -> str:
    audio = mutagen.File(path, easy=True)
    tags = getattr(audio, 'tags', None)
    if tags is None:
        return ''
    try:
        values = audio.get('tracknumber', [])
    except (KeyError, TypeError, ValueError):
        values = []
    if not values and hasattr(tags, 'getall'):
        values = [str(f) for f in tags.getall('TRCK')]
    value = values[0] if isinstance(values, (list, tuple)) and values else values
    return str(value or '').split('/')[0]


def audio_length(path) -> float:
    try:
        audio = mutagen.File(path)
        return float(getattr(getattr(audio, 'info', None), 'length', 0) or 0)
    except (OSError, mutagen.MutagenError):
        return 0.0


# --------------------------------------------------------------------------
# ffmpeg helpers (optional: features switch off when ffmpeg is missing)
# --------------------------------------------------------------------------

def find_ffmpeg(configured: str = '') -> str:
    """ffmpeg.exe from the setting, PATH, or a WinGet / common install folder; '' if absent."""
    candidates = [configured] if configured else []
    candidates.append(shutil.which('ffmpeg') or '')
    local = Path(os.environ.get('LOCALAPPDATA', ''))
    candidates += [str(p) for p in sorted((local / 'Microsoft' / 'WinGet' / 'Packages').glob('*FFmpeg*/*/bin/ffmpeg.exe'))]
    candidates += [r'C:\ffmpeg\bin\ffmpeg.exe', r'C:\Program Files\ffmpeg\bin\ffmpeg.exe']
    return next((c for c in candidates if c and Path(c).is_file()), '')


NO_CUTOFF = 22.0      # returned when the spectrum has no lowpass wall
CLIFF_DB = 15.0       # a drop this steep within ~0.5 kHz is an encoder lowpass, not natural roll-off


def spectral_cutoff(path, ffmpeg: str, seconds: int = 30) -> float | None:
    """Frequency (kHz) of the encoder lowpass "wall", or NO_CUTOFF when there is none.

    MP3 encoders cut everything above a fixed frequency (128 kbps ~16 kHz, 192 ~19,
    320 ~20), leaving a drop of 20-60 dB. Real masters only roll off gradually; dark
    tracks lose 5-12 dB at most, so a level threshold alone flags them wrongly.
    Measured on a 30 s slice from the middle; None when the file cannot be decoded.
    """
    import numpy
    length = audio_length(path)
    start = max(0.0, length / 2 - seconds / 2) if length > seconds else 0.0
    try:
        raw = subprocess.run([ffmpeg, '-v', 'error', '-ss', f'{start:.1f}', '-t', str(seconds), '-i', str(path),
                              '-ac', '1', '-ar', '44100', '-f', 'f32le', '-'],
                             capture_output=True, timeout=60, creationflags=_NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    samples = numpy.frombuffer(raw, dtype=numpy.float32)
    window = 8192
    if len(samples) < window * 4 or not numpy.any(samples):
        return None
    frames = samples[:len(samples) // window * window].reshape(-1, window) * numpy.hanning(window)
    power = numpy.mean(numpy.abs(numpy.fft.rfft(frames, axis=1)) ** 2, axis=0)
    db = 10 * numpy.log10(power + 1e-20)
    step = 44100 / window
    near, gap = int(400 / step), int(80 / step)
    best_freq, best_drop = None, 0.0
    for i in range(int(15000 / step), int(21600 / step)):
        drop = numpy.median(db[i - near:i - gap]) - numpy.median(db[i + gap:i + near])
        if drop > best_drop:
            best_freq, best_drop = i * step, drop
    if best_freq is None or best_drop < CLIFF_DB:
        return NO_CUTOFF
    return round(best_freq / 1000, 1)


def convert_to_aiff(source, destination, ffmpeg: str) -> None:
    """Lossless PCM in an AIFF container, keeping bit depth and tags (ID3)."""
    bits = getattr(getattr(mutagen.File(source), 'info', None), 'bits_per_sample', 16) or 16
    codec = 'pcm_s24be' if bits > 16 else 'pcm_s16be'
    result = subprocess.run([ffmpeg, '-v', 'error', '-y', '-i', str(source), '-map', '0:a', '-map_metadata', '0',
                             '-c:a', codec, '-write_id3v2', '1', '-f', 'aiff', str(destination)],
                            capture_output=True, timeout=300, creationflags=_NO_WINDOW)
    if result.returncode != 0 or not Path(destination).is_file():
        raise ValueError('AIFF dönüşümü başarısız.')
