"""Developer integration check. Starts isolated containers, never connects to a user database.

Build the API image first: docker build -t match-agent-api:deployment-check mongodb_api
Then: python mongodb_api/tests/run_docker_check.py
"""
from pathlib import Path
import subprocess
import uuid


def docker(*args):
    return subprocess.check_output(['docker',*args],text=True).strip()


def main():
    suffix=uuid.uuid4().hex[:10]
    mongo='match-agent-check-mongo-'+suffix
    api='match-agent-check-api-'+suffix
    started=[]
    try:
        docker('run','--rm','-d','--name',mongo,'--network','none','mongo:7.0')
        started.append(mongo)
        docker('run','--rm','-d','--name',api,'--network','container:'+mongo,
               '-e','MATCH_AGENT_DISPOSABLE_TEST=1',
               '-e','MONGODB_URI=mongodb://127.0.0.1:27017',
               '-e','MONGODB_DB=match_agent_disposable_test',
               '-e','MONGODB_API_KEY=isolated-fixture-key',
               '--entrypoint','sleep','match-agent-api:deployment-check','infinity')
        started.append(api)
        docker('cp',str(Path(__file__).parents[1])+'/.',api+':/checks')
        docker('exec','-u','0',api,'chown','-R','10001:10001','/checks')
        print(docker('exec',api,'python','/checks/tests/integration_mongo.py'))
    finally:
        for name in reversed(started):
            subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,check=False)


if __name__ == '__main__':main()
