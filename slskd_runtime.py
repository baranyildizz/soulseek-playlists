"""Start the local service without exposing credentials or blocking the GUI."""
import csv
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from urllib.parse import urlsplit


@contextmanager
def external_library_search():
    """Do not pass PyInstaller's DLL directory to the separately installed slskd."""
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        yield
        return
    import ctypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetDllDirectoryW.argtypes = [ctypes.c_ulong, ctypes.c_wchar_p]
    kernel.GetDllDirectoryW.restype = ctypes.c_ulong
    kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
    kernel.SetDllDirectoryW.restype = ctypes.c_int
    length = kernel.GetDllDirectoryW(0, None)
    previous = ctypes.create_unicode_buffer(length+1)
    kernel.GetDllDirectoryW(len(previous), previous)
    if not kernel.SetDllDirectoryW(None):
        raise OSError('External library search could not be reset')
    try:
        yield
    finally:
        kernel.SetDllDirectoryW(previous.value or None)


def external_environment():
    env = os.environ.copy()
    bundle = getattr(sys, '_MEIPASS', None)
    if bundle:
        prefix = str(Path(bundle).resolve()).casefold()
        env['PATH'] = os.pathsep.join(part for part in env.get('PATH','').split(os.pathsep)
            if part and not str(Path(part).resolve()).casefold().startswith(prefix))
    return env


class SlskdRuntime:
    def __init__(self, config):
        self.config=config
        self.process=None
        self.next_start=0
        self.message='slskd başlatılıyor…'

    def reachable(self):
        url=urlsplit(self.config['slskd_url'])
        try:
            with socket.create_connection((url.hostname,url.port or (443 if url.scheme=='https' else 80)),timeout=1):
                return True
        except OSError:
            return False

    def running(self, executable):
        if self.process is not None and self.process.poll() is None:
            return True
        # Do not start another copy if a manually launched service is still booting.
        with external_library_search():
            result=subprocess.run(['tasklist.exe','/FI','IMAGENAME eq '+executable.name,'/FO','CSV','/NH'],
                capture_output=True,timeout=5,creationflags=subprocess.CREATE_NO_WINDOW,check=True,
                env=external_environment())
        return any(row and row[0].casefold()==executable.name.casefold()
                   for row in csv.reader(result.stdout.decode(errors='replace').splitlines()))

    def prepare(self):
        if self.reachable(): return True, ''
        url=urlsplit(self.config['slskd_url'])
        if not self.config.get('auto_start_slskd',True) or url.hostname not in ('localhost','127.0.0.1','::1') or os.name!='nt':
            return False,'slskd erişilemiyor · 5 sn sonra yeniden denenecek'
        configured=self.config.get('slskd_executable')
        if not configured:
            found=sorted((Path.home()/'Downloads').glob('slskd*/slskd.exe'))
            configured=str(found[-1]) if found else ''
        executable=Path(os.path.expandvars(configured))
        if not executable.is_file():return False,'slskd bulunamadı · config.json içindeki slskd_executable yolunu kontrol edin'
        if time.monotonic()<self.next_start:return False,self.message
        try:
            if self.running(executable):
                return False,'slskd çalışıyor · web servisinin hazır olması bekleniyor'
            command=[str(executable)]
            listen_port=self.config.get('slskd_listen_port')
            if listen_port not in (None,''):
                try:
                    listen_port=int(listen_port)
                    if not 1 <= listen_port <= 65535:raise ValueError
                except (ValueError,TypeError):
                    return False,'Soulseek giriş portu geçersiz · Ayarlar bölümünü kontrol edin'
                command.extend(['--slsk-listen-port',str(listen_port)])
            config_path=self.config.get('slskd_config_path')
            if config_path:
                config_path=Path(os.path.expandvars(config_path))
                if not config_path.is_file():return False,'slskd ayar dosyası bulunamadı · slskd_config_path yolunu kontrol edin'
                command.extend(['--config',str(config_path)])
            # Only paths go on the command line. Soulseek reads its existing YAML.
            self.next_start=time.monotonic()+60
            with external_library_search():
                self.process=subprocess.Popen(command,cwd=str(executable.parent),
                    stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,env=external_environment())
            self.message='slskd başlatılıyor · kayıtlı hesapla bağlantı kuruluyor'
        except (OSError,subprocess.SubprocessError):
            self.next_start=time.monotonic()+60
            self.message='slskd başlatılamadı · 60 sn sonra yeniden denenecek'
        return False,self.message
