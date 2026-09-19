from __future__ import annotations

import argparse
import csv
import difflib
import getpass
import json
import logging
import os
import re
import shutil
import sys
import textwrap
import time
import unicodedata
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable
from urllib.parse import quote

import requests

try:
    import yaml
except ImportError:  # Ortam değişkeni/API anahtarı ile çalışma yine mümkündür.
    yaml = None

try:
    from rapidfuzz import fuzz
except ImportError:  # Yalnızca tanılama için yavaş ama güvenli standart-kütüphane fallback'i.
    class _FuzzFallback:
        @staticmethod
        def ratio(left: str, right: str) -> float:
            return 100.0 * difflib.SequenceMatcher(None, left, right).ratio()

        @classmethod
        def token_set_ratio(cls, left: str, right: str) -> float:
            left_tokens = set(left.split())
            right_tokens = set(right.split())
            if not left_tokens or not right_tokens:
                return 0.0
            common = " ".join(sorted(left_tokens & right_tokens))
            left_only = " ".join(sorted(left_tokens - right_tokens))
            right_only = " ".join(sorted(right_tokens - left_tokens))
            return max(
                cls.ratio(common, " ".join(filter(None, (common, left_only)))),
                cls.ratio(common, " ".join(filter(None, (common, right_only)))),
                cls.ratio(
                    " ".join(filter(None, (common, left_only))),
                    " ".join(filter(None, (common, right_only))),
                ),
            )

        @classmethod
        def WRatio(cls, left: str, right: str) -> float:
            direct = cls.ratio(left, right)
            if not left or not right:
                return direct
            shorter, longer = sorted((left, right), key=len)
            partial = max(
                (cls.ratio(shorter, longer[i:i + len(shorter)]) for i in range(max(1, len(longer) - len(shorter) + 1))),
                default=0.0,
            )
            return max(direct, partial * 0.95, cls.token_set_ratio(left, right) * 0.95)

    fuzz = _FuzzFallback()


ROOT = Path(__file__).resolve().parent
UNWANTED_PHRASES = (
    "live", "concert", "karaoke", "cover", "tribute", "instrumental",
    "remix", "mix", "edit", "radio edit", "sped up", "slowed", "nightcore",
)
CSV_FIELDS = {
    "downloaded.csv": [
        "artist", "title", "selected_filename", "peer", "format", "bitrate",
        "size", "score", "status", "timestamp",
    ],
    "manual_review.csv": [
        "artist", "title", "candidate_rank", "candidate_filename", "peer",
        "format", "bitrate", "size", "score", "reason",
    ],
    "failed.csv": ["artist", "title", "reason", "attempt_count", "timestamp"],
}


class SlskdError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_ci(mapping: dict[str, Any], name: str, default: Any = None) -> Any:
    wanted = name.casefold()
    for key, value in mapping.items():
        if str(key).casefold() == wanted:
            return value
    return default


