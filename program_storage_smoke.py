"""Exercise installed-path selection, legacy migration and cleanup using local fixtures only."""
import json,sys,tempfile,shutil
from pathlib import Path
from core import Store,digest
from browser_journal import Journal
from program_storage import program_root,maintain,save_json,cleanup_rollbacks
from file_rollback import inventory
from fs_paths import io_path


def run(output):
    actual=program_root({})
    if getattr(sys,'frozen',False):assert actual==Path(sys.executable).resolve().parent
    with tempfile.TemporaryDirectory(prefix='sdr-storage-check-') as directory:
        base=Path(directory).resolve();shared=base/'shared';shared.mkdir();program=base/'Program'
        config={'root':str(shared),'_program_dir':str(program),'spreadsheet_id':'fixture-sheet'}
        store=Store(base/'history.sqlite')
        try:
            old_journal=shared/'.수발신-브라우저이력';old_journal.mkdir()
            entry={'sid':'fixture-sheet','tab':'fixture-ledger','row':6,'after':['fixture title'],'markers':['SDR:fixture'],'complete':True}
            (old_journal/'completed.json').write_text(json.dumps(entry),encoding='utf-8')
            old_backup=shared/'.수발신-되돌리기'/('b'*64)/('a'*32)/'0'/('긴공문제목_'*16)
            io_path(old_backup).mkdir(parents=True)
            document=old_backup/('첨부문서제목_'*18+'.pdf')
            io_path(document).write_bytes(b'preserved attachment fixture')
            job={'hash':'case','id':'b'*64,'state':'완료','receipt_done':True,'number':'581','files_rolled_back':True,'files':[{'path':str(document),'hash':digest(document)}],
                 'rollback_history':[{'id':'a'*32,'scope':'receipt','moves':[{'source':str(shared/'original-case'),'backup':str(old_backup),'backup_root':str(shared),'slot':'0','inventory':inventory(old_backup),'done':True}]}]}
            store.put(job);maintain(config,store);updated=store.get('case');backup=Path(updated['rollback_history'][0]['moves'][0]['backup'])
            assert not old_journal.exists() and not (shared/'.수발신-되돌리기').exists()
            assert backup.is_relative_to(program) and digest(Path(updated['files'][0]['path']))==updated['files'][0]['hash']
            assert Journal(config).entries()[0]['markers']==['SDR:fixture']
            assert not Journal(config).folder.exists()
            assert len(updated['files'][0]['path'])>400 and not updated['files'][0]['path'].startswith('\\\\?\\')
            updated['files_rolled_back']=False;cleanup_rollbacks(updated,config,store);assert io_path(backup).exists(), 'Only remaining attachment copy must stay'
            restored=base/'original.pdf';shutil.copy2(io_path(updated['files'][0]['path']),restored)
            updated['files'][0]['path']=str(restored);store.put(updated);cleanup_rollbacks(updated,config,store)
            assert not io_path(backup).exists() and restored.is_file() and updated['number']=='581'
            result={'version':'storage-v2','long_unicode_paths_verified':True,'exe_folder_selected':True,'shared_folders_removed_after_verified_migration':True,'temporary_browser_files_removed':True,'registration_identity_preserved':True,'referenced_attachment_preserved':True,'normal_completion_backup_cleanup':True,'receipt_number_preserved':True,'real_sheet_writes':False,'real_shared_folder_writes':False}
        finally:store.db.close()
    Path(output).write_text(json.dumps(result,indent=2),encoding='utf-8');return result
