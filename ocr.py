"""Offline Korean/English OCR for scanned PDF pages, preserving the original PDF."""
from __future__ import annotations
import csv
import io
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def runtime_path(prepare=True):
    bundled=Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "ocr-runtime"
    if not getattr(sys,'frozen',False) and (bundled/'tesseract.exe').is_file():return bundled
    from ocr_runtime import ensure,installed
    return ensure() if prepare else installed()


def run_engine(image, mode, timeout=120):
    runtime = runtime_path()
    exe = runtime / "tesseract.exe"
    if not exe.is_file():
        raise ValueError("한국어 OCR 엔진이 없습니다. OCR 포함 배포판을 설치해 주세요.")
    args = [str(exe), str(image), "stdout", "--tessdata-dir", str(runtime / "tessdata")]
    args += ["--psm", "0"] if mode == "orientation" else ["-l", "kor+eng", "--oem", "1", "--psm", "6", "--dpi", "300", "-c", "tessedit_create_tsv=1"]
    env = os.environ.copy()
    env["PATH"] = str(runtime) + os.pathsep + env.get("PATH", "")
    env["OMP_THREAD_LIMIT"] = "2"
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
                            encoding="utf-8", errors="replace", env=env,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode and mode != "orientation":
        raise ValueError("한국어 OCR 처리에 실패했습니다. PDF 손상 여부와 OCR 엔진 설치를 확인해 주세요.")
    return result.stdout if result.returncode == 0 else ""


def parse_tsv(text):
    lines = {}
    weighted, chars = 0.0, 0
    for word in csv.DictReader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE):
        token = (word.get("text") or "").strip()
        try:
            confidence = float(word.get("conf", -1))
        except (TypeError, ValueError):
            continue
        if token and confidence >= 0:
            key = tuple(word.get(k, "") for k in ("page_num", "block_num", "par_num", "line_num"))
            lines.setdefault(key, []).append(token)
            count = len(token)
            weighted += confidence * count
            chars += count
    return "\n".join(" ".join(words) for words in lines.values()), weighted / chars if chars else 0.0


def recognize_image(image):
    from PIL import ImageOps, Image
    with tempfile.TemporaryDirectory(prefix="seoul-ocr-") as temporary:
        source = Path(temporary) / "page.png"
        gray = ImageOps.autocontrast(image.convert("L"), cutoff=0.2)
        if gray.width < 1600:
            scale = min(1800/gray.width, (15_000_000/max(1,gray.width*gray.height))**0.5, 10000/max(gray.width,gray.height))
            if scale > 1:
                enlarged = gray.resize((round(gray.width*scale), max(1, round(gray.height*scale))), Image.Resampling.LANCZOS)
                gray.close()
                gray = enlarged
        try:
            gray.save(source)
            orientation = run_engine(source, "orientation", timeout=30)
            rotate = re.search(r"^Rotate:\s*(90|180|270)\s*$", orientation, re.M)
            certainty = re.search(r"^Orientation confidence:\s*([0-9.]+)", orientation, re.M)
            if rotate and certainty and float(certainty.group(1)) >= 15:
                turned = gray.rotate(-int(rotate.group(1)), expand=True)
                try:
                    turned.save(source)
                finally:
                    turned.close()
            return parse_tsv(run_engine(source, "text"))
        finally:
            gray.close()


def read_pdf(path, progress=None):
    import pypdfium2 as pdfium
    paragraphs, pages = [], []
    with pdfium.PdfDocument(str(path)) as doc:
        if len(doc) > 100:
            raise ValueError("100쪽을 넘는 PDF입니다. 공문과 지침 파일을 나누어 확인해 주세요.")
        for i in range(len(doc)):
            if progress:
                progress(f"PDF 본문·한국어 OCR 확인 중 · {i+1}/{len(doc)}쪽")
            page = doc[i]
            try:
                textpage = page.get_textpage()
                try:
                    native = textpage.get_text_range().strip()
                finally:
                    textpage.close()
                if len(re.sub(r"\s", "", native)) >= 25:
                    paragraphs.append(native)
                    pages.append({"page": i+1, "method": "text", "confidence": 100.0})
                    continue
                width, height = page.get_size()
                scale = min(300 / 72, (15_000_000 / max(1, width * height)) ** 0.5)
                bitmap = page.render(scale=scale, fill_color=(255, 255, 255, 255))
                try:
                    image = bitmap.to_pil().copy()
                finally:
                    bitmap.close()
                try:
                    text, confidence = recognize_image(image)
                finally:
                    image.close()
                paragraphs.append(text)
                pages.append({"page": i+1, "method": "ocr", "confidence": round(confidence, 1)})
            finally:
                page.close()
    scanned = [x for x in pages if x["method"] == "ocr"]
    meta = {"pages": pages, "ocr_pages": len(scanned),
            "confidence": round(sum(x["confidence"] for x in scanned) / len(scanned), 1) if scanned else 100.0,
            "needs_review": any(x["confidence"] < 85 for x in scanned)}
    return "\n\n".join(paragraphs), meta