def normalize_text(value: str, *, strip_extension: bool = False) -> str:
    text = unicodedata.normalize("NFKC", value or "").casefold()
    text = text.replace("–", "-").replace("—", "-").replace("−", "-")
    text = text.replace("_", " ")
    if strip_extension:
        text = re.sub(r"\.(flac|mp3|wav|m4a|aac|ogg|opus|wma|ape)$", "", text, flags=re.I)
    text = re.sub(r"^\s*\d{1,3}\s*(?:[-.]\s*)", "", text)
    text = re.sub(r"[^\w\s'-]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s*[-]+\s*", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def safe_message(value: Any) -> str:
    text = str(value)
    patterns = (
        r"(?i)(password|passwd|api[_ -]?key|authorization|bearer|token)\s*[:=]\s*[^\s,;]+",
        r"(?i)(X-API-Key)\s*:\s*[^\s,;]+",
    )
    for pattern in patterns:
        text = re.sub(pattern, lambda m: m.group(1) + "=<redacted>", text)
    return text[:500]


def setup_logging() -> logging.Logger:
    (ROOT / "logs").mkdir(exist_ok=True)
    logger = logging.getLogger("soulseek_batch")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = logging.FileHandler(ROOT / "logs" / "downloader.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def prompt_windows_credentials() -> tuple[str | None, str | None]:
    """Kimlik bilgilerini diske/çıktıya yazmadan küçük bir Windows penceresinde alır."""
    try:
        import tkinter as tk
        from tkinter import messagebox
    except ImportError:
        return None, None

    result: dict[str, str | None] = {"username": None, "password": None}
    root = tk.Tk()
    root.title("Soulseek Batch Downloader — slskd girişi")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    frame = tk.Frame(root, padx=18, pady=16)
    frame.pack()
    tk.Label(frame, text="slskd web kullanıcı adı").grid(row=0, column=0, sticky="w", pady=(0, 4))
    username_entry = tk.Entry(frame, width=34)
    username_entry.grid(row=1, column=0, columnspan=2, pady=(0, 12))
    tk.Label(frame, text="slskd web parolası (kaydedilmez)").grid(row=2, column=0, sticky="w", pady=(0, 4))
    password_entry = tk.Entry(frame, width=34, show="•")
    password_entry.grid(row=3, column=0, columnspan=2, pady=(0, 14))

    def submit() -> None:
        username = username_entry.get().strip()
        password = password_entry.get()
        if not username or not password:
            messagebox.showwarning("Eksik bilgi", "Kullanıcı adı ve parola gereklidir.", parent=root)
            return
        result["username"] = username
        result["password"] = password
        root.destroy()

    def cancel() -> None:
        root.destroy()

    tk.Button(frame, text="İptal", width=10, command=cancel).grid(row=4, column=0, sticky="w")
    tk.Button(frame, text="Giriş yap", width=12, command=submit).grid(row=4, column=1, sticky="e")
    root.protocol("WM_DELETE_WINDOW", cancel)
    root.bind("<Return>", lambda _event: submit())
    root.bind("<Escape>", lambda _event: cancel())
    root.update_idletasks()
    root.geometry(f"+{max(0, (root.winfo_screenwidth() - root.winfo_width()) // 2)}+{max(0, (root.winfo_screenheight() - root.winfo_height()) // 2)}")
    username_entry.focus_set()
    root.mainloop()
    return result["username"], result["password"]


@dataclass(frozen=True)
class Track:
    artist: str
    title: str
    line_number: int

    @property
    def key(self) -> str:
        return f"{normalize_text(self.artist)}::{normalize_text(self.title)}"


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
    reason: str = ""
    eligible: bool = False


class TrackParser:
    SEPARATOR = re.compile(r"\s+[-–—]\s+")
    PLAYLIST_NUMBER = re.compile(r"^\s*\d{1,4}\s*[.)-]\s+")

    @classmethod
    def parse_file(cls, path: Path) -> list[Track]:
        tracks: list[Track] = []
        seen: set[str] = set()
        for line_number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            numbered_playlist = bool(cls.PLAYLIST_NUMBER.match(line))
            line = cls.PLAYLIST_NUMBER.sub("", line, count=1)
            parts = cls.SEPARATOR.split(line)
            if len(parts) < 2 or not all(part.strip() for part in parts):
                raise ValueError(f"songs.txt satır {line_number}: 'Artist - Title' biçimi bekleniyor")
            if numbered_playlist:
                # Yaygın playlist export biçimi: "1. Title - Artist". Başlıktaki ek
                # tireleri korumak için son bölüm sanatçı kabul edilir.
                artist = parts[-1].strip()
                title = " - ".join(part.strip() for part in parts[:-1])
            else:
                artist = parts[0].strip()
                title = " - ".join(part.strip() for part in parts[1:])
            track = Track(artist, title, line_number)
            if track.key not in seen:
                tracks.append(track)
                seen.add(track.key)
        return tracks


class SlskdClient:
    API = "/api/v0"

    def __init__(self, config: dict[str, Any], logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.base_url = str(config["slskd_url"]).rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json", "User-Agent": "SoulseekBatchDownloader/1.0"})
        self.timeout = max(5, int(config.get("search_timeout_seconds", 15)))
        self.auth_mode = "none"
        self._last_search_started = 0.0
        self._search_not_before = 0.0

    def _url(self, endpoint: str) -> str:
        return f"{self.base_url}{self.API}{endpoint}"

    def _request(self, method: str, endpoint: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout + 5)
        try:
            response = self.session.request(method, self._url(endpoint), **kwargs)
        except requests.RequestException as exc:
            raise SlskdError(f"slskd isteği başarısız: {safe_message(exc)}") from None
        if response.status_code >= 400:
            detail = ""
            if response.status_code not in (401, 403):
                detail = safe_message(response.text.strip())
            suffix = f" - {detail}" if detail else ""
            raise SlskdError(f"{method} {endpoint}: HTTP {response.status_code}{suffix}")
        return response

    @staticmethod
    def _yaml_scalar(raw: str) -> str | None:
        value = raw.strip()
        if not value or value in {"~", "null", "Null", "NULL"}:
            return None
        if value.startswith("'"):
            match = re.match(r"^'((?:[^']|'')*)'", value)
            return match.group(1).replace("''", "'") if match else None
        if value.startswith('"'):
            match = re.match(r'^"(?:[^"\\]|\\.)*"', value)
            if not match:
                return None
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
        # YAML'de yorum işareti ancak önünde whitespace varsa yorum başlatır.
        return re.split(r"\s+#", value, maxsplit=1)[0].rstrip() or None

    @classmethod
    def _auth_from_simple_yaml(cls, text: str) -> tuple[str | None, str | None, str | None]:
        """PyYAML yoksa yalnızca web.authentication bölümünü güvenli biçimde okur."""
        text = textwrap.dedent(text)
        in_web = False
        in_auth = False
        in_api_keys = False
        current_key: dict[str, str | None] = {}
        keys: list[tuple[int, str]] = []
        username: str | None = None
        password: str | None = None

        def finish_key() -> None:
            nonlocal current_key
            key_value = current_key.get("key")
            if key_value:
                role = str(current_key.get("role") or "readonly").casefold()
                keys.append(({"administrator": 0, "readwrite": 1, "readonly": 2}.get(role, 3), key_value))
            current_key = {}

        for raw_line in text.splitlines():
            if not raw_line.strip() or raw_line.lstrip().startswith("#"):
                continue
            indent = len(raw_line) - len(raw_line.lstrip(" "))
            stripped = raw_line.strip()
            if indent == 0:
                finish_key()
                in_web = stripped == "web:"
                in_auth = in_api_keys = False
                continue
            if not in_web:
                continue
            if indent == 2:
                finish_key()
                in_auth = stripped == "authentication:"
                in_api_keys = False
                continue
            if not in_auth:
                continue
            if indent == 4:
                finish_key()
                if stripped == "api_keys:":
                    in_api_keys = True
                    continue
                in_api_keys = False
                name, separator, raw_value = stripped.partition(":")
                if separator and name in {"username", "password"}:
                    value = cls._yaml_scalar(raw_value)
                    if name == "username":
                        username = value
                    else:
                        password = value
                continue
            if in_api_keys and indent == 6 and stripped.endswith(":"):
                finish_key()
                continue
            if in_api_keys and indent == 8:
                name, separator, raw_value = stripped.partition(":")
                if separator and name in {"key", "role"}:
                    current_key[name] = cls._yaml_scalar(raw_value)
        finish_key()
        if keys:
            keys.sort(key=lambda item: item[0])
            return keys[0][1], username, password
        return None, username, password

    def _credentials_from_yaml(self) -> tuple[str | None, str | None, str | None]:
        path = Path(os.path.expandvars(str(self.config.get("slskd_config_path", ""))))
        if not path.is_file():
            return None, None, None
        try:
            text = path.read_text(encoding="utf-8")
            if yaml is None:
                active_credentials = self._auth_from_simple_yaml(text)
                document = {}
            else:
                document = yaml.safe_load(text) or {}
                auth = ((document.get("web") or {}).get("authentication") or {})
                active_credentials = (None, str(auth.get("username") or "") or None, str(auth.get("password") or "") or None)

            auth = ((document.get("web") or {}).get("authentication") or {})
            keys = auth.get("api_keys") or {}
            ranked: list[tuple[int, str]] = []
            for entry in keys.values():
                if not isinstance(entry, dict) or not entry.get("key"):
                    continue
                role = str(entry.get("role", "readonly")).casefold()
                rank = {"administrator": 0, "readwrite": 1, "readonly": 2}.get(role, 3)
                ranked.append((rank, str(entry["key"])))
            if ranked:
                ranked.sort(key=lambda item: item[0])
                return ranked[0][1], None, None
            if active_credentials[0] or (active_credentials[1] and active_credentials[2]):
                return active_credentials

            # slskd.yml çoğu kurulumda örnek dosyanın yorumlarıyla gelir. Etkin web
            # ayarı yoksa slskd bu yorumlarda gösterilen yerleşik varsayılanları kullanır.
            # Değerler yalnızca bellekte çözülür; hiçbir çıktıya veya dosyaya yazılmaz.
            uncommented: list[str] = []
            for raw_line in text.splitlines():
                stripped = raw_line.lstrip(" ")
                if not stripped.startswith("#"):
                    continue
                leading = raw_line[:len(raw_line) - len(stripped)]
                content = stripped[1:]
                if content.startswith(" "):
                    content = content[1:]
                uncommented.append(leading + content)
            _, default_username, default_password = self._auth_from_simple_yaml("\n".join(uncommented))
            return None, default_username, default_password
        except Exception as exc:
            self.logger.warning("Yapılandırma kimlik bilgileri okunamadı: %s", safe_message(type(exc).__name__))
            return None, None, None

    def authenticate(self) -> str:
        enabled_response = requests.get(self._url("/session/enabled"), timeout=8)
        enabled_response.raise_for_status()
        if enabled_response.json() is False:
            self.auth_mode = "disabled"
            return self.auth_mode

        api_key = os.getenv(str(self.config.get("api_key_env", "SLSKD_API_KEY")))
        username = os.getenv(str(self.config.get("web_username_env", "SLSKD_WEB_USERNAME")))
        password = os.getenv(str(self.config.get("web_password_env", "SLSKD_WEB_PASSWORD")))
        if not api_key and not (username and password):
            api_key, username, password = self._credentials_from_yaml()

        if not api_key and not (username and password) and self.config.get("prompt_for_web_credentials", True):
            prompt_mode = str(self.config.get("credential_prompt", "auto")).casefold()
            if os.name == "nt" and prompt_mode == "gui":
                username, password = prompt_windows_credentials()
            elif sys.stdin.isatty():
                username = input("slskd web kullanıcı adı: ").strip()
                password = getpass.getpass("slskd web parolası (gösterilmez): ")
            elif os.name == "nt":
                username, password = prompt_windows_credentials()

        if api_key:
            self.session.headers["X-API-Key"] = api_key
            self.auth_mode = "api-key"
        elif username and password:
            response = self._request("POST", "/session", json={"username": username, "password": password})
            payload = response.json()
            token = get_ci(payload, "token")
            token_type = get_ci(payload, "tokenType", "Bearer")
            if not token:
                raise SlskdError("slskd oturum yanıtında token bulunamadı")
            self.session.headers["Authorization"] = f"{token_type} {token}"
            self.auth_mode = "jwt"
        else:
            raise SlskdError(
                "slskd kimlik doğrulaması etkin, fakat API anahtarı veya web kimlik bilgileri bulunamadı. "
                "Ortam değişkenlerini ayarlayın ya da slskd_config_path erişimini kontrol edin."
            )

        self._request("GET", "/session")
        return self.auth_mode

    def application(self) -> dict[str, Any]:
        return self._request("GET", "/application").json()

    def server(self) -> dict[str, Any]:
        return self._request("GET", "/server").json()

    @staticmethod
    def server_connected(server: dict[str, Any]) -> bool:
        for name in ("isConnected", "connected", "state"):
            value = get_ci(server, name)
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and "connected" in value.casefold() and "disconnected" not in value.casefold():
                return True
        # 0.26.0 exposes the Soulseek state as an integer flag value; non-zero plus a username
        # is treated as connected only when no explicit boolean/string is available.
        state = get_ci(server, "state")
        username = get_ci(server, "username")
        return isinstance(state, int) and state != 0 and bool(username)

    def defer_search(self, seconds: float) -> None:
        self._search_not_before = max(self._search_not_before, time.monotonic() + max(0.0, seconds))

    def _wait_for_search_slot(self) -> None:
        cooldown = float(self.config.get("search_cooldown_seconds", 0))
        target = max(self._search_not_before, self._last_search_started + cooldown)
        wait_seconds = target - time.monotonic()
        if wait_seconds > 0.25:
            print(f"Soulseek rate-limit cooldown: {wait_seconds:.0f}s", flush=True)
            time.sleep(wait_seconds)

    def start_search(self, search_text: str) -> str:
        self._wait_for_search_slot()
        search_id = str(uuid.uuid4())
        payload = {
            "id": search_id,
            "searchText": search_text,
            # slskd 0.26.0 DTO açıklaması saniye dese de Soulseek.NET 10.0.2
            # SearchOptions değeri milisaniye olarak yorumlar.
            "searchTimeout": int(self.config.get("search_timeout_seconds", 15)) * 1000,
            "responseLimit": int(self.config.get("response_limit", 100)),
            "fileLimit": int(self.config.get("file_limit", 5000)),
            "filterResponses": True,
        }
        response = self._request("POST", "/searches", json=payload).json()
        self._last_search_started = time.monotonic()
        self._search_not_before = 0.0
        return str(get_ci(response, "id", search_id))

    def search_status(self, search_id: str) -> dict[str, Any]:
        return self._request("GET", f"/searches/{search_id}").json()

    def search_responses(self, search_id: str) -> list[dict[str, Any]]:
        response = self._request("GET", f"/searches/{search_id}/responses").json()
        return response if isinstance(response, list) else []

    def wait_and_collect(self, search_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        deadline = time.monotonic() + float(self.config.get("search_result_wait_seconds", 8))
        status: dict[str, Any] = {}
        while time.monotonic() < deadline:
            time.sleep(1)
            status = self.search_status(search_id)
            if bool(get_ci(status, "isComplete", False)):
                break
        return status, self.search_responses(search_id)

    def queue_download(self, candidate: Candidate) -> dict[str, Any]:
        payload = {
            "searchId": candidate.search_id,
            "username": candidate.peer,
            "files": [{"filename": candidate.filename, "size": candidate.size}],
            "options": {},
        }
        response = self._request("POST", "/transfers/downloads/batches", json=payload)
        if response.status_code not in (200, 201, 207):
            raise SlskdError(f"İndirme kuyruğu beklenmeyen HTTP {response.status_code} döndürdü")
        body = response.json()
        failures = get_ci(body, "failures", []) or []
        if failures:
            message = get_ci(failures[0], "message", "kuyruğa eklenemedi")
            raise SlskdError(safe_message(message))
        return body

    def get_download(self, username: str, transfer_id: str) -> dict[str, Any]:
        return self._request("GET", f"/transfers/downloads/{quote(username, safe='')}/{transfer_id}").json()


class CandidateScorer:
    def __init__(self, config: dict[str, Any]):
        self.minimum_mp3_bitrate = int(config.get("minimum_mp3_bitrate", 320))

    @staticmethod
    def _parts(filename: str) -> tuple[str, list[str]]:
        path = PureWindowsPath(filename.replace("/", "\\"))
        stem = path.stem
        segments = [normalize_text(part, strip_extension=True) for part in path.parts[:-1]]
        return normalize_text(stem, strip_extension=True), [part for part in segments if part]

    @staticmethod
    def _phrase_present(text: str, phrase: str) -> bool:
        return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None

    def score(self, track: Track, candidate: Candidate) -> Candidate:
        artist = normalize_text(track.artist)
        title = normalize_text(track.title)
        stem, directories = self._parts(candidate.filename)
        full = " ".join(directories + [stem])

        # WRatio deliberately rewards short substring matches.  That is useful
        # for ranking, but it must never be enough to authorize a download: a
        # title-only search can otherwise give an unrelated artist a very high
        # score.  Treat the first credited artist as the identity anchor and
        # require it to occur as a complete normalized phrase in the path/name.
        primary_artist_raw = re.split(r"\s*(?:,|&|\bfeat\.?\b|\bft\.?\b|\bx\b)\s*", track.artist, maxsplit=1, flags=re.I)[0]
        primary_artist = normalize_text(primary_artist_raw)
        artist_identity_ok = bool(primary_artist and self._phrase_present(full, primary_artist))

        artist_sources = directories + [stem, full]
        artist_similarity = max((fuzz.WRatio(artist, source) for source in artist_sources), default=0.0)
        title_similarity = max(fuzz.ratio(title, stem), fuzz.token_set_ratio(title, stem))
        expected_tokens = set((artist + " " + title).split())
        actual_tokens = set(full.split())
        token_score = 100.0 * len(expected_tokens & actual_tokens) / max(1, len(expected_tokens))

        extension = candidate.extension.casefold().lstrip(".")
        size_mb = candidate.size / (1024 * 1024) if candidate.size else 0.0
        if extension == "flac":
            format_score = 5.0
            quality_ok = size_mb >= 5
            quality_reason = "FLAC"
            if candidate.bit_depth is None or candidate.sample_rate is None:
                quality_reason += "; spektral/gerçek kayıpsız doğrulaması indirme öncesi yapılamaz"
        elif extension == "mp3":
            quality_ok = candidate.bitrate is not None and candidate.bitrate >= self.minimum_mp3_bitrate
            format_score = 3.0 if quality_ok else -12.0
            quality_reason = f"MP3 {candidate.bitrate or 'bilinmeyen'} kbps"
        else:
            quality_ok = False
            format_score = -20.0
            quality_reason = f"desteklenmeyen format: {extension or 'bilinmeyen'}"

        size_ok = (5 <= size_mb <= 500) if extension == "flac" else (2 <= size_mb <= 80)
        size_score = 1.0 if size_ok else -4.0
        availability_score = 0.0
        if candidate.free_upload_slot is True:
            availability_score += 1.5
        if candidate.queue_length is not None and candidate.queue_length <= 5:
            availability_score += 0.5
        if candidate.upload_speed is not None and candidate.upload_speed >= 100_000:
            availability_score += 0.5

        score = (
            0.39 * artist_similarity
            + 0.43 * title_similarity
            + 0.12 * token_score
            + format_score
            + size_score
            + availability_score
        )

        requested = f"{artist} {title}"
        unwanted: list[str] = []
        for phrase in UNWANTED_PHRASES:
            if self._phrase_present(full, phrase) and not self._phrase_present(requested, phrase):
                unwanted.append(phrase)
        if unwanted:
            score -= min(45.0, 18.0 + 8.0 * (len(unwanted) - 1))
        if artist_similarity < 72:
            score -= 12.0
        if title_similarity < 78:
            score -= 12.0

        # A missing artist identity is a hard safety gate.  Keep the candidate
        # visible for manual review, but below any normal auto-download cutoff.
        if not artist_identity_ok:
            score = min(score, 89.0)

        candidate.score = round(max(0.0, min(100.0, score)), 1)
        candidate.eligible = bool(quality_ok and size_ok and not unwanted and artist_identity_ok)
        details = [
            f"artist={artist_similarity:.0f}", f"title={title_similarity:.0f}",
            f"tokens={token_score:.0f}", quality_reason,
        ]
        if unwanted:
            details.append("istenmeyen sürüm: " + ", ".join(unwanted))
        if not artist_identity_ok:
            details.append("ana sanatçı dosya yolu/adında doğrulanamadı")
        if not size_ok:
            details.append(f"şüpheli boyut: {size_mb:.1f} MB")
        candidate.reason = "; ".join(details)
        return candidate


class StateManager:
    def __init__(self, path: Path):
        self.path = path
        try:
            self.data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = {"version": 1, "tracks": {}}
        self.data.setdefault("tracks", {})

    def status(self, track: Track) -> str | None:
        item = self.data["tracks"].get(track.key, {})
        return item.get("status")

    def update(self, track: Track, status: str, **details: Any) -> None:
        self.data["tracks"][track.key] = {
            "artist": track.artist,
            "title": track.title,
            "status": status,
            "timestamp": utc_now(),
            **details,
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def transition(self, key: str, status: str, **details: Any) -> None:
        record = self.data["tracks"].get(key)
        if not record:
            return
        record.update({"status": status, "timestamp": utc_now(), **details})
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)


class ReportWriter:
    def __init__(self, root: Path):
        self.root = root
        for name, fields in CSV_FIELDS.items():
            path = root / name
            if not path.exists() or path.stat().st_size == 0:
                with path.open("w", encoding="utf-8-sig", newline="") as handle:
                    csv.DictWriter(handle, fieldnames=fields).writeheader()

    def append(self, name: str, row: dict[str, Any]) -> None:
        with (self.root / name).open("a", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS[name], extrasaction="ignore")
            writer.writerow({field: row.get(field, "") for field in CSV_FIELDS[name]})

    def update_download_status(self, peer: str, filename: str, status: str) -> None:
        path = self.root / "downloaded.csv"
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        changed = False
        for row in rows:
            if row.get("peer") == peer and row.get("selected_filename") == filename:
                row["status"] = status
                row["timestamp"] = utc_now()
                changed = True
        if not changed:
            return
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS["downloaded.csv"])
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(path)


class BatchDownloader:
    def __init__(self, config: dict[str, Any], dry_run: bool, logger: logging.Logger):
        self.config = config
        self.dry_run = dry_run
        self.logger = logger
        self.client = SlskdClient(config, logger)
        self.scorer = CandidateScorer(config)
        self.state = StateManager(ROOT / "state.json")
        self.reports = ReportWriter(ROOT)

    def _playlist_directory(self) -> Path:
        configured = str(self.config.get("playlist_output_dir", "downloads"))
        path = Path(os.path.expandvars(configured))
        return path if path.is_absolute() else ROOT / path

    def _slskd_download_directory(self) -> Path:
        configured = str(self.config.get(
            "slskd_download_dir",
            r"%LOCALAPPDATA%\slskd\downloads",
        ))
        return Path(os.path.expandvars(configured))

    @staticmethod
    def _unique_destination(directory: Path, filename: str, source_size: int) -> Path:
        destination = directory / filename
        if not destination.exists() or destination.stat().st_size == source_size:
            return destination
        for number in range(2, 1000):
            candidate = directory / f"{destination.stem} ({number}){destination.suffix}"
            if not candidate.exists() or candidate.stat().st_size == source_size:
                return candidate
        raise SlskdError(f"Aynı adlı dosya için güvenli hedef üretilemedi: {filename}")

    def organize_completed(self, tracks: list[Track]) -> tuple[int, int]:
        """Tamamlanan dosyaları düz bir DJ klasörüne taşı ve M3U8 üret."""
        source_root = self._slskd_download_directory().resolve()
        playlist_dir = self._playlist_directory().resolve()
        playlist_dir.mkdir(parents=True, exist_ok=True)
        moved = 0
        missing = 0

        for track in tracks:
            record = self.state.data["tracks"].get(track.key, {})
            if record.get("status") != "succeeded":
                continue
            existing = record.get("playlist_path")
            if existing and Path(existing).is_file():
                continue
            selected = str(record.get("selected_filename", ""))
            filename = PureWindowsPath(selected).name
            if not filename:
                missing += 1
                continue
            matches = [path for path in source_root.rglob(filename) if path.is_file()] if source_root.exists() else []
            if not matches:
                already = playlist_dir / filename
                if already.is_file():
                    self.state.transition(track.key, "succeeded", playlist_path=str(already))
                else:
                    missing += 1
                continue
            source = max(matches, key=lambda path: path.stat().st_mtime)
            destination = self._unique_destination(playlist_dir, filename, source.stat().st_size)
            if destination.exists() and destination.stat().st_size == source.stat().st_size:
                source.unlink()
            else:
                shutil.move(str(source), str(destination))
            self.state.transition(track.key, "succeeded", playlist_path=str(destination))
            moved += 1

            parent = source.parent
            while parent != source_root and source_root in parent.parents:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent

        playlist_lines = ["#EXTM3U"]
        for track in tracks:
            record = self.state.data["tracks"].get(track.key, {})
            path_text = record.get("playlist_path")
            if record.get("status") == "succeeded" and path_text and Path(path_text).is_file():
                playlist_lines.append(Path(path_text).name)
        (playlist_dir / "DJ Playlist.m3u8").write_text(
            "\n".join(playlist_lines) + "\n",
            encoding="utf-8-sig",
        )
        return moved, missing

    def connect(self) -> dict[str, Any]:
        mode = self.client.authenticate()
        app = self.client.application()
        server = self.client.server()
        print(f"slskd API: erişilebilir (kimlik doğrulama: {mode})")
        version = get_ci(app, "version", get_ci(app, "currentVersion", "bilinmiyor"))
        if isinstance(version, dict):
            version = get_ci(version, "current", get_ci(version, "full", "bilinmiyor"))
        print(f"slskd sürümü: {version}")
        if not self.client.server_connected(server):
            raise SlskdError("API erişilebilir, fakat Soulseek sunucu bağlantısı aktif görünmüyor")
        print("Soulseek: bağlı")
        return server

    def reconcile_queued(self) -> tuple[int, int]:
        succeeded = 0
        failed = 0
        for key, record in list(self.state.data["tracks"].items()):
            if record.get("status") != "queued" or not record.get("transfer_id") or not record.get("peer"):
                continue
            try:
                transfer = self.client.get_download(str(record["peer"]), str(record["transfer_id"]))
            except SlskdError as exc:
                self.logger.warning("Transfer durumu alınamadı: %s", safe_message(exc))
                continue
            state_text = str(get_ci(transfer, "state", "")).casefold()
            if "completed" not in state_text:
                continue
            filename = str(record.get("selected_filename", ""))
            if "succeeded" in state_text:
                self.state.transition(key, "succeeded")
                self.reports.update_download_status(str(record["peer"]), filename, "succeeded")
                succeeded += 1
            else:
                reason = safe_message(get_ci(transfer, "exception", "Transfer başarısız"))
                self.reports.update_download_status(str(record["peer"]), filename, "failed")
                attempted_peers = list(record.get("attempted_peers") or [str(record["peer"])])
                attempted_keys = {peer.casefold() for peer in attempted_peers}
                fallbacks = list(record.get("fallback_candidates") or [])
                max_attempts = int(self.config.get("max_peer_attempts", 3))
                fallback_queued = False
                while fallbacks and len(attempted_peers) < max_attempts:
                    raw_candidate = fallbacks.pop(0)
                    try:
                        candidate = Candidate(**raw_candidate)
                    except (TypeError, ValueError):
                        continue
                    if candidate.peer.casefold() in attempted_keys:
                        continue
                    attempted_peers.append(candidate.peer)
                    attempted_keys.add(candidate.peer.casefold())
                    try:
                        response = self.client.queue_download(candidate)
                        batch = get_ci(response, "batch", {}) or {}
                        transfers = get_ci(batch, "transfers", []) or []
                        transfer_id = get_ci(transfers[0], "id") if transfers else None
                        self.reports.append("downloaded.csv", {
                            "artist": record.get("artist", ""), "title": record.get("title", ""),
                            "selected_filename": candidate.filename, "peer": candidate.peer,
                            "format": candidate.extension, "bitrate": candidate.bitrate or "",
                            "size": candidate.size, "score": candidate.score,
                            "status": "queued", "timestamp": utc_now(),
                        })
                        self.state.transition(
                            key, "queued", selected_filename=candidate.filename,
                            peer=candidate.peer, score=candidate.score,
                            transfer_id=transfer_id, size=candidate.size,
                            extension=candidate.extension, attempted_peers=attempted_peers,
                            peer_attempt_count=len(attempted_peers),
                            fallback_candidates=fallbacks,
                            previous_failure=reason,
                        )
                        print(
                            f"Peer fallback: {record.get('artist', '')} - {record.get('title', '')} "
                            f"için {len(attempted_peers)}. kullanıcı denendi"
                        )
                        fallback_queued = True
                        break
                    except SlskdError as exc:
                        self.logger.warning("Fallback peer kuyruğa eklenemedi: %s", safe_message(exc))
                if fallback_queued:
                    continue
                self.state.transition(
                    key, "failed", reason=reason,
                    attempted_peers=attempted_peers,
                    peer_attempt_count=len(attempted_peers),
                    fallback_candidates=fallbacks,
                )
                self.reports.append("failed.csv", {
                    "artist": record.get("artist", ""), "title": record.get("title", ""),
                    "reason": reason, "attempt_count": len(attempted_peers),
                    "timestamp": utc_now(),
                })
                failed += 1
        return succeeded, failed

    @staticmethod
    def _extract_candidates(responses: Iterable[dict[str, Any]], search_id: str) -> list[Candidate]:
        candidates: list[Candidate] = []
        for response in responses:
            peer = str(get_ci(response, "username", ""))
            queue = get_ci(response, "queueLength")
            speed = get_ci(response, "uploadSpeed")
            free = get_ci(response, "hasFreeUploadSlot")
            for item in get_ci(response, "files", []) or []:
                filename = str(get_ci(item, "filename", ""))
                extension = str(get_ci(item, "extension", "") or Path(filename).suffix).lstrip(".").casefold()
                if not peer or not filename:
                    continue
                candidates.append(Candidate(
                    peer=peer,
                    filename=filename,
                    size=int(get_ci(item, "size", 0) or 0),
                    bitrate=int(get_ci(item, "bitRate")) if get_ci(item, "bitRate") is not None else None,
                    extension=extension,
                    queue_length=int(queue) if queue is not None else None,
                    upload_speed=int(speed) if speed is not None else None,
                    free_upload_slot=bool(free) if free is not None else None,
                    bit_depth=get_ci(item, "bitDepth"),
                    sample_rate=get_ci(item, "sampleRate"),
                    length=get_ci(item, "length"),
                    search_id=search_id,
                ))
        return candidates

    def test(self) -> None:
        self.connect()
        query = str(self.config.get("test_search_text", "Massive Attack Teardrop"))
        print(f"Test araması: {query}")
        search_id = self.client.start_search(query)
        status, responses = self.client.wait_and_collect(search_id)
        candidates = self._extract_candidates(responses, search_id)
        print(
            f"Arama API'si: başarılı; {len(responses)} peer yanıtı, "
            f"{len(candidates)} dosya adayı (durum: {get_ci(status, 'state', 'bilinmiyor')})"
        )
        for candidate in candidates[:5]:
            bitrate = f", {candidate.bitrate} kbps" if candidate.bitrate else ""
            print(f"  - {candidate.filename} [{candidate.extension.upper()}{bitrate}] peer={candidate.peer}")
        print("İndirme isteği gönderilmedi.")

    def _search_variants(self, track: Track) -> list[str]:
        primary_artist = re.split(r"\s*(?:,|\bfeat\.?\b|\bft\.?\b)\s*", track.artist, maxsplit=1, flags=re.I)[0]
        base_title = re.sub(
            r"\s*[\[(](?:feat\.?|ft\.?|featuring)\b.*?[\])]\s*",
            " ",
            track.title,
            flags=re.I,
        ).strip()
        raw_variants = [
            normalize_text(base_title),
            f"{normalize_text(primary_artist)} {normalize_text(base_title)}",
            f"{normalize_text(track.artist)} {normalize_text(track.title)}",
            f"{track.artist} {track.title}",
            f'"{primary_artist}" "{base_title}"',
        ]
        variants: list[str] = []
        seen: set[str] = set()
        for variant in raw_variants:
            key = variant.casefold()
            if key not in seen:
                variants.append(variant)
                seen.add(key)
        limit = max(1, int(self.config.get("max_search_variants", 2)))
        return variants[:limit]

    def search_track(self, track: Track) -> list[Candidate]:
        all_candidates: dict[tuple[str, str], Candidate] = {}
        for index, query in enumerate(self._search_variants(track), 1):
            if index > 1 and all_candidates:
                best = max(item.score for item in all_candidates.values())
                if best >= float(self.config.get("auto_download_threshold", 92)):
                    break
            print(f"Searching{' (alternative)' if index > 1 else ''}...")
            search_id = self.client.start_search(query)
            _, responses = self.client.wait_and_collect(search_id)
            for candidate in self._extract_candidates(responses, search_id):
                scored = self.scorer.score(track, candidate)
                key = (scored.peer.casefold(), scored.filename.casefold())
                previous = all_candidates.get(key)
                if previous is None or scored.score > previous.score:
                    all_candidates[key] = scored
            if not all_candidates:
                self.client.defer_search(float(self.config.get("zero_result_backoff_seconds", 25)))
                continue
        return sorted(
            all_candidates.values(),
            key=lambda item: (item.score, item.extension == "flac", item.free_upload_slot is True, item.upload_speed or 0),
            reverse=True,
        )

    @staticmethod
    def _print_best(candidate: Candidate) -> None:
        bitrate = f"{candidate.bitrate} kbps" if candidate.bitrate else "metadata yok"
        print("\nBest:")
        print(candidate.filename)
        print(f"{candidate.extension.upper()} | {bitrate} | {candidate.size / (1024 * 1024):.1f} MB")
        print(f"score: {candidate.score:.1f} | peer: {candidate.peer}")

    def _write_review(self, track: Track, candidates: list[Candidate], reason: str) -> None:
        count = int(self.config.get("manual_review_candidates", 5))
        for rank, candidate in enumerate(candidates[:count], 1):
            self.reports.append("manual_review.csv", {
                "artist": track.artist,
                "title": track.title,
                "candidate_rank": rank,
                "candidate_filename": candidate.filename,
                "peer": candidate.peer,
                "format": candidate.extension,
                "bitrate": candidate.bitrate or "",
                "size": candidate.size,
                "score": candidate.score,
                "reason": f"{reason}; {candidate.reason}",
            })

    def process_track(self, track: Track) -> None:
        candidates = self.search_track(track)
        print(f"{len(candidates)} candidates received.")
        if not candidates:
            reason = "Sonuç bulunamadı"
            self.reports.append("failed.csv", {
                "artist": track.artist, "title": track.title, "reason": reason,
                "attempt_count": 0, "timestamp": utc_now(),
            })
            self.state.update(track, "failed", reason=reason, attempt_count=0)
            print("LOW / NOT FOUND\nfailed.csv dosyasına eklendi.")
            return

        best = candidates[0]
        self._print_best(best)
        auto_threshold = float(self.config.get("auto_download_threshold", 92))
        review_threshold = float(self.config.get("review_threshold", 75))
        high = [item for item in candidates if item.score >= auto_threshold and item.eligible]

        if best.score >= auto_threshold and high:
            if self.dry_run:
                print("HIGH CONFIDENCE\nDRY RUN: indirme kuyruğuna eklenmedi.")
                return
            attempts = 0
            errors: list[str] = []
            peers_seen: set[str] = set()
            max_attempts = int(self.config.get("max_peer_attempts", 3))
            peer_candidates: list[Candidate] = []
            for candidate in high:
                if candidate.peer.casefold() not in {item.peer.casefold() for item in peer_candidates}:
                    peer_candidates.append(candidate)
                if len(peer_candidates) >= max_attempts:
                    break
            for candidate_index, candidate in enumerate(peer_candidates):
                if candidate.peer.casefold() in peers_seen:
                    continue
                peers_seen.add(candidate.peer.casefold())
                attempts += 1
                try:
                    response = self.client.queue_download(candidate)
                    batch = get_ci(response, "batch", {}) or {}
                    transfers = get_ci(batch, "transfers", []) or []
                    transfer_id = get_ci(transfers[0], "id") if transfers else None
                    self.reports.append("downloaded.csv", {
                        "artist": track.artist, "title": track.title,
                        "selected_filename": candidate.filename, "peer": candidate.peer,
                        "format": candidate.extension, "bitrate": candidate.bitrate or "",
                        "size": candidate.size, "score": candidate.score,
                        "status": "queued", "timestamp": utc_now(),
                    })
                    self.state.update(
                        track, "queued", selected_filename=candidate.filename,
                        peer=candidate.peer, score=candidate.score, transfer_id=transfer_id,
                        size=candidate.size, extension=candidate.extension,
                        attempted_peers=[item.peer for item in peer_candidates[:candidate_index + 1]],
                        peer_attempt_count=attempts,
                        fallback_candidates=[asdict(item) for item in peer_candidates[candidate_index + 1:]],
                    )
                    print("HIGH CONFIDENCE\nQueued for download.")
                    return
                except SlskdError as exc:
                    errors.append(safe_message(exc))
                    self.logger.warning("Kuyruğa ekleme başarısız: %s", safe_message(exc))
                    if attempts >= max_attempts:
                        break
            reason = "Yüksek güvenli adaylar kuyruğa eklenemedi: " + " | ".join(errors)
            self.reports.append("failed.csv", {
                "artist": track.artist, "title": track.title, "reason": reason,
                "attempt_count": attempts, "timestamp": utc_now(),
            })
            self.state.update(track, "failed", reason=reason, attempt_count=attempts)
            print("FAILED\nfailed.csv dosyasına eklendi.")
            return

        if best.score >= review_threshold:
            reason = "REVIEW: eşleşme otomatik indirme eşiğinin altında veya kalite uygun değil"
            self._write_review(track, candidates, reason)
            if not self.dry_run:
                self.state.update(track, "review", reason=reason, best_score=best.score)
            print("REVIEW\nNot downloading automatically. Added to manual_review.csv.")
        else:
            reason = f"LOW: en iyi skor {best.score:.1f}"
            self._write_review(track, candidates, reason)
            if not self.dry_run:
                self.reports.append("failed.csv", {
                    "artist": track.artist, "title": track.title, "reason": reason,
                    "attempt_count": 0, "timestamp": utc_now(),
                })
                self.state.update(track, "failed", reason=reason, attempt_count=0)
            print("LOW / NOT FOUND\nOtomatik indirme yapılmadı.")

    def run(self, tracks: list[Track], retry_failed: bool) -> None:
        self.connect()
        reconciled_succeeded, reconciled_failed = self.reconcile_queued()
        if reconciled_succeeded or reconciled_failed:
            print(f"Transfer sync: {reconciled_succeeded} succeeded, {reconciled_failed} failed")
        selected: list[Track] = []
        for track in tracks:
            status = self.state.status(track)
            if retry_failed:
                if status in {"failed", "review"}:
                    selected.append(track)
            elif status is not None:
                print(f"SKIP: {track.artist} - {track.title} ({status})")
            else:
                selected.append(track)
        total = len(selected)
        all_tracks = TrackParser.parse_file(ROOT / "songs.txt")
        for index, track in enumerate(selected, 1):
            # Uzun batch sırasında tamamlanan dosyaları turun sonuna kadar albüm
            # klasörlerinde bekletme. Her yeni parça öncesinde transferleri
            # eşitleyip bitenleri düz DJ klasörüne taşı.
            if not self.dry_run:
                synced_succeeded, synced_failed = self.reconcile_queued()
                moved, missing = self.organize_completed(all_tracks)
                if synced_succeeded or synced_failed or moved:
                    print(
                        f"Ara eşitleme: {synced_succeeded} tamamlandı, "
                        f"{synced_failed} başarısız, {moved} playlist klasörüne taşındı"
                    )
            print(f"\n[{index}/{total}] {track.artist} - {track.title}\n")
            try:
                self.process_track(track)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                reason = safe_message(exc)
                self.logger.error("Parça işlenemedi: %s", reason)
                self.reports.append("failed.csv", {
                    "artist": track.artist, "title": track.title, "reason": reason,
                    "attempt_count": 0, "timestamp": utc_now(),
                })
                if not self.dry_run:
                    self.state.update(track, "failed", reason=reason, attempt_count=0)
                print(f"ERROR: {reason}")
        if not self.dry_run:
            reconciled_succeeded, reconciled_failed = self.reconcile_queued()
            if reconciled_succeeded or reconciled_failed:
                print(f"Transfer sync: {reconciled_succeeded} succeeded, {reconciled_failed} failed")
            moved, missing = self.organize_completed(all_tracks)
            if moved or missing:
                print(f"Playlist düzenleme: {moved} taşındı, {missing} dosya henüz bulunamadı")


def load_config() -> dict[str, Any]:
    path = ROOT / "config.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"config.json okunamadı: {safe_message(exc)}")
    if int(config.get("concurrency", 1)) not in (1, 2):
        raise SystemExit("Güvenlik için concurrency 1 veya 2 olmalıdır")
    if float(config.get("review_threshold", 75)) >= float(config.get("auto_download_threshold", 92)):
        raise SystemExit("review_threshold, auto_download_threshold değerinden küçük olmalıdır")
    return config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="slskd üzerinden yüksek güvenli Soulseek toplu indirme aracı")
    parser.add_argument("--test", action="store_true", help="Bağlantı, Soulseek ve tek arama testi; indirme yapmaz")
    parser.add_argument("--dry-run", action="store_true", help="Ara ve puanla; indirme yapma")
    parser.add_argument("--limit", type=int, help="İşlenecek en fazla parça sayısı")
    parser.add_argument("--retry-failed", action="store_true", help="Daha önce failed/review olan çözülememiş parçaları yeniden dene")
    parser.add_argument("--check-transfers", action="store_true", help="Kuyruktaki transfer durumlarını eşitle ve çık")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit en az 1 olmalıdır")
    if args.test and (args.dry_run or args.retry_failed or args.limit or args.check_transfers):
        parser.error("--test başka işlem seçenekleriyle birlikte kullanılamaz")
    if args.check_transfers and (args.dry_run or args.retry_failed or args.limit):
        parser.error("--check-transfers başka işlem seçenekleriyle birlikte kullanılamaz")
    return args


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    args = parse_args()
    logger = setup_logging()
    try:
        downloader = BatchDownloader(load_config(), args.dry_run, logger)
        if args.test:
            downloader.test()
            return 0
        if args.check_transfers:
            downloader.connect()
            succeeded, failed = downloader.reconcile_queued()
            print(f"Transfer sync: {succeeded} succeeded, {failed} failed")
            tracks = TrackParser.parse_file(ROOT / "songs.txt")
            moved, missing = downloader.organize_completed(tracks)
            print(f"Playlist düzenleme: {moved} taşındı, {missing} dosya henüz bulunamadı")
            return 0
        tracks = TrackParser.parse_file(ROOT / "songs.txt")
        if args.limit:
            tracks = tracks[:args.limit]
        if not tracks:
            print("songs.txt içinde işlenecek parça yok.")
            return 0
        downloader.run(tracks, retry_failed=args.retry_failed)
        return 0
    except KeyboardInterrupt:
        print("\nDurduruldu. state.json korunmuştur.")
        return 130
    except Exception as exc:
        message = safe_message(exc)
        logger.error("Program sonlandı: %s", message)
        print(f"ERROR: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
