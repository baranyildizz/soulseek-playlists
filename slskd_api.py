"""Small slskd REST client (tested against slskd 0.26).

Errors are deliberately terse: response bodies and exception texts from the
server can contain credentials or paths, so they never reach the UI or logs.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

import requests

try:
    import yaml
except ImportError:  # API key / environment credentials still work
    yaml = None


class ConnectionLost(RuntimeError):
    """slskd did not answer; the engine reconnects and retries later."""


class ApiError(RuntimeError):
    def __init__(self, status: int, message: str = ''):
        super().__init__(f'slskd HTTP {status}' + (f' · {message}' if message else ''))
        self.status = status


def get_ci(mapping, name, default=None):
    """slskd JSON is camelCase; accept any casing."""
    if not isinstance(mapping, dict):
        return default
    wanted = name.casefold()
    for key, value in mapping.items():
        if str(key).casefold() == wanted:
            return value
    return default


def _yaml_credentials(path: str) -> tuple[str | None, str | None, str | None]:
    """(api_key, username, password) from slskd.yml web.authentication; never logged."""
    if not path or yaml is None:
        return None, None, None
    try:
        document = yaml.safe_load(Path(os.path.expandvars(path)).read_text(encoding='utf-8')) or {}
    except Exception:
        return None, None, None
    auth = ((document.get('web') or {}).get('authentication') or {}) if isinstance(document, dict) else {}
    ranked = []
    for entry in (auth.get('api_keys') or {}).values():
        if isinstance(entry, dict) and entry.get('key'):
            role = str(entry.get('role', 'readonly')).casefold()
            ranked.append(({'administrator': 0, 'readwrite': 1}.get(role, 2), str(entry['key'])))
    if ranked:
        return sorted(ranked)[0][1], None, None
    username = auth.get('username')
    password = auth.get('password')
    return None, str(username) if username else None, str(password) if password else None


class SlskdApi:
    PREFIX = '/api/v0'

    def __init__(self, config: dict, session: requests.Session | None = None):
        self.config = config
        self.base = str(config.get('slskd_url', 'http://localhost:5030')).rstrip('/')
        self.session = session or requests.Session()
        self.session.headers.update({'Accept': 'application/json', 'User-Agent': 'SoulseekPlaylists/2'})

    # -- plumbing ---------------------------------------------------------
    def _request(self, method: str, endpoint: str, timeout=(4, 15), **kwargs) -> requests.Response:
        try:
            response = self.session.request(method, self.base + self.PREFIX + endpoint, timeout=timeout, **kwargs)
        except requests.RequestException:
            raise ConnectionLost('slskd yanıt vermedi') from None
        if response.status_code == 401:
            raise PermissionError('slskd oturumu yenilenmeli')
        if response.status_code >= 400:
            raise ApiError(response.status_code)
        return response

    def _json(self, method: str, endpoint: str, **kwargs):
        response = self._request(method, endpoint, **kwargs)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    # -- session ----------------------------------------------------------
    def authenticate(self) -> str:
        self.session.headers.pop('X-API-Key', None)
        self.session.headers.pop('Authorization', None)
        if self._json('GET', '/session/enabled') is False:
            return 'disabled'
        api_key = os.getenv(str(self.config.get('api_key_env', 'SLSKD_API_KEY')))
        username = os.getenv(str(self.config.get('web_username_env', 'SLSKD_WEB_USERNAME')))
        password = os.getenv(str(self.config.get('web_password_env', 'SLSKD_WEB_PASSWORD')))
        if not api_key and not (username and password):
            api_key, username, password = _yaml_credentials(str(self.config.get('slskd_config_path', '')))
        if not api_key and not (username and password):
            username, password = 'slskd', 'slskd'          # slskd's documented defaults
        if api_key:
            self.session.headers['X-API-Key'] = api_key
            mode = 'api-key'
        else:
            payload = self._json('POST', '/session', json={'username': username, 'password': password}) or {}
            token = get_ci(payload, 'token')
            if not token:
                raise PermissionError('slskd oturum anahtarı alınamadı')
            self.session.headers['Authorization'] = f"{get_ci(payload, 'tokenType', 'Bearer')} {token}"
            mode = 'jwt'
        self._request('GET', '/session')
        return mode

    def server(self) -> dict:
        return self._json('GET', '/server') or {}

    def application(self) -> dict:
        return self._json('GET', '/application') or {}

    @staticmethod
    def server_connected(server: dict) -> bool:
        for name in ('isLoggedIn', 'isConnected', 'connected'):
            value = get_ci(server, name)
            if isinstance(value, bool):
                return value
        state = get_ci(server, 'state')
        if isinstance(state, str):
            state = state.casefold()
            return 'connected' in state and 'disconnected' not in state
        return isinstance(state, int) and state != 0 and bool(get_ci(server, 'username'))

    # -- searches ---------------------------------------------------------
    def create_search(self, ident: str, text: str) -> None:
        self._request('POST', '/searches', json={
            'id': ident,
            'searchText': text,
            # Soulseek.NET treats this as milliseconds of silence before the search completes.
            'searchTimeout': int(float(self.config.get('search_timeout_seconds', 15)) * 1000),
            'responseLimit': int(self.config.get('response_limit', 500)),
            'fileLimit': int(self.config.get('file_limit', 20000)),
            'filterResponses': True,
        })

    def search_state(self, ident: str) -> dict:
        return self._json('GET', f'/searches/{quote(ident, safe="")}') or {}

    def search_responses(self, ident: str) -> list:
        data = self._json('GET', f'/searches/{quote(ident, safe="")}/responses', timeout=(4, 30))
        return data if isinstance(data, list) else []

    def delete_search(self, ident: str) -> None:
        try:
            self._request('DELETE', f'/searches/{quote(ident, safe="")}')
        except ApiError:
            pass

    # -- downloads --------------------------------------------------------
    def enqueue(self, batch_id: str, peer: str, filename: str, size: int, destination: str) -> dict:
        """POST a one-file batch. 404 means the peer is offline; 429 means slskd is busy."""
        return self._json('POST', '/transfers/downloads/batches', timeout=(4, 45), json={
            'id': batch_id, 'username': peer,
            'files': [{'filename': filename, 'size': int(size or 0)}],
            'options': {'destination': destination, 'externalId': 'SoulseekPlaylists:' + batch_id},
        }) or {}

    def batch(self, batch_id: str) -> dict:
        return self._json('GET', '/transfers/downloads/batches/' + quote(batch_id, safe='')) or {}

    def downloads(self) -> dict:
        """All transfers keyed by id (one request instead of one per attempt)."""
        users = self._json('GET', '/transfers/downloads') or []
        return {str(get_ci(f, 'id')): f for u in users for d in get_ci(u, 'directories', []) or []
                for f in get_ci(d, 'files', []) or []}

    def get_download(self, peer: str, transfer_id: str) -> dict:
        return self._json('GET', f'/transfers/downloads/{quote(peer, safe="")}/{quote(transfer_id, safe="")}') or {}

    def cancel(self, peer: str, transfer_id: str) -> None:
        self._request('DELETE', f'/transfers/downloads/{quote(peer, safe="")}/{quote(transfer_id, safe="")}?remove=false')

    def remove_download(self, peer: str, transfer_id: str) -> None:
        """Drop a finished transfer from slskd's list (the file itself is not touched)."""
        self._request('DELETE', f'/transfers/downloads/{quote(peer, safe="")}/{quote(transfer_id, safe="")}?remove=true')
