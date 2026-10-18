"""Изолированный, ограниченный по времени процесс извлечения текста PDF."""

from io import BytesIO
import re
import sys
import unicodedata


def normalize_glyphs(text):
    # Some PDF text layers expose OpenType old-style numeral glyph names.
    digits = {name: str(i) for i, name in enumerate(('zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine'))}
    text = re.sub(r'/(' + '|'.join(digits) + r')\.osf', lambda m: digits[m[1]], text)
    return re.sub(r'/uni(FB0[0-6])', lambda m: unicodedata.normalize('NFKC', chr(int(m[1], 16))), text)


def main():
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(sys.stdin.buffer.read(8 * 1024 * 1024 + 1)))
    if reader.is_encrypted:
        raise ValueError('Encrypted PDF')
    remaining = 100000
    for page in reader.pages[:20]:
        text = normalize_glyphs(page.extract_text() or '')[:remaining]
        sys.stdout.write(text + '\n')
        remaining -= len(text)
        if remaining <= 0:
            break


if __name__ == '__main__':
    main()
