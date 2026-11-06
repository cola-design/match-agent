from pathlib import Path
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parent


def main():
    app = ROOT / 'PDF电气识图_V6_1_校验兼容与结果保留.yml'
    files = [app, ROOT/'README_CN.md']
    sha = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    (ROOT/'SHA256.json').write_text(json.dumps(sha, ensure_ascii=False, indent=2))
    output = ROOT/'pdf_ollama_qwen38_v6_1.zip'
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in files + [ROOT/'SHA256.json']: z.write(p, p.name)
    with zipfile.ZipFile(output) as z:
        assert z.testzip() is None
        assert len(z.namelist()) == 3
        assert not any(n.endswith('.difypkg') or n.endswith('.pdf') for n in z.namelist())
    print(output.name, output.stat().st_size)


if __name__ == '__main__': main()
