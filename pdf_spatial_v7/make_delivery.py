from pathlib import Path
import hashlib
import json
import zipfile

ROOT=Path(__file__).resolve().parent


def main():
    files=['pdf_electrical_v7_spatial_crop.yml','README_CN.md']
    manifest={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in files}
    (ROOT/'SHA256.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    out=ROOT/'pdf_electrical_v7_spatial_crop.zip'
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        for n in files+['SHA256.json']:z.write(ROOT/n,n)
    with zipfile.ZipFile(out) as z:
        assert z.testzip() is None
        assert len(z.namelist())==3
        for n,sha in manifest.items():assert hashlib.sha256(z.read(n)).hexdigest()==sha
    print(out.name,out.stat().st_size)


if __name__=='__main__':main()
