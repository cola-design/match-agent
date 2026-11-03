from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import threading
import uuid

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env', override=False, encoding='utf-8-sig')


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


class Problem(Exception):
    def __init__(self, code, message, status=409):
        self.code, self.message, self.status = code, message, status
        super().__init__(code)


def number(value, *, positive=False):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise Problem('INVALID_NUMBER', '数值必须是有效数字。') from None
    if not result.is_finite() or (positive and result <= 0):
        raise Problem('INVALID_NUMBER', '数值不在允许范围内。')
    return result


class Settings:
    def __init__(self, **overrides):
        self.data_dir = Path(os.getenv('PROJECT_DATA_DIR', str(ROOT / 'project_data'))).resolve()
        self.api_key = os.getenv('PROJECT_API_KEY', '')
        self.signing_key = os.getenv('PROJECT_SIGNING_KEY', '')
        self.public_base = os.getenv('PROJECT_PUBLIC_BASE_URL', '').rstrip('/')
        self.vision_base = os.getenv('VISION_API_BASE_URL', '').rstrip('/')
        self.vision_key = os.getenv('VISION_API_KEY', '')
        self.vision_model = os.getenv('VISION_MODEL', '')
        self.vision_timeout = int(os.getenv('VISION_TIMEOUT_SECONDS', '120'))
        self.vision_max_tokens = int(os.getenv('VISION_MAX_TOKENS', '8192'))
        self.product_base = os.getenv('PRODUCT_QUERY_API_BASE_URL', '').rstrip('/')
        self.product_key = os.getenv('PRODUCT_QUERY_API_KEY', '')
        self.cad_url = os.getenv('CAD_CONVERT_URL', '')
        self.cad_key = os.getenv('CAD_CONVERT_API_KEY', '')
        self.max_pages = int(os.getenv('MAX_PDF_PAGES', '20'))
        self.max_mb = int(os.getenv('MAX_UPLOAD_MB', '32'))
        self.wait_seconds = int(os.getenv('COMMAND_WAIT_SECONDS', '45'))
        self.markup = os.getenv('QUOTE_MARKUP_RATE', '0.15')
        self.discount = os.getenv('QUOTE_DISCOUNT_RATE', '0')
        for key, value in overrides.items():
            setattr(self, key, value)
        self.data_dir = Path(self.data_dir).resolve()

    def validate(self):
        if len(self.api_key) < 24 or len(self.signing_key) < 32:
            raise Problem('SERVICE_KEY_REQUIRED', '先配置独立的PROJECT_API_KEY和PROJECT_SIGNING_KEY。', 503)
        if not self.public_base.startswith(('http://', 'https://')):
            raise Problem('PUBLIC_BASE_REQUIRED', '配置用户浏览器可访问的PROJECT_PUBLIC_BASE_URL。', 503)


class Store:
    def __init__(self, settings):
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = settings.data_dir / 'projects.sqlite'
        self.lock = threading.RLock()
        with self.connection() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
                    state TEXT NOT NULL, payload TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, project_id TEXT, revision INTEGER,
                    action TEXT, actor TEXT, created TEXT, summary TEXT);
                CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY, project_id TEXT, revision INTEGER,
                    kind TEXT, path TEXT, filename TEXT, created TEXT);
            ''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def create(self, name, actor):
        project = {'id': uuid.uuid4().hex, 'name': name[:160], 'revision': 1,
                   'state': 'UPLOADED', 'facts': {}, 'issues': [], 'bom': [], 'artifacts': [],
                   'documents': [], 'approvals': [], 'delivery': {}, 'actor': actor}
        with self.connection() as db:
            db.execute('INSERT INTO projects VALUES(?,?,?,?,?,?)', (project['id'], 1, project['state'], canonical(project), now(), now()))
        return project

    def get(self, project_id):
        if not isinstance(project_id, str) or not __import__('re').fullmatch('[0-9a-f]{32}', project_id):
            raise Problem('PROJECT_NOT_FOUND', '请选择已有项目或新建项目。', 404)
        with self.connection() as db:
            row = db.execute('SELECT payload FROM projects WHERE id=?', (project_id,)).fetchone()
        if not row:
            raise Problem('PROJECT_NOT_FOUND', '项目不存在。', 404)
        return json.loads(row['payload'])

    def save(self, project, action, actor, *, expected_revision=None):
        with self.lock, self.connection() as db:
            old = db.execute('SELECT revision FROM projects WHERE id=?', (project['id'],)).fetchone()
            if old is None or (expected_revision is not None and old['revision'] != expected_revision):
                raise Problem('PROJECT_VERSION_CHANGED', '项目版本已变化，请下载最新确认表。')
            db.execute('UPDATE projects SET revision=?,state=?,payload=?,updated=? WHERE id=?',
                       (project['revision'], project['state'], canonical(project), now(), project['id']))
            db.execute('INSERT INTO audit(project_id,revision,action,actor,created,summary) VALUES(?,?,?,?,?,?)',
                       (project['id'], project['revision'], action, actor, now(), canonical({'state': project['state']})))

    def folder(self, project_id):
        path = self.settings.data_dir / project_id
        path.mkdir(exist_ok=True)
        return path

    def artifact(self, project, kind, path):
        aid = uuid.uuid4().hex
        with self.connection() as db:
            db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)',
                       (aid, project['id'], project['revision'], kind, str(path), Path(path).name, now()))
        item = {'id': aid, 'kind': kind, 'filename': Path(path).name, 'revision': project['revision']}
        project['artifacts'].append(item)
        return item

    def signature(self, payload):
        return hmac.new(self.settings.signing_key.encode(), canonical(payload).encode(), hashlib.sha256).hexdigest()

    def link(self, artifact):
        token = self.signature({'artifact': artifact['id']})
        return self.settings.public_base + '/v1/files/' + artifact['id'] + '?token=' + token

    def resolve_file(self, aid, token):
        if not hmac.compare_digest(self.signature({'artifact': aid}), token):
            raise Problem('FILE_TOKEN_INVALID', '文件链接无效。', 403)
        with self.connection() as db:
            row = db.execute('SELECT * FROM artifacts WHERE id=?', (aid,)).fetchone()
        if not row:
            raise Problem('FILE_NOT_FOUND', '文件不存在。', 404)
        path = Path(row['path']).resolve()
        if self.settings.data_dir not in path.parents or not path.is_file():
            raise Problem('FILE_NOT_FOUND', '文件不存在。', 404)
        return path
