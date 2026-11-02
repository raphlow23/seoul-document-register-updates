"""Launch an installed browser without inheriting frozen-app DLL search paths."""
from contextlib import contextmanager
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import threading

_launch_lock = threading.Lock()


@contextmanager
def system_dll_search():
    if os.name != "nt" or not getattr(sys, "frozen", False):
        yield
        return
    with _launch_lock:
        kernel = ctypes.windll.kernel32
        length = kernel.GetDllDirectoryW(0, None)
        value = ctypes.create_unicode_buffer(length + 1)
        kernel.GetDllDirectoryW(len(value), value)
        if not kernel.SetDllDirectoryW(None):
            raise OSError("브라우저 실행 환경을 준비하지 못했습니다.")
        try:
            yield
        finally:
            kernel.SetDllDirectoryW(value.value or None)


def launch(arguments, log):
    env = os.environ.copy()
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        root = os.path.normcase(os.path.abspath(bundled))
        env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep)
                                      if p and not os.path.normcase(os.path.abspath(p)).startswith(root + os.sep)
                                      and os.path.normcase(os.path.abspath(p)) != root)
    Path(log).parent.mkdir(parents=True, exist_ok=True)
    with Path(log).open("ab") as error_log, system_dll_search():
        return subprocess.Popen(arguments, env=env, cwd=str(Path(arguments[0]).parent),
                                stdout=subprocess.DEVNULL, stderr=error_log,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
