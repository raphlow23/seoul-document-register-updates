"""List visibility is independent of files and durable registration history."""
import os
from pathlib import Path


def resume_downloads(saved,inventory):
    seen={p:tuple(v) for p,v in saved.get('seen',{}).items()}
    # v2.7.0 removed old ZIPs from the resume baseline. Do not replay that backlog
    # when upgrading to the new-files-only policy.
    if not saved.get('zip_new_only'):
        seen.update({p:sig for p,sig in inventory.items() if Path(p).suffix.lower()=='.zip'})
    return seen


def source_modified(job):
    paths=[job['source_archive']] if job.get('source_archive') else [f.get('path','') for f in job.get('files',[])]
    stamps=[]
    for value in paths:
        if not value:continue
        try:stamps.append(Path(value).stat().st_mtime_ns)
        except OSError:pass
    return max(stamps,default=0)


def missing_download_source(job, downloads):
    if job.get('files_rolled_back'):return False
    if not downloads:return False
    folder=Path(downloads)
    try:
        if not folder.is_dir():return False
    except OSError:
        return False
    # Keep uncertain partial transactions visible for recovery.
    if job.get("sheet_attempted") and not job.get("receipt_done"):return False
    if job.get("files_started") and job.get("state") not in ("완료","묶음 처리 완료"):return False
    paths=[job["source_archive"]] if job.get("source_archive") else [f.get("path") for f in job.get("files",[]) if f.get("path")]
    if not paths:return False
    root=os.path.normcase(os.path.abspath(folder))
    for value in paths:
        path=Path(value)
        try:
            if os.path.commonpath((root,os.path.normcase(os.path.abspath(path))))!=root:return False
            path.stat()
            return False
        except FileNotFoundError:
            continue
        except (OSError,ValueError):
            return False
    return True
