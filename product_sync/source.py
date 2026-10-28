"""Fetch a complete source snapshot into a local SQLite staging database."""
import gzip
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request


class SyncError(RuntimeError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def source_stamp(value):
    if isinstance(value,datetime):parsed=value
    else:
        try:parsed=datetime.fromisoformat(str(value).strip().replace('Z','+00:00'))
        except ValueError:return None
    if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=timezone(timedelta(hours=8)))
    return parsed.astimezone(timezone.utc)


def newer_version(old, incoming):
    before=source_stamp(old.get('LAST_UPDATE_DATE'))
    after=source_stamp(incoming.get('LAST_UPDATE_DATE'))
    if before is None or after is None or before==after:
        raise SyncError('CONFLICTING_DUPLICATE_VERSIONS_WITHOUT_ORDER')
    return after>before


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request_page(url, api_key, date_from, kind, page, page_size):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query:
        raise SyncError('SOURCE_URL_MUST_NOT_CONTAIN_CREDENTIALS_OR_QUERY')
    params = {'dateFrom':date_from, 'type':kind, 'pageNum':page, 'pageSize':page_size}
    request = urllib.request.Request(url+'?'+urllib.parse.urlencode(params),
                                    headers={'Accept':'application/json', 'apikey':api_key})
    for attempt in range(3):
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
                body = response.read(32*1024*1024+1)
            if len(body) > 32*1024*1024:
                raise SyncError('SOURCE_PAGE_TOO_LARGE')
            return json.loads(body.decode('utf-8-sig'))
        except urllib.error.HTTPError as exc:
            if exc.code not in (429,500,502,503,504) or attempt == 2:
                raise SyncError('SOURCE_HTTP_'+str(exc.code)) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == 2:
                raise SyncError('SOURCE_NETWORK_UNAVAILABLE') from None
        except (ValueError, UnicodeError):
            raise SyncError('SOURCE_INVALID_JSON') from None
        time.sleep(2**attempt)


def stage_snapshot(folder, fetch, page_size=1000, max_pages=20000):
    """fetch(kind,page) is injectable. No MongoDB writes occur in this phase."""
    connection = sqlite3.connect(folder/'stage.sqlite')
    connection.executescript('''
        CREATE TABLE type1(code TEXT PRIMARY KEY, body TEXT NOT NULL, hash TEXT NOT NULL);
        CREATE TABLE type2(code TEXT NOT NULL, body TEXT NOT NULL, hash TEXT PRIMARY KEY, identity TEXT UNIQUE);
        CREATE INDEX type2_code ON type2(code);
    ''')
    stats = {}
    try:
        for kind in (1,2):
            count=0; pages=0; previous_hashes=set();resolved=0
            for page in range(1,max_pages+1):
                payload=fetch(kind,page)
                raw=canonical(payload).encode()
                raw_folder=folder/'raw'/('type'+str(kind))
                raw_folder.mkdir(parents=True,exist_ok=True)
                with gzip.open(raw_folder/f'page-{page:06d}.json.gz','wb') as output:
                    output.write(raw)
                if not isinstance(payload,dict) or payload.get('status') is not True or not isinstance(payload.get('data'),list):
                    raise SyncError('SOURCE_BUSINESS_OR_RESPONSE_ERROR')
                rows=payload['data']
                if not rows:
                    break
                if len(rows)>page_size:
                    raise SyncError('SOURCE_IGNORED_PAGE_SIZE')
                page_rows=[]
                for row in rows:
                    if not isinstance(row,dict):raise SyncError('SOURCE_ROW_NOT_OBJECT')
                    code=row.get('ITEM_NUM')
                    if not isinstance(code,str) or not code.strip() or any(c.isspace() for c in code.strip()):
                        raise SyncError('SOURCE_MATERIAL_CODE_NOT_NONEMPTY_STRING')
                    code=code.strip()
                    # Row numbers change with pagination and do not identify product changes.
                    stable={k:v for k,v in row.items() if k!='RN'}
                    fingerprint=digest(stable)
                    page_rows.append(fingerprint)
                    if kind==1:
                        old=connection.execute('SELECT body,hash FROM type1 WHERE code=?',(code,)).fetchone()
                        if old and old[1]!=fingerprint:
                            if newer_version(json.loads(old[0]),stable):
                                connection.execute('UPDATE type1 SET body=?,hash=? WHERE code=?',(canonical(stable),fingerprint,code))
                            resolved+=1
                        else:
                            connection.execute('INSERT OR IGNORE INTO type1 VALUES(?,?,?)',(code,canonical(stable),fingerprint))
                    else:
                        identity=digest([code,str(stable['ATTR_ID']),str(stable['SA_ID'])]) if stable.get('ATTR_ID') and stable.get('SA_ID') else None
                        old=connection.execute('SELECT body,hash FROM type2 WHERE identity=?',(identity,)).fetchone() if identity else None
                        if old and old[1]!=fingerprint:
                            if newer_version(json.loads(old[0]),stable):
                                connection.execute('UPDATE type2 SET body=?,hash=? WHERE identity=?',(canonical(stable),fingerprint,identity))
                            resolved+=1
                        else:
                            connection.execute('INSERT OR IGNORE INTO type2 VALUES(?,?,?,?)',(code,canonical(stable),fingerprint,identity))
                page_hash=digest(sorted(page_rows))
                if page_hash in previous_hashes:raise SyncError('REPEATED_PAGE_REFETCH_SNAPSHOT')
                previous_hashes.add(page_hash)
                connection.commit()
                pages+=1;count+=len(rows)
                print(f'fetch type={kind} page={page} rows={len(rows)} total={count}',flush=True)
                # Continue to an empty page even after a short page, rather than assuming completeness.
            else:
                raise SyncError('SOURCE_MAX_PAGES_REACHED')
            stats['type'+str(kind)]={'pages':pages,'rows':count,'duplicate_versions_resolved':resolved}
        stats['unique_products']=connection.execute('SELECT count(*) FROM type1').fetchone()[0]
        stats['type2_only_codes']=connection.execute('SELECT count(DISTINCT code) FROM type2 WHERE code NOT IN (SELECT code FROM type1)').fetchone()[0]
        if not stats['unique_products']:raise SyncError('EMPTY_PRODUCT_SNAPSHOT')
        return connection,stats
    except Exception:
        connection.close()
        raise
