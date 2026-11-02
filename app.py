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

from core import CATEGORIES, EXTENSIONS, ROOT, SHEET_ID, TAB, Sheets, Store, ingest, process
from release_info import VERSION,UPDATE_BASE_URL
from updater import check_and_stage, launch_helper, helper_main
from queue_visibility import missing_download_source

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

    def emit(self, kind, data):
        self.events.put((kind, data))

    def run(self):
        self.store = Store(self.dbpath)
        while True:
            try:
                cmd, data = self.commands.get(timeout=1)
                self.working.set()
                try:
                    if cmd == "quit":
                        self.store.db.close()
                        return
                    if cmd == "config":
                        self.config = data.copy()
                        self.watch = False
                    elif cmd == "watch":
                        self.watch = bool(data)
                        self.seen = self.files() if self.watch else {}
                        resume = Path(self.dbpath).parent / "watch-resume.json"
                        if resume.is_file():
                            try:
                                saved = json.loads(resume.read_text(encoding="utf-8"))
                                if self.watch and saved.get("downloads") == self.config["downloads"] and time.time() - saved.get("time", 0) < 600:
                                    self.seen = {p: tuple(v) for p, v in saved.get("seen", {}).items()}
                            finally:
                                resume.unlink(missing_ok=True)
                        self.pending = {}
                        self.emit("status", "다운로드 감시 중 · 새로 저장되는 파일부터 확인합니다." if self.watch else "다운로드 감시 중지")
                    elif cmd == "startup_baseline":
                        resume = Path(self.dbpath).parent / "watch-resume.json"
                        saved = {}
                        if resume.exists():
                            try:
                                saved = json.loads(resume.read_text(encoding="utf-8"))
                            except ValueError:
                                pass
                        valid = saved.get("downloads") == self.config["downloads"] and time.time() - saved.get("time", 0) < 600
                        self.seen = {p: tuple(v) for p,v in saved.get("seen", {}).items()} if valid else self.files()
                        resume.write_text(json.dumps({"watch": saved.get("watch", True) if valid else True,
                                                      "downloads": self.config["downloads"], "time": time.time(), "seen": self.seen}, ensure_ascii=False), encoding="utf-8")
                    elif cmd == "prepare_update":
                        resume = Path(self.dbpath).parent / "watch-resume.json"
                        restart_watch = self.watch
                        if data.get("_startup") and resume.exists():
                            restart_watch = json.loads(resume.read_text(encoding="utf-8")).get("watch", True)
                        resume.write_text(json.dumps({"watch": restart_watch, "downloads": self.config["downloads"],
                                                      "time": time.time(), "seen": self.seen}, ensure_ascii=False), encoding="utf-8")
                        self.watch = False
                        self.emit("update_quiesced", data)
                    elif cmd == "add":
                        for path in data:
                            self.add(path)
                    elif cmd == "save":
                        self.store.put(data)
                    elif cmd == "hide_list":
                        self.hide_list(data)
                    elif cmd == "google_login":
                        config = {**self.config, **data, "auth_mode": "oauth"}
                        self.emit("status", "브라우저의 Google 로그인 창에서 편집 권한이 있는 계정을 선택해 주세요.")
                        result = google_login.login(config)
                        self.config = config
                        self.emit("google_login_success", {"config": config, "message": result})
                    elif cmd == "browser_login":
                        from browser_sheets import BrowserUI
                        config={**self.config,"auth_mode":"browser"}
                        BrowserUI(config).open_login()
                        self.config=config
                        self.emit("google_login_success",{"config":config,"message":"일반 브라우저에서 직접 로그인해 시트가 보이는지 확인하세요. 그 브라우저 창을 모두 닫은 뒤 프로그램의 ‘연결 확인’을 눌러 주세요. Cloud/API 설정은 필요하지 않습니다."})
                    elif cmd == "browser_open":
                        from browser_sheets import BrowserUI
                        ui=BrowserUI(self.config)
                        if ui.available()[0] is not None:ui.connect().close()
                        else:ui.open_login()
                    elif cmd == "google_logout":
                        if self.config.get("auth_mode")=="browser":
                            self.emit("status","전용 시트 브라우저의 Google 계정 메뉴에서 로그아웃해 주세요. 브라우저 로그인 상태는 Google이 관리합니다.")
                        else:
                            google_login.logout(self.config)
                            self.emit("google_logout_success", None)
                    elif cmd == "outgoing_save":
                        from outgoing import merge_edit
                        job=self.store.get(data["hash"])
                        if not job:raise ValueError("해당 문서 이력을 찾지 못했습니다.")
                        if not job.get("sheet_attempted") and not job.get("receipt_done"):
                            if not job.get("files_started"):job.update(data.get("receipt_fields",{}))
                            elif data.get("receipt_fields",{}).get("receipt_type"):job["receipt_type"]=data["receipt_fields"]["receipt_type"]
                        merge_edit(job,data["outgoing"])
                        self.store.put(job)
                        self.emit("job",job)
                        self.emit("outgoing_saved",{"hash":job["hash"],"template":job["outgoing"].get("template_path","")})
                    elif cmd == "draft_source":
                        saved=self.store.get(data["file"]["hash"])
                        text=saved.get("text","") if saved and saved.get("text_file_hash",saved["hash"])==data["file"]["hash"] else ""
                        if not text:
                            from core import extract
                            text=extract(data["file"]["path"],progress=lambda t:self.emit("status",t))
                        self.emit("draft_source_result",{**data,"text":text})
                    elif cmd == "ocr_clean":
                        from ocr_excerpt import clean
                        try:
                            result=clean(data["text"],self.config,data["start"],data["attachments"],data["spacing"])
                            self.emit("ocr_clean_result",{**data,**result})
                        except Exception as error:self.emit("ocr_clean_result",{**data,"error":str(error)})
                    elif cmd == "draft_export":
                        from outgoing import merge_edit
                        from document_draft import fill_hwpx,field_values,template_copy
                        job=self.store.get(data["hash"])
                        if not job:raise ValueError("문서 이력을 찾지 못했습니다.")
                        merge_edit(job,data["outgoing"])
                        out=job["outgoing"]
                        if data.get("native_hwp"):
                            from hwp_documents import export_hwp,style
                            export_hwp(style(out["template_id"]),out,data["path"])
                        elif data["copy_only"]:
                            template_copy(out["template_path"],data["path"])
                        else:
                            fill_hwpx(out["template_path"],data["path"],field_values(job,out))
                        self.store.put(job)
                        self.emit("draft_exported",{"path":data["path"],"copy_only":data["copy_only"],"open_after":data.get("native_hwp",False),"body_paste_needed":out.get("hwp_body_paste_needed",False) if data.get("native_hwp") else False})
                    elif cmd == "title_prepare":
                        from title_tools import suggestions, agency_key, pdf_preview
                        from core import extract
                        file = data["file"]
                        saved = self.store.get(file["hash"])
                        text = saved.get("text", "") if saved and saved.get("text_file_hash",saved["hash"])==file["hash"] else ""
                        if not text:
                            try:
                                text = extract(file["path"], progress=lambda t: self.emit("status", t))
                            except ValueError:
                                text = ""
                        agency = agency_key(text)
                        preferred = self.config.get("title_preferences", {}).get(agency, "header")
                        image, count = pdf_preview(file["path"], data["page"]) if Path(file["path"]).suffix.lower() == ".pdf" else (None, 1)
                        self.emit("title_result", {"kind": "prepare", "owner": data["owner"], "request": data["request"],
                                                   "text": text, "image": image, "page_count": count, "agency": agency,
                                                   "options": suggestions(text, file["path"], preferred)})
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
                        if self.config.get("auto") and primary.get("routing") and primary.get("category") in CATEGORIES and primary.get("recipient"):
                            self.execute(primary)
                    elif cmd == "route_tree":
                        from folder_naming import route_tree
                        self.emit("route_result", {**data,"records":route_tree(self.config["root"],data["agency"])})
                    elif cmd == "route_apply":
                        from folder_naming import preflight
                        job = self.store.get(data["hash"])
                        if not job or job["state"] in ("완료","묶음 처리 완료","묶음에 포함"):
                            raise ValueError("완료된 공문의 저장 위치는 여기서 변경할 수 없습니다.")
                        old_targets = {t["relative"]:t for t in job.get("routing",{}).get("targets",[])}
                        if job.get("files_started"):
                            before = {(t["relative"],t["template"]) for t in old_targets.values()}
                            after = {(t["relative"],t["template"]) for t in data["routing"]["targets"]}
                            if before != after or job.get("routing",{}).get("children",[])!=data["routing"].get("children",[]) or job.get("routing",{}).get("agency")!=data["routing"]["agency"]:
                                raise ValueError("이미 파일 저장을 시작한 공문입니다. 기존 위치·이름으로 재시도해 주세요.")
                        if job.get("sheet_attempted") and job.get("routing",{}).get("agency") != data["routing"]["agency"]:
                            raise ValueError("등록을 시도한 공문의 종류는 변경할 수 없습니다.")
                        for target in data["routing"]["targets"]:
                            old = old_targets.get(target["relative"],{})
                            if old.get("template") == target["template"]:
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
                        self.emit("route_applied",{"hash":job["hash"],"templates":self.config["route_templates"],"document_template":job.get("outgoing",{}).get("template_path","")})
                        if data["execute"]:
                            self.execute(job)
                    elif cmd == "process":
                        for job in data:
                            self.execute(job)
                    elif cmd == "test":
                        Sheets(self.config).snapshot()
                        exists = Path(self.config["root"]).is_dir()
                        missing = [p for p in dict.fromkeys(p for paths in CATEGORIES.values() for p in paths)
                                   if not (Path(self.config["root"]) / p).is_dir()]
                        self.emit("notice", "구글 시트 읽기 연결 성공.\n" + ("공유폴더와 저장 하위폴더 확인 완료." if exists and not missing else "공유폴더 또는 저장 하위폴더에 접근할 수 없습니다.\n" + "\n".join(missing)))
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
                    self.working.clear()
                    self.emit("idle", None)
            except queue.Empty:
                pass
            if time.monotonic()-self.last_list_sync>=2:
                self.last_list_sync=time.monotonic()
                self.sync_sources()
            if self.watch:
                self.working.set()
                try:
                    self.scan()
                except Exception as e:
                    self.watch = False
                    self.emit("error", f"다운로드 감시 중단: {e}")
                finally:
                    self.working.clear()

    def hide_list(self,keys):
        for key in keys:
            job=self.store.get(key)
            if job:
                job["list_hidden"]=True
                self.store.put(job);self.emit("job",job)
        self.emit("status",f"{len(keys)}개 항목을 목록에서 삭제했습니다. 원본 파일과 등록 이력은 그대로 보관합니다.")

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
            if p.suffix.lower() in EXTENSIONS and p.is_file():
                try:
                    s = p.stat()
                    result[str(p)] = (s.st_size, s.st_mtime_ns)
                except OSError:
                    pass
        return result

    def scan(self):
        now = self.files()
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
        self.emit("status", "본문 확인 중: " + Path(path).name)
        try:
            job, new = ingest(path, self.store, progress=lambda text: self.emit("status", text))
        except Exception as error:
            if Path(path).suffix.lower()!=".zip":raise
            self.emit("error",Path(path).name+": "+str(error))
            return
        if not new and (job["state"] == "제외" or job.get("list_hidden")):
            if job["state"] == "제외":job["state"] = "확인 필요"
            job.pop("list_hidden",None)
            self.store.put(job)
        if new:
            job["recipient"] = self.config.get("recipient", "")
            self.store.put(job)
        self.emit("job", job)
        self.emit("status", "확인 목록에 추가했습니다. 첨부에서 제목을 골라 주세요." if new else "이미 확인한 파일입니다. 기존 처리 이력을 표시합니다.")

    def execute(self, job):
        # A queued UI snapshot must not undo a completed/attempted database transaction.
        saved = self.store.get(job["hash"])
        if saved and (saved["state"] in ("완료", "묶음 처리 완료", "묶음에 포함") or saved.get("sheet_attempted")):
            job = saved
        self.emit("status", "저장·접수등록 중: " + job["title"])
        try:
            if not job.get("routing") and not job.get("receipt_done"):
                raise ValueError("‘저장 위치 선택’에서 공문 종류와 저장할 폴더를 먼저 골라 주세요.")
            result = process(job, self.config, self.store)
            self.emit("job", result)
            self.emit("completed", result)
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
        root.title(f"서울센터 공문 수신등록 v{VERSION}")
        root.geometry("1080x850")
        root.minsize(900, 730)
        root.configure(bg="#f3f5f8")
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("Treeview", rowheight=31, font=("맑은 고딕", 10))
        style.configure("Treeview.Heading", font=("맑은 고딕", 10, "bold"))
        style.configure("TButton", padding=(12, 7), font=("맑은 고딕", 10))
        style.configure("TLabel", font=("맑은 고딕", 10))
        outer = ttk.Frame(root, padding=20)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="공문 저장부터 수신등록까지", font=("맑은 고딕", 20, "bold")).pack(anchor="w")
        ttk.Label(outer, text="첨부파일을 다운로드하세요. 등록번호를 확인하고 인쇄물에 직접 적으시면 됩니다.").pack(anchor="w", pady=(5, 14))
        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x")
        for text, command in [("파일 추가", self.add_files), ("설정", self.settings), ("연결 확인", lambda: self.send("test", None)),
                              ("구글 시트 열기", self.open_sheet), ("업데이트 확인", lambda: self.check_update(manual=True))]:
            ttk.Button(toolbar, text=text, command=command).pack(side="left", padx=(0, 6))
        self.watching = tk.BooleanVar(value=False)
        ttk.Checkbutton(toolbar, text="다운로드 감시", variable=self.watching, command=self.toggle_watch).pack(side="right")
        authbar = ttk.Frame(outer)
        authbar.pack(fill="x", pady=(8, 0))
        ttk.Button(authbar, text="시트 로그인 (브라우저)", command=lambda:self.send("browser_login",None)).pack(side="left")
        ttk.Button(authbar, text="API 로그인 (선택)", command=self.google_sign_in).pack(side="left",padx=4)
        ttk.Button(authbar, text="이 PC 연결 해제", command=lambda: self.send("google_logout", None)).pack(side="left", padx=6)
        self.auth_status = tk.StringVar(value="브라우저 로그인 방식 · API 설정 불필요" if self.config.get("auth_mode")=="browser" else "본인 Google 계정 연결됨" if google_login.has_login(self.config) else "Google 연결 방식을 설정하세요.")
        ttk.Label(authbar, textvariable=self.auth_status).pack(side="left", padx=8)
        self.status = tk.StringVar(value="처음에는 설정에서 담당자를 지정하고 Google 로그인을 연결해 주세요.")
        ttk.Label(outer, textvariable=self.status, foreground="#1c5295", wraplength=1000).pack(fill="x", pady=12)
        self.update_status = tk.StringVar(value=f"v{VERSION} · 자동 업데이트 확인 대기")
        ttk.Label(outer, textvariable=self.update_status, foreground="#667085").pack(anchor="w", pady=(0, 8))
        table = ttk.Frame(outer)
        table.pack(fill="both", expand=True)
        cols = ("state", "category", "title", "number")
        self.tree = ttk.Treeview(table, columns=cols, show="headings", selectmode="extended", height=10)
        for c, label, width in [("state", "처리 상태", 155), ("category", "분류", 160), ("title", "공문 제목 / 파일명", 430), ("number", "수신 / 발신 번호", 190)]:
            self.tree.heading(c, text=label)
            self.tree.column(c, width=width, minwidth=65, stretch=c == "title")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.select)
        form = ttk.LabelFrame(outer, text="선택한 공문 확인", padding=12)
        form.pack(fill="x", pady=(14, 8))
        self.fields = {k: tk.StringVar() for k in ("title", "category", "recipient", "date", "issuer", "receipt_type")}
        ttk.Label(form, text="제목").grid(row=0, column=0, sticky="w", padx=(0, 10))
        ttk.Entry(form, textvariable=self.fields["title"]).grid(row=0, column=1, columnspan=5, sticky="ew", pady=4)
        ttk.Label(form, text="분류").grid(row=1, column=0, sticky="w")
        from folder_naming import AGENCIES
        ttk.Combobox(form, textvariable=self.fields["category"], values=list(AGENCIES), state="readonly", width=32).grid(row=1, column=1, sticky="ew")
        ttk.Label(form, text="수신자").grid(row=1, column=2, padx=(12, 5))
        ttk.Entry(form, textvariable=self.fields["recipient"], width=12).grid(row=1, column=3)
        ttk.Label(form, text="접수일").grid(row=1, column=4, padx=(12, 5))
        ttk.Entry(form, textvariable=self.fields["date"], width=13).grid(row=1, column=5)
        ttk.Label(form, text="발신처").grid(row=2,column=0,sticky="w",pady=5)
        ttk.Entry(form,textvariable=self.fields["issuer"]).grid(row=2,column=1,sticky="ew")
        ttk.Label(form,text="수신대장").grid(row=2,column=2,sticky="e",padx=5)
        from core import RECEIPT_TABS
        ttk.Combobox(form,textvariable=self.fields["receipt_type"],values=list(RECEIPT_TABS),state="readonly").grid(row=2,column=3,columnspan=3,sticky="ew")
        self.outgoing_enabled=tk.BooleanVar(value=False)
        ttk.Checkbutton(form,text="발신도 함께 등록 (직인날인대장 포함)",variable=self.outgoing_enabled,command=self.toggle_outgoing).grid(row=3,column=0,columnspan=3,sticky="w",pady=5)
        ttk.Button(form,text="발신 정보·공문 작성 / OCR 복사",command=self.edit_outgoing).grid(row=3,column=3,columnspan=3,sticky="e")
        form.columnconfigure(1, weight=1)
        self.detail = tk.StringVar(value="공문을 선택하면 본문과 저장 결과를 확인할 수 있습니다.")
        ttk.Label(form, textvariable=self.detail, wraplength=960).grid(row=4, column=0, columnspan=6, sticky="w", pady=8)
        actions = ttk.Frame(outer)
        actions.pack(fill="x")
        for text, fn in [("첨부에서 제목 고르기", self.choose_title), ("저장 위치 선택", self.choose_routes),
                         ("선택 공문 저장·등록", self.register), ("수정 저장", self.save_edit), ("본문 / 저장 결과", self.preview), ("목록 삭제", self.ignore)]:
            ttk.Button(actions, text=text, command=fn).pack(side="left", padx=(0, 6))
        ttk.Label(outer, text="목록 삭제는 원본 파일·등록 이력을 보존합니다 · 다운로드에서 삭제·이동된 파일은 목록에서 자동 제외 · 다시 표시는 ‘파일 추가’", foreground="#667085").pack(anchor="w", pady=(10, 0))
        for job in self.jobs.values():
            self.render(job)
        self.worker = Worker(self.commands, self.events, self.config, self.data / "history.sqlite")
        self.worker.start()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(200, self.poll)
        if not smoke:
            root.after(100, self.startup)

    def render(self, job):
        key = job["hash"]
        self.jobs[key] = job
        hidden = job.get("list_hidden") or job["state"] in ("제외", "묶음에 포함", "묶음 처리 완료") or missing_download_source(job,self.config.get("downloads"))
        if hidden:
            if self.tree.exists(key):
                self.tree.delete(key)
            return
        numbers=job.get("number","")
        if job.get("outgoing",{}).get("enabled"):
            numbers=f"수신{numbers or '대기'} / 발신{job['outgoing'].get('number') or '대기'}"
        vals = (job["state"], job["category"] or "확인 필요", job["title"], numbers)
        if self.tree.exists(key):
            self.tree.item(key, values=vals)
        else:
            self.tree.insert("", 0, iid=key, values=vals)
        if key in self.tree.selection() and (self.busy or job.get("files_started")):
            self.select()

    def send(self, cmd, data):
        if getattr(self, "closing", False) or self.updating:
            return False
        if self.startup_gate:
            messagebox.showinfo("시작 준비 중", "새 버전 확인·설치가 끝나면 업무를 시작할 수 있습니다.")
            return False
        if self.busy or (hasattr(self, "worker") and self.worker.working.is_set()):
            messagebox.showinfo("처리 중", "진행 중인 작업이 끝난 뒤 다시 시도해 주세요.")
            return False
        self.busy = True
        self.commands.put((cmd, data))
        return True

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
                    self.status.set(data)
                    messagebox.showerror("확인이 필요합니다", data)
                elif kind == "notice":
                    messagebox.showinfo("연결 확인", data)
                elif kind == "completed":
                    out=data.get("outgoing",{})
                    detail=f"수신 {data['number']}"+(f" · 발신 {out.get('number','')} · 직인대장 등록 완료" if out.get("enabled") else " · 저장·수신등록 완료")
                    self.status.set(detail+" · "+data["title"])
                    self.root.bell()
                elif kind == "outgoing_saved":
                    self.config["document_template"]=data["template"]
                    self.persist_config()
                    self.tree.selection_set(data["hash"]);self.select()
                    self.status.set("발신 설정·초안을 저장했습니다. 저장·접수등록을 누르면 선택한 대장과 직인날인대장에 함께 등록합니다.")
                elif kind == "draft_source_result":
                    editor=getattr(self,"outgoing_editor",None)
                    if editor is not None:editor.deliver(data)
                elif kind == "ocr_clean_result":
                    editor=getattr(self,"ocr_text_editor",None)
                    if editor is not None:editor.deliver(data)
                elif kind == "draft_exported":
                    self.status.set("한글 문서를 새 파일로 저장했습니다: "+data["path"])
                    messagebox.showinfo("새 공문 저장",data["path"]+"\n\n원본 폼·스타일·문단 서식을 유지한 HWP를 엽니다. 기존 문단 안에서 수정해 주세요."+("\n프로그램에서 편집한 본문은 ‘본문 복사’로 복사해 한글의 필요한 문단에 넣어 주세요." if data.get("body_paste_needed") else ""))
                    if data["copy_only"] or data.get("open_after"):
                        try:os.startfile(data["path"])
                        except OSError:pass
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
                    self.tree.selection_set(data["hash"])
                    self.tree.see(data["hash"])
                    self.select()
                    self.status.set("선택한 제목을 적용했습니다. 분류·수신자·접수일을 확인한 뒤 저장·등록하세요.")
                    if not self.jobs[data["hash"]].get("routing"):
                        self.root.after(350,self.choose_routes)
                elif kind == "route_result":
                    dialog = getattr(self,"route_dialog",None)
                    if dialog is not None: dialog.deliver(data)
                elif kind == "route_applied":
                    self.config["route_templates"] = data["templates"]
                    if data.get("document_template"):self.config["document_template"]=data["document_template"]
                    self.persist_config()
                    self.tree.selection_set(data["hash"])
                    self.select()
        except queue.Empty:
            pass
        self.root.after(200, self.poll)

    def selected(self):
        return [self.jobs[k].copy() for k in self.tree.selection()]

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
        self.detail.set(f"{job['reason']}\n첨부 {len(job['files'])}개 · {Path(job['files'][0]['path']).name}")

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
        def copy_ocr(clean=False):
            from document_draft import reflow
            body=reflow(job.get("text","")) if clean else job.get("text","")
            dialog.clipboard_clear();dialog.clipboard_append(body);dialog.update_idletasks()
        ttk.Button(buttons,text="OCR 본문 복사 → 한글에 붙여넣기",command=copy_ocr).pack(side="left")
        ttk.Button(buttons,text="줄바꿈 정리하여 복사",command=lambda:copy_ocr(True)).pack(side="left",padx=6)
        def excerpt():
            from ocr_text_editor import OcrTextEditor
            OcrTextEditor(self,job.get("text",""),parent=dialog)
        ttk.Button(buttons,text="필요 본문 · 띄어쓰기 정리",command=excerpt).pack(side="left")
        text = tk.Text(dialog, wrap="word", font=("맑은 고딕", 11), padx=15, pady=15)
        text.pack(fill="both", expand=True)
        content = f"등록번호: {job.get('number', '')}\n상태: {job['state']}\n{job['reason']}\n\n첨부파일\n"
        content += "\n".join(f["path"] for f in job["files"])
        content += "\n\n저장 위치\n" + "\n".join(job.get("destinations", []))
        if job.get("routing"):
            content += "\n\n선택한 저장 규칙\n" + "\n".join(t["relative"]+" / "+t["template"] for t in job["routing"]["targets"])
        content += "\n\n읽은 본문\n" + (job["text"] or "본문을 읽지 못했습니다. 제목과 분류를 직접 입력할 수 있습니다.")
        text.insert("1.0", content)
        text.configure(state="disabled")

    def ignore(self):
        jobs = self.selected()
        if not jobs:
            return
        self.send("hide_list",[job["hash"] for job in jobs])

    def add_files(self):
        paths = filedialog.askopenfilenames(title="공문 첨부파일 선택", filetypes=[("공문 및 압축파일", "*.pdf *.hwp *.hwpx *.zip"), ("모든 파일", "*.*")])
        if paths:
            self.send("add", paths)

    def open_sheet(self):
        if self.config.get("auth_mode")=="browser":return self.send("browser_open",None)
        webbrowser.open("https://docs.google.com/spreadsheets/d/" + self.config["spreadsheet_id"] + "/edit")

    def start_watch(self):
        watch = True
        resume = self.data / "watch-resume.json"
        if resume.exists():
            try:
                watch = bool(json.loads(resume.read_text(encoding="utf-8")).get("watch", True))
            except (OSError, ValueError):
                pass
        self.watching.set(watch)
        self.toggle_watch()

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
        if (self.busy or self.worker.working.is_set() or not self.commands.empty() or edited or dialogs
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
        win = tk.Toplevel(self.root)
        win.title("업무용 PC 최초 설정")
        win.geometry("850x680")
        win.transient(self.root)
        win.grab_set()
        body = ttk.Frame(win, padding=20)
        body.pack(fill="both", expand=True)
        mode = tk.StringVar(value="브라우저 로그인 (API 설정 없음)" if self.config.get("auth_mode")=="browser" else "본인 Google 계정 로그인" if self.config.get("auth_mode") == "oauth" else "기존 서비스 계정")
        auth_files = {"브라우저 로그인 (API 설정 없음)":"","본인 Google 계정 로그인": self.config.get("oauth_client_path", ""), "기존 서비스 계정": self.config.get("key_path", "")}
        values = {k: tk.StringVar(value=str(self.config.get(k, ""))) for k in ("downloads", "root", "recipient", "spreadsheet_id", "tab", "update_url")}
        values["auth_file"] = tk.StringVar(value=auth_files[mode.get()])
        for i, (key, label) in enumerate([("downloads", "다운로드 폴더"), ("root", "공유 실적 폴더"), ("recipient", "기본 수신자(담당자)"), ("auth_file", "Google 로그인 설정(JSON)"), ("spreadsheet_id", "구글 시트 ID"), ("tab", "접수대장 탭"), ("update_url", "온라인 업데이트 주소")]):
            ttk.Label(body, text=label).grid(row=i, column=0, sticky="w", pady=8)
            ttk.Entry(body, textvariable=values[key], width=65).grid(row=i, column=1, sticky="ew", padx=10)
            if key in ("downloads", "root", "auth_file", "update_folder"):
                def choose(k=key):
                    path = filedialog.askopenfilename(filetypes=[("Google 인증 설정", "*.json")]) if k == "auth_file" else filedialog.askdirectory()
                    if path:
                        values[k].set(path)
                ttk.Button(body, text="선택", command=choose).grid(row=i, column=2)
        auto = tk.BooleanVar(value=bool(self.config.get("auto")))
        ttk.Label(body, text="Google 연결 방식").grid(row=7, column=0, sticky="w")
        auth_select = ttk.Combobox(body, textvariable=mode, values=list(auth_files), state="readonly")
        auth_select.grid(row=7, column=1, sticky="ew", padx=10)
        last_mode = [mode.get()]
        def mode_changed(_):
            auth_files[last_mode[0]] = values["auth_file"].get()
            values["auth_file"].set(auth_files[mode.get()])
            last_mode[0] = mode.get()
        auth_select.bind("<<ComboboxSelected>>", mode_changed)
        ttk.Checkbutton(body, text="제목 선택 후 분류·담당자가 확인된 공문은 자동 저장·등록", variable=auto).grid(row=8, column=0, columnspan=3, sticky="w", pady=(14, 5))
        ttk.Label(body, text="스캔 PDF는 PC 내 한국어 OCR로 읽습니다. 인식이 불확실한 문서는 확인 목록에 남깁니다.", wraplength=760).grid(row=9, column=0, columnspan=3, sticky="w")
        ttk.Label(body, text="‘시트 로그인’은 제어 기능 없는 일반 Edge/Chrome 창을 엽니다. 직접 로그인해 시트를 확인하고\n그 창을 모두 닫은 뒤 ‘연결 확인’을 누르세요. Cloud 프로젝트·JSON은 API 방식에서만 필요합니다.", wraplength=760).grid(row=10, column=0, columnspan=3, sticky="w", pady=12)
        auto_update = tk.BooleanVar(value=self.config.get("auto_update", True))
        ttk.Checkbutton(body, text="실행 시 새 버전 먼저 자동 설치 (설정과 접수 이력 유지)", variable=auto_update).grid(row=11, column=0, columnspan=3, sticky="w")
        ttk.Label(body, text="실행할 때 GitHub에서 최신 버전을 확인하고 내려받아 자동 설치합니다.\n업무용 PC의 GitHub 로그인·공유폴더 게시가 필요하지 않습니다.", wraplength=760).grid(row=12, column=0, columnspan=3, sticky="w", pady=8)
        def save():
            config = {**self.config, **{k: v.get().strip() for k, v in values.items() if k != "auth_file"}, "auto": auto.get(), "auto_update": auto_update.get()}
            config["auth_mode"] = "browser" if mode.get()=="브라우저 로그인 (API 설정 없음)" else "oauth" if mode.get() == "본인 Google 계정 로그인" else "service"
            config["oauth_client_path" if config["auth_mode"] == "oauth" else "key_path"] = values["auth_file"].get().strip()
            if auto.get() and not config["recipient"]:
                return messagebox.showerror("설정", "자동 처리를 켜려면 기본 수신자를 지정해 주세요.", parent=win)
            self.config = config
            self.update_ready = None
            self.persist_config()
            self.commands.put(("config", config))
            self.watching.set(False)
            self.status.set("설정을 저장했습니다. ‘연결 확인’ 후 다운로드 감시를 켜 주세요.")
            win.destroy()
            self.root.after(1000, self.scheduled_update_after_settings)
        from outgoing_editor import folder_settings_dialog
        ttk.Button(body, text="발신 6종 양식별 기본 저장 위치", command=lambda:folder_settings_dialog(self,win)).grid(row=13,column=0,columnspan=2,sticky="w")
        ttk.Button(body, text="설정 저장", command=save).grid(row=14, column=1, sticky="e", pady=12)
        body.columnconfigure(1, weight=1)

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
    if os.name == "nt":
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        ctypes.windll.kernel32.CreateMutexW.restype = ctypes.c_void_p
        mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\SeoulDocumentRegister-v1")
        if ctypes.windll.kernel32.GetLastError() == 183:
            ctypes.windll.user32.MessageBoxW(None, "프로그램이 이미 실행 중입니다.", "서울센터 공문 수신등록", 0)
            return
    try:
        root = tk.Tk()
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
            assert (runtime_path() / "tessdata" / "kor.traineddata").is_file()
            import subprocess
            check = subprocess.run([str(runtime_path() / "tesseract.exe"), "--list-langs", "--tessdata-dir", str(runtime_path() / "tessdata")], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
            assert check.returncode == 0 and b"kor" in check.stdout and b"eng" in check.stdout
            root.withdraw()
            with tempfile.TemporaryDirectory() as temporary:
                app = App(root, temporary, smoke=True)
                root.update_idletasks()
                root.update()
                assert app.tree.winfo_exists()
                assert len(app.fields) == 6
                app.commands.put(("quit", None))
                app.worker.join(timeout=5)
                assert not app.worker.is_alive()
                root.destroy()
        else:
            App(root)
            root.mainloop()
    finally:
        if mutex:
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(mutex))


if __name__ == "__main__":
    main()
