"""Package only runtime sync files and the shared product contract; never include secrets/state."""
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
FILES=['.dockerignore','mongodb_api/product_contract.py']+['product_sync/'+name for name in (
    'sync.py','source.py','cleaning.py','requirements.txt','Dockerfile','compose.yaml',
    '.env.example','.gitignore','README.md','deploy/match-agent-product-sync.service',
    'deploy/match-agent-product-sync.timer')]


if __name__=='__main__':
    with zipfile.ZipFile(ROOT/'product_sync_bundle.zip','w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            archive.write(ROOT/name,'product_sync_bundle/'+name)
