"""TXT parsing, Soulseek query planning and candidate scoring.

Everything here is pure (no network, database or audio I/O) so it can be tested
directly against the real file names that peers share.

Vocabulary
    credit   one artist name from the artist field ("Oden & Fatzo")
    core     the title without version / feat. / bracket extras ("Stop Motion")
    version  the significant words of a mix descriptor ("Club Mix" -> {"club"});
             None means the default/original version. "Original Mix", "Extended
             Mix" and "Remastered" are all treated as the default version.
"""
from __future__ import annotations

import re
import statistics
import unicodedata
from dataclasses import asdict, dataclass, fields
from pathlib import Path, PureWindowsPath

from rapidfuzz import fuzz

# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------

_FOLD = str.maketrans({'ø': 'o', 'Ø': 'o', 'đ': 'd', 'ł': 'l', 'æ': 'ae', 'œ': 'oe',
                       'ß': 'ss', 'þ': 'th', 'ı': 'i', '&': ' ', '+': ' '})
_APOSTROPHES = re.compile(r"['’‘`´]")


def norm(text: str) -> str:
    """Comparison form: accent-free, lowercase words; '&' and 'and' are ignored."""
    text = unicodedata.normalize('NFKD', (text or '').replace('_', ' ').translate(_FOLD))
    text = ''.join(c for c in text if not unicodedata.combining(c)).casefold()
    text = _APOSTROPHES.sub('', text)
    words = re.sub(r'[^\w]+', ' ', text).split()
    return ' '.join(w for w in words if w != 'and')


def contains_phrase(text: str, phrase: str, prefix: bool = False) -> bool:
    """Whole-word phrase test on normalised strings; `prefix` lets the last word be partial."""
    if not phrase:
        return False
    if not prefix:
        return f' {phrase} ' in f' {text} '
    return re.search(r'(?:^|\s)' + re.escape(phrase), text) is not None


# --------------------------------------------------------------------------
# Version descriptors
# --------------------------------------------------------------------------

# A bracket or " - " suffix is a version descriptor only if it contains one of these.
VERSION_KEYWORDS = {
    'mix', 'remix', 'rmx', 'edit', 'reedit', 'dub', 'version', 'vip', 'rework', 'bootleg',
    'refix', 'flip', 'live', 'instrumental', 'acapella', 'acappella', 'karaoke', 'cover',
    'remaster', 'remastered', 'extended', 'original', 'mixed', 'sped', 'slowed', 'nightcore',
    'reprise', 'demo', 'acoustic', 'mashup', 'rerub', 'remake', 'retouch', 'radio',
}
# Words that describe the default release rather than a different recording.
NEUTRAL_WORDS = {'original', 'extended', 'remaster', 'remastered', 'mixed', 'album', 'single',
                 'main', 'full', 'length', 'clean', 'explicit', 'digital', 'bonus', 're', 'the'}
GENERIC_WORDS = {'mix', 'remix', 'version'}
_CANONICAL = {'rmx': 'remix', 'reedit': 'edit', 'acappella': 'acapella', 'remastered': 'remaster'}
_FEAT = re.compile(r'^(?:feat|ft|featuring|with)\b\.?\s*', re.I)


def is_version_descriptor(text: str) -> bool:
    return any(w in VERSION_KEYWORDS for w in norm(text).split())


def version_tokens(text: str) -> frozenset | None:
    """Significant words of a descriptor; None for the default version."""
    words = [_CANONICAL.get(w, w) for w in norm(text).split()]
    words = {w for w in words if w not in NEUTRAL_WORDS and not re.fullmatch(r'\d{2,4}', w)}
    named = words - GENERIC_WORDS
    if named:
        return frozenset(named)
    return frozenset({'remix'}) if 'remix' in words else None


def neutral_prefix(text: str) -> bool:
    """A truncated descriptor such as 'Exten…' that can only be a neutral word."""
    text = norm(text)
    return bool(text) and any(p.startswith(text) for p in ('extended mix', 'original mix', 'remastered'))


