"""Windows desktop entry point. All mutations run on one background worker."""
from __future__ import annotations
import ctypes
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser
import google_login

from core import CATEGORIES, ROOT, SHEET_ID, TAB, Sheets, Store, ingest, process
from attachment_files import eligible,can_extract,FILE_TYPES
from release_info import VERSION,UPDATE_BASE_URL
from updater import check_and_stage, launch_helper, helper_main
from queue_visibility import missing_download_source,resume_downloads

DATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SeoulDocumentRegister"


def defaults():
    return {"downloads": str(Path.home() / "Downloads"), "root": ROOT,
            "spreadsheet_id": SHEET_ID, "tab": TAB, "recipient": "", "key_path": "", "auto": False,
            "auto_update": True, "update_url": UPDATE_BASE_URL,"update_folder": "", "auth_mode": "browser", "oauth_client_path": ""}


class Worker(threading.Thread):
    def __init__(self, commands, events, config, dbpath):
        super().__init__(daemon=True)
        self.commands, self.events, self.config, self.dbpath = commands, events, config.copy(), dbpath
        self.watch, self.seen, self.pending = False, {}, {}
        self.source_visibility={};self.last_list_sync=0
        self.working = threading.Event()
        self.active_command=None;self.active_document=None
        self.cancelled_exports=set()

    def emit(self, kind, data):
        self.events.put((kind, data))

    def run(self):
        self.store = Store(self.dbpath)
        from ocr_runtime import configure
        configure(self.config)
        while True:
            try:
                cmd, data = self.commands.get(timeout=1)
                self.working.set()
                self.active_command=cmd;self.active_document=data.get('hash') if isinstance(data,dict) else None
                try:
                    if cmd == "quit":
                        self.store.db.close()
                        return
                    if cmd == "config":
                        from sheet_connections import pin_existing
                        for job in pin_existing(self.store,self.config):self.emit('job',job)
                        from copy import deepcopy
                        from folder_naming import preserve_changed_profiles
                        previous_downloads=self.config.get("downloads")
                        updated=deepcopy(data)
                        preferences=preserve_changed_profiles(self.config,updated)
                        self.config = updated
                        configure(self.config)
                        if preferences:self.emit("folder_preferences",preferences)
                        if self.watch and previous_downloads!=updated.get("downloads"):
                            self.seen=self.files();self.pending={}
                    elif cmd == "watch":
                        self.watch = bool(data)
                        self.seen = self.files() if self.watch else {}
                        resume = Path(self.dbpath).parent / "watch-resume.json"
                        if resume.is_file():
                            try:
                                saved = json.loads(resume.read_text(encoding="utf-8"))
                                if self.watch and saved.get("downloads") == self.config["downloads"] and time.time() - saved.get("time", 0) < 600:
                                    self.seen = resume_downloads(saved,self.seen)
                            finally:
                                resume.unlink(missing_ok=True)
                        self.pending = {}
                        self.emit("status", "다운로드 감시 중 · 감시 시작 이후 새로 저장되는 파일만 확인합니다." if self.watch else "다운로드 감시 중지")
                    elif cmd == "startup_baseline":
                        resume = Path(self.dbpath).parent / "watch-resume.json"
                        saved = {}
                        if resume.exists():
                            try:
                                saved = json.loads(resume.read_text(encoding="utf-8"))
                            except ValueError:
                                pass
                        valid = saved.get("downloads") == self.config["downloads"] and time.time() - saved.get("time", 0) < 600
                        inventory=self.files()
                        self.seen = resume_downloads(saved,inventory) if valid else inventory
                        resume.write_text(json.dumps({"watch": saved.get("watch", True) if valid else True,
                                                      "downloads": self.config["downloads"], "time": time.time(), "seen": self.seen,"zip_new_only":True}, ensure_ascii=False), encoding="utf-8")
                    elif cmd == "prepare_update":
                        resume = Path(self.dbpath).parent / "watch-resume.json"
                        restart_watch = self.watch
                        if data.get("_startup") and resume.exists():
                            restart_watch = json.loads(resume.read_text(encoding="utf-8")).get("watch", True)
                        resume.write_text(json.dumps({"watch": restart_watch, "downloads": self.config["downloads"],
                                                      "time": time.time(), "seen": self.seen,"zip_new_only":True}, ensure_ascii=False), encoding="utf-8")
                        self.watch = False
                        self.emit("update_quiesced", data)
                    elif cmd == "add":
                        for path in data:
                            self.add(path)
                    elif cmd == "save":
                        self.store.put(data)
                    elif cmd == "hide_list":
                        self.hide_list(data)
                    elif cmd == 'hide_history':
                        self.hide_list(data,history=True)
                    elif cmd == 'maintenance':
                        from program_storage import maintain
                        self.emit('status','기존 공유폴더의 보관 자료를 설치 폴더로 옮기고 확인하는 중…')
                        message=maintain(self.config,self.store)
                        for job in self.store.all():self.emit('job',job)
                        self.emit('status',message)
                    elif cmd == 'outgoing_new':
                        from outgoing_new import create
                        job=create(self.config,self.store)
                        self.emit('job',job)
                        self.emit('outgoing_new_done',{'hash':job['hash']})
                    elif cmd == 'sheet_override':
                        from sheet_connections import set_override
                        job=self.store.get(data['hash'])
                        if not job:raise ValueError('공문 이력을 찾지 못했습니다.')
                        set_override(job,self.config,data['scope'],data['target']);self.store.put(job)
                        self.emit('job',job);self.emit('status','이 문서의 대장 연결을 저장했습니다.')
                    elif cmd == 'outgoing_restart':
                        from outgoing_restart import restart
                        job=self.store.get(data['hash'])
                        if not job:raise ValueError('초기화할 문서 이력을 찾지 못했습니다.')
                        result=restart(job,self.config,self.store,hide=data.get('hide',False),expected_id=data.get('expected_id'))
                        self.emit('job',result)
                        self.emit('outgoing_restart_done',{**data,'job':result})
                    elif cmd == "google_login":
                        config = {**self.config, **data, "auth_mode": "oauth"}
                        self.emit("status", "브라우저의 Google 로그인 창에서 편집 권한이 있는 계정을 선택해 주세요.")
                        result = google_login.login(config)
                        self.config = config
                        self.emit("google_login_success", {"config": config, "message": result})
                    elif cmd == "browser_login":
                        from browser_sheets import BrowserUI
                        from sheet_connections import browser_config
                        config={**self.config,"auth_mode":"browser"}
                        BrowserUI(browser_config(config)).open_login()
                        self.config=config
                        self.emit("google_login_success",{"config":config,"message":"일반 브라우저에서 직접 로그인해 시트가 보이는지 확인하세요. 그 브라우저 창을 모두 닫은 뒤 프로그램의 ‘연결 확인’을 눌러 주세요. Cloud/API 설정은 필요하지 않습니다."})
                    elif cmd == "browser_open":
                        from browser_sheets import BrowserUI
                        from sheet_connections import browser_config
                        ui=BrowserUI(browser_config(self.config))
                        if ui.available()[0] is not None:ui.connect().close()
                        else:ui.open_login()
                    elif cmd == "google_logout":
                        if self.config.get("auth_mode")=="browser":
                            self.emit("status","전용 시트 브라우저의 Google 계정 메뉴에서 로그아웃해 주세요. 브라우저 로그인 상태는 Google이 관리합니다.")
                        else:
                            google_login.logout(self.config)
                            self.emit("google_logout_success", None)
                    elif cmd in ('receipt_attach','receipt_attach_paths'):
                        from receipt_attachments import append,load_sources,discard_sources
                        job=self.store.get(data['hash'])
                        if not job:raise ValueError('첨부파일을 추가할 공문을 찾지 못했습니다.')
                        additions=[]
                        if cmd=='receipt_attach_paths':
                            additions=load_sources(data['paths'],job,self.store,progress=lambda t:self.emit('status',t))
                        else:additions=[self.store.get(key) for key in data['jobs']]
                        if any(source is None for source in additions):raise ValueError('추가할 첨부파일 이력이 없습니다.')
                        try:result=append(job,additions,self.config,self.store)
                        finally:discard_sources(additions,self.store)
                        self.emit('job',result)
                        for source in additions:
                            if source['hash']!=job['hash']:self.emit('job',self.store.get(source['hash']))
                        for file in result['files']:
                            if file['hash']!=result['hash']:
                                alias=self.store.get(file['hash'])
                                if alias:self.emit('job',alias)
                        self.emit('receipt_attached',{'job':result})
                    elif cmd == "file_rollback":
                        from file_rollback import execute
                        job=self.store.get(data['hash'])
                        if not job:raise ValueError('되돌릴 문서 이력을 찾지 못했습니다.')
                        result=execute(job,self.config,self.store,data['scope'])
                        self.emit('job',result)
                        self.emit('file_rollback_done',{**data,'job':result})
                    elif cmd == "outgoing_save":
                        from outgoing import merge_edit
                        job=self.store.get(data["hash"])
                        if not job:raise ValueError("해당 문서 이력을 찾지 못했습니다.")
                        if not job.get('standalone_outgoing') and not job.get("sheet_attempted") and not job.get("receipt_done"):
                            if not job.get("files_started"):job.update(data.get("receipt_fields",{}))
                            elif data.get("receipt_fields",{}).get("receipt_type"):job["receipt_type"]=data["receipt_fields"]["receipt_type"]
                        merge_edit(job,data["outgoing"])
                        job.pop('outgoing_list_hidden',None)
                        plan=job["outgoing"].get("folder_plan")
                        if plan:
                            from folder_naming import remember_profile
                            prefs=remember_profile(self.config,"outgoing:"+job["outgoing"]["template_id"],plan["base"],plan["template"],plan["agency"],plan["children"],plan["save_in"],plan.get("child_order"))
                            self.emit("folder_preferences",prefs)
                        self.store.put(job)
                        self.emit("job",job)
                        self.emit("outgoing_saved",{"hash":job["hash"],"template":job["outgoing"].get("template_path","")})
                    elif cmd == "draft_source":
                        saved=self.store.get(data["file"]["hash"])
                        text=saved.get("text","") if saved and saved.get("text_file_hash",saved["hash"])==data["file"]["hash"] else ""
                        if not text and can_extract(data['file']['path']):
                            from core import extract
                            text=extract(data["file"]["path"],progress=lambda t:self.emit("status",t))
                        self.emit("draft_source_result",{**data,"text":text})
                    elif cmd == 'ocr_preview':
                        from source_document_view import load
                        try:
                            image,count=load(data['file']['path'],data['page'])
                            self.emit('ocr_preview_result',{**data,'image':image,'page_count':count})
                        except Exception as error:self.emit('ocr_preview_result',{**data,'image':None,'page_count':1,'error':str(error)})
                    elif cmd == "ocr_clean":
                        from ocr_excerpt import clean
                        try:
                            result=clean(data["text"],self.config,data["start"],data["attachments"],data["spacing"])
                            self.emit("ocr_clean_result",{**data,**result})
                        except Exception as error:self.emit("ocr_clean_result",{**data,"error":str(error)})
                    elif cmd == "draft_export":
                        if data['hash'] in self.cancelled_exports:continue
                        self.emit('status','한글 양식을 열고 HWP를 저장하는 중입니다. 한글 접근 확인창이 뜨면 접근 허용을 누르세요.')
                        from outgoing import merge_edit
                        from document_draft import fill_hwpx,field_values,template_copy
                        job=self.store.get(data["hash"])
                        if not job:raise ValueError("문서 이력을 찾지 못했습니다.")
                        if job.get('registration_cancel_pending'):raise ValueError('시작한 등록 취소를 이전 단계 버튼으로 먼저 완료하세요.')
                        if data.get('native_hwp'):
                            from outgoing import require_registered_number
                            require_registered_number(job.get('outgoing',{}))
                        merge_edit(job,data["outgoing"])
                        out=job["outgoing"]
                        if data.get("native_hwp"):
                            from hwp_documents import export_hwp,style,filename
                            name=filename(style(out["template_id"]),out)
                            if out.get("folder_plan"):
                                from outgoing_folders import prepare
                                data["path"]=str(prepare(job,self.config,self.store)/name)
                                self.emit("job",job)
                            else:
                                folder=Path(out.get('save_folder',''))
                                if not folder.is_absolute() or not folder.is_dir():raise ValueError('HWP를 저장할 폴더를 확인하세요.')
                                data['path']=str(folder/name)
                            export_hwp(style(out["template_id"]),out,data["path"])
                            out["hwp_path"]=data["path"]
                            out["hwp_saved_number"]=str(out.get("number") or out.get("file_number",""))
                            from core import digest
                            from file_rollback import track_file
                            out['hwp_created_sha256']=digest(data['path']);out['files_rolled_back']=False
                            track_file(job,data['path'],out['hwp_created_sha256'],'outgoing')
                        elif data["copy_only"]:
                            template_copy(out["template_path"],data["path"])
                        else:
                            fill_hwpx(out["template_path"],data["path"],field_values(job,out))
                        self.store.put(job)
                        self.emit("job",job)
                        self.emit("draft_exported",{"hash":job['hash'],"path":data["path"],"copy_only":data["copy_only"],"open_after":data.get("native_hwp",False),"body_paste_needed":out.get("hwp_body_paste_needed",False) if data.get("native_hwp") else False})
                    elif cmd == 'cancel_registration':
                        from registration_cancel import execute as cancel
                        job=self.store.get(data['hash'])
                        if not job:raise ValueError('취소할 공문을 찾지 못했습니다.')
                        self.emit('status','등록 내용과 생성 파일·폴더를 취소하는 중입니다. 다른 문서의 번호와 서식은 유지합니다.')
                        try:result=cancel(job,self.config,self.store,data['scope'])
                        except Exception:
                            self.emit('job',self.store.get(data['hash']));raise
                        self.emit('job',result);self.emit('registration_cancelled',data)
                    elif cmd == "title_prepare":
                        from title_document import prepare
                        result=prepare(data['file'],self.store,self.config,data['page'],progress=lambda t:self.emit('status',t))
                        self.emit('title_result',{'kind':'prepare','owner':data['owner'],'request':data['request'],**result})
                    elif cmd == "title_crop":
                        from ocr import recognize_image
                        try:
                            text, confidence = recognize_image(data["image"])
                        finally:
                            data["image"].close()
                        self.emit("title_result", {"kind": "crop", "owner": data["owner"], "request": data["request"],
                                                   "text": text, "confidence": confidence, "crop_mode": data["crop_mode"]})
                    elif cmd == "title_apply":
                        from core import apply_title_choice
                        primary, grouped = apply_title_choice(data, self.store)
                        for job in grouped:
                            self.emit("job", job)
                        self.emit("job", primary)
                        if data.get("agency") and data["source"]["mode"] in ("header", "project", "header_address", "filename"):
                            self.config.setdefault("title_preferences", {})[data["agency"]] = data["source"]["mode"]
                        self.emit("title_applied", {"hash": primary["hash"], "preferences": self.config.get("title_preferences", {})})
                        if not self.config.get("_guided_ui") and self.config.get("auto") and primary.get("routing") and primary.get("category") in CATEGORIES and primary.get("recipient"):
                            self.execute(primary)
                    elif cmd == "route_tree":
                        from folder_naming import route_tree
                        self.emit("route_result", {**data,"records":route_tree(self.config["root"],data["agency"])})
                    elif cmd == "folder_describe":
                        from folder_naming import describe_directory
                        self.emit("outgoing_folder_result",{**data,"record":describe_directory(data["path"])})
                    elif cmd == "route_apply":
                        from folder_naming import preflight
                        job = self.store.get(data["hash"])
                        file_only=bool(job and job.get('receipt_done') and job.get('files_rolled_back'))
                        if not job or (job["state"] in ("완료","묶음 처리 완료","묶음에 포함") and not file_only):
                            raise ValueError("완료된 공문의 저장 위치는 여기서 변경할 수 없습니다.")
                        old_targets = {t["relative"]:t for t in job.get("routing",{}).get("targets",[])}
                        if job.get("files_started"):
                            def signature(t,routing):
                                return (t['relative'],t['template'],tuple(t.get('children',routing.get('children',[]))),tuple(t.get('child_order') or []),t.get('folder_title') or routing.get('folder_title') or job['title'],t.get('storage_mode','case'),t.get('year_folder',job['date'][:4]))
                            before = {signature(t,job['routing']) for t in old_targets.values()}
                            after = {signature(t,data['routing']) for t in data['routing']['targets']}
                            if before != after or job.get("routing",{}).get("agency")!=data["routing"]["agency"]:
                                raise ValueError("이미 파일 저장을 시작한 공문입니다. 기존 위치·이름으로 재시도해 주세요.")
                        if job.get("sheet_attempted") and job.get("routing",{}).get("agency") != data["routing"]["agency"]:
                            raise ValueError("등록을 시도한 공문의 종류는 변경할 수 없습니다.")
                        for target in data["routing"]["targets"]:
                            old = old_targets.get(target["relative"],{})
                            if old.get("template") == target["template"] and old.get('storage_mode','case')==target.get('storage_mode','case') and (old.get('folder_title') or job.get('routing',{}).get('folder_title') or job['title'])==(target.get('folder_title') or data['routing'].get('folder_title') or job['title']):
                                target.update({k:old[k] for k in ("folder_number","folder_name") if k in old})
                        if not job.get("sheet_attempted") and not job.get("files_started"):
                            job.update(data["fields"])
                        elif not job.get("sheet_attempted") and data["fields"].get("receipt_type"):
                            job["receipt_type"]=data["fields"]["receipt_type"]
                        if data.get("outgoing") is not None:
                            from outgoing import merge_edit
                            merge_edit(job,data["outgoing"])
                        job["routing"] = data["routing"]
                        job["category"] = data["routing"]["agency"]
                        preflight(job, self.config["root"])
                        job["reason"] = f"제목·저장 위치 확인 완료 · {len(job['routing']['targets'])}곳"
                        self.store.put(job)
                        self.emit("job",job)
                        self.config.setdefault("route_templates",{}).update({t["relative"]:t["template"] for t in job["routing"]["targets"]})
                        from folder_naming import remember_profile,destination
                        for target in job["routing"]["targets"]:
                            prefs=remember_profile(self.config,"incoming:"+target["relative"],destination(self.config["root"],target["relative"]),target["template"],job["routing"]["agency"],target.get('children',job["routing"].get("children",[])),"incoming",target.get("child_order"))
                            self.config['folder_profiles']['incoming:'+target['relative']].update(storage_mode=target.get('storage_mode','case'))
                        self.emit("folder_preferences",prefs)
                        self.emit("route_applied",{"hash":job["hash"],"templates":self.config["route_templates"],"document_template":job.get("outgoing",{}).get("template_path","")})
                        if data["execute"]:
                            self.execute(job)
                    elif cmd in ("process","process_outgoing"):
                        for job in data:
                            self.execute(job,register_outgoing=cmd=='process_outgoing')
                    elif cmd == "test":
                        from sheet_connections import browser_config
                        Sheets(browser_config(self.config)).snapshot()
                        exists = Path(self.config["root"]).is_dir()
                        missing = [p for p in dict.fromkeys(p for paths in CATEGORIES.values() for p in paths)
                                   if not (Path(self.config["root"]) / p).is_dir()]
                        self.emit("notice", "구글 시트 읽기 연결 성공.\n" + ("공유폴더와 저장 하위폴더 확인 완료." if exists and not missing else "공유폴더 또는 저장 하위폴더에 접근할 수 없습니다.\n" + "\n".join(missing)))
                    elif cmd == 'test_sheet_target':
                        from sheet_connections import normalized,SCOPES
                        target=normalized(data['target']);config={**self.config,**target,'_target_tab':target['tab'],'_outgoing_family':data['family']}
                        if data['scope']=='outgoing':
                            from outgoing import OutgoingSheets
                            ledger=OutgoingSheets(config)
                        elif data['scope']=='stamp':
                            from stamp_ledger import StampSheets
                            ledger=StampSheets(config)
                        else:ledger=Sheets(config)
                        ledger.snapshot();self.emit('notice',SCOPES[data['scope']]+' 읽기 연결과 열 구성 확인 완료.\n'+target['tab']+'\n시트에 쓰지는 않았습니다.')
                    elif cmd == "group":
                        primary, others = data
                        if primary.get("sheet_attempted") or any(x.get("sheet_attempted") for x in others):
                            raise ValueError("등록을 시도한 문서는 묶을 수 없습니다. 먼저 등록 상태를 확인해 주세요.")
                        for other in others:
                            for f in other["files"]:
                                if f["hash"] not in [x["hash"] for x in primary["files"]]:
                                    primary["files"].append(f)
                            other["state"] = "묶음에 포함"
                            other["parent"] = primary["hash"]
                            self.store.put(other)
                            self.emit("job", other)
                        primary["ready"] = False
                        primary["reason"] = f"{len(primary['files'])}개 첨부파일 묶음 · 분류와 제목 확인 필요"
                        self.store.put(primary)
                        self.emit("job", primary)
                except Exception as e:
                    self.emit("error", str(e))
                finally:
                    if cmd=='draft_export':
                        self.cancelled_exports.discard(self.active_document)
                        self.emit('hwp_export_finished',{'hash':self.active_document})
                    self.active_command=None;self.active_document=None
                    self.working.clear()
                    self.emit("idle", None)
            except queue.Empty:
                pass
            if self.commands.empty() and time.monotonic()-self.last_list_sync>=2:
                self.last_list_sync=time.monotonic()
                self.sync_sources()
            if self.watch and self.commands.empty():
                self.working.set()
                try:
                    self.scan()
                except Exception as e:
                    self.watch = False
                    self.emit("error", f"다운로드 감시 중단: {e}")
                finally:
                    self.working.clear()

    def hide_list(self,keys,history=False):
        for key in keys:
            job=self.store.get(key)
            if job:
                job['history_hidden' if history else 'list_hidden']=True
                self.store.put(job);self.emit("job",job)
        self.emit("status",f"{len(keys)}개 항목을 {'처리 이력 목록' if history else '첨부 목록'}에서 삭제했습니다. 파일·기존 시트 기록은 유지하며 다시 다운로드하면 새 공문으로 시작합니다.")

    def sync_sources(self):
        for job in self.store.all():
            hidden=missing_download_source(job,self.config.get("downloads"))
            previous=self.source_visibility.get(job["hash"],False)
            self.source_visibility[job["hash"]]=hidden
            if hidden!=previous:self.emit("job",job)

    def files(self):
        folder = Path(self.config["downloads"])
        if not folder.is_dir():
            raise ValueError("다운로드 폴더를 찾을 수 없습니다.")
        result = {}
        for p in folder.iterdir():
            if eligible(p):
                try:
                    s = p.stat()
                    result[str(p)] = (s.st_size, s.st_mtime_ns)
                except OSError:
                    pass
        return result

    def scan(self):
        now = self.files()
        for path in list(self.seen):
            if path not in now:self.seen.pop(path,None)
        for path, sig in now.items():
            if self.seen.get(path) == sig:
                self.pending.pop(path, None)
                continue
            last, since = self.pending.get(path, (None, time.monotonic()))
            if sig != last:
                self.pending[path] = (sig, time.monotonic())
            elif time.monotonic() - since >= 6 and sig[0] > 0:
                # Test readability before ingest; locked/incomplete files remain pending.
                try:
                    with open(path, "rb"):
                        pass
                except OSError:
                    continue
                self.add(path)
                self.seen[path] = sig
                self.pending.pop(path, None)
        for path in list(self.pending):
            if path not in now:
                self.pending.pop(path, None)

    def add(self, path):
        self.emit("status", "첨부파일 확인 중: " + Path(path).name)
        try:
            job, new = ingest(path, self.store, progress=lambda text: self.emit("status", text))
        except Exception as error:
            if Path(path).suffix.lower()!=".zip":raise
            from zip_intake import NoDocuments
            if isinstance(error,NoDocuments):
                self.emit('status',Path(path).name+': '+str(error));return
            self.emit("error",Path(path).name+": "+str(error))
            return
        if not new and (job["state"] == "제외" or job.get("list_hidden")):
            if job["state"] == "제외":job["state"] = "확인 필요"
            job.pop("list_hidden",None)
            self.store.put(job)
        if new:
            job["recipient"] = self.config.get("recipient", "")
            self.store.put(job)
            if job.get('reimported_from'):
                archived=self.store.get(job['reimported_from'])
                if archived:self.emit('job',archived)
        self.emit("job", job)
        self.emit("status", "확인 목록에 추가했습니다. 첨부에서 제목을 골라 주세요." if new else "이미 확인한 파일입니다. 기존 처리 이력을 표시합니다.")

    def execute(self, job,register_outgoing=False):
        # A queued UI snapshot must not undo a completed/attempted database transaction.
        saved = self.store.get(job["hash"])
        if saved and (saved.get('standalone_outgoing') or saved["state"] in ("완료", "묶음 처리 완료", "묶음에 포함") or saved.get("sheet_attempted")):
            job = saved
        self.emit("status", ("발신·직인날인대장 등록 중: " if job.get('standalone_outgoing') else "저장·접수등록 중: ") + job["title"])
        try:
            from sheet_connections import preflight as check_connections
            if job.get('registration_cancel_pending'):raise ValueError('시작한 등록 취소를 이전 단계 버튼으로 먼저 완료하세요.')
            check_connections(self.config,job)
            if (register_outgoing or not job.get('receipt_done')) and job.get("outgoing",{}).get("enabled") and job["outgoing"].get("open_hwp_after_registration") and not job["outgoing"].get("complete"):
                from hwp_documents import check_template
                check_template(job["outgoing"]["template_id"])
                if job["outgoing"].get("folder_plan") and job['outgoing'].get('folder_mode')!='direct':
                    from outgoing_folders import validate
                    validate(job["outgoing"]["folder_plan"],job["outgoing"])
            if not job.get('standalone_outgoing') and not job.get("routing") and not job.get("receipt_done"):
                raise ValueError("‘저장 위치 선택’에서 공문 종류와 저장할 폴더를 먼저 골라 주세요.")
            files_only=bool(job.get('receipt_done') and job.get('files_rolled_back') and not register_outgoing)
            result = process(job,{**self.config,'_register_outgoing_after_restore':True} if register_outgoing else self.config,self.store)
            self.emit("job", result)
            self.emit("files_resaved" if files_only else "completed", result)
        except Exception as e:
            job["reason"] = str(e)
            self.store.put(job)
            self.emit("job", job)
            self.emit("error", str(e))


