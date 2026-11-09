"""Pending browser writes are temporary; verified identities live in a compact local DB."""
import hashlib,json,sqlite3
from contextlib import contextmanager
from pathlib import Path
from program_storage import program_root,temporary_root,save_json,prune_empty


class Journal:
    def __init__(self,config):
        self.config=config;self.folder=temporary_root(config,'수발신-브라우저이력')
        self.database=program_root(config)/'수발신-등록확인.sqlite'

    @contextmanager
    def _db(self):
        self.database.parent.mkdir(parents=True,exist_ok=True)
        db=sqlite3.connect(self.database)
        try:
            db.execute('CREATE TABLE IF NOT EXISTS registrations (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            yield db;db.commit()
        except Exception:db.rollback();raise
        finally:db.close()

    @staticmethod
    def valid(entry):
        return isinstance(entry,dict) and all(k in entry for k in ('sid','tab','row','after','markers','complete')) and isinstance(entry['row'],int) and entry['row']>0 and isinstance(entry['after'],list) and bool(entry['markers'])

    def archive(self,path,entry):
        if not self.valid(entry) or not entry['complete']:raise ValueError('완료된 등록 확인 기록이 아닙니다.')
        compact={k:entry[k] for k in ('sid','tab','row','after','markers','complete')}
        key=hashlib.sha256((str(entry['sid'])+str(entry['tab'])+str(entry['markers'][0])).encode()).hexdigest()
        with self._db() as db:
            db.execute('INSERT INTO registrations VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',(key,json.dumps(compact,ensure_ascii=False)))
        if path.exists():
            if json.loads(path.read_text(encoding='utf-8'))!=entry:raise ValueError('정리 전 등록 이력이 변경되었습니다.')
            path.unlink();prune_empty(path.parent,program_root(self.config))

    def entries(self):
        records=[]
        if self.database.is_file():
            with self._db() as db:records.extend(json.loads(row[0]) for row in db.execute('SELECT payload FROM registrations'))
        if self.folder.is_dir():
            for path in self.folder.glob('*.json'):
                entry=json.loads(path.read_text(encoding='utf-8'))
                if not self.valid(entry):raise ValueError('브라우저 등록 이력 형식이 다릅니다: '+str(path))
                records.append(entry)
                if entry['complete']:self.archive(path,entry)
        return records

    def migrate(self):
        if not self.config.get('root'):return
        folder=Path(self.config['root'])/'.수발신-브라우저이력'
        if not folder.is_dir():return
        if folder.is_symlink() or (hasattr(folder,'is_junction') and folder.is_junction()):raise ValueError('연결된 이전 이력 폴더는 자동 정리하지 않습니다.')
        for path in folder.glob('*.json'):
            if path.is_symlink() or path.stat().st_size>5*1024*1024:continue
            raw=path.read_bytes();entry=json.loads(raw)
            if not self.valid(entry):continue
            local=self.folder/('legacy-'+hashlib.sha256(raw).hexdigest()+'.json')
            if not local.exists():save_json(local,entry)
            if json.loads(local.read_text(encoding='utf-8'))!=entry:raise ValueError('이전 등록 이력 복사 검증에 실패했습니다.')
            if entry['complete']:self.archive(local,entry)
            if path.read_bytes()==raw:path.unlink()
        try:folder.rmdir()
        except OSError:pass