# --------------------------------------------------------------------------
# Requested tracks
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Track:
    artist: str
    title: str
    line_number: int = 0
    duration: float | None = None     # seconds, when the list came from a CSV export
    album: str = ''

    @property
    def key(self) -> str:
        return f'{norm(self.artist)}::{norm(self.title)}'


_TRUNCATED = re.compile(r'\s*(?:\.{3}|…)\s*$')
TRUNCATION_MIN_LENGTH = 15     # exports cut long names; a short "Ok..." is the real title


def _is_cut(text: str) -> bool:
    return bool(_TRUNCATED.search(text)) and len(text.strip()) >= TRUNCATION_MIN_LENGTH
_UNBRACKETED_VERSION = re.compile(
    r'\s((?:original|extended|club|radio|vocal|dub|instrumental|piano|short|main)\s+(?:mix|edit|version)'
    r'|(?:remix|rmx))$', re.I)
_CREDIT_SPLIT = re.compile(r'\s*(?:,|;|/|\s(?:feat\.?|ft\.?|featuring|vs\.?|x|with)\s)\s*', re.I)
_BRACKETS = re.compile(r'[(\[{]([^)\]}]*)[)\]}]')


@dataclass(frozen=True)
class Spec:
    """A requested track broken into the parts that matter for matching."""
    credits: tuple            # normalised credits, primary first
    pieces: tuple             # '&' halves of credits; only stripped from titles, never an identity
    primary_display: str      # primary artist as typed (for queries)
    other_display: tuple      # other credits as typed (for queries)
    core: str                 # normalised core title
    core_display: str         # core title as typed (for queries)
    version: frozenset | None
    version_prefix: str       # normalised partial descriptor when the TXT was cut off
    version_display: str
    title_truncated: bool
    primary_truncated: bool
    duration: float | None = None


def split_credits(artist: str) -> tuple[list[str], bool]:
    """Credits as typed; a final credit cut off with '…' is dropped (or kept as prefix if alone)."""
    truncated = _is_cut(artist)
    names = [p.strip() for p in _CREDIT_SPLIT.split(_TRUNCATED.sub('', artist) if truncated else artist)
             if p and p.strip()]
    if truncated and len(names) > 1:
        names.pop()
        truncated = False
    return names, truncated


def build_spec(track: Track) -> Spec:
    names, primary_truncated = split_credits(track.artist)
    title = track.title.strip()
    cut = _is_cut(title)
    if cut:
        title = _TRUNCATED.sub('', title)

    parts = re.split(r'\s+[-–—]\s+', title)
    core, descriptors = parts[0], parts[1:]
    partial = ''            # descriptor cut off by the TXT export ("Andre Lo…")
    title_truncated = False
    if cut:
        open_bracket = re.search(r'[(\[]([^)\]]*)$', parts[-1])
        if open_bracket:
            partial = open_bracket.group(1)
            if len(parts) == 1:
                core = core[:open_bracket.start()]
            else:
                descriptors[-1] = descriptors[-1][:open_bracket.start()]
        elif len(parts) > 1:
            partial = descriptors.pop()
        else:
            title_truncated = True
    for inner in _BRACKETS.findall(core):
        if _FEAT.match(inner.strip()):
            names.extend(p.strip() for p in _CREDIT_SPLIT.split(_FEAT.sub('', inner.strip())) if p.strip())
        else:
            descriptors.append(inner)
    core = _BRACKETS.sub(' ', core)
    feat = re.split(r'\s(?:feat\.?|ft\.?|featuring)\s', core, maxsplit=1, flags=re.I)
    if len(feat) == 2:
        core = feat[0]
        names.extend(p.strip() for p in _CREDIT_SPLIT.split(feat[1]) if p.strip())
    core_display = re.sub(r'\s+', ' ', core).strip(' -')
    # Unbracketed suffix typed into the title: "Ten Thousand Dreams Extended Mix".
    tail = _UNBRACKETED_VERSION.search(core_display)
    if tail and tail.start() > 0:
        descriptors.append(tail.group(1))
        core_display = core_display[:tail.start()].strip()

    version = None
    version_display = ''
    for descriptor in descriptors:
        if is_version_descriptor(descriptor):
            tokens = version_tokens(descriptor)
            if tokens:
                version = (version or frozenset()) | tokens
                version_display = descriptor
    version_prefix = ''
    if partial.strip() and not neutral_prefix(partial):
        # The cut-off part is usually a remixer name; match it as a prefix.
        version_prefix = norm(partial)
        version_display = partial

    credits = list(dict.fromkeys(n for n in (norm(x) for x in names) if n))
    pieces = []
    for name in names:
        if '&' in name:
            pieces.extend(norm(p) for p in name.split('&') if len(norm(p)) >= 4)
    return Spec(
        credits=tuple(credits), pieces=tuple(dict.fromkeys(p for p in pieces if p not in credits)),
        primary_display=names[0] if names else '', other_display=tuple(names[1:]),
        core=norm(core_display), core_display=core_display, version=version,
        version_prefix=version_prefix, version_display=version_display,
        title_truncated=title_truncated, primary_truncated=primary_truncated,
        duration=track.duration or None,
    )


