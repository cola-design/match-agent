"""Requires an installed ODA File Converter. No DWG binary is bundled."""
import hmac
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, UploadFile, File
from fastapi.responses import Response

load_dotenv(Path(__file__).with_name('.env'),override=False)
app=FastAPI(title='CAD转换适配服务',version='1.0.0')


def authorized(x_api_key:str=Header(default='')):
    key=os.getenv('CAD_API_KEY','')
    if len(key)<24 or not hmac.compare_digest(key,x_api_key):raise HTTPException(401,'CAD API key invalid')


@app.get('/live')
def live():return {'status':'up'}


@app.get('/ready',dependencies=[Depends(authorized)])
def ready():
    from ezdxf.addons import odafc
    configure()
    if not odafc.is_installed():raise HTTPException(503,'Install and configure ODA File Converter first')
    return {'status':'converter_installed','conversion_verified':False}


def configure():
    import ezdxf
    path=os.getenv('ODA_FILE_CONVERTER_PATH','')
    if path:ezdxf.options.set('odafc-addon','win_exec_path' if os.name=='nt' else 'unix_exec_path',path)


def convert_local(source,target):
    import ezdxf
    from ezdxf.addons import odafc
    configure()
    document=ezdxf.readfile(source)
    if document.audit().has_errors:raise ValueError('DXF audit failed')
    if not odafc.is_installed():raise ValueError('ODA File Converter not installed')
    odafc.convert(source,target,version='R2018',audit=True,replace=False)


@app.post('/convert',dependencies=[Depends(authorized)])
def convert(file:UploadFile=File(...)):
    if not (file.filename or '').lower().endswith('.dxf'):raise HTTPException(422,'Upload a DXF file')
    content=file.file.read(32*1024*1024+1)
    if len(content)>32*1024*1024:raise HTTPException(413,'DXF exceeds 32MB')
    with tempfile.TemporaryDirectory(prefix='cad_convert_') as temp:
        source=Path(temp)/'drawing.dxf';target=Path(temp)/'drawing.dwg';source.write_bytes(content)
        try:
            subprocess.run([sys.executable,'-m','cad_service.app','--convert-local',str(source),str(target)],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                timeout=75,check=True,cwd=Path(__file__).resolve().parents[1])
            result=target.read_bytes()
        except subprocess.TimeoutExpired:
            raise HTTPException(504,'CAD conversion timeout') from None
        except Exception:
            raise HTTPException(502,'CAD conversion failed; verify installed ODA executable and runtime dependencies') from None
    if len(result)<128 or not result.startswith(b'AC10'):raise HTTPException(502,'Invalid DWG output')
    return Response(result,media_type='application/acad',headers={'Content-Disposition':'attachment; filename="drawing.dwg"'})


if __name__=='__main__':
    if len(sys.argv)==4 and sys.argv[1]=='--convert-local':convert_local(sys.argv[2],sys.argv[3])
