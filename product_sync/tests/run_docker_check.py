"""Developer-only isolated HTTP/MongoDB integration check; no host ports are published."""
from pathlib import Path
import subprocess
import uuid


def docker(*args):
    try:return subprocess.check_output(['docker',*args],text=True).strip()
    except subprocess.CalledProcessError as exc:
        print(exc.output,flush=True)
        raise


def main():
    suffix=uuid.uuid4().hex[:10];started=[]
    mongo='product-sync-check-mongo-'+suffix;worker='product-sync-check-worker-'+suffix
    try:
        docker('run','--rm','-d','--name',mongo,'--network','none','mongo:7.0');started.append(mongo)
        docker('run','--rm','-d','--name',worker,'--network','container:'+mongo,
               '-e','MATCH_AGENT_DISPOSABLE_TEST=1','-e','SYNC_MONGODB_URI=mongodb://127.0.0.1:27017',
               '-e','MONGODB_DB=match_agent_disposable_test','--entrypoint','sleep',
               'match-agent-api:deployment-check','infinity');started.append(worker)
        root=Path(__file__).resolve().parents[2]
        docker('exec','-u','0',worker,'mkdir','-p','/checks/mongodb_api')
        docker('cp',str(root/'product_sync'),worker+':/checks/product_sync')
        docker('cp',str(root/'mongodb_api/product_contract.py'),worker+':/checks/mongodb_api/product_contract.py')
        docker('exec','-u','0',worker,'chown','-R','10001:10001','/checks')
        print(docker('exec',worker,'python','/checks/product_sync/tests/integration_mongo.py'))
    finally:
        for name in reversed(started):subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,check=False)


if __name__=='__main__':main()