# --------------------------------------------------------------------------
# TXT files
# --------------------------------------------------------------------------

_NUMBERED = re.compile(r'^\s*\d{1,4}\s*[.)]\s+')
_SEPARATOR = re.compile(r'\s+[-–—]\s+')


def _title_signals(text: str) -> int:
    signals = 0
    if _BRACKETS.search(text):
        signals += 1
    if is_version_descriptor(_BRACKETS.sub(' ', text)):
        signals += 1
    return signals


def _artist_signals(text: str) -> int:
    outside = _BRACKETS.sub(' ', text)
    return int(',' in outside) + int(bool(re.search(r'\s(?:feat\.?|ft\.?)\s', outside, re.I)))


def _split_lines(path: Path) -> list[tuple[int, list[str], bool]]:
    rows = []
    for number, raw in enumerate(Path(path).read_text(encoding='utf-8-sig').splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        numbered = bool(_NUMBERED.match(line))
        parts = [p.strip() for p in _SEPARATOR.split(_NUMBERED.sub('', line, count=1))]
        if len(parts) < 2 or not all(parts):
            raise ValueError(f'Satır {number}: sanatçı ve şarkıyı boşluk-tire-boşluk ile ayırın.')
        rows.append((number, parts, numbered))
    if not rows:
        raise ValueError('TXT dosyasında parça bulunamadı.')
    return rows


_CSV_COLUMNS = {
    'title': ('track name', 'title', 'track', 'name', 'song', 'şarkı', 'sarki', 'parça'),
    'artist': ('artist name(s)', 'artist names', 'artist', 'artists', 'sanatçı', 'sanatci'),
    'duration': ('duration (ms)', 'duration_ms', 'duration', 'length', 'time', 'süre', 'sure'),
    'album': ('album name', 'album', 'albüm'),
}


def _duration_seconds(value: str, header: str) -> float | None:
    value = (value or '').strip()
    if not value:
        return None
    if ':' in value:
        try:
            parts = [float(p) for p in value.split(':')]
        except ValueError:
            return None
        seconds = 0.0
        for part in parts:
            seconds = seconds * 60 + part
        return seconds or None
    try:
        number = float(value.replace(',', '.'))
    except ValueError:
        return None
    return (number / 1000 if 'ms' in header or number > 10000 else number) or None


def parse_csv(path) -> list[Track]:
    """Exportify / spreadsheet CSV: columns are found by header name; duration enables length checks."""
    import csv
    text = Path(path).read_text(encoding='utf-8-sig')
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=',;\t')
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(text.splitlines(), dialect))
    if not rows:
        raise ValueError('CSV dosyasında parça bulunamadı.')
    header = [h.strip().casefold() for h in rows[0]]
    columns = {}
    for field, names in _CSV_COLUMNS.items():
        for name in names:
            if name in header:
                columns[field] = header.index(name)
                break
    if 'title' not in columns or 'artist' not in columns:
        raise ValueError('CSV başlığında şarkı ve sanatçı sütunları bulunamadı (ör. "Track Name", "Artist Name(s)").')
    tracks, seen = [], set()
    for number, row in enumerate(rows[1:], 2):
        def cell(field):
            index = columns.get(field)
            return row[index].strip() if index is not None and index < len(row) else ''
        artist, title = cell('artist'), cell('title')
        if not artist or not title:
            continue
        duration = _duration_seconds(cell('duration'), header[columns['duration']]) if 'duration' in columns else None
        track = Track(artist, title, number, duration, cell('album'))
        if track.key not in seen:
            tracks.append(track)
            seen.add(track.key)
    if not tracks:
        raise ValueError('CSV dosyasında parça bulunamadı.')
    return tracks


