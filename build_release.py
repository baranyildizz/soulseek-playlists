"""Build from an explicit clean source allowlist; never copy live user data."""
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE_FILES = (
    'playlist_app.py', 'playlist_core.py', 'downloader.py', 'slskd_runtime.py',
    'app_setup.py', 'package_check.py', 'package_lifecycle.py', 'requirements.txt', 'requirements-gui.txt',
    'requirements-build.txt', 'build_release.py', 'test_playlist.py',
    'test_slskd_runtime.py', 'test_distribution.py', 'PAYLASIM.md',
)


def clean_environment():
    """Do not resolve Qt dependencies from unrelated tools on the host PATH."""
    env = os.environ.copy()
    windows = Path(env.get('SystemRoot', 'C:/Windows'))
    env['PATH'] = os.pathsep.join(map(str, (windows/'System32', windows,
        Path(sys.executable).parent, Path(sys.base_prefix))))
    for key in ('PYTHONPATH', 'PYTHONHOME', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH',
                'QML2_IMPORT_PATH', 'QML_IMPORT_PATH'):
        env.pop(key, None)
    return env


def main():
    release = ROOT / 'releases' / datetime.now().strftime('%Y%m%d-%H%M%S')
    source = release / 'source'
    source.mkdir(parents=True)
    for name in SOURCE_FILES:
        shutil.copy2(ROOT / name, source / name)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
        '--onedir', '--windowed', '--name', 'Soulseek Playlists',
        '--collect-submodules', 'mutagen', '--collect-submodules', 'rapidfuzz',
        '--distpath', str(release / 'dist'), '--workpath', str(release / 'build'),
        '--specpath', str(release), str(source / 'playlist_app.py')],
        cwd=source, env=clean_environment(), check=True)
    finalize(release)


def finalize(release):
    release = Path(release).resolve()
    source = release / 'source'
    app = release / 'dist' / 'Soulseek Playlists'
    # Qt uses the Windows ICU ABI (unversioned exports). Reject accidental
    # third-party ICU such as Poppler's ucnv_open_78 before distributing anything.
    import pefile
    for binary in (app/'_internal').rglob('icuuc.dll'):
        pe = pefile.PE(str(binary))
        exports = {symbol.name for symbol in pe.DIRECTORY_ENTRY_EXPORT.symbols}
        if b'ucnv_open' not in exports:
            raise RuntimeError('Incompatible ICU library in package: rebuild with clean PATH')
    # Python 3.10 bundles older MSVC DLLs. The bootloader loads those before Qt,
    # so use the compatible runtimes shipped by this exact PySide6 wheel.
    import PySide6
    qt_root = Path(PySide6.__file__).parent
    for name in ('vcruntime140.dll', 'vcruntime140_1.dll'):
        shutil.copy2(qt_root / name, app / '_internal' / name)
    shutil.copy2(source / 'PAYLASIM.md', app / 'BASLANGIC.md')
    licenses = app / 'LICENSES'
    licenses.mkdir(exist_ok=True)
    versions = {}
    for name in ('requests','urllib3','certifi','charset-normalizer','idna','RapidFuzz',
                 'PyYAML','mutagen','PySide6-Essentials','shiboken6','pyinstaller'):
        dist = importlib.metadata.distribution(name)
        versions[name] = dist.version
        for entry in dist.files or ():
            if any(word in str(entry).lower() for word in ('license', 'copying', 'notice')):
                original = Path(dist.locate_file(entry))
                if original.is_file():
                    target = licenses / name / str(entry).replace('..', '_')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(original, target)
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.is_file():
        shutil.copy2(python_license, licenses / 'Python-LICENSE.txt')
    (app / 'versions.json').write_text(json.dumps(versions, indent=2), encoding='utf-8')
    result = release / 'package-check.json'
    subprocess.run([str(app / 'Soulseek Playlists.exe'), '--package-check', str(result)],
                   cwd=release, env=clean_environment(), check=True, timeout=90)
    if not json.loads(result.read_text(encoding='utf-8'))['passed']:
        raise RuntimeError('Packaged self-check failed')
    with tempfile.TemporaryDirectory(prefix='playlist-lifecycle-') as folder:
        for phase in ('first', 'restart'):
            output = release / ('lifecycle-' + phase + '.json')
            subprocess.run([str(app/'Soulseek Playlists.exe'), '--lifecycle-check', phase, folder, str(output)],
                cwd=release, env=clean_environment(), check=True, timeout=40)
            if not json.loads(output.read_text(encoding='utf-8'))['passed']:
                raise RuntimeError('Packaged lifecycle check failed: ' + phase)
    binary_zip = Path(shutil.make_archive(str(release / 'Soulseek-Playlists-Windows'), 'zip', app.parent, app.name))
    source_zip = Path(shutil.make_archive(str(release / 'Soulseek-Playlists-Source'), 'zip', source))
    hashes = {}
    for path in (binary_zip, source_zip):
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        hashes[path.name] = digest.hexdigest()
    (release / 'SHA256.json').write_text(json.dumps(hashes, indent=2), encoding='utf-8')
    print('Release:', release)
    print('Packaged self-check: passed')


if __name__ == '__main__':
    main()
