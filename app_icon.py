"""Use the same transparent document icon for the executable, app windows and shortcuts."""
import os
from pathlib import Path
import sys
import tkinter as tk


def apply_window_icon(root):
    base=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))/'assets'
    ico=base/'app_icon.ico';png=base/'app_icon.png'
    if os.name=='nt' and ico.is_file():root.iconbitmap(default=str(ico))
    elif png.is_file():
        root._document_icon=tk.PhotoImage(master=root,file=str(png));root.iconphoto(True,root._document_icon)