_TIMESTAMP = re.compile(r'^\s*[\[(]?\d{1,2}[:.]\d{2}(?:[:.]\d{2})?[\])]?\s*[-–—|.]?\s*')
_OCR_CHARS = str.maketrans({'•': "'", '‘': "'", '’': "'", '´': "'", '`': "'", '–': '-', '—': '-', '|': 'I'})
_VERSION_FIXES = {w.replace('m', 'rn'): w for w in ('remix', 'mix', 'mixed', 'remaster', 'remastered', 'dream')}


def _fix_ocr_word(word: str) -> str:
    """OCR reads "m" as "rn" ("Rernix"); undo it only where the result is a known version word."""
    key = word.casefold()
    if key in _VERSION_FIXES:
        fixed = _VERSION_FIXES[key]
        return fixed.capitalize() if word[:1].isupper() else fixed
    return word


def tracklist_from_ocr(lines) -> list[str]:
    """Turn OCR'd tracklist lines into "Artist - Title" lines.

    Timestamps, numbering and stray digit fragments are dropped; "Title by Artist"
    (YouTube / 1001Tracklists style) is turned around. Lines that already contain
    " - " are kept as they are and the column order is detected later.
    """
    result = []
    for raw in lines:
        line = ' '.join(str(raw).translate(_OCR_CHARS).split())
        line = _TIMESTAMP.sub('', line)
        line = _NUMBERED.sub('', line)
        if sum(c.isalpha() for c in line) < 3:
            continue
        line = re.sub(r"[\w']+", lambda m: _fix_ocr_word(m.group(0)), line)
        if not _SEPARATOR.search(line):
            parts = re.split(r'\s+by\s+', line)
            if len(parts) >= 2 and parts[-1].strip() and ' by '.join(parts[:-1]).strip():
                line = f"{parts[-1].strip()} - {' by '.join(parts[:-1]).strip()}"
        result.append(line)
    return result


def detect_layout(path) -> str:
    """Vote between 'Artist - Title' and 'Title - Artist' using brackets, mix words and credit lists."""
    if Path(path).suffix.casefold() == '.csv':
        return 'csv'
    votes = {'artist_title': 0.0, 'title_artist': 0.0}
    for _number, parts, numbered in _split_lines(path):
        first, last = parts[0], parts[-1]
        votes['artist_title'] += _title_signals(last) + _artist_signals(first)
        votes['title_artist'] += _title_signals(first) + _artist_signals(last)
        if len(parts) > 2:
            # "Title - Remix Name - Artist" vs "Artist - Title - Remix Name"
            votes['title_artist'] += is_version_descriptor(parts[-2])
            votes['artist_title'] += is_version_descriptor(last)
        if numbered:
            votes['title_artist'] += 0.25    # common streaming-export shape
    return 'title_artist' if votes['title_artist'] > votes['artist_title'] else 'artist_title'


def parse_tracks(path, layout: str = 'auto') -> list[Track]:
    if Path(path).suffix.casefold() == '.csv':
        return parse_csv(path)
    if layout == 'auto':
        layout = detect_layout(path)
    tracks, seen = [], set()
    for number, parts, _numbered in _split_lines(path):
        if layout == 'title_artist':
            artist, title = parts[-1], ' - '.join(parts[:-1])
        else:
            artist, title = parts[0], ' - '.join(parts[1:])
        track = Track(artist, title, number)
        if track.key not in seen:
            tracks.append(track)
            seen.add(track.key)
    return tracks


