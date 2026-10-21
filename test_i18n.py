"""Every translated string has an English version, and no Turkish UI text is left unwrapped."""
import ast
import re
import string
import unittest
from pathlib import Path

import i18n
from i18n import EN, note_text, set_language, tr

ROOT = Path(__file__).resolve().parent
MODULES = ['playlist_app.py', 'app_setup.py', 'engine.py', 'matching.py', 'store.py', 'ocr.py', 'audio.py',
           'slskd_runtime.py', 'slskd_api.py']
TURKISH = re.compile(r'[çğıöşüÇĞİÖŞÜ]')
# Turkish text that is data, not interface: matching aliases, internal markers, Windows OCR fixes.
INTERNAL = {'ı', 'parça', 'şarkı', 'sanatçı', 'süre', 'albüm', 'sarki', 'sanatci', 'sure', 'şüpheli', ' · şüpheli',
            'kesme yok', ' (daha iyisi bulunamadı)', 'Kütüphane', 'Şüpheli kalite%', 'Geçersiz geçici klasör',
            'Sistem dili / System language', 'Türkçe', 'Dosya doğrulaması%'}


def placeholders(text):
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def translated_literals(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'tr' and node.args:
            argument = node.args[0]
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                yield argument.value


class TranslationTests(unittest.TestCase):
    def tearDown(self):
        set_language('tr')

    def test_every_tr_call_has_an_english_text_with_the_same_placeholders(self):
        missing, mismatched = [], []
        for name in MODULES:
            tree = ast.parse((ROOT / name).read_text(encoding='utf-8'))
            for text in translated_literals(tree):
                if text not in EN:
                    missing.append(f'{name}: {text!r}')
                elif placeholders(text) != placeholders(EN[text]):
                    mismatched.append(f'{name}: {text!r}')
        self.assertEqual(missing, [])
        self.assertEqual(mismatched, [])

    def test_no_unwrapped_turkish_interface_text(self):
        leftovers = []
        for name in MODULES:
            tree = ast.parse((ROOT / name).read_text(encoding='utf-8'))
            skip = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body \
                        and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
                    skip.add(id(node.body[0].value))
                if isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'tr':
                    skip.update(id(a) for a in node.args)
                if isinstance(node, ast.Assign) and any(getattr(t, 'id', '') in ('EN', '_CSV_COLUMNS', 'LANGUAGES', 'FILTERS')
                                                        for t in node.targets):
                    skip.update(id(n) for n in ast.walk(node.value))
            for node in ast.walk(tree):
                if id(node) in skip or not isinstance(node, (ast.Constant, ast.JoinedStr)):
                    continue
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    text = node.value
                elif isinstance(node, ast.JoinedStr):
                    text = ''.join(v.value for v in node.values if isinstance(v, ast.Constant))
                    skip.update(id(v) for v in node.values)
                else:
                    continue
                if TURKISH.search(text) and text not in INTERNAL and not text.lstrip().startswith(
                        ('SELECT', 'UPDATE', 'INSERT', 'DELETE')):
                    leftovers.append(f'{name}:{node.lineno}: {text!r}')
        self.assertEqual(leftovers, [])

    def test_filter_labels_are_translatable(self):
        from playlist_app import FILTERS
        self.assertTrue(all(label in EN for _key, label, _statuses in FILTERS))

    def test_switching_language(self):
        set_language('en')
        self.assertEqual(tr('Tümü'), 'All')
        self.assertEqual(tr('{count} parça hazır.').format(count=3), '3 tracks ready.')
        self.assertEqual(note_text('16.5 kHz · şüpheli'), '16.5 kHz · suspect')
        self.assertEqual(tr('metin yok'), 'metin yok')        # unknown text falls back unchanged
        set_language('tr')
        self.assertEqual(tr('Tümü'), 'Tümü')
        self.assertIn(set_language('auto'), ('tr', 'en'))

    def test_english_messages_stay_machine_readable(self):
        # Engine logic counts suspicious rejections in both languages.
        self.assertTrue(EN["Şüpheli kalite: ses {khz} kHz'de kesiliyor (düşük bitrate MP3'ten dönüştürülmüş olabilir)"]
                        .startswith('Suspicious quality'))
        # History search relies on these separators in both languages.
        self.assertIn(' — ', EN['Aranıyor · {artist} — {title} · "{query}"'])
        self.assertIn(' · {title} · ', EN['Peer isteği · {title} · {peer} · {ext} · %{score}'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