class App:
    def __init__(self, root, data=DATA, smoke=False):
        self.root, self.data = root, Path(data)
        self.data.mkdir(parents=True, exist_ok=True)
        self.config = defaults()
        cp = self.data / "config.json"
        if cp.exists():
            saved = json.loads(cp.read_text(encoding="utf-8"))
            self.config.update(saved)
            if "auth_mode" not in saved and saved.get("key_path"):
                self.config["auth_mode"] = "service"
            elif self.config.get("auth_mode")=="oauth" and not self.config.get("oauth_client_path"):
                self.config["auth_mode"]="browser"
        self.config["_auth_data_dir"] = str(self.data)
        self.config['_program_dir']=str(Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else (self.data/'Program').resolve())
        history = Store(self.data / "history.sqlite")
        self.jobs = {x["hash"]: x for x in history.all()}
        history.db.close()
        self.commands, self.events = queue.Queue(), queue.Queue()
        self.busy = False
        self.last_activity = time.monotonic()
        self.update_check_running = False
        self.update_ready = None
        self.updating = False
        self.startup_gate = False
        self.startup_copying = False
        root.bind_all("<KeyPress>", self.activity, add="+")
        root.bind_all("<ButtonPress>", self.activity, add="+")
        root.title(f"서울센터 공문 업무 v{VERSION}")
        from app_icon import apply_window_icon
        apply_window_icon(root,self.data)
        root.geometry(f"{min(1180,root.winfo_screenwidth()-60)}x{min(900,root.winfo_screenheight()-100)}")
        root.minsize(1000, 660)
        root.configure(bg="#f3f5f8")
        from modern_ui import apply_theme
        apply_theme(root)
        outer = ttk.Frame(root, padding=20,style='App.TFrame')
        outer.pack(fill="both", expand=True)
        from workflow_ui import WorkflowShell
        self.workflow = WorkflowShell(self, outer)
        self.config["_guided_ui"] = True
        for job in self.jobs.values():
            self.render(job)
        self.worker = Worker(self.commands, self.events, self.config, self.data / "history.sqlite")
        self.worker.start()
        try:
            root.tk.call("package","present","tkdnd")
        except tk.TclError:
            self.file_drop=None
        else:
            from file_drop import FileDrop
            self.file_drop=FileDrop(self,[root,self.tree,self.workflow.pages["queue"],self.workflow.drop_zone])
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(200, self.poll)
        if not smoke:
            root.after(100, self.startup)
            root.after(1000,self.prepare_ocr_background)

    def prepare_ocr_background(self):
        if getattr(self,'closing',False) or self.updating:return
        if self.startup_gate:
            self.root.after(1000,self.prepare_ocr_background);return
        if not getattr(sys,'frozen',False):return
        config=dict(self.config)
        def prepare():
            from ocr_runtime import installed,ensure
            try:
                if installed(config):return
                self.events.put(('status','최초 OCR 구성 준비 중 · 프로그램은 계속 사용할 수 있습니다.'))
                ensure(config)
                if not self.worker.working.is_set():self.events.put(('status','OCR 구성 준비 완료 · 다음 실행부터 설치된 파일을 재사용합니다.'))
            except Exception:
                if not self.worker.working.is_set():self.events.put(('status','OCR 구성은 PDF를 읽을 때 다시 준비합니다.'))
        threading.Thread(target=prepare,daemon=True).start()

    def render(self, job):
        key = job["hash"]
        self.jobs[key] = job
        self.workflow.update_job(job)

    def send(self, cmd, data):
        if getattr(self, "closing", False) or self.updating:
            return False
        if self.startup_gate:
            messagebox.showinfo("시작 준비 중", "새 버전 확인·설치가 끝나면 업무를 시작할 수 있습니다.")
            return False
        if self.busy or (hasattr(self, "worker") and self.worker.working.is_set() and getattr(self.worker,'active_command','unknown') is not None):
            messagebox.showinfo("처리 중", "진행 중인 작업이 끝난 뒤 다시 시도해 주세요.")
            return False
        self.busy = True
        self.commands.put((cmd, data))
        return True

    def queue_hwp_export(self,data):
        if getattr(self,'closing',False) or self.updating or self.startup_gate:return False
        key=data['hash'];pending=getattr(self,'hwp_pending',set())
        if key in pending or (getattr(self.worker,'active_command',None)=='draft_export' and getattr(self.worker,'active_document',None)==key):
            self.status.set('이 공문의 한글 작성이 진행 중입니다. 한글 접근 확인창과 작업표시줄의 한글 창을 확인하세요.');return True
        pending.add(key);self.hwp_pending=pending;self.busy=True
        self.status.set('현재 작업이 끝나면 이 공문의 HWP 저장·열기를 이어갑니다. 대장은 다시 등록하지 않습니다.')
        self.commands.put(('draft_export',data));return True

    def poll(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "job":
                    self.render(data)
                elif kind == "status":
                    self.status.set(data)
                elif kind == "idle":
                    self.busy = False
                elif kind == "error":
                    self.workflow.recover()
                    self.status.set(data)
                    messagebox.showerror("확인이 필요합니다", data)
                elif kind == "notice":
                    messagebox.showinfo("연결 확인", data)
                elif kind == 'file_rollback_done':
                    self.busy=False
                    self.workflow.rollback_finished(data)
                    self.status.set('파일·폴더 저장을 취소했습니다. 구글시트 등록과 번호는 유지합니다.')
                elif kind == 'receipt_attached':
                    self.busy=False
                    self.workflow.attachments_added(data['job'])
                    self.status.set(data['job']['reason'])
                elif kind == 'outgoing_new_done':
                    self.busy=False
                    self.workflow.outgoing_created(data)
                    self.status.set('수신 없이 새 발신 공문을 작성합니다. 사용할 양식을 선택하세요.')
                elif kind == 'outgoing_restart_done':
                    self.busy=False
                    self.workflow.outgoing_restarted(data)
                    self.status.set(data['job']['reason']+' · 기존 구글시트 행과 파일은 유지합니다.')
                elif kind in ("completed","files_resaved"):
                    out=data.get("outgoing",{})
                    detail=(f"발신 {out.get('number','')} · 직인대장 등록 완료" if data.get('standalone_outgoing') else f"수신 {data['number']}"+(f" · 발신 {out.get('number','')} · 직인대장 등록 완료" if out.get("enabled") else " · 저장·수신등록 완료"))
                    if kind=='files_resaved':detail=f"수신 {data['number']} · 파일 다시 저장 완료 · 기존 대장 유지"
                    self.status.set(detail+" · "+data["title"])
                    self.workflow.completed(data)
                    self.root.bell()
                elif kind == "outgoing_saved":
                    self.config["document_template"]=data["template"]
                    self.persist_config()
                    self.workflow.outgoing_saved(data["hash"])
                    self.status.set("발신 작성 정보를 저장했습니다." if not self.busy else "발신대장과 직인날인대장에 등록하는 중…")
                elif kind == "draft_source_result":
                    editor=getattr(self,"outgoing_editor",None)
                    if editor is not None:editor.deliver(data)
                    self.workflow.deliver_excerpt(data)
                elif kind == "ocr_clean_result":
                    editor=getattr(self,"ocr_text_editor",None)
                    if editor is not None:editor.deliver(data)
                elif kind == 'ocr_preview_result':
                    editor=getattr(self,'ocr_text_editor',None)
                    if editor is not None:editor.deliver_preview(data)
                    elif data.get('image') is not None:data['image'].close()
                elif kind == "draft_exported":
                    back=getattr(self.workflow,'pending_back',None)
                    if back and back['hash']==data.get('hash'):continue
                    self.status.set("한글 문서를 새 파일로 저장했습니다: "+data["path"])
                    if data.get("open_after"):
                        self.status.set("번호·오늘 날짜를 넣은 HWP를 엽니다. OCR 또는 한글에서 복사한 문구를 붙여넣고 Ctrl+S로 저장하세요.")
                    else:messagebox.showinfo("새 공문 저장",data["path"])
                    if data["copy_only"] or data.get("open_after"):
                        try:os.startfile(data["path"])
                        except OSError as error:
                            self.status.set('HWP는 저장됐지만 한글을 실행하지 못했습니다: '+data['path'])
                            messagebox.showerror('저장된 HWP 열기',str(error)+'\n\n저장 파일: '+data['path'])
                elif kind == 'hwp_export_finished':
                    getattr(self,'hwp_pending',set()).discard(data['hash'])
                elif kind == 'registration_cancelled':
                    self.status.set(self.jobs.get(data['hash'],{}).get('reason') or '해당 공문의 시트 등록 내용과 생성 파일·폴더를 취소했습니다. 다시 등록하면 최신 번호를 확인합니다.')
                    self.workflow.cancel_finished(data)
                elif kind == "google_login_success":
                    self.config.update(data["config"])
                    self.persist_config()
                    self.auth_status.set("직접 로그인 → 시트 표시 확인 → 창 닫기 → 연결 확인" if self.config.get("auth_mode")=="browser" else "본인 Google 계정 연결됨")
                    self.status.set(data["message"])
                elif kind == "google_logout_success":
                    self.auth_status.set("이 PC의 Google 연결을 해제했습니다.")
                elif kind == "update_found":
                    if data == self.update_folder():
                        self.startup_copying = True
                        self.update_status.set("새 버전 파일을 준비하는 중 · 준비 후 자동 재시작합니다.")
                elif kind == "update_check":
                    self.update_check_running = False
                    if data["folder"] != self.update_folder():
                        continue
                    if data.get("error"):
                        self.update_status.set("업데이트 확인 보류 · " + data["error"])
                        self.release_startup()
                    elif data.get("update"):
                        self.update_ready = data["update"]
                        self.update_status.set(f"새 버전 {self.update_ready['manifest']['version']} 준비됨 · 사용을 마친 뒤 자동 적용")
                        self.root.after(0 if self.startup_gate else 5000, self.apply_when_idle)
                    else:
                        self.update_status.set(f"v{VERSION} · 새로 배포된 버전 없음")
                        self.release_startup()
                elif kind == "update_quiesced":
                    self.finish_update(data)
                elif kind == "title_result":
                    dialog = getattr(self, "title_dialog", None)
                    if dialog is not None:
                        dialog.deliver(data)
                    elif data.get("image") is not None:
                        data["image"].close()
                elif kind == "title_applied":
                    self.config["title_preferences"] = data["preferences"]
                    self.persist_config()
                    self.workflow.focus(data["hash"])
                    self.status.set("선택한 제목을 적용했습니다. 분류·수신자·접수일을 확인한 뒤 저장·등록하세요.")
                    self.root.after(350,self.workflow.route_step)
                elif kind == "route_result":
                    dialog = getattr(self,"route_dialog",None)
                    if dialog is not None: dialog.deliver(data)
                elif kind == "folder_preferences":
                    self.config.update(data);self.persist_config()
                elif kind == "outgoing_folder_result":
                    dialog=getattr(self,"outgoing_folder_dialog",None)
                    if dialog is not None:dialog.deliver(data)
                elif kind == "route_applied":
                    self.config["route_templates"] = data["templates"]
                    if data.get("document_template"):self.config["document_template"]=data["document_template"]
                    self.persist_config()
                    self.workflow.focus(data["hash"])
        except queue.Empty:
            pass
        self.root.after(200, self.poll)

    def selected(self):
        return self.workflow.selected_jobs()

    def persist_config(self):
        tmp = self.data / "config.tmp"
        tmp.write_text(json.dumps({k:v for k,v in self.config.items() if not k.startswith("_")}, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.data / "config.json")

    def google_sign_in(self):
        if self.busy or self.worker.working.is_set() or self.updating or self.startup_gate or getattr(self, "closing", False):
            return messagebox.showinfo("처리 중", "진행 중인 작업이 끝난 뒤 로그인해 주세요.")
        config = self.config.copy()
        try:
            google_login.client_config(config)
        except (ValueError, OSError):
            messagebox.showinfo("Google 로그인 최초 설정", "처음 한 번 Google Cloud에서 만든 ‘데스크톱 앱 OAuth 클라이언트’ 설정 파일이 필요합니다.\n시트 소유자의 서비스 계정 공유는 필요하지 않습니다. 다음 창에서 로그인 설정 JSON을 선택하세요.")
            path = filedialog.askopenfilename(title="Google 데스크톱 앱 OAuth 설정 선택", filetypes=[("OAuth 클라이언트 설정", "*.json")])
            if not path:
                return
            config["oauth_client_path"] = path
            try:
                google_login.client_config(config)
            except Exception as e:
                return messagebox.showerror("로그인 설정", str(e))
        self.send("google_login", config)

    def select(self, _=None):
        jobs = self.selected()
        if not jobs:
            return
        job = jobs[0]
        for k, var in self.fields.items():
            var.set(job.get(k,"공문" if k=="receipt_type" else ""))
        self.outgoing_enabled.set(job.get("outgoing",{}).get("enabled",False))
        self.detail.set(f"{job['reason']}\n첨부 {len(job['files'])}개"+(" · "+Path(job['files'][0]['path']).name if job['files'] else ' · 수신 없이 새 발신'))

    def edited(self):
        jobs = self.selected()
        if len(jobs) != 1:
            messagebox.showinfo("선택", "수정할 공문 한 개를 선택해 주세요.")
            return None
        job = jobs[0]
        if job.get("files_started"):
            if any(v.get().strip()!=str(job.get(k,"")) for k,v in self.fields.items() if k!="receipt_type"):
                messagebox.showinfo("저장 이력","이미 파일이 저장된 공문은 기존 제목·정보로 재시도해 주세요.")
                return None
            if not job.get("sheet_attempted"):job["receipt_type"]=self.fields["receipt_type"].get()
            return job
        if job["state"] == "완료" or job.get("sheet_attempted"):
            messagebox.showinfo("등록 이력", "등록을 시도한 공문은 여기서 수정할 수 없습니다. 재시도 시 기존 등록 여부부터 확인합니다.")
            return None
        job.update({k: v.get().strip() for k, v in self.fields.items()})
        return job

    def save_edit(self):
        job = self.edited()
        if job and self.send("save", job):
            self.render(job)
            self.status.set("수정 내용을 저장했습니다.")

    def register(self):
        jobs = self.selected()
        if not jobs:
            return messagebox.showinfo("선택", "처리할 공문을 선택해 주세요.")
        if len(jobs) == 1 and not jobs[0].get("sheet_attempted") and jobs[0]["state"] != "완료":
            job = self.edited()
            if not job:
                return
            jobs = [job]
        if any(j["state"] == "완료" for j in jobs):
            return messagebox.showinfo("등록 완료", "이미 완료된 공문은 다시 등록하지 않습니다.")
        if any(not j.get("routing") and not j.get("receipt_done") for j in jobs):
            if len(jobs) == 1:
                return self.choose_routes()
            return messagebox.showinfo("저장 위치 선택","공문마다 저장 위치를 선택해 주세요. 같은 메일의 첨부들은 ‘첨부에서 제목 고르기’에서 한 공문으로 묶을 수 있습니다.")
        self.send("process", jobs)

    def toggle_outgoing(self):
        enabled=self.outgoing_enabled.get()
        jobs=self.selected()
        if len(jobs)!=1:
            self.outgoing_enabled.set(False)
            return messagebox.showinfo("문서 선택","발신을 함께 등록할 수신 공문을 하나 선택해 주세요.")
        if jobs[0].get("outgoing",{}).get("sheet_attempted"):
            self.select()
            return messagebox.showinfo("발신 등록 이력","발신 등록을 시도한 문서는 이 체크를 해제할 수 없습니다.")
        if enabled:self.edit_outgoing(enable=True)
        else:
            from outgoing import defaults
            out=defaults(jobs[0],self.config);out["enabled"]=False
            self.send("outgoing_save",{"hash":jobs[0]["hash"],"outgoing":out})

    def edit_outgoing(self,enable=False):
        if self.busy or self.worker.working.is_set() or self.updating or self.startup_gate:
            self.select()
            return messagebox.showinfo("처리 중","작업이 끝난 뒤 발신 정보를 작성해 주세요.")
        jobs=self.selected()
        if len(jobs)!=1:return messagebox.showinfo("문서 선택","공문 한 개를 선택해 주세요.")
        job=jobs[0]
        if not job.get("files_started") and not job.get("sheet_attempted"):
            job.update({k:v.get().strip() for k,v in self.fields.items()})
        from outgoing_editor import OutgoingEditor
        OutgoingEditor(self,job,enable=enable,on_close=lambda saved:None if saved else self.select())

    def choose_routes(self):
        if getattr(self,"route_dialog",None) is not None:
            return
        if self.busy or self.worker.working.is_set() or self.updating or self.startup_gate:
            return messagebox.showinfo("처리 중","진행 중인 작업이 끝난 뒤 저장 위치를 선택해 주세요.")
        jobs = self.selected()
        if len(jobs) != 1 or jobs[0]["state"] == "완료":
            return messagebox.showinfo("공문 선택","저장할 공문 한 개를 선택해 주세요. 여러 첨부는 먼저 제목을 골라 한 공문으로 묶어 주세요.")
        job = jobs[0]
        if not job.get("sheet_attempted") and not job.get("files_started"):
            job.update({k:v.get().strip() for k,v in self.fields.items()})
        from route_chooser import RouteChooser
        self.route_dialog = RouteChooser(self,job)

    def choose_title(self):
        if self.busy or self.worker.working.is_set() or self.updating or self.startup_gate:
            return messagebox.showinfo("처리 중", "진행 중인 작업이 끝난 뒤 제목을 선택해 주세요.")
        jobs = self.selected()
        if not jobs:
            return messagebox.showinfo("공문 선택", "같은 메일에서 받은 첨부파일을 선택해 주세요. 여러 개는 Ctrl 클릭으로 함께 선택할 수 있습니다.")
        if any(j["state"] == "완료" or j.get("sheet_attempted") or j.get("files_started") for j in jobs):
            return messagebox.showinfo("등록 이력", "등록을 시도하기 전 공문에서 제목을 고를 수 있습니다.")
        if len(jobs) == 1:
            jobs[0].update({k: v.get().strip() for k,v in self.fields.items()})
        from title_chooser import TitleChooser
        self.title_dialog = TitleChooser(self, jobs)

    def group(self):
        jobs = self.selected()
        if len(jobs) < 2:
            return messagebox.showinfo("첨부 묶기", "같은 메일의 본 공문과 붙임을 함께 선택해 주세요(Ctrl 클릭).")
        if any(j["state"] == "완료" or j.get("sheet_attempted") for j in jobs):
            return messagebox.showinfo("묶기", "처리 전 공문끼리만 묶을 수 있습니다.")
        # Choose the main document explicitly; Treeview selection order is not click order.
        dialog = tk.Toplevel(self.root)
        dialog.title("대표 공문 선택")
        dialog.geometry("620x270")
        ttk.Label(dialog, text="이 묶음의 제목으로 사용할 본 공문을 선택하세요.", padding=12).pack(anchor="w")
        chosen = tk.IntVar(value=0)
        for i, job in enumerate(jobs):
            ttk.Radiobutton(dialog, text=job["title"][:75], variable=chosen, value=i).pack(anchor="w", padx=14, pady=3)
        def apply():
            primary = jobs[chosen.get()]
            others = [x for x in jobs if x["hash"] != primary["hash"]]
            if self.send("group", (primary, others)):
                dialog.destroy()
        ttk.Button(dialog, text="첨부파일로 묶기", command=apply).pack(pady=10)

    def preview(self):
        jobs = self.selected()
        if not jobs:
            return
        job = jobs[0]
        dialog = tk.Toplevel(self.root)
        dialog.title("공문 본문과 저장 결과")
        dialog.geometry("800x600")
        buttons=ttk.Frame(dialog);buttons.pack(fill="x",padx=10,pady=6)
        def excerpt():
            from ocr_text_editor import OcrTextEditor
            source=next((f for f in job['files'] if f['hash']==job.get('text_file_hash',job['hash'])),job['files'][0])
            OcrTextEditor(self,job.get("text",""),parent=dialog,source_file=source)
        if job['files']:ttk.Button(buttons,text="자동 정리된 본문 확인 · 복사",command=excerpt).pack(side="left")
        text = tk.Text(dialog, wrap="word", font=("맑은 고딕", 11), padx=15, pady=15)
        text.pack(fill="both", expand=True)
        content = f"등록번호: {job.get('number', '')}\n상태: {job['state']}\n{job['reason']}\n\n첨부파일\n"
        if job.get('standalone_outgoing'):content=f"발신번호: {job.get('outgoing',{}).get('document_number') or job.get('outgoing',{}).get('number','')}\n상태: {job['state']}\n{job['reason']}\n\n수신 없이 새로 작성한 발신 공문\n"
        content += "\n".join(f["path"] for f in job["files"])
        content += "\n\n저장 위치\n" + "\n".join(job.get("destinations", []))
        if job.get('rollback_history'):
            content+='\n\n되돌린 파일 복구 보관 위치\n'+'\n'.join(m['backup'] for m in job['rollback_history'][-1]['moves'])
        if job.get("routing"):
            from folder_naming import folder_name
            content += "\n\n폴더 이름\n" + "\n".join(t["relative"]+" / "+(t.get('folder_name') or folder_name(t['template'],t.get('folder_title') or job['routing'].get('folder_title') or job['title'],job['date'],t.get('folder_number'))) for t in job["routing"]["targets"])
        content += "\n\n읽은 본문\n" + (job["text"] or "본문을 읽지 못했습니다. 제목과 분류를 직접 입력할 수 있습니다.")
        text.insert("1.0", content)
        text.configure(state="disabled")

    def ignore(self):
        jobs = self.selected()
        if not jobs:
            return
        self.send("hide_list",[job["hash"] for job in jobs])

    def hide_history(self):
        keys=list(self.workflow.history_tree.selection())
        if not keys:return messagebox.showinfo('처리 이력','목록에서 삭제할 항목을 선택하세요. Ctrl 또는 Shift로 여러 항목을 선택할 수 있습니다.')
        self.send('hide_history',keys)

    def add_files(self):
        paths = filedialog.askopenfilenames(title="공문 첨부파일 선택",initialdir=self.config.get('downloads',''),filetypes=FILE_TYPES)
        if paths:
            self.send("add", paths)

    def open_sheet(self):
        if self.config.get("auth_mode")=="browser":return self.send("browser_open",None)
        webbrowser.open("https://docs.google.com/spreadsheets/d/" + self.config["spreadsheet_id"] + "/edit")

    def start_watch(self):
        self.watching.set(True)
        self.commands.put(('watch',True))

    def startup(self):
        if self.config.get("auto_update", True):
            self.startup_gate = True
            self.commands.put(("startup_baseline", None))
            self.status.set("실행 전 새 버전 확인 중 · 업데이트가 있으면 먼저 설치합니다.")
            self.check_update()
            self.root.after(15000, self.startup_timeout)
            self.root.after(180000, lambda: self.startup_timeout(force=True))
        else:
            self.start_watch()
        self.root.after(60 * 60 * 1000, self.scheduled_update)

    def startup_timeout(self, force=False):
        if self.startup_gate and (force or not self.startup_copying) and not self.updating:
            self.update_status.set("업데이트 조회가 지연되어 현재 버전으로 시작합니다. 확인은 계속합니다.")
            self.release_startup()

    def release_startup(self):
        if self.startup_gate and not self.updating:
            self.startup_gate = False
            self.startup_copying = False
            self.start_watch()

    def activity(self, _=None):
        self.last_activity = time.monotonic()

    def update_folder(self):
        return self.config.get("update_url", "").strip() or UPDATE_BASE_URL

    def scheduled_update(self):
        if self.config.get("auto_update", True):
            self.check_update()
        if not getattr(self, "closing", False):
            self.root.after(60 * 60 * 1000, self.scheduled_update)

    def check_update(self, manual=False):
        if self.update_check_running or self.updating or getattr(self, "closing", False):
            return
        self.update_check_running = True
        folder = self.update_folder()
        self.update_status.set("새 버전 확인 중…")
        def check():
            try:
                if not folder.startswith("https://") and not (Path(folder)/"latest.json").is_file():
                    raise ValueError("업데이트 배포 파일이 없습니다: "+folder+" · 새 설치본의 install.cmd 또는 publish_update.cmd로 게시해야 합니다.")
                update = check_and_stage(folder, self.data, progress=lambda: self.events.put(("update_found", folder)))
                self.events.put(("update_check", {"folder": folder, "update": update}))
            except Exception as e:
                self.events.put(("update_check", {"folder": folder, "error": str(e)}))
        threading.Thread(target=check, daemon=True).start()

    def apply_when_idle(self):
        if not self.update_ready or getattr(self, "closing", False) or self.updating:
            return
        if not self.config.get("auto_update", True):
            self.update_status.set("새 버전 준비됨 · 설정에서 자동 업데이트를 켜면 적용합니다.")
            return
        jobs = self.selected()
        edited = len(jobs) == 1 and any(v.get().strip() != str(jobs[0].get(k, "")) for k, v in self.fields.items())
        dialogs = any(isinstance(w, tk.Toplevel) and w.winfo_exists() for w in self.root.winfo_children())
        if (self.busy or self.worker.working.is_set() or not self.commands.empty() or edited or dialogs or getattr(getattr(self,"workflow",None),"draft_active",False)
                or (not getattr(self, "startup_gate", False) and time.monotonic() - self.last_activity < 60)):
            self.root.after(5000, self.apply_when_idle)
            return
        self.updating = True
        self.update_ready["_startup"] = getattr(self, "startup_gate", False)
        self.update_status.set("공문 처리를 마무리하고 업데이트를 적용하는 중…")
        self.commands.put(("prepare_update", self.update_ready))

    def finish_update(self, update):
        try:
            launch_helper(update, self.data)
        except Exception as e:
            self.updating = False
            self.update_ready = None
            self.update_status.set("업데이트 보류 · " + str(e))
            self.commands.put(("watch", self.watching.get()))
            self.release_startup()
            return
        # Worker has acknowledged its pause; no document registration can start now.
        self.shutdown()

    def toggle_watch(self):
        if not self.send("watch", self.watching.get()):
            self.watching.set(not self.watching.get())

    def settings(self):
        if self.busy or self.worker.working.is_set() or self.updating or self.startup_gate:
            return messagebox.showinfo("처리 중", "진행 중인 작업이 끝난 뒤 설정해 주세요.")
        from settings_ui import open_settings
        open_settings(self)

    def close(self):
        if self.busy or self.worker.working.is_set() or self.worker.commands.qsize():
            messagebox.showinfo("처리 중", "저장·등록 중에는 종료할 수 없습니다. 작업 완료 후 종료해 주세요.")
            return
        self.shutdown()

    def scheduled_update_after_settings(self):
        if self.config.get("auto_update", True):
            self.check_update()

    def shutdown(self):
        self.commands.put(("quit", None))
        self.closing = True
        self.status.set("처리 이력을 닫고 종료하는 중입니다.")
        def finish():
            if self.worker.is_alive():
                self.root.after(100, finish)
            else:
                self.root.destroy()
        self.root.after(100, finish)


def main():
    if len(sys.argv)==3 and sys.argv[1]=='--test-update-fallback':
        try:
            from update_fallback_smoke import run
            run(sys.argv[2])
        except Exception:
            import traceback
            Path(sys.argv[2]+'.error.txt').write_text(traceback.format_exc(),encoding='utf-8')
            sys.exit(1)
        return
    if len(sys.argv)==3 and sys.argv[1]=='--test-program-storage':
        from program_storage_smoke import run
        run(sys.argv[2]);return
    if len(sys.argv)==3 and sys.argv[1]=='--test-taskbar':
        try:
            from taskbar_smoke import run
            run(sys.argv[2])
        except Exception:
            import traceback
            Path(sys.argv[2]+'.error.txt').write_text(traceback.format_exc(),encoding='utf-8')
            sys.exit(1)
        return
    ocr_test_data=None
    if '--ocr-data' in sys.argv:
        index=sys.argv.index('--ocr-data');ocr_test_data=sys.argv[index+1]
        from ocr_runtime import configure
        configure({'_auth_data_dir':ocr_test_data})
        del sys.argv[index:index+2]
    if len(sys.argv) in (3,4) and sys.argv[1]=='--prepare-ocr-runtime':
        from ocr_runtime import ensure
        source=Path(sys.argv[2]);config={'_auth_data_dir':sys.argv[3] if len(sys.argv)==4 else str(DATA),'ocr_source':sys.argv[2]}
        ensure(config,archive=source if not sys.argv[2].startswith('https://') and source.is_file() else None)
        return
    if len(sys.argv)==3 and sys.argv[1]=='--test-update-restart':
        from updater import apply_plan
        apply_plan(sys.argv[2],test_restart=True)
        return
    if len(sys.argv)==2 and sys.argv[1]=="--test-workflow":
        from workflow_smoke import run
        run()
        return
    if len(sys.argv)==3 and sys.argv[1]=="--test-browser-startup":
        from browser_launch_check import check
        Path(sys.argv[2]).write_text(json.dumps(check()),encoding="utf-8")
        return
    if len(sys.argv)==5 and sys.argv[1]=="--test-online-update":
        from updater import check_and_stage,file_hash
        update=check_and_stage(sys.argv[2],sys.argv[3],current="0.0.0")
        Path(sys.argv[4]).write_text(json.dumps({"version":update["manifest"]["version"],"sha256":file_hash(update["staged"])}),encoding="utf-8")
        return
    if len(sys.argv)==3 and sys.argv[1]=="--prepare-install":
        from local_setup import prepare
        try:prepare(sys.argv[2],DATA)
        except Exception as error:
            DATA.mkdir(parents=True,exist_ok=True)
            (DATA/"install-error.txt").write_text(str(error),encoding="utf-8")
            raise SystemExit(1)
        return
    if len(sys.argv)==3 and sys.argv[1]=="--prepare-spacing-model":
        from korean_spacing import model_path
        try:model_path({"_auth_data_dir":str(DATA),"model_source":sys.argv[2]})
        except Exception as error:
            DATA.mkdir(parents=True,exist_ok=True)
            (DATA/"spacing-setup-error.txt").write_text(str(error),encoding="utf-8")
            raise SystemExit(1)
        return
    if len(sys.argv)==4 and sys.argv[1]=="--test-clean":
        from ocr_excerpt import clean
        values=json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        result=clean(values["text"],values["config"],values.get("start",2))
        Path(sys.argv[3]).write_text(json.dumps(result,ensure_ascii=False),encoding="utf-8")
        return
    if len(sys.argv)==4 and sys.argv[1]=="--test-zip":
        target=Path(sys.argv[3])
        store=Store(target.parent/"zip-test.sqlite")
        try:
            job,_=ingest(sys.argv[2],store)
            target.write_text(json.dumps({"count":len(job["files"]),"title":job["title"],"files":[Path(f["path"]).name for f in job["files"]]},ensure_ascii=False),encoding="utf-8")
        finally:store.db.close()
        return
    if len(sys.argv)==4 and sys.argv[1]=="--test-ocr":
        from PIL import Image
        from ocr import recognize_image
        with Image.open(sys.argv[2]) as image:
            text,confidence = recognize_image(image)
        Path(sys.argv[3]).write_text(json.dumps({"text":text,"confidence":confidence},ensure_ascii=False),encoding="utf-8")
        return
    if len(sys.argv) == 3 and sys.argv[1] == "--apply-update":
        raise SystemExit(helper_main(sys.argv[2]))
    if len(sys.argv) == 3 and sys.argv[1] == "--test-apply-update":
        raise SystemExit(helper_main(sys.argv[2], launch=False))
    mutex = None
    if os.name == "nt" and '--self-test' not in sys.argv:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        ctypes.windll.kernel32.CreateMutexW.restype = ctypes.c_void_p
        mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\SeoulDocumentRegister-v1")
        if ctypes.windll.kernel32.GetLastError() == 183:
            ctypes.windll.user32.MessageBoxW(None, "프로그램이 이미 실행 중입니다.", "서울센터 공문 수신등록", 0)
            return
    try:
        from app_icon import prepare_process_identity,prepare_desktop_identity
        prepare_process_identity()
        if getattr(sys,'frozen',False) and '--self-test' not in sys.argv and '--test-restart' not in sys.argv:
            try:prepare_desktop_identity(DATA,sys.executable)
            except Exception as error:
                DATA.mkdir(parents=True,exist_ok=True)
                (DATA/'taskbar-setup-error.txt').write_text(str(error),encoding='utf-8')
        from file_drop import create_root
        root = create_root()
        if "--self-test" in sys.argv:
            import ssl
            import certifi
            ssl.create_default_context(cafile=certifi.where())
            import tempfile
            import olefile
            from pypdf import PdfReader
            from google.oauth2 import service_account
            from google.auth.transport.requests import AuthorizedSession
            from google_auth_oauthlib.flow import InstalledAppFlow
            from lxml import etree
            import websocket
            import win32clipboard
            import pythoncom
            from win32com.client import DispatchEx
            from hwp_documents import catalog
            from hwp_fidelity import records
            assert list(records(b""))==[] and websocket.__version__
            from outgoing import OUTGOING_TABS
            from stamp_ledger import STAMP_TAB
            assert len(OUTGOING_TABS)==3 and etree.fromstring(b"<test/>").tag=="test"
            import pypdfium2
            from ocr import run_engine, runtime_path
            import subprocess
            runtime=runtime_path(prepare=False)
            if ocr_test_data:assert runtime is not None
            if runtime:
                assert (runtime / 'tessdata' / 'kor.traineddata').is_file()
                from ocr import run_native
                check=run_native([str(runtime/'tesseract.exe'),'--list-langs','--tessdata-dir',str(runtime/'tessdata')],runtime,20)
                assert check.returncode==0 and 'kor' in check.stdout and 'eng' in check.stdout
            root.withdraw()
            with tempfile.TemporaryDirectory() as temporary:
                app = App(root, temporary, smoke=True)
                root.update_idletasks()
                root.update()
                assert app.tree.winfo_exists()
                assert app.file_drop is not None and root.tk.call("package","present","tkdnd")
                assert len(app.fields) == 6
                app.commands.put(("quit", None))
                app.worker.join(timeout=5)
                assert not app.worker.is_alive()
                root.destroy()
        else:
            receipt=Path(sys.argv[2]).resolve() if len(sys.argv)>=3 and sys.argv[1]=='--after-update' else None
            test_restart=receipt is not None and '--test-restart' in sys.argv
            try:
                app=App(root,receipt.parent/'restart-test-data' if test_restart else DATA,smoke=test_restart)
            except Exception as error:
                if receipt:receipt.write_text(json.dumps({'status':'error','message':str(error)},ensure_ascii=False),encoding='utf-8')
                raise
            if receipt:
                def ready():
                    if not app.worker.is_alive():return
                    root.deiconify()
                    root.lift()
                    temporary=receipt.with_suffix('.part')
                    temporary.write_text(json.dumps({'status':'ready','version':VERSION,'pid':os.getpid()}),encoding='utf-8')
                    os.replace(temporary,receipt)
                    if test_restart:root.after(1000,app.shutdown)
                root.after(500,ready)
            root.mainloop()
    finally:
        if mutex:
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(mutex))


if __name__ == "__main__":
    main()
