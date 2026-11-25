"""Paths recorded by receipt and outgoing saves, including optional copies."""
from pathlib import Path
from tkinter import filedialog, ttk
import tkinter as tk


def note_directory(job, scope, directory):
    record = {"scope": scope, "path": str(directory)}
    directories = job.setdefault("created_directories", [])
    if record not in directories:
        directories.append(record)


def make_directory(job, scope, directory):
    try:
        directory.mkdir()
    except FileExistsError:
        if not directory.is_dir():
            raise
    else:
        note_directory(job, scope, directory)


def saved_paths(job, scope):
    if scope == "receipt":
        return list(job.get("destinations", []))
    outgoing = job.get("outgoing", {})
    path = outgoing.get("hwp_path")
    if path and outgoing.get("number") and str(outgoing.get("hwp_saved_number")) == str(outgoing["number"]):
        return [path]
    return []


def result_text(job, scope):
    files = saved_paths(job, scope)
    roots = [item["path"] for key in ("storage_roots", "created_directories")
             for item in job.get(key, []) if item["scope"] == scope]
    copies = [item for item in job.get("additional_saves", []) if item["scope"] == scope]
    copy_paths = {item.get("folder") for item in copies}
    groups = []
    roots = list(dict.fromkeys(path for path in roots if path not in copy_paths))
    if roots:
        groups.append("만든 폴더\n" + "\n".join(roots))
    if files:
        groups.append("저장한 파일\n" + "\n".join(dict.fromkeys(map(str, files))))
    for copy in copies:
        paths = copy["files"]
        if copy.get("folder"):
            groups.append("추가로 만든 폴더\n" + copy["folder"])
        if paths:
            groups.append("추가로 저장한 파일\n" + "\n".join(paths))
    pending = job.get("additional_save_pending") or {}
    if pending.get("scope") == scope and pending.get("items"):
        parent = Path(pending["items"][0]["target"]).parent
        groups.append(f"추가 저장 진행 위치\n{parent}\n중단된 경우 같은 위치와 폴더 이름을 선택해 이어서 저장하세요.")
    return "\n\n".join(groups)


def choose_additional(app, job, scope):
    base = filedialog.askdirectory(title="사본을 저장할 위치 선택", parent=app.root, mustexist=True)
    if not base:
        return
    dialog = tk.Toplevel(app.root)
    dialog.title("다른 위치에도 저장")
    dialog.transient(app.root)
    frame = ttk.Frame(dialog, padding=16)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="추가 저장 위치", font=("맑은 고딕", 12, "bold")).pack(anchor="w")
    subfolder = tk.BooleanVar(dialog, False)
    existing = next((Path(r["path"]).name for r in job.get("storage_roots", []) if r["scope"] == scope), "")
    from folder_naming import safe_component
    title = job.get("outgoing", {}).get("title", "") if scope == "outgoing" else job["title"]
    name = tk.StringVar(dialog, existing or safe_component(title))
    selected = tk.StringVar(dialog)
    entry = ttk.Entry(frame, textvariable=name, width=52)
    def refresh(*_):
        entry.configure(state="normal" if subfolder.get() else "disabled")
        selected.set(str(Path(base) / name.get().strip()) if subfolder.get() else str(Path(base)))
        save.configure(state="normal" if not subfolder.get() or name.get().strip() else "disabled")
    for label, value in (("선택한 위치에 파일만 저장", False), ("새 폴더를 만들어 그 안에 저장", True)):
        ttk.Radiobutton(frame, text=label, variable=subfolder, value=value, command=refresh).pack(anchor="w", pady=2)
    entry.pack(fill="x", pady=3)
    ttk.Label(frame, textvariable=selected, wraplength=480).pack(anchor="w", pady=8)
    actions = ttk.Frame(frame)
    actions.pack(fill="x")
    def submit():
        owner = job.get("outgoing", {}) if scope == "outgoing" else job
        if app.send("additional_save", {"hash":job["hash"], "scope":scope, "expected_id":owner.get("id"),
                    "base":base, "folder_name":name.get().strip() if subfolder.get() else ""}):
            dialog.destroy()
    ttk.Button(actions, text="취소", command=dialog.destroy).pack(side="left")
    save = ttk.Button(actions, text="이 위치에 사본 저장", command=submit, style="Accent.TButton")
    save.pack(side="right")
    name.trace_add("write", refresh)
    refresh()
    dialog.grab_set()
