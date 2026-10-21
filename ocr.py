"""Read track lists from screenshots with the OCR engine built into Windows 10/11.

The engine is reached through PowerShell's WinRT bridge, so nothing has to be
installed and it also works from the packaged EXE. Tracklist screenshots are
usually small text; enlarging them first matters most. Measured on a 20-line
dark-theme tracklist: lanczos x3 (ffmpeg) reads 17/20 lines exactly, Qt's smooth
x4 scaling 15/20, the raw image far fewer. The remaining slips are fixed by the
user in the editable preview before anything is searched.
"""
from __future__ import annotations

import base64
import subprocess
import tempfile
from pathlib import Path

from slskd_runtime import external_environment, external_library_search

_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$t) { $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait(-1) | Out-Null; $task.Result }
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync('__PATH__')) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($engine -eq $null) { Write-Error 'no-ocr-language' }
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$result.Lines | ForEach-Object { $_.Text }
'''
MAX_SIDE = 9000          # the engine rejects larger images


class OcrError(RuntimeError):
    pass


def recognize_file(path, timeout=60) -> list[str]:
    """Text lines from an image file using Windows OCR."""
    script = _SCRIPT.replace('__PATH__', str(Path(path).resolve()).replace("'", "''"))
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    try:
        with external_library_search():
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                                    capture_output=True, timeout=timeout, creationflags=_NO_WINDOW,
                                    env=external_environment())
    except (OSError, subprocess.SubprocessError):
        raise OcrError('Windows metin tanıma çalıştırılamadı.') from None
    if result.returncode != 0:
        if b'no-ocr-language' in result.stderr:
            raise OcrError('Windows’ta metin tanıma dili yüklü değil (Ayarlar › Zaman ve dil › Dil).')
        raise OcrError('Ekran görüntüsü okunamadı.')
    return [line.strip() for line in result.stdout.decode('utf-8', errors='replace').splitlines() if line.strip()]


def _enlarge(image, folder: Path, ffmpeg: str) -> Path:
    """Grey, enlarged copy of a QImage for OCR (lanczos via ffmpeg when available)."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    original = folder / 'original.png'
    if not image.save(str(original), 'PNG'):
        raise OcrError('Görüntü geçici dosyaya yazılamadı.')
    largest = max(1, image.width(), image.height())
    if ffmpeg:
        scale = max(1, min(3, MAX_SIDE // largest))
        prepared = folder / 'prepared.png'
        try:
            subprocess.run([ffmpeg, '-v', 'error', '-y', '-i', str(original), '-vf',
                            f'scale=iw*{scale}:ih*{scale}:flags=lanczos,format=gray', str(prepared)],
                           capture_output=True, timeout=60, creationflags=_NO_WINDOW, check=True)
            return prepared
        except (OSError, subprocess.SubprocessError):
            pass
    scale = max(1, min(4, MAX_SIDE // largest))
    gray = image.convertToFormat(QImage.Format.Format_Grayscale8)
    gray = gray.scaled(gray.width() * scale, gray.height() * scale, Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
    prepared = folder / 'prepared-qt.png'
    gray.save(str(prepared), 'PNG')
    return prepared


def recognize_image(image, ffmpeg: str = '') -> list[str]:
    """Text lines from a QImage (clipboard or image file)."""
    if image.isNull():
        raise OcrError('Görüntü okunamadı.')
    with tempfile.TemporaryDirectory(prefix='playlist-ocr-') as folder:
        return recognize_file(_enlarge(image, Path(folder), ffmpeg))
