"""Build-time closure of normal and delayed PE imports; never borrow developer PATH DLLs."""
from pathlib import Path

SYSTEM_DLLS=set(('kernel32 kernelbase ntdll user32 gdi32 gdi32full advapi32 shell32 ole32 oleaut32 combase rpcrt4 ws2_32 mswsock crypt32 secur32 ncrypt bcrypt bcryptprimitives normaliz wldap32 dnsapi iphlpapi netapi32 winmm winhttp wininet psapi userenv version shlwapi uxtheme dwmapi setupapi cfgmgr32 powrprof authz cryptbase wintrust imagehlp comdlg32 comctl32 imm32 usp10 dbghelp ucrtbase').split())


def collect(library,roots=('tesseract.exe',),pe_factory=None):
    if pe_factory is None:
        import pefile
        pe_factory=pefile.PE
    library=Path(library);available={p.name.lower():p for p in library.iterdir() if p.suffix.lower() in ('.exe','.dll')}
    pending=list(roots);selected={}
    while pending:
        name=pending.pop().lower()
        if name in selected:continue
        if name not in available:raise ValueError('OCR 필수 DLL이 빌드 환경에 없습니다: '+name)
        path=available[name];selected[name]=path
        with pe_factory(str(path)) as pe:
            for kind in ('DIRECTORY_ENTRY_IMPORT','DIRECTORY_ENTRY_DELAY_IMPORT'):
                for block in getattr(pe,kind,[]):
                    dependency=block.dll.decode('ascii').lower()
                    if dependency.startswith(('api-ms-win-','ext-ms-win-')) or dependency.removesuffix('.dll') in SYSTEM_DLLS:continue
                    if dependency not in available:raise ValueError(path.name+'의 필수 DLL이 없습니다: '+dependency)
                    pending.append(dependency)
    return selected