# --------------------------------------------------------------------------
# Query planning
# --------------------------------------------------------------------------

_STOP_WORDS = {'the', 'a', 'an', 'and', 'of', 'in', 'to', 'de', 'la', 'le', 'el', 'feat', 'ft'}


def _query_words(text: str, fold: bool = False) -> list[str]:
    """Words as Soulseek tokenises them: any punctuation, apostrophes included, splits a word.

    Measured against the live network: "Prospa Don Stop" finds Don't/Don’t files
    (111 peers), while "Prospa Don't Stop" misses the curly-apostrophe ones (42 peers).
    """
    text = unicodedata.normalize('NFKC', text.replace('_', ' '))
    if fold:
        text = unicodedata.normalize('NFKD', text.translate(_FOLD))
        text = ''.join(c for c in text if not unicodedata.combining(c))
    return [w for w in re.split(r'[^\w]+', text) if len(w) > 1]


def _without_stop_words(words: list[str]) -> list[str]:
    kept = [w for w in words if w.casefold() not in _STOP_WORDS]
    return kept if len(kept) >= 2 else words


def _distinctive(words: list[str]) -> bool:
    return len(words) >= 2 and sum(len(w) for w in words) >= 9


def queries(track: Track, limit: int = 6) -> list[str]:
    """Ordered Soulseek searches; every term must match, so fewer and plainer words win."""
    spec = build_spec(track)
    core = _query_words(spec.core_display)
    if spec.title_truncated and len(core) > 1:
        core = core[:-1]                 # the cut-off word would never match
    primary = _query_words(spec.primary_display)
    result: list[list[str]] = []
    if spec.primary_truncated and primary:
        primary = primary[:-1]
    if primary or core:
        result.append(primary + core)
    if spec.version and core:
        named = [w for w in _query_words(spec.version_display) if norm(w) in spec.version]
        if named:
            result.append(core + named)
    for other in spec.other_display[:2]:
        words = _query_words(other)
        if words and core:
            result.append(words + core)
    if _distinctive(_without_stop_words(core)):
        result.append(core)
    result.append(_query_words(spec.primary_display, fold=True) + _query_words(spec.core_display, fold=True))

    plans = []
    for words in result:
        words = _without_stop_words(words)
        text = ' '.join(words)
        if len(words) >= 2 or len(text) >= 5:
            plans.append(text)
    unique = list(dict.fromkeys(q for q in plans if q))
    return unique[:max(1, limit)]


# --------------------------------------------------------------------------
# Candidates
# --------------------------------------------------------------------------

AUDIO_EXTENSIONS = {'flac', 'mp3', 'wav', 'aif', 'aiff', 'm4a', 'aac', 'ogg', 'opus', 'alac', 'wma', 'ape'}


@dataclass
class Candidate:
    peer: str
    filename: str
    size: int
    bitrate: int | None
    extension: str
    queue_length: int | None
    upload_speed: int | None
    free_upload_slot: bool | None
    bit_depth: int | None = None
    sample_rate: int | None = None
    length: int | None = None
    search_id: str | None = None
    score: float = 0.0
    reason: str = ''
    eligible: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> 'Candidate':
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_dict(self) -> dict:
        return asdict(self)


def _get(mapping: dict, name: str, default=None):
    wanted = name.casefold()
    for key, value in mapping.items():
        if str(key).casefold() == wanted:
            return value
    return default


