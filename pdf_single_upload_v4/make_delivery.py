"""Allowlisted delivery; never include PDFs, private outputs, .env or caches."""
from pathlib import Path
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parent
APP = next(ROOT.glob('*Dify导入.yml'))
ZIP = ROOT / 'electrical_pdf_single_upload_v4.zip'


def main():
    files = [APP, ROOT / 'electrical-pdf-0.1.0.difypkg', ROOT / 'README_CN.md']
    checksums = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    (ROOT / 'SHA256.json').write_text(json.dumps(checksums, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(ZIP, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in files + [ROOT / 'SHA256.json']: z.write(p, p.name)
    with zipfile.ZipFile(ZIP) as z:
        assert len(z.namelist()) == 4
        assert z.testzip() is None
    print(json.dumps({'delivery': ZIP.name, 'bytes': ZIP.stat().st_size, 'files': [p.name for p in files]}, ensure_ascii=False))


if __name__ == '__main__': main()
