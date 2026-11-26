"""Save another copy of the files produced by a receipt or dispatch."""
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import uuid

from core import digest
from attachment_files import storage_files
from file_rollback import checked, track_file
from folder_naming import safe_component
from fs_paths import io_path


MARKER = '.수발신-추가저장.json'


def _sources(job, scope):
    if scope == 'outgoing':
        hwp = job.get('outgoing', {}).get('hwp_path')
        paths = [hwp] if hwp and Path(hwp).suffix.lower() == '.hwp' else []
    elif scope == 'receipt':
        paths = job.get('destinations', [])
    else:
        raise ValueError('수신 또는 발신 업무를 선택하세요.')
    # A primary destination may have added the content hash after a name collision.
    # The same attachment in another primary destination can still have its original name.
    aliases = {}
    expected = {}
    if scope == 'receipt':
        for original in storage_files(job):
            old = Path(original['path'])
            checksum = original['hash']
            name = old.name.casefold()
            suffixed = f'{old.stem}_{checksum[:12]}{old.suffix}'.casefold()
            aliases[(suffixed, checksum)] = name
            for candidate in (name, suffixed):
                expected.setdefault(candidate, set()).add((name, checksum))
        journal=job.get('receipt_originals') or {}
        for directory,names in journal.get('names',{}).items():
            for source,name in names.items():
                record=journal['copies'].get(str((Path(directory)/name).resolve()))
                if record:aliases[(name.casefold(),record['sha256'])]=Path(source).name.casefold()
    files = {}
    missing = []
    for raw in paths:
        path = checked(raw)
        if not io_path(path).is_file():
            if not io_path(path).is_dir():
                missing.append(path)
            continue
        checksum = digest(path)
        name = path.name.casefold()
        key = (aliases.get((name, checksum), name), checksum)
        if key in files and name != key[0]:
            continue
        files[key] = {'source': str(path), 'sha256': checksum}
    recorded = {Path(a['path']): a['sha256'] for a in job.get('file_artifacts', []) if a['scope'] == scope}
    for path in missing:
        name = path.name.casefold()
        checksum = recorded.get(path)
        candidates = {(aliases.get((name, checksum), name), checksum)} if checksum else expected.get(name, set())
        if len(candidates) != 1 or not candidates.issubset(files):
            raise ValueError(f'복사할 저장 파일이 누락되었습니다. 원래 저장 위치를 확인하세요. {path}')
    names = set()
    for item in files.values():
        path = Path(item['source'])
        if path.name.casefold() in names:
            raise ValueError(f'이름이 같고 내용이 다른 저장 파일이 있습니다. {path.name}')
        names.add(path.name.casefold())
    if not files:
        raise ValueError('복사할 저장 파일을 찾을 수 없습니다. 원래 저장 위치를 확인하세요.')
    return list(files.values())


def _file_id(path):
    info = io_path(path).stat()
    return [info.st_dev, info.st_ino]


def _same_file(path, identity):
    return bool(identity and identity[1] and io_path(path).is_file() and _file_id(path) == identity)


def _verify_folder(path, token):
    marker = checked(path / MARKER)
    if not io_path(marker).is_file() or json.loads(io_path(marker).read_text(encoding='utf-8')).get('id') != token:
        raise ValueError(f'프로그램이 만든 추가 저장 폴더인지 확인할 수 없습니다. {path}')
    return marker


def _publish(stage, target):
    if os.name == 'nt':
        os.rename(io_path(stage), io_path(target))  # Windows rename refuses existing destinations.
    else:
        os.link(io_path(stage), io_path(target))
        io_path(stage).unlink()


def _copy_one(item, store, job):
    audited=bool(job.get('receipt_originals') and (job.get('additional_save_pending') or {}).get('scope')=='receipt')
    if audited:
        from receipt_originals import pending_artifacts,signature
        pending_artifacts(item)
    try:
        copy_staged(item, lambda:store.put(job))
    except Exception:
        stage=checked(item['stage'])
        if audited and _same_file(stage,item.get('identity')):
            item.update(partial_sha256=digest(stage),partial_identity=signature(stage))
            store.put(job)
        raise


def copy_staged(item, checkpoint):
    target, stage = checked(item['target']), checked(item['stage'])
    identity = item.get('identity')
    if io_path(target).exists():
        if not _same_file(target, identity) or digest(target) != item['sha256']:
            raise FileExistsError(f'같은 이름의 파일이 있습니다. {target}')
        if _same_file(stage, identity):
            io_path(stage).unlink()
        return
    if io_path(stage).exists():
        if not _same_file(stage, identity):
            raise ValueError(f'임시 사본의 소유권을 확인할 수 없습니다. {stage}')
        io_path(stage).unlink()
    with io_path(stage).open('xb') as output:
        item['identity'] = _file_id(stage)
        checkpoint()
        with io_path(checked(item['source'])).open('rb') as source:
            shutil.copyfileobj(source, output, 1024 * 1024)
        output.flush()
        os.fsync(output.fileno())
    if digest(stage) != item['sha256']:
        raise ValueError(f'복사 중 원본이 변경되었습니다. {item["source"]}')
    _publish(stage, target)