def _int(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def extract_candidates(responses, search_id: str | None = None) -> list[Candidate]:
    """Audio files from slskd search responses; artwork, playlists and checksums are dropped."""
    found = []
    for response in responses or []:
        peer = str(_get(response, 'username', '') or '')
        if not peer:
            continue
        queue = _int(_get(response, 'queueLength'))
        speed = _int(_get(response, 'uploadSpeed'))
        free = _get(response, 'hasFreeUploadSlot')
        for item in _get(response, 'files', []) or []:
            filename = str(_get(item, 'filename', '') or '')
            extension = str(_get(item, 'extension', '') or PureWindowsPath(filename).suffix).lstrip('.').casefold()
            if not extension:
                extension = PureWindowsPath(filename).suffix.lstrip('.').casefold()
            if not filename or extension not in AUDIO_EXTENSIONS:
                continue
            found.append(Candidate(
                peer=peer, filename=filename, size=_int(_get(item, 'size', 0)) or 0,
                bitrate=_int(_get(item, 'bitRate')), extension=extension,
                queue_length=queue, upload_speed=speed,
                free_upload_slot=bool(free) if free is not None else None,
                bit_depth=_int(_get(item, 'bitDepth')), sample_rate=_int(_get(item, 'sampleRate')),
                length=_int(_get(item, 'length')), search_id=search_id,
            ))
    return found


# --------------------------------------------------------------------------
# Quality
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Quality:
    tier: int          # higher is better; used to rank peers
    automatic: bool    # may be downloaded without asking
    label: str


LOSSLESS_SIZE = {'flac': (3, 600), 'wav': (8, 1200), 'aif': (8, 1200), 'aiff': (8, 1200)}


def quality(c: Candidate, config: dict | None = None) -> Quality:
    config = config or {}
    minimum = int(config.get('minimum_mp3_bitrate', 320))
    accept_pcm = bool(config.get('accept_wav_aiff', True))
    accept_unknown = bool(config.get('accept_unknown_mp3_bitrate', True))
    ext = c.extension
    mb = (c.size or 0) / 1048576
    if ext in LOSSLESS_SIZE:
        low, high = LOSSLESS_SIZE[ext]
        if not low <= mb <= high:
            return Quality(1, False, f'{ext.upper()} boyutu şüpheli ({mb:.1f} MB)')
        if ext == 'flac':
            return Quality(40, True, 'FLAC')
        return Quality(36 if ext.startswith('aif') else 35, accept_pcm, ext.upper())
    if ext == 'mp3':
        if not 1.5 <= mb <= 80:
            return Quality(1, False, f'MP3 boyutu şüpheli ({mb:.1f} MB)')
        rate = c.bitrate
        estimated = None
        if rate is None and c.length:
            estimated = c.size * 8 / c.length / 1000
        if rate is not None and rate >= minimum:
            return Quality(30, True, f'MP3 {rate}')
        if rate is None and (estimated is None or estimated >= minimum * 0.9):
            return Quality(25, accept_unknown, 'MP3 bitrate bilinmiyor · indirince doğrulanır')
        shown = rate or round(estimated or 0)
        return Quality(10 + min(9, shown // 40), False, f'MP3 {shown} kbps (düşük)')
    return Quality(8, False, f'{ext.upper()} · yalnızca elle seçim')


def quality_allowed(c: Candidate, manual: bool = False, config: dict | None = None) -> bool:
    if c.extension not in AUDIO_EXTENSIONS or not c.size:
        return False
    return True if manual else quality(c, config).automatic


def quality_label(data: dict) -> str:
    ext = str(data.get('extension', '')).lower()
    if ext == 'mp3':
        return 'MP3 · ' + (f"{data['bitrate']}k" if data.get('bitrate') else '? kbps')
    if ext in ('flac', 'wav', 'aif', 'aiff'):
        bits, rate = data.get('bit_depth'), data.get('sample_rate')
        return ext.upper() + (f' · {bits} bit' if bits else '') + (f' / {rate / 1000:g}k' if rate else '')
    return ext.upper() or '—'


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

_TRACK_PREFIX = re.compile(
    r'^\s*(?:(?:cd|disc)\s*\d+\s*[-._ ]\s*)?(?:[a-d]\d{1,2}|\d{1,2}[-.]\d{1,3}|\d{1,3})(?:\s*[-._)]\s*|\s+)(?=\S)',
    re.I)


_UNBRACKETED_FEAT = re.compile(r'\s(?:feat\.?|ft\.?|featuring)\s.*$', re.I)


@dataclass
class _Name:
    options: list          # normalised title candidates (segments, joins, whole text)
    versions: list         # version token sets found in the name
    version_texts: list    # normalised descriptor texts (for truncated prefixes)


def _parse_name(stem: str, spec: Spec) -> _Name:
    text = stem.replace('_', ' ')
    # A peer's file name can itself be cut off: "Hachi (Original Mi"
    text = re.sub(r'[(\[]([^)\]]*)$', r'(\1)', text)
    versions, version_texts, extras = [], [], []
    for inner in _BRACKETS.findall(text):
        if _FEAT.match(inner.strip()):
            continue
        if is_version_descriptor(inner):
            version_texts.append(norm(inner))
            tokens = version_tokens(inner)
            if tokens:
                versions.append(tokens)
        else:
            extras.append(inner)
    text = _BRACKETS.sub(' ', text)

    segments = [s for s in re.split(r'\s+[-–—]\s+|(?<=\w)-(?=\w)|\s+-|-\s+', text) if s.strip()]
    title_segments = []
    for segment in segments:
        words = norm(segment).split()
        if (words and words[-1] in VERSION_KEYWORDS and len(words) <= 5
                and not contains_phrase(norm(segment), spec.core)):
            version_texts.append(norm(segment))
            tokens = version_tokens(segment)
            if tokens:
                versions.append(tokens)
        else:
            title_segments.append(segment)

    removable = sorted(set(spec.credits) | set(spec.pieces), key=len, reverse=True)

    def strip_credits(value: str) -> str:
        value = norm(_UNBRACKETED_FEAT.sub('', value))
        for credit in removable:
            if not contains_phrase(spec.core, credit):
                value = re.sub(r'(?:^|(?<=\s))' + re.escape(credit) + r'(?=\s|$)', ' ', value)
        return re.sub(r'\s+', ' ', value).strip()

    raw_options = list(title_segments)
    raw_options += [a + ' ' + b for a, b in zip(title_segments, title_segments[1:])]
    raw_options.append(' '.join(title_segments))
    options = []
    for raw in raw_options:
        for variant in (raw, _TRACK_PREFIX.sub('', raw)):
            value = strip_credits(variant)
            if value:
                options.append(value)
            # Extras such as "(Turn It Up)" or "(DJ-Kicks)" may be part of the real title.
            for extra in extras:
                options.append(strip_credits(variant + ' ' + extra))
    # An unbracketed suffix after the exact title: "Kamasutra Express Train Version".
    for option in list(options):
        rest = option[len(spec.core):].strip() if option.startswith(spec.core + ' ') else ''
        if rest and is_version_descriptor(rest):
            options.append(spec.core)
            version_texts.append(rest)
            tokens = version_tokens(rest)
            if tokens:
                versions.append(tokens)
    return _Name(list(dict.fromkeys(options)), versions, version_texts)


def _title_similarity(spec: Spec, options: list) -> float:
    best = 0.0
    for option in options:
        if option == spec.core:
            return 100.0
        if spec.title_truncated and len(spec.core) >= 6 and option.startswith(spec.core):
            return 100.0
        best = max(best, fuzz.ratio(spec.core, option))
    return best


def _version_ok(spec: Spec, name: _Name) -> tuple[bool, str]:
    if spec.version_prefix:
        ok = any(text.startswith(spec.version_prefix) for text in name.version_texts)
        return ok, '' if ok else 'Sürüm farklı'
    found = [v for v in name.versions if v]
    if spec.version is None:
        return (not found), ('Sürüm farklı: ' + ', '.join(sorted(found[0])) if found else '')
    if any(v == spec.version for v in found):
        return True, ''
    if not found:
        return False, 'Dosyada istenen sürüm yok: ' + ' '.join(sorted(spec.version))
    return False, 'Sürüm farklı: ' + ', '.join(sorted(found[0]))


def score_candidate(track: Track | Spec, c: Candidate, config: dict | None = None) -> Candidate:
    """Identity first (artist, title, version), then quality. Score is 0-100; eligible means auto."""
    spec = track if isinstance(track, Spec) else build_spec(track)
    path = PureWindowsPath(c.filename.replace('/', '\\'))
    stem = path.stem if path.suffix.lstrip('.').casefold() in AUDIO_EXTENSIONS else path.name
    folders = [norm(p) for p in path.parts[-5:-1]]
    name = _parse_name(stem, spec)
    contexts = folders + [norm(stem)]

    primary = spec.credits[0] if spec.credits else ''
    if primary and any(contains_phrase(t, primary, spec.primary_truncated) for t in contexts):
        artist_points, artist_ok = 40, True
    elif any(contains_phrase(t, credit) for credit in spec.credits[1:] for t in contexts):
        artist_points, artist_ok = 34, True
    else:
        artist_points, artist_ok = 0, False

    similarity = _title_similarity(spec, name.options)
    title_ok = similarity == 100 or (similarity >= 92 and len(spec.core) >= 6)
    version_ok, version_reason = _version_ok(spec, name)
    q = quality(c, config)
    length_ok, length_reason = duration_ok(spec.duration, c.length)

    score = artist_points + 0.5 * similarity + (10 if version_ok else 0)
    c.eligible = bool(artist_ok and title_ok and version_ok and length_ok and q.automatic)
    if not (artist_ok and title_ok and version_ok and length_ok):
        score = min(score, 89.0)
    elif not q.automatic:
        score = min(score, 90.0)
    c.score = round(max(0.0, min(100.0, score)), 1)

    reasons = []
    if not artist_ok:
        reasons.append('Sanatçı dosya yolunda yok')
    if not title_ok:
        reasons.append(f'Başlık benzerliği %{similarity:.0f}')
    if version_reason:
        reasons.append(version_reason)
    if length_reason:
        reasons.append(length_reason)
    if not q.automatic:
        reasons.append(q.label)
    c.reason = '; '.join(reasons) or f'Eşleşme doğrulandı · {q.label}'
    return c


def clock(seconds) -> str:
    seconds = int(round(seconds or 0))
    return f'{seconds // 60}:{seconds % 60:02d}'


def duration_ok(expected: float | None, actual: float | None) -> tuple[bool, str]:
    """A known length rules out previews, cut files and radio edits; longer (extended) is fine."""
    if not expected or not actual:
        return True, ''
    if actual < expected * 0.85 - 5:
        return False, f'Süre kısa ({clock(actual)} < {clock(expected)})'
    return True, ''


def mismatch_html(track: Track, filename: str) -> str:
    """File name as HTML with version descriptors that differ from the request highlighted."""
    import html
    spec = build_spec(track)
    name = PureWindowsPath(filename.replace('/', '\\')).name

    def wanted(text):
        if spec.version_prefix:
            return norm(text).startswith(spec.version_prefix)
        return version_tokens(text) == spec.version

    out, last = [], 0
    for match in re.finditer(r'[(\[]([^)\]]*)[)\]]', name):
        out.append(html.escape(name[last:match.start()]))
        piece = html.escape(match.group(0))
        if is_version_descriptor(match.group(1)) and not wanted(match.group(1)):
            piece = f'<b style="color:#d9534f">{piece}</b>'
        out.append(piece)
        last = match.end()
    out.append(html.escape(name[last:]))
    return ''.join(out)


# --------------------------------------------------------------------------
# Peer ranking
# --------------------------------------------------------------------------

def duration_outliers(candidates: list[Candidate], tolerance: float = 0.15) -> set:
    """(peer, filename) of matches whose length disagrees with the others (previews, cut files)."""
    lengths = [c.length for c in candidates if c.length]
    if len(lengths) < 3:
        return set()
    median = statistics.median(lengths)
    return {(c.peer, c.filename) for c in candidates
            if c.length and abs(c.length - median) > max(10, median * tolerance)}


def rank_key(c: Candidate, config: dict | None = None, failures: int = 0, outlier: bool = False,
             reputation: int = 0):
    """Sort descending: reliable available peers first, then quality, short queues and speed.

    `reputation` is the peer's history across all tracks; below -2 the peer is tried last.
    """
    q = quality(c, config)
    free = 1 if c.free_upload_slot else 0
    queue = c.queue_length if c.queue_length is not None else 50
    return (-failures, not outlier, reputation > -3, free, q.tier, min(reputation, 3), -min(queue, 1000),
            c.upload_speed or 0, c.score)
