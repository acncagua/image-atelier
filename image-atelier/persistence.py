"""Versioned local data migration and durable file replacement. No provider calls."""
import json
import os
import shutil
import sqlite3
import uuid
import time
from contextlib import closing
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


def safe_id(value):
    if not isinstance(value,str) or len(value) not in (32,36):
        raise ValueError('IDが不正です。')
    try:
        parsed=uuid.UUID(value)
    except ValueError:
        raise ValueError('IDが不正です。') from None
    if value not in (parsed.hex,str(parsed)):
        raise ValueError('IDが不正です。')
    return value


def atomic_write(path,content):
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('xb') as output:
            output.write(content);output.flush();os.fsync(output.fileno())
        for attempt in range(20):
            try:
                os.replace(temporary,path)
                break
            except PermissionError:
                if os.name!='nt' or attempt==19:raise
                # Short-lived readers (including Windows sync/indexing) may deny
                # replacement. Retry this same durable file, never the inference.
                time.sleep(.05)
    finally:
        if temporary.exists():temporary.unlink()


def publish_new(path,content):
    """Publish a complete file without replacing any existing destination."""
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('xb') as output:
            output.write(content);output.flush();os.fsync(output.fileno())
        if os.name=='nt':
            # Windows rename is atomic and fails if the destination already exists.
            os.rename(temporary,path)
        else:
            os.link(temporary,path)
    finally:
        if temporary.exists():temporary.unlink()


def migrate(store):
    with store.lock:
        version=store.db.execute('PRAGMA user_version').fetchone()[0]
        if version>=1:return
        if store.db.execute('SELECT COUNT(*) FROM assets').fetchone()[0] or store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]:
            backup=store.path/'backups'/('before-schema-1-'+datetime.now().strftime('%Y%m%d_%H%M%S')+'-'+uuid.uuid4().hex[:8])
            backup.mkdir(parents=True)
            with closing(sqlite3.connect(backup/'history.sqlite3')) as destination:
                store.db.backup(destination)
            # Existing images and responses are immutable. Never recurse into backups.
            shutil.copytree(store.path/'assets',backup/'assets')
            for pattern in ('http_response_*.json','response_*.json','response_meta_*.json'):
                for file in store.path.glob(pattern):shutil.copy2(file,backup/file.name)
            atomic_write(backup/'manifest.json',json.dumps({'schema':version,'created':now(),'database':'history.sqlite3','files':'assets and saved responses; API key configuration is unchanged and not copied'},ensure_ascii=False).encode())
        with store.db:
            store.db.execute('CREATE TABLE IF NOT EXISTS local_edits (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            for (body,) in store.db.execute('SELECT meta FROM assets').fetchall():
                meta=json.loads(body)
                if meta.get('kind')=='adjusted':
                    edit={'id':meta['id'],'source_id':meta.get('parent'),'result':meta,'method':None,'requested':None,'created':None,'legacy':True,'details':{},'context':None}
                    store.db.execute('INSERT OR IGNORE INTO local_edits VALUES (?,?)',(edit['id'],json.dumps(edit,ensure_ascii=False)))
            store.db.execute('PRAGMA user_version=1')