def save_additional(job, scope, base, folder_name, store):
    """Return the persisted scope/folder/files entry, resuming this same request on retry."""
    if not base:
        raise ValueError('기존 저장 위치를 선택하세요.')
    base = checked(base)
    if not io_path(base).is_dir():
        raise ValueError('추가 저장 위치에 접근할 수 없습니다.')
    name = (folder_name or '').strip()
    if name and (safe_component(name) != name or PureWindowsPath(name).is_reserved()):
        raise ValueError('새 폴더 이름에 경로 또는 사용할 수 없는 문자가 있습니다.')
    parent = checked(base / name) if name else base
    sources = _sources(job, scope)
    targets = [checked(parent / Path(item['source']).name) for item in sources]
    folder = str(parent) if name else ''
    files = [str(path) for path in targets]
    pending = job.get('additional_save_pending')
    if job.get('rollback_pending'):
        raise ValueError('진행 중인 되돌리기를 먼저 완료하세요.')
    if scope=='receipt' and (job.get('receipt_originals') or {}).get('state','ready')!='ready':
        raise ValueError('원본 보관을 시작한 접수입니다. 저장 결과를 확인하세요.')
    if pending:
        if (pending['scope'], pending['folder'], [i['target'] for i in pending['items']]) != (scope, folder, files):
            raise ValueError('진행 중인 추가 저장을 같은 위치에서 마치거나 이전 단계에서 취소하세요.')
    else:
        for record in job.get('additional_saves', []):
            if (record['scope'], record['folder'], record['files']) == (scope, folder, files):
                if name:
                    _verify_folder(parent, record['token'])
                hashes = {a['path']: a['sha256'] for a in job.get('file_artifacts', []) if a['scope'] == scope}
                if all(io_path(p).is_file() and digest(p) == hashes.get(str(p)) == s['sha256'] for p, s in zip(targets, sources)):
                    return record
                raise ValueError('기존 추가 사본 또는 원본이 변경되었습니다. 다른 저장 위치를 선택하세요.')
        if name and io_path(parent).exists():
            raise FileExistsError(f'같은 이름의 폴더 또는 파일이 있습니다. {parent}')
        for path in targets:
            if io_path(path).exists() or path.name.casefold() == MARKER.casefold():
                raise FileExistsError(f'같은 이름의 파일이 있습니다. {path}')
        token = uuid.uuid4().hex
        items = [dict(source, target=str(path), stage=str(parent / f'.copy-{token}-{i}.part'))
                 for i, (source, path) in enumerate(zip(sources, targets))]
        pending = {'scope': scope, 'folder': folder, 'token': token, 'items': items}
        job['additional_save_pending'] = pending
        store.put(job)
    if name:
        if io_path(parent).exists():
            _verify_folder(parent, pending['token'])
        else:
            io_path(parent).mkdir()
            with io_path(parent / MARKER).open('x', encoding='utf-8') as marker:
                json.dump({'id': pending['token']}, marker)
    for item in pending['items']:
        _copy_one(item, store, job)
        track_file(job, item['target'], item['sha256'], scope)
        store.put(job)
    entry = {'scope': scope, 'folder': folder, 'files': files, 'token': pending['token'],
             'identities': {item['target']: item['identity'] for item in pending['items']}}
    job.setdefault('additional_saves', []).append(entry)
    del job['additional_save_pending']
    store.put(job)
    return entry


def rollback_entries(job, scopes):
    """Return only additional directories, markers and unfinished copies we can prove."""
    roots, markers, partial = [], {}, {}
    records = list(job.get('additional_saves', []))
    pending = job.get('additional_save_pending')
    if pending and pending['scope'] in scopes:
        records.append(pending)
        tracked = {f['path'] for f in job.get('file_artifacts', []) if f['scope'] in scopes}
        for item in pending['items']:
            if pending['scope']=='receipt' and job.get('receipt_originals'):
                from receipt_originals import pending_artifacts
                partial.update(pending_artifacts(item))
                continue
            for field in ('stage', 'target'):
                path = checked(item[field])
                if _same_file(path, item.get('identity')):
                    partial[path] = digest(path) if field == 'stage' else item['sha256']
                elif str(path) in tracked and io_path(path).exists():
                    raise ValueError(f'추가 저장한 파일이 교체되어 보호합니다. {path}')
    for record in records:
        if record['scope'] not in scopes:
            continue
        for raw, identity in record.get('identities', {}).items():
            path = checked(raw)
            if io_path(path).exists() and not _same_file(path, identity):
                raise ValueError(f'추가 저장한 파일이 교체되어 보호합니다. {path}')
        if record['folder']:
            path = checked(record['folder'])
            if io_path(path).exists():
                marker = _verify_folder(path, record['token'])
                roots.append(path)
                markers[marker] = record['token']
    return roots, markers, partial


def discard_unpublished_stages(pending):
    """Leave published documents in place when retiring a cancelled outgoing attempt."""
    for i, item in enumerate(pending['items']):
        stage = checked(item['stage'])
        expected = checked(item['target']).parent / f'.copy-{pending["token"]}-{i}.part'
        if stage != expected:
            raise ValueError(f'임시 사본 경로를 확인할 수 없습니다. {stage}')
        if io_path(stage).exists():
            if not _same_file(stage, item.get('identity')):
                raise ValueError(f'임시 사본의 소유권을 확인할 수 없습니다. {stage}')
            io_path(stage).unlink()
