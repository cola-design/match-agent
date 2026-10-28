"""Build allowlisted API bundles without local environments, credentials or repair plans."""
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
COMMON = ['app.py','product_contract.py','repair_products.py','requirements.txt',
          'README.md','产品字段修复步骤.md']
DEPLOY = COMMON + ['Dockerfile','compose.yaml','openapi.yaml','.env.example','.dockerignore','.gitignore']


def package(name, files):
    with zipfile.ZipFile(ROOT/name,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name in files:
            archive.write(ROOT/'mongodb_api'/name,'mongodb_api/'+name)


if __name__ == '__main__':
    package('mongodb_product_repair_bundle.zip',COMMON)
    package('mongodb_api_server_bundle.zip',DEPLOY)
    package('mongodb_api_bundle.zip',DEPLOY)
