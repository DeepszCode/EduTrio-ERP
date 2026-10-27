"""
ocr_service.py - SMART ERP
Text extraction (PDF / image) + certificate field parsing + validation.
Every parser returns a dict whose keys match the columns in your SQL tables.
"""
import copy
import io
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import cv2
import numpy as np
import pymupdf as fitz
import pytesseract
from dotenv import load_dotenv
from PIL import Image, ImageOps

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
LOCAL_TESSDATA = BASE_DIR / "tessdata"
if LOCAL_TESSDATA.exists():
    os.environ["TESSDATA_PREFIX"] = str(LOCAL_TESSDATA)

TESS_LANG = (os.getenv("OCR_LANGUAGES") or os.getenv("TESS_LANG") or "eng+tam").replace(",", "+").replace(" ", "")

def _available_ocr_languages() -> set:
    """Return installed Tesseract language codes; empty set means detection was unavailable."""
    try:
        return set(pytesseract.get_languages(config=""))
    except Exception:
        return set()

def _effective_ocr_lang(requested: str = TESS_LANG) -> str:
    """Use requested languages that are installed; fall back to English when Tamil data is absent."""
    available = _available_ocr_languages()
    if not available:
        return requested or "eng"
    wanted = [part for part in (requested or "eng").split("+") if part in available]
    if "eng" in available and "eng" not in wanted:
        wanted.insert(0, "eng")
    return "+".join(dict.fromkeys(wanted)) or ("eng" if "eng" in available else next(iter(available)))
MIN_PDF_TEXT_CHARS = 80
PDF_DPI = 180

# Windows Tesseract path: checks .env, default Program Files, AppData user location, or PATH
_DEFAULT_TESS = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
_USER_TESS = os.path.expanduser(r"~\AppData\Local\Programs\Tesseract-OCR\tesseract.exe")
_tess_cmd = (os.getenv("TESSERACT_CMD") or "").strip().strip('"')

if not _tess_cmd or not os.path.exists(_tess_cmd):
    if os.path.exists(_DEFAULT_TESS):
        _tess_cmd = _DEFAULT_TESS
    elif os.path.exists(_USER_TESS):
        _tess_cmd = _USER_TESS
    else:
        _which = shutil.which("tesseract")
        if _which:
            _tess_cmd = _which

if _tess_cmd and os.path.exists(_tess_cmd):
    pytesseract.pytesseract.tesseract_cmd = _tess_cmd

TEXT, DATE, INT, NUM = "text", "date", "int", "num"


# ----------------------------------------------------------------------------
# 1. Text extraction & Preprocessing
# ----------------------------------------------------------------------------
def _clean(text: str) -> str:
    """Normalize whitespace and linebreaks."""
    text = (text or "").replace("\r", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _strip_tamil(text: str) -> str:
    """Remove Tamil Unicode script chars (U+0B80 to U+0BFF and ZWNJ/ZWJ) for clean English field processing."""
    return re.sub(r"[\u0B80-\u0BFF\u200c\u200d]+", " ", text or "")


def _orient_for_ocr(img: Image.Image) -> Image.Image:
    """Use Tesseract orientation detection to correct sideways or upside-down scans."""
    img = ImageOps.exif_transpose(img)
    # OSD confidence is particularly low for sideways phone photos of Tamil
    # Nadu marksheets. Compare the two readable portrait orientations using
    # English certificate labels, which are printed on the document and give
    # a much more reliable signal than guessing from page geometry alone.
    if img.width > img.height:
        try:
            probe = img.copy()
            probe.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
            signals = (
                r"HIGHER\s+SECONDARY", r"SECONDARY\s+SCHOOL", r"TOTAL\s+MARKS",
                r"NAME\s+OF\s+(?:THE\s+)?CANDIDATE", r"DATE\s+OF\s+BIRTH",
                r"ECONOMICS", r"COMMERCE", r"ACCOUNTANCY", r"TAMIL", r"ENGLISH",
                r"MATHEMATICS", r"202[0-9]",
            )
            candidates = []
            for angle in (90, 270):
                rotated = probe.rotate(angle, expand=True)
                sample = pytesseract.image_to_string(rotated, lang="eng", config="--oem 3 --psm 6")
                score = sum(bool(re.search(pattern, sample, re.I)) for pattern in signals)
                candidates.append((score, angle))
            best_score, best_angle = max(candidates)
            if best_score >= 3:
                return img.rotate(best_angle, expand=True)
        except Exception:
            pass
    try:
        orientation = pytesseract.image_to_osd(img, config="--psm 0")
        match = re.search(r"\bRotate:\s*(\d+)\b", orientation)
        rotation = int(match.group(1)) % 360 if match else 0
        if rotation in {90, 180, 270}:
            # Apply the orientation correction using an expanded quarter-turn
            # so the full certificate remains inside the image bounds.
            return img.rotate(rotation, expand=True)
    except Exception:
        # Orientation data may be unavailable (for example, missing osd.traineddata).
        # Keep the original scan usable rather than failing certificate extraction.
        pass
    return img


def _variants(img: Image.Image) -> dict:
    """Preprocessed versions of the page. ALL arrays share the same size/geometry, so a box found
    on one can be cropped from the others.
      plain : grayscale, normalised (no binarisation)
      otsu  : global threshold
      ink   : min(R,G,B) - makes red/coloured ink (e.g. the serial number) as dark as black ink
      adaptive: local adaptive threshold
      clahe : local contrast enhancement
    """
    img = ImageOps.exif_transpose(img)
    arr = np.array(img.convert("RGB"))
    plain = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    ink = np.min(arr, axis=2)

    h, w = plain.shape
    if max(h, w) > 2800:
        scale = 2800 / max(h, w)
        plain = cv2.resize(plain, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        ink = cv2.resize(ink, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    elif max(h, w) < 1400:
        scale = 1400 / max(h, w)
        plain = cv2.resize(plain, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        ink = cv2.resize(ink, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    clahe_obj = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    clahe = clahe_obj.apply(plain)
    _, otsu = cv2.threshold(cv2.GaussianBlur(plain, (3, 3), 0), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    adaptive = cv2.adaptiveThreshold(plain, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15)

    pad = lambda a: cv2.copyMakeBorder(a, 35, 35, 35, 35, cv2.BORDER_CONSTANT, value=255)
    return {
        "plain": pad(plain),
        "otsu": pad(otsu),
        "adaptive": pad(adaptive),
        "ink": pad(ink),
        "clahe": pad(clahe)
    }


def _serial_from_raw(text: str) -> Optional[str]:
    """Find serial numbers in raw OCR text."""
    # 1. Direct on same line or with whitespace / punctuation
    m12 = re.search(r"\bH[S5]{2}[ \t\n:._]*(\d{7,8})\b", text, re.I)
    if m12:
        return f"HSS {m12.group(1)}"
    m10 = re.search(r"\bSEC[ \t\n:._]*(\d{8})\b", text, re.I)
    if m10:
        return f"SEC {m10.group(1)}"

    # 2. Check top chunk (first 600 chars) for HSS / SEC prefix and nearby 7-8 digits
    top_chunk = text[:600]
    m_pfx = re.search(r"\b(H[S5]{2}|SEC)\b", top_chunk, re.I)
    if m_pfx:
        pfx = "HSS" if "H" in m_pfx.group(1).upper() else "SEC"
        for dig in re.findall(r"\b(\d{7,8})\b", top_chunk):
            if not dig.startswith("600006"):
                return f"{pfx} {dig}"

    m_top = re.search(r"\b(\d{8})\b", text[:300])
    if m_top and re.search(r"SECONDARY|X\s+STANDARD|இடைநிலைப்\s*பள்ளி", text, re.I):
        return f"SEC {m_top.group(1)}"
    return None


def _serial_crop_ocr(variants: dict) -> Optional[str]:
    """Specialized high-resolution crop OCR for red/colored certificate serial numbers (HSS / SEC)."""
    anchor_arr = variants.get("ink", variants.get("plain"))
    if anchor_arr is None:
        return None
    h, w = anchor_arr.shape
    top = anchor_arr[: int(h * 0.25), :]
    for psm in (6, 3):
        try:
            raw = pytesseract.image_to_string(Image.fromarray(top), lang=_effective_ocr_lang(), config=f"--oem 3 --psm {psm}")
            ser = _serial_from_raw(raw)
            if ser:
                return ser
        except Exception:
            continue
    return None


def _ocr_image(img: Image.Image) -> str:
    """Multi-pass OCR with score-based variant selection, plus high-res top & table crops for State Board marksheets."""
    started = time.perf_counter()
    print("[OCR-TIMING] _ocr_image started", flush=True)
    img = _orient_for_ocr(img)
    high_resolution_page = img.copy()
    img.thumbnail((1400, 1400), Image.Resampling.LANCZOS)
    variants = _variants(img)
    best_score = -1
    best_text = ""

    ocr_lang = _effective_ocr_lang()

    for name in ("plain",):
        arr = variants[name]
        for psm in (6, 3):
            cfg = f"--oem 3 --psm {psm}"
            try:
                data = pytesseract.image_to_data(
                    Image.fromarray(arr), lang=ocr_lang, config=cfg, output_type=pytesseract.Output.DICT
                )
                score = sum(len(w) for w, c in zip(data["text"], data["conf"]) if w.strip() and float(c) >= 55)
                if score > best_score:
                    best_score = score
                    best_text = pytesseract.image_to_string(Image.fromarray(arr), lang=ocr_lang, config=cfg)
            except Exception:
                continue

    if not best_text:
        try:
            best_text = pytesseract.image_to_string(Image.fromarray(variants["plain"]), lang=ocr_lang, config="--oem 3 --psm 6")
        except Exception:
            best_text = ""

    # Bilingual TN scans can score well in the combined language model while
    # losing the English field labels needed by the structured parsers. Compare
    # English-only passes by certificate-specific signals and use them only
    # when they clearly recognize more of the document.
    if "eng" in ocr_lang.split("+") and len(ocr_lang.split("+")) > 1:
        signals = (
            r"HIGHER\s+SECONDARY", r"SECONDARY\s+SCHOOL", r"TOTAL\s+MARKS",
            r"NAME\s+OF\s+(?:THE\s+)?CANDIDATE", r"DATE\s+OF\s+BIRTH",
            r"PHYSICS", r"CHEMISTRY", r"COMPUTER\s+SCIENCE", r"MATHEMATICS",
        )
        score_text = lambda value: sum(bool(re.search(pattern, value, re.I)) for pattern in signals)
        if score_text(best_text) < len(signals):
            english_candidates = []
            for psm in (6, 3):
                try:
                    candidate = pytesseract.image_to_string(
                        Image.fromarray(variants["plain"]), lang="eng", config=f"--oem 3 --psm {psm}"
                    )
                    english_candidates.append((score_text(candidate), candidate))
                except Exception:
                    continue
            if english_candidates:
                english_score, english_text = max(english_candidates, key=lambda item: item[0])
                if english_score > score_text(best_text):
                    best_text = english_text

    # Identity and marks are printed in fixed regions on the portrait Tamil
    # Nadu SSLC/HSE templates. Full-page OCR often merges the name with the
    # adjacent session column or loses subject labels, so run focused crops
    # whenever a portrait scan contains certificate-like text.
    w, h = img.size
    if h > w and re.search(r"CANDIDATE|EXAMINATION|CERTIFICATE|TAMILNADU|TAMIL\s*NADU", best_text, re.I):
        try:
            identity_w, identity_h = high_resolution_page.size
            # On SSLC certificates the session/year occupy a separate column
            # to the right of the candidate name. Isolate the left identity
            # block so those neighbouring digits do not contaminate the name.
            identity_crop = high_resolution_page.crop((0, int(identity_h * 0.22), int(identity_w * 0.68), int(identity_h * 0.46)))
            identity_crop = identity_crop.resize(
                (identity_crop.width * 3, identity_crop.height * 3),
                Image.Resampling.LANCZOS,
            ).convert("L")
            identity_crop = ImageOps.autocontrast(identity_crop)
            identity_text = pytesseract.image_to_string(identity_crop, lang="eng", config="--oem 3 --psm 11")
            if not re.search(r"NAME\s+OF\s+(?:THE\s+)?CANDIDATE|CANDIDATE\s*NAME", identity_text, re.I):
                identity_text = pytesseract.image_to_string(identity_crop, lang="eng", config="--oem 3 --psm 6")
            if re.search(r"NAME\s+OF\s+(?:THE\s+)?CANDIDATE|CANDIDATE\s*NAME|DATE\s+OF\s+BIRTH|PERMANENT\s+REGISTER", identity_text, re.I):
                best_text += "\n--- IDENTITY CROP ---\n" + identity_text
        except Exception:
            pass

    # 1. Top crop for Serial Number (top 22% of image)
    if not re.search(r"\b(?:HSS\s*\d{7,8}|SEC\s*\d{8})\b", best_text):
        serial = _serial_crop_ocr(variants)
        if not serial:
            try:
                top_crop = img.crop((0, 0, w, int(h * 0.22)))
                txt_top = pytesseract.image_to_string(top_crop, lang=ocr_lang, config="--oem 3 --psm 6")
                serial = _serial_from_raw(txt_top)
            except Exception:
                pass
        if serial:
            best_text = f"CERTIFICATE SL. NO. {serial}\n" + best_text

    # 2. Table crop for Marksheet tables. PSM 6 often preserves each HSE score
    # row better than PSM 4, so compare how many subjects and numeric rows each finds.
    if re.search(
        r"HIGHER\s+SECONDARY|SECOND\s+YEAR|FIRST\s+YEAR|SECONDARY\s+SCHOOL|X\s+STANDARD|TAMILNADU|TAMIL\s*NADU|CANDIDATE.{0,160}(?:PASSED|FOLLOWING|SUBJECTS)",
        best_text,
        re.I | re.S,
    ):
        try:
            table_crop = img.crop((0, int(h * 0.28), w, int(h * 0.72)))
            table_crop = table_crop.resize(
                (table_crop.width * 2, table_crop.height * 2),
                Image.Resampling.LANCZOS,
            )
            table_candidates = []
            subject_patterns = (
                r"TAMIL", r"ENGLISH", r"MATHEMATICS", r"PHYSICS", r"CHEMISTRY",
                r"BIOLOGY", r"COMMERCE", r"ACCOUNTANCY", r"ECONOMICS",
                r"COMPUTER\s+SCIENCE", r"COMPUTER\s+APPLICATIONS", r"HISTORY",
            )
            for psm in (6, 4):
                candidate = pytesseract.image_to_string(
                    table_crop, lang="eng", config=f"--oem 3 --psm {psm}"
                )
                subject_hits = sum(bool(re.search(rf"\b(?:{pattern})\b", candidate, re.I)) for pattern in subject_patterns)
                mark_rows = sum(bool(re.search(r"\b\d{2,3}\s+\d{2,3}\s+\d{2,3}\b", line)) for line in candidate.splitlines())
                table_candidates.append((subject_hits, mark_rows, candidate))
            # PSM 6 keeps the score row aligned with its subject on TN HSE
            # certificates. Prefer it when it recognizes a usable set of rows;
            # PSM 4 is a fallback for scans where PSM 6 finds too little text.
            psm6_candidate, psm4_candidate = table_candidates
            if psm6_candidate[0] >= 4 and psm6_candidate[1] >= 3:
                txt_table = psm6_candidate[2]
            else:
                _, _, txt_table = max(table_candidates, key=lambda item: (item[0], item[1]))
            if re.search(r"\b(?:TAMIL|ENGLISH|MATHEMATICS|PHYSICS|CHEMISTRY|SCIENCE|COMMERCE)\b", txt_table, re.I):
                best_text += "\n--- TABLE CROP ---\n" + txt_table
        except Exception:
            pass
    print(f"[OCR-TIMING] _ocr_image finished in {time.perf_counter() - started:.2f}s", flush=True)

    return best_text


def extract_text(content: bytes, filename: str) -> str:
    """PDF -> extract embedded text if >= 80 chars; fallback to rendering pages and OCR. Image -> OCR."""
    if not content:
        return ""
    if filename.lower().endswith(".pdf"):
        doc = fitz.open(stream=content, filetype="pdf")
        try:
            parts = []
            for page in doc:
                t = page.get_text("text")
                if len(t.strip()) >= MIN_PDF_TEXT_CHARS:
                    parts.append(t)
                else:
                    zoom = PDF_DPI / 72
                    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csRGB)
                    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                    parts.append(_ocr_image(img))
            return _clean("\n".join(parts))
        finally:
            doc.close()

    try:
        img = Image.open(io.BytesIO(content))
        return _clean(_ocr_image(img))
    except Exception as e:
        raise ValueError(f"Failed to process image: {e}")


def mask_sensitive(text: str) -> str:
    """Mask 12-digit Aadhaar and 16-digit VID while keeping EMIS IDs intact."""
    out = []
    for line in (text or "").split("\n"):
        if re.search(r"\bEMIS\b", line, re.I):
            out.append(line)
            continue
        line = re.sub(r"VID\s*:?\s*(?:\d{4}[ \t]?){4}", "VID: XXXX XXXX XXXX XXXX", line, flags=re.I)
        line = re.sub(r"(?<!\d)\d{4}[ \t]?\d{4}[ \t]?\d{4}[ \t]?\d{4}(?!\d)", "XXXX XXXX XXXX XXXX", line)
        line = re.sub(r"(?<!\d)\d{4}[ \t]?\d{4}[ \t]?(\d{4})(?![ \t]?\d)", r"XXXX XXXX \1", line)
        out.append(line)
    return "\n".join(out)


# ----------------------------------------------------------------------------
# 2. Field Conversion & Normalization Helpers
# ----------------------------------------------------------------------------
def to_date(s: str):
    """Normalize date string to YYYY-MM-DD or None."""
    if not s:
        return None
    m = re.search(r"\b(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})\b", str(s))
    if m:
        d, mth, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1900 <= y <= 2050:
            try:
                if d > 12 and 1 <= mth <= 12:
                    datetime(y, mth, d)
                    return f"{y:04d}-{mth:02d}-{d:02d}"
                if mth > 12 and 1 <= d <= 12 and mth <= 31:
                    datetime(y, d, mth)
                    return f"{y:04d}-{d:02d}-{mth:02d}"
                if 1 <= mth <= 12 and 1 <= d <= 31:
                    datetime(y, mth, d)
                    return f"{y:04d}-{mth:02d}-{d:02d}"
            except ValueError:
                pass
    m_iso = re.search(r"\b(\d{4})[/\-\.](\d{1,2})[/\-\.](\d{1,2})\b", str(s))
    if m_iso:
        y, mth, d = int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))
        if 1900 <= y <= 2050 and 1 <= mth <= 12 and 1 <= d <= 31:
            try:
                datetime(y, mth, d)
                return f"{y:04d}-{mth:02d}-{d:02d}"
            except ValueError:
                pass
    m_txt = re.search(r"\b(\d{1,2})\s*[\-\s]\s*([A-Za-z]{3,9})\s*[\-\s]\s*(\d{4})\b", str(s))
    if m_txt:
        d = int(m_txt.group(1))
        y = int(m_txt.group(3))
        mon_str = m_txt.group(2).upper()[:3]
        months = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
        if mon_str in months and 1900 <= y <= 2050 and 1 <= d <= 31:
            return f"{y:04d}-{months[mon_str]:02d}-{d:02d}"
    return None


def to_num(s: str) -> Optional[float]:
    """Parse number or float."""
    if not s:
        return None
    m = re.search(r"[-+]?\d*\.?\d+", str(s).replace(",", ""))
    return float(m.group(0)) if m else None


def to_year(s: str) -> Optional[int]:
    """Extract 4-digit calendar year."""
    if not s:
        return None
    m = re.search(r"\b(19\d{2}|20\d{2})\b", str(s))
    return int(m.group(1)) if m else None


def clean_name(s: str) -> Optional[str]:
    """Clean candidate/student name, stripping titles, noise characters, and Tamil text."""
    if not s:
        return None
    s = _strip_tamil(str(s))
    s = re.sub(r"^(?:Thiru|Selvi|Selvan|Kumari|Tmt|Mr|Mrs|Ms|Shri|Sri|Smt)\.?\s+", "", s, flags=re.I)
    s = re.sub(r"[^A-Za-z\s.]", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    if len(s) < 2 or not re.search(r"[A-Za-z]{2,}", s):
        return None
    return s.upper()


def normalize_name(s: str) -> str:
    """Letters only, upper-case."""
    return re.sub(r"[^A-Z]", "", (s or "").upper())


def names_match(a: str, b: str) -> bool:
    na, nb = normalize_name(a), normalize_name(b)
    return bool(na) and na == nb


def compare_with_record(fields: dict, saved_name=None, saved_dob=None) -> list:
    """Compare parsed fields with saved student record for discrepancies."""
    warns = []
    name = fields.get("candidate_name") or fields.get("student_name") or fields.get("name_on_card")
    if saved_name and name and not names_match(name, saved_name):
        warns.append(f"Name differs from the saved record: '{saved_name}' (this sheet reads '{name}')")
    if saved_dob and fields.get("date_of_birth") and str(saved_dob) != str(fields["date_of_birth"]):
        warns.append(f"Date of birth differs from the saved record: {saved_dob} vs {fields['date_of_birth']}")
    return warns


def find_field(text: str, labels: List[str]) -> Optional[str]:
    """Return the text after the first matching label (same line, else next line)."""
    lines = text.split("\n")
    for lab in labels:
        rx = re.compile(rf"(?:{lab})[ \t]*[:\-.]*[ \t]*(.*)", re.I)
        for i, line in enumerate(lines):
            m = rx.search(line)
            if not m:
                continue
            val = m.group(1).strip(" :-.\t")
            # If label line has Tamil script right after slash, check next line
            if val.startswith("/") or not val:
                if i + 1 < len(lines):
                    next_val = lines[i + 1].strip(" :-.\t")
                    if next_val and not re.search(r"[\u0B80-\u0BFF]", next_val):
                        val = next_val
            val = _strip_tamil(val).strip(" :-.\t")
            if val:
                return val
    return None


def block_after(text: str, label_rx: str, max_lines: int = 1) -> List[str]:
    """Lines starting at a label: remainder + following lines until next label."""
    lines = text.split("\n")
    rx = re.compile(rf"(?:{label_rx})[ \t]*[:\-.]*[ \t]*(.*)", re.I)
    for i, line in enumerate(lines):
        m = rx.search(line)
        if not m:
            continue
        out = [m.group(1).strip()] if m.group(1).strip() else []
        for nxt in lines[i + 1: i + 1 + max_lines]:
            if re.match(r"^[A-Za-z .'/()]{3,40}\s*:", nxt.strip()):
                break
            if nxt.strip():
                out.append(nxt.strip())
        return out
    return []


def _convert(val: Any, kind: str) -> Any:
    if val is None:
        return None
    if kind == DATE:
        return to_date(val)
    if kind == NUM:
        return to_num(val)
    if kind == INT:
        n = to_num(val)
        return int(n) if n is not None else None
    s = _strip_tamil(str(val)).strip()
    return re.sub(r"\s+", " ", s) if s else None


def apply_specs(text: str, specs: List[Tuple[str, str, List[str]]]) -> Dict[str, Any]:
    return {col: _convert(find_field(text, rxes), kind) for col, kind, rxes in specs}


def detect_result(text: str) -> Optional[str]:
    u = text.upper()
    if re.search(r"\b(?:PASS|PASSED|QUALIFIED|FIRST\s+CLASS|SECOND\s+CLASS|DISTINCTION)\b", u):
        return "PASS"
    if re.search(r"\b(?:FAIL|FAILED|REAPPEAR|COMPARTMENT|ABSENT)\b", u):
        return "FAIL"
    return None


def _split_list(lines: List[str]) -> List[str]:
    out = []
    for l in lines:
        for p in re.split(r"[,\n;/|]+", l):
            s = _strip_tamil(p).strip(" .-_")
            if s and len(s) > 2:
                out.append(s)
    return out


def _finish_marksheet(d: dict, text: str) -> dict:
    d["percentage"] = None
    if d.get("total_marks") is not None and d.get("maximum_total_marks"):
        try:
            d["percentage"] = round(float(d["total_marks"]) * 100 / float(d["maximum_total_marks"]), 2)
        except ZeroDivisionError:
            pass
    if not d.get("result"):
        d["result"] = detect_result(text)
    return d


# ----------------------------------------------------------------------------
# 3. Common Label Constants
# ----------------------------------------------------------------------------
L_CERT_NAME = [r"Certificate\s+Name", r"Name\s+of\s+(?:the\s+)?Certificate"]
L_BOARD = [r"Examination\s+Board", r"Board\s+of\s+Examination", r"Board"]
L_STANDARD = [r"\bStandard\b", r"\bClass\b", r"Course\s+Studied"]
L_EXAM_YEAR = [r"Exam(?:ination)?\s+Year", r"Year\s+of\s+Passing", r"Year"]
L_SESSION = [r"\bSession\b", r"Month\s*(?:&|and)\s*Year"]
L_ROLL = [r"Roll\s*(?:No|Number)", r"\bRoll\b"]
L_REG = [r"Register\s*(?:No|Number)", r"Reg\s*(?:No|Number)", r"Registration\s*(?:No|Number)"]
L_PRN = [r"Permanent\s+Reg(?:ister)?\s*(?:No|Number)", r"\bPRN\b"]
L_SERIAL = [r"Certificate\s+Serial\s*(?:No|Number)", r"Serial\s*(?:No|Number)", r"\bSl\.?\s*No\b"]
L_NAME = [r"Candidate(?:'s)?\s*Name", r"Student(?:'s)?\s*Name", r"Name\s+of\s+(?:the\s+)?(?:Candidate|Student|Pupil)", r"\bName\b"]
L_DOB = [r"Date\s+of\s+Birth", r"\bD\.?O\.?B\b", r"Born\s+on"]
L_FATHER = [r"Father(?:'s)?\s*Name", r"Name\s+of\s+(?:the\s+)?Father", r"\bFather\b"]
L_MOTHER = [r"Mother(?:'s)?\s*Name", r"Name\s+of\s+(?:the\s+)?Mother", r"\bMother\b"]
L_SCHOOL = [r"School\s*Name", r"Name\s+of\s+(?:the\s+)?School", r"Institution"]
L_SCHOOL_ADDR = [r"School\s+Address", r"Address\s+of\s+(?:the\s+)?School"]
L_MEDIUM = [r"Medium\s+of\s+Instruction", r"\bMedium\b"]
L_EMIS = [r"EMIS\s*(?:Number|No|ID)", r"\bEMIS\b"]
L_ADDRESS = [r"Residential\s+Address", r"Permanent\s+Address", r"\bAddress\b"]
L_ISSUER = [r"Issuing\s+Authority", r"Issued\s+By", r"Designation"]
L_ISSUE_DATE = [r"Date\s+of\s+Issue", r"Issue\s+Date", r"Date"]


# ----------------------------------------------------------------------------
# 4. Tamil Nadu State Board Marksheets (10th & 12th)
# ----------------------------------------------------------------------------
_SSLC_SUBJECTS = ["TAMIL", "ENGLISH", "MATHEMATICS", "SCIENCE", "SOCIAL SCIENCE"]

_HSS_SUBJECTS = [
    ("tamil", r"TAMIL"),
    ("english", r"ENGLISH"),
    ("physics", r"PHYSICS"),
    ("chemistry", r"CHEMISTRY"),
    ("biology", r"BIOLOGY"),
    ("mathematics", r"MATHEMATICS|MATHS?"),
    ("computer_science", r"COMPUTER\s+SCIENCE"),
    ("computer_applications", r"COMPUTER\s+APPLICATIONS"),
    ("commerce", r"COMMERCE"),
    ("accountancy", r"ACCOUNTANCY"),
    ("economics", r"ECONOMICS"),
    ("history", r"HISTORY"),
    ("geography", r"GEOGRAPHY"),
    ("political_science", r"POLITICAL\s+SCIENCE"),
    ("botany", r"BOTANY"),
    ("zoology", r"ZOOLOGY"),
    ("business_mathematics", r"BUSINESS\s+MATHS?|BUSINESS\s+MATHEMATICS"),
    ("business_studies", r"BUSINESS\s+STUDIES"),
]

# Science subjects in TN State Board that have practical marks
_PRACTICAL_SUBJECTS = {"physics", "chemistry", "biology", "computer_science", "computer_applications", "botany", "zoology"}


def _state_board_group_category(text: str, group_code: Optional[str]) -> Optional[str]:
    """Identify one of the State Board HSE groups from its printed code or subjects."""
    code_categories = {"2502": "Computer Science", "2503": "Biology", "2702": "Arts"}
    if group_code in code_categories:
        return code_categories[group_code]

    upper = text.upper()
    if re.search(r"\b(?:BIOLOGY|BOTANY|ZOOLOGY)\b", upper):
        return "Biology"
    if re.search(r"\bCOMPUTER\s+SCIENCE\b", upper):
        return "Computer Science"
    if re.search(r"\b(?:ACCOUNTANCY|COMMERCE|ECONOMICS|HISTORY|COMPUTER\s+APPLICATIONS|POLITICAL\s+SCIENCE)\b", upper):
        return "Arts"
    return None


def _tn_lines(text: str) -> List[str]:
    return [re.sub(r"[ \t]+", " ", l).strip() for l in text.split("\n") if l.strip()]


def _tn_region(text: str, top_rx: str, bottom_rx: str) -> List[str]:
    lines = _tn_lines(text)
    out, armed = [], False
    for l in lines:
        if not armed:
            if re.search(top_rx, l, re.I):
                armed = True
            continue
        if re.search(bottom_rx, l, re.I):
            break
        out.append(l)
    return out


def _mark_tokens(line: str) -> List[int]:
    toks = []
    for t in re.findall(r"(?<![\d.])\d{1,3}(?![\d.])", line):
        v = int(t)
        if 0 <= v <= 200:
            toks.append(v)
    return toks


def _tn_name(text: str) -> Optional[str]:
    # 1. Look on same line after colon / hyphen
    m = re.search(r"(?:CANDIDATE\s*NAME|NAME\s+OF\s+THE\s+CANDIDATE)[^\n:]*[:\-]\s*([A-Za-z\s.]+)", text, re.I)
    if m:
        c = clean_name(m.group(1))
        if c and not re.search(r"GOVERNMENT|TAMIL|NADU|SECONDARY|HIGHER|BOARD|EXAMINATION", c):
            return c

    # 2. In Tamil Nadu State Board marksheets, name is on the line following the header box
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    for i, l in enumerate(lines):
        if re.search(r"(?:NAME\s+OF\s+THE\s+CANDIDATE|CANDIDATE\s*NAME|தேர்வரின்\s*பெயர்)", l, re.I):
            for next_idx in range(i + 1, min(i + 6, len(lines))):
                cand = _strip_tamil(lines[next_idx]).strip()
                if re.search(r"\b(?:SESSION|SESSIO|MONTH|YEAR|PARUVAM|STANDARD|CLASS)\b", cand, re.I) and not re.search(r"[A-Z]{3,}\s+[A-Z]{3,}", cand):
                    continue
                cand = re.sub(r"\b(?:AND|YEAR|ISSUE|OF|MONTH|SESSION|SESSIO|MAY|MARCH|MAR|JUNE|JULY|MARK|CERTIFICATE|DATE|PERMANENT|REGISTER|THAT|FOR|20\d{2})\b.*$", "", cand, flags=re.I)
                cand = re.sub(r"[^A-Za-z0-9\s.]", " ", cand)
                m_cand = re.search(r"\b([A-Z]{3,}(?:\s+[A-Z0-9\.]+)*)\b", cand)
                if not m_cand:
                    m_cand = re.search(r"\b([A-Z]\.\s*[A-Z]{3,}(?:\s+[A-Z0-9\.]+)*)\b", cand)
                if m_cand:
                    val = m_cand.group(1).strip()
                    val = re.sub(r"\s+5$", " S", val)
                    c = clean_name(val)
                    if c and len(c) >= 4 and not re.search(r"GOVERNMENT|TAMIL|NADU|SECONDARY|HIGHER|BOARD|EXAMINATION|SCHOOL|CERTIFICATE|COURSE|SESSION|SESSIO", c):
                        return c

    # 3. Look for Thiru / Selvi / Selvan
    for l in lines[:25]:
        if re.search(r"\b(?:THIRU|SELVI|SELVAN)\b", l, re.I):
            c = clean_name(l)
            if c:
                return c
    return None


_WORDS_DIGITS = {
    "ZERO": 0, "ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4,
    "FIVE": 5, "SIX": 6, "SEVEN": 7, "EIGHT": 8, "NINE": 9,
}


def _words_to_int(text: str) -> Optional[int]:
    """Extract a 2 or 3 digit number represented as English words (e.g. ZERO NINE FOUR -> 94)."""
    m = re.findall(r"\b(ZERO|ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE)\b", text, re.I)
    if len(m) in (2, 3):
        digits = [str(_WORDS_DIGITS[w.upper()]) for w in m]
        return int("".join(digits))
    return None


def _tn_school(text: str) -> Optional[str]:
    # 1. Search for clear school naming pattern anywhere in the text
    for l in _tn_lines(text):
        if re.search(r"\b(?:MATRIC\s*(?:HR\s*SEC)?\s*SCHOOL|HIGHER\s+SECONDARY\s+SCHOOL|HIGH\s+SCHOOL|VIDYALAYA|G\.?H\.?S\.?S)\b", l, re.I):
            clean = _strip_tamil(l).strip(" :.-|/\\'\"‘’")
            clean = re.sub(r"^[|/:.\s(0-9)]+|[|/:.\s)]+$", "", clean).strip(" :.-|/\\'\"‘’")
            clean = re.sub(r"\s+\d{3,5}\s+[A-Z0-9]{1,3}$", "", clean).strip()
            if len(clean) > 8 and not re.search(r"GOVERNMENT\s+OF|EXAMINATION", clean, re.I):
                return re.sub(r"\s+", " ", clean)

    # 2. Look under NAME OF THE SCHOOL label
    lines = _tn_lines(text)
    for i, l in enumerate(lines):
        if re.search(r"(?:NAME\s+OF\s+THE\s+SCHOOL|பள்ளியின்\s*பெயர்)", l, re.I):
            for j in range(i + 1, min(i + 6, len(lines))):
                clean = _strip_tamil(lines[j]).strip(" :.-|/\\'\"‘’")
                clean = re.sub(r"^[|/:.\s(0-9)]+|[|/:.\s)]+$", "", clean).strip(" :.-|/\\'\"‘’")
                clean = re.sub(r"\s+\d{3,5}\s+[A-Z0-9]{1,3}$", "", clean).strip()
                if len(clean) > 8 and not re.search(r"EDUCATION|GROUP|ENGLISH|DATE|^\d", clean, re.I):
                    return re.sub(r"\s+", " ", clean)

    m = re.search(r"(?:SCHOOL|INSTITUTION)[^\n:]*[:\-]\s*([A-Za-z0-9\s,.\-]+)", text, re.I)
    if m:
        s = _strip_tamil(m.group(1)).strip(" :.-|/\\'\"‘’")
        s = re.sub(r"\s+\d{3,5}\s+[A-Z0-9]{1,3}$", "", s).strip()
        if len(s) > 3 and not re.search(r"GOVERNMENT\s+OF|EXAMINATION", s, re.I):
            s = re.sub(r"^[|/:.\s]+|[|/:.\s]+$", "", s)
            return re.sub(r"\s+", " ", s)
    return None


def _tn_header(text: str) -> dict:
    lines = _tn_lines(text)
    h = {
        "candidate_name": _tn_name(text),
        "father_name": None,
        "mother_name": None,
        "school_name": _tn_school(text),
        "school_address": None,
        "date_of_birth": None,
        "roll_number": None,
        "register_number": None,
        "permanent_register_number": None,
        "certificate_serial_number": _serial_from_raw(text),
        "tmr_code_number": None,
        "tmr_code_date": None,
        "emis_id": None,
        "medium_of_instruction": None,
        "group_code": None,
        "group_name": None,
        "group_category": None,
        "exam_year": None,
        "session": None,
        "examination_board": "State Board of School Examinations, Tamil Nadu",
    }

    # 1. Row-aligned headers (Labels on row i, values on row i+1 or i+2)
    for i, l in enumerate(lines):
        # Row with DOB and ROLL NO (common in TN 10th SSLC)
        if re.search(r"DATE\s+OF\s+BIRTH|பிறந்த\s+தேதி", l, re.I) and re.search(r"ROLL\s*NO|தேர்வெண்", l, re.I):
            for j in range(i + 1, min(i + 4, len(lines))):
                m = re.search(r"(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{4})\s+(\d{6,10})(?:\s+([A-Za-z]+))?", lines[j])
                if m:
                    dob = to_date(m.group(1))
                    if dob and not h["date_of_birth"]:
                        h["date_of_birth"] = dob
                    if not h["roll_number"]:
                        h["roll_number"] = m.group(2)
                    if m.group(3) and not h["medium_of_instruction"]:
                        h["medium_of_instruction"] = "English" if "ENG" in m.group(3).upper() else m.group(3)
                    break

        # Row with DOB and PERMANENT REGISTER NUMBER (common in TN 11th & 12th HSE)
        if re.search(r"DATE\s+OF\s+BIRTH|பிறந்த\s+தேதி", l, re.I) and re.search(r"PERMANENT\s+REG|நிரந்தரப்\s*பதிவெண்", l, re.I):
            for j in range(i + 1, min(i + 4, len(lines))):
                m_dob = re.search(r"(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{4})", lines[j])
                if m_dob and not h["date_of_birth"]:
                    h["date_of_birth"] = to_date(m_dob.group(1))
                m_prn = re.search(r"\b(2\d{9})\b", lines[j])
                if m_prn and not h["permanent_register_number"]:
                    h["permanent_register_number"] = m_prn.group(1)

        # Row with TMR Code & Date
        if re.search(r"T\s*\.?\s*M\s*\.?\s*R|குறியீட்டெண்|CODE\s*NO\s*&\s*DATE", l, re.I):
            for j in range(i, min(i + 8, len(lines))):
                m_tmr = re.search(r"\b([A-Za-z0-9]{7,12})\s+(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{4})\b", lines[j])
                if m_tmr:
                    h["tmr_code_number"] = m_tmr.group(1)
                    h["tmr_code_date"] = to_date(m_tmr.group(2))
                    break

    # 2. Standalone Date of Birth if not yet found
    if not h["date_of_birth"]:
        m_dob = re.search(r"(?:DOB|DATE\s+OF\s+BIRTH|பிறந்த\s+தேதி)[^\d\n]*(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{4})", text, re.I)
        if m_dob:
            h["date_of_birth"] = to_date(m_dob.group(1))

    # 3. Permanent Register Number (strictly separate from roll and register numbers)
    if not h["permanent_register_number"]:
        # Support alphanumeric PRN e.g. XM22R0371265535 or 10-digit 2\d{9}
        for i, l in enumerate(lines):
            if re.search(r"PERMANENT\s+REG(?:ISTER)?(?:\s*NO|\s*NUMBER)?|PRN|நிரந்தரப்\s*பதிவெண்", l, re.I):
                for j in range(i, min(i + 4, len(lines))):
                    if re.search(r"\bEMIS\b", lines[j], re.I):
                        continue
                    m_prn = re.search(r"\b([A-Za-z0-9]{10,18})\b", lines[j])
                    if m_prn and not re.search(r"REGISTER|PERMANENT|NUMBER|CODE|DATE", m_prn.group(1), re.I):
                        h["permanent_register_number"] = m_prn.group(1)
                        break
                if h["permanent_register_number"]:
                    break
        if not h["permanent_register_number"]:
            m_prn = re.search(r"(?:PERMANENT\s+REG(?:ISTER)?(?:\s*NO|\s*NUMBER)?|PRN|நிரந்தரப்\s*பதிவெண்)[^\d\n]*(\d{9,12})", text, re.I)
            if m_prn:
                h["permanent_register_number"] = m_prn.group(1)

    # 4. Roll Number (strictly separate from register number)
    if not h["roll_number"]:
        m_roll = re.search(r"(?:ROLL\s*(?:NO|NUMBER)|தேர்வெண்)[^\d\n]*(\d{6,10})", text, re.I)
        if m_roll:
            h["roll_number"] = m_roll.group(1)

    # 5. Register Number (strictly separate from roll number and permanent register number)
    if not h["register_number"]:
        for l in lines:
            if re.search(r"PERMANENT|நிரந்தரப்|PRN", l, re.I):
                continue
            m_rn = re.search(r"(?:REG(?:ISTER)?\s*(?:NO|NUMBER)|தோவெண்|பதிவு\s*எண்)[^\d\n]*(\d{6,10})", l, re.I)
            if m_rn:
                h["register_number"] = m_rn.group(1)
                break

    # 6. Fallback register number from HSE subject rows (7 digits e.g. 6218338, 3218314)
    if not h["register_number"]:
        candidate_regs = re.findall(r"\b([3-8]\d{6})\b", text)
        if candidate_regs:
            counts = {}
            for reg in candidate_regs:
                counts[reg] = counts.get(reg, 0) + 1
            best_reg, freq = max(counts.items(), key=lambda kv: kv[1])
            if freq >= 3:
                h["register_number"] = best_reg

    # 7. EMIS ID (10 to 20 digits)
    m_emis = re.search(r"EMIS\s*[0-9A-Za-z]*\s*(?:ID)?\s*(?:NO\.?)?[^\d\n]*(\d{10,20})", text, re.I)
    if m_emis:
        h["emis_id"] = m_emis.group(1)

    # 8. Medium of Instruction
    if not h["medium_of_instruction"]:
        if re.search(r"\bENGLISH\s+MEDIUM\b|\bENG\s+MED\b", text, re.I):
            h["medium_of_instruction"] = "English"
        elif re.search(r"\bTAMIL\s+MEDIUM\b", text, re.I):
            h["medium_of_instruction"] = "Tamil"
        elif re.search(r"MEDIUM\s+OF\s+INSTRUCTION[^\n:]*[:\-]?\s*ENGLISH", text, re.I):
            h["medium_of_instruction"] = "English"
        elif re.search(r"(?:MEDIUM|பயிற்றுமொழி)[^\n]*\n(?:[^\n]*\n){0,3}[^\n]*\bENGLISH\b", text, re.I):
            h["medium_of_instruction"] = "English"
        elif re.search(r"(?:MEDIUM|பயிற்றுமொழி)[^\n]*\n(?:[^\n]*\n){0,3}[^\n]*\bTAMIL\b", text, re.I):
            h["medium_of_instruction"] = "Tamil"

    # 9. Group Code and Group Name (e.g. 2503 GENERAL EDUCATION)
    for i, l in enumerate(lines):
        if re.search(r"GROUP\s*CODE|பாடத்தொகுப்பு", l, re.I):
            for j in range(i + 1, min(i + 6, len(lines))):
                codes = [c for c in re.findall(r"\b([12]\d{3})\b", lines[j]) if not (2015 <= int(c) <= 2035)]
                if codes:
                    h["group_code"] = codes[0]
                    break
            if h["group_code"]:
                break
    if re.search(r"GENERAL\s+EDUCATION|(?:GENE\w*\s+)?RAL\s+EDUCATION|GENERAL|பொதுக்கல்வி", text, re.I):
        h["group_name"] = "GENERAL EDUCATION"
    h["group_category"] = _state_board_group_category(text, h["group_code"])

    # 10. Year & Session
    m_yr = re.search(r"\b(MARCH|APRIL|MAY|JUNE|JULY|SEPTEMBER|OCTOBER|MAR)\s*[-/ ]?\s*(20\d{2})\b", text, re.I)
    if m_yr:
        h["session"] = m_yr.group(1).upper()
        matched_year = int(m_yr.group(2))
        plausible_years = [
            int(year)
            for year in re.findall(r"\b(20\d{2})\b", text)
            if int(year) <= datetime.now().year + 1
        ]
        # The certificate repeats the passing session across each subject row.
        # Prefer the most frequent plausible year when a header digit is
        # misread (for example, 2022 as 2072) and correct elsewhere on the page.
        if plausible_years:
            frequent_year = max(set(plausible_years), key=plausible_years.count)
            if matched_year > datetime.now().year + 1 or plausible_years.count(frequent_year) > plausible_years.count(matched_year):
                matched_year = frequent_year
        h["exam_year"] = matched_year

    # 11. Father Name and Mother Name
    for i, l in enumerate(lines):
        if re.search(r"FATHER|தந்தை", l, re.I):
            for j in range(i + 1, min(i + 6, len(lines))):
                clean = _strip_tamil(lines[j])
                clean = re.sub(r"[^A-Za-z0-9\s.‘\'\"]", " ", clean).strip()
                parts = re.split(r"[\s]{2,}|[|‘'\",]+", clean)
                parts = [p.strip() for p in parts if p.strip()]
                cand_names = []
                for p in parts:
                    c = clean_name(p)
                    if c and len(c) >= 3 and not re.search(r"SCHOOL|GOVT|EXAM|CERTIFICATE|DATE|PASS|FATHER|MOTHER|NAME", c, re.I):
                        m_init = re.search(r"\b([A-Z]{3,})([A-Z])\.?$", c)
                        if m_init:
                            c = f"{m_init.group(1)} {m_init.group(2)}"
                        cand_names.append(c)
                if len(cand_names) >= 2:
                    h["father_name"] = cand_names[0]
                    if not h.get("mother_name"):
                        h["mother_name"] = cand_names[1]
                    break
                elif len(cand_names) == 1 and not h["father_name"]:
                    h["father_name"] = cand_names[0]
            if h.get("father_name"):
                break

    if not h.get("mother_name"):
        for i, l in enumerate(lines):
            if re.search(r"MOTHER|தாயார்", l, re.I):
                for j in range(i + 1, min(i + 6, len(lines))):
                    clean = _strip_tamil(lines[j]).strip()
                    m_m = re.search(r"\b([A-Z]{3,}(?:\s+[A-Z0-9\.]+)*)\b", clean)
                    if m_m and not re.search(r"GOVERNMENT|SCHOOL|EXAM|CERTIFICATE|DATE|PASS|FATHER|MOTHER", m_m.group(1), re.I):
                        c_val = clean_name(m_m.group(1))
                        norm_f = (h.get("father_name") or "").replace(" ", "").upper()
                        if c_val.replace(" ", "").upper() != norm_f:
                            h["mother_name"] = c_val
                            break
                if h.get("mother_name"):
                    break

    return h


def parse_state_10th(text: str) -> dict:
    """Parse TN State Board 10th marksheet while preserving the existing DB field names.

    This parser deliberately returns None for uncertain subject marks instead of assigning
    an unrelated number from another part of the certificate.
    """
    h = _tn_header(text)
    d = {
        "certificate_name": "Secondary School Leaving Certificate",
        "examination_board": h["examination_board"],
        "standard": "10th",
        "exam_year": h["exam_year"],
        "session": h["session"],
        "roll_number": h["roll_number"],
        "register_number": h["register_number"],
        "permanent_register_number": h["permanent_register_number"],
        "tmr_code_number": h["tmr_code_number"],
        "tmr_code_date": h["tmr_code_date"],
        "certificate_serial_number": h["certificate_serial_number"],
        "candidate_name": h["candidate_name"],
        "father_name": h.get("father_name"),
        "mother_name": h.get("mother_name"),
        "date_of_birth": h["date_of_birth"],
        "school_name": h["school_name"],
        "school_address": h["school_address"],
        "medium_of_instruction": h["medium_of_instruction"],
        "emis_id": h["emis_id"],
        "subject_1_name": "TAMIL", "subject_1_marks": None,
        "subject_2_name": "ENGLISH", "subject_2_marks": None,
        "subject_3_name": "MATHEMATICS", "subject_3_marks": None,
        "subject_4_name": "SCIENCE", "subject_4_marks": None,
        "subject_5_name": "SOCIAL SCIENCE", "subject_5_marks": None,
        "total_marks": None,
        "maximum_total_marks": 500.0,
        "percentage": None,
        "result": None,
    }

    lines = _tn_lines(text)

    # Prefer the dedicated table crop when the OCR payload contains one. The crop is
    # usually more faithful to the marks grid than full-page OCR, where columns can
    # be reordered or single digits can be detached from subject labels.
    crop_marker = next((i for i, line in enumerate(lines) if "--- TABLE CROP ---" in line.upper()), None)
    crop_lines = lines[crop_marker + 1:] if crop_marker is not None else lines
    start_idx = next((i for i, line in enumerate(crop_lines)
                      if re.search(r"MARKS\s+OBTAINED\s+FOR\s+100", line, re.I)), None)
    end_idx = next((i for i, line in enumerate(crop_lines)
                    if i > (start_idx if start_idx is not None else -1) and re.search(r"TOTAL\s+MARKS", line, re.I)), None)
    if start_idx is not None:
        table_lines = crop_lines[start_idx + 1:end_idx if end_idx is not None else len(crop_lines)]
        table_values = []
        digit_words = r"ZERO|ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE"
        for table_line in table_lines:
            upper_line = table_line.upper()
            # Ignore subject headings and empty optional-language rows.
            if re.search(r"OPTIONAL\s+LANGUAGE|MEDIUM|DATE\s+OF\s+BIRTH|ROLL\s+NO", upper_line):
                continue
            normalized = upper_line
            # Common OCR joins number words (e.g. ZERONINE SIX).
            normalized = re.sub(r"\bZERO(?=ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE)", "ZERO ", normalized)
            word_matches = re.findall(rf"(?<![A-Z])(?:{digit_words})(?:\s+(?:{digit_words})){{1,2}}(?![A-Z])", normalized)
            word_values = []
            for phrase in word_matches:
                value = _words_to_int(phrase)
                if value is not None and 0 <= value <= 100:
                    word_values.append(value)
            if word_values:
                # The number-in-words is the clearest value; in theory/practical
                # rows it represents the combined subject mark.
                table_values.append(word_values[-1])
                continue
            nums = [int(n) for n in re.findall(r"(?<![\d.])\d{1,3}(?![\d.])", table_line)]
            nums = [n for n in nums if 0 <= n <= 100]
            if len(nums) >= 3 and nums[-3] + nums[-2] == nums[-1]:
                table_values.append(nums[-1])
            elif len(nums) == 1:
                table_values.append(nums[0])
        # Only override the generic parser when we found exactly five plausible
        # subject values in the crop; never shift an incomplete sequence.
        if len(table_values) == 5:
            for subject_no, value in enumerate(table_values, 1):
                d[f"subject_{subject_no}_marks"] = float(value)

    sub_defs = [
        ("subject_1", r"\bTAMIL\b"),
        ("subject_2", r"\bENGLISH\b"),
        ("subject_3", r"\b(?:MATHEMATICS|MATHS)\b"),
        ("subject_4", r"\bSCIENCE\b"),
        ("subject_5", r"\bSOCIAL\s*SCIENCE\b"),
    ]

    # Identify the actual subject-label rows first. SOCIAL SCIENCE must not also match SCIENCE.
    subject_rows = {}
    for key, rx in sub_defs:
        found = []
        for i, line in enumerate(lines):
            if key == "subject_4" and re.search(r"SOCIAL", line, re.I):
                continue
            if re.search(rx, line, re.I):
                found.append(i)
        if found:
            # Prefer the occurrence that looks like a marks-table row over headings elsewhere.
            def row_quality(idx):
                line = lines[idx]
                return (2 if re.search(r"\b(?:PASS|FAIL|P|F)\b|\([PF]\)", line, re.I) else 0) + \
                       (1 if re.search(r"\d|ZERO|ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE", line, re.I) else 0)
            subject_rows[key] = max(found, key=row_quality)

    def mark_candidates(line: str):
        """Return plausible mark values, preferring number words over noisy numeric fragments."""
        word_value = _words_to_int(line)
        if word_value is not None and 0 <= word_value <= 100:
            return [(word_value, 4)]
        nums = []
        # Do not read years, register numbers, or long identifiers as marks.
        for token in re.findall(r"(?<![\d.])\d{1,3}(?![\d.])", line):
            value = int(token)
            if 0 <= value <= 100:
                nums.append(value)
        # In a theory/practical row, the final number is often the subject total.
        if len(nums) >= 3 and nums[-3] + nums[-2] == nums[-1]:
            return [(nums[-1], 3)]
        return [(v, 1) for v in nums]

    # Read the label row and only its nearby lines. Never let a candidate cross another subject row.
    used_source_lines = set()
    for key, _ in sub_defs:
        idx = subject_rows.get(key)
        if idx is None:
            continue
        other_rows = sorted(i for k, i in subject_rows.items() if k != key)
        left_limit = max([i for i in other_rows if i < idx] + [-3]) + 1
        right_limit = min([i for i in other_rows if i > idx] + [len(lines)])
        lo = max(left_limit, idx - 2)
        hi = min(right_limit, idx + 3)
        candidates = []
        for j in range(lo, hi):
            line = lines[j]
            if re.search(r"\bTOTAL\b|REGISTER|ROLL\s*(?:NO|NUMBER)|DATE\s+OF\s+BIRTH|PERMANENT", line, re.I):
                continue
            if j != idx and any(re.search(rx, line, re.I) for _, rx in sub_defs):
                continue
            for value, strength in mark_candidates(line):
                # Same-row values are strongest; adjacent lines are weaker.
                score = strength * 10 - abs(j - idx) * 2
                if re.search(r"\([PF]\)|\bPASS\b|\bFAIL\b", line, re.I):
                    score += 2
                if j in used_source_lines:
                    score -= 5
                candidates.append((score, value, j))
        if candidates:
            candidates.sort(reverse=True)
            best_score, best_value, best_line = candidates[0]
            # Avoid using a single ambiguous digit as a mark unless it is explicitly worded or on the row.
            if best_score >= 6 and d.get(f"{key}_marks") is None:
                d[f"{key}_marks"] = float(best_value)
                used_source_lines.add(best_line)

    # Read the printed total only from a line explicitly labelled TOTAL / TOTAL MARKS.
    for i, line in enumerate(lines):
        if not re.search(r"\bTOTAL(?:\s+MARKS)?\b", line, re.I):
            continue
        if re.search(r"SUBJECT|MAXIMUM|OUT\s+OF", line, re.I):
            continue
        # Totals may be 300-500, so don't use the per-subject 0-100 filter here.
        total_lines = [line]
        if i + 1 < len(lines):
            total_lines.append(lines[i + 1])
        valid_totals = []
        for total_line in total_lines:
            word_total = _words_to_int(total_line)
            if word_total is not None and 0 <= word_total <= 500:
                valid_totals.append(word_total)
            for token in re.findall(r"(?<![\d.])\d{2,3}(?![\d.])", total_line):
                value = int(token)
                if 0 <= value <= 500:
                    valid_totals.append(value)
        if valid_totals:
            d["total_marks"] = float(valid_totals[-1])
            break

    known_marks = [d[f"subject_{i}_marks"] for i in range(1, 6) if d.get(f"subject_{i}_marks") is not None]
    if d.get("total_marks") is None and len(known_marks) == 5:
        d["total_marks"] = float(sum(known_marks))

    return _finish_marksheet(d, text)


def _parse_hse_marks(lines: List[str]) -> Tuple[Dict[str, float], List[str], List[Tuple[str, int]]]:
    """Extract HSE subject marks using section ordering, row claims, and mathematical invariants."""
    results: Dict[str, float] = {}
    found_reg_nos: List[str] = []
    found_sessions: List[Tuple[str, int]] = []
    claimed_lines = set()

    # Split lines if table crop is present: prioritize table crop lines first
    crop_start = -1
    for i, l in enumerate(lines):
        if "--- TABLE CROP ---" in l:
            crop_start = i
            break

    # Build ordered list of sections to inspect:
    # 1. Table crop section (if present)
    # 2. Main section (up to table crop, or whole document)
    sections = []
    if crop_start >= 0:
        sections.append((lines[crop_start:], crop_start))
        sections.append((lines[:crop_start], 0))
    else:
        sections.append((lines, 0))

    # Pass 1: Row-adjacent matching (search +/- 2 lines of subject name)
    for section_lines, sec_offset in sections:
        for sub_key, rx in _HSS_SUBJECTS:
            # Skip if we already have a complete match with internal marks
            if f"{sub_key}_marks" in results and f"{sub_key}_internal_marks" in results:
                continue

            for i, l in enumerate(section_lines):
                if re.search(r"BOARD|GOVERNMENT|EXAMINATIONS\s+TAMIL", l, re.I):
                    continue
                if re.search(rf"\b(?:{rx})\b", l, re.I):
                    # Check candidate lines within +/- 2 lines
                    for offset in (-1, 0, 1, -2, 2):
                        cidx = i + offset
                        abs_idx = sec_offset + cidx
                        if not (0 <= cidx < len(section_lines)) or abs_idx in claimed_lines:
                            continue
                        cline = section_lines[cidx]

                        m_reg = re.search(r"\b([3-8]\d{6})\b", cline)
                        if m_reg:
                            found_reg_nos.append(m_reg.group(1))
                        m_ses = re.search(r"\b(MARCH|APRIL|MAY|JUNE|JULY|SEPTEMBER|OCTOBER|MAR)\s*(20\d{2})\b", cline, re.I)
                        if m_ses:
                            found_sessions.append((m_ses.group(1).upper(), int(m_ses.group(2))))

                        cleaned = re.sub(r"\b[oO](\d{2})\b", r"0\1", cline)
                        cleaned = re.sub(r"\|\s*o10\b", " 010", cleaned)
                        raw_nums = re.findall(r"(?<!\d)\d{2,3}(?!\d)", cleaned)
                        nums = [int(n) for n in raw_nums if int(n) <= 100]

                        matched = False
                        # 4 numbers: Theory + Practical + Internal == Total
                        if sub_key in _PRACTICAL_SUBJECTS and len(nums) >= 4:
                            for a in range(len(nums)):
                                for b in range(a + 1, len(nums)):
                                    for c in range(b + 1, len(nums)):
                                        for d_idx in range(c + 1, len(nums)):
                                            th, pr, int_m, tot = nums[a], nums[b], nums[c], nums[d_idx]
                                            if abs((th + pr + int_m) - tot) <= 1:
                                                results[f"{sub_key}_theory_marks"] = float(th)
                                                results[f"{sub_key}_practical_marks"] = float(pr)
                                                results[f"{sub_key}_internal_marks"] = float(int_m)
                                                results[f"{sub_key}_marks"] = float(th + pr + int_m)
                                                claimed_lines.add(abs_idx)
                                                matched = True
                                                break
                                        if matched: break
                                    if matched: break
                                if matched: break

                        # 3 numbers: Theory + Internal == Total
                        if not matched and len(nums) >= 3:
                            for a in range(len(nums)):
                                for b in range(a + 1, len(nums)):
                                    for c in range(b + 1, len(nums)):
                                        th, int_m, tot = nums[a], nums[b], nums[c]
                                        if abs((th + int_m) - tot) <= 1:
                                            results[f"{sub_key}_theory_marks"] = float(th)
                                            results[f"{sub_key}_internal_marks"] = float(int_m)
                                            results[f"{sub_key}_marks"] = float(th + int_m)
                                            claimed_lines.add(abs_idx)
                                            matched = True
                                            break
                                    if matched: break
                                if matched: break

                        if matched:
                            break
                    if f"{sub_key}_marks" in results and f"{sub_key}_internal_marks" in results:
                        break

    # Pass 2: Column-split fallback for any subjects not yet found
    main_lines = lines[:crop_start] if crop_start >= 0 else lines
    bottom_idx = len(main_lines)
    for i, l in enumerate(main_lines):
        if re.search(r"TOTAL\s*MARKS|மொத்த\s*மதிப்பெண்கள்", l, re.I):
            bottom_idx = i
            break

    table_lines = main_lines[:bottom_idx]
    unmatched_subs = []
    for i, l in enumerate(table_lines):
        for sub_key, rx in _HSS_SUBJECTS:
            if re.search(rf"\b(?:{rx})\b", l, re.I) and f"{sub_key}_marks" not in results:
                if not re.search(r"BOARD|GOVERNMENT|EXAMINATIONS\s+TAMIL", l, re.I):
                    unmatched_subs.append((i, sub_key))
                break

    if unmatched_subs:
        max_sub_idx = max(s[0] for s in unmatched_subs)
        marks_rows = []
        for j in range(max_sub_idx + 1, bottom_idx):
            if j in claimed_lines:
                continue
            l = table_lines[j]
            cleaned = re.sub(r"\b[oO](\d{2})\b", r"0\1", l)
            cleaned = re.sub(r"\|\s*o10\b", " 010", cleaned)
            raw_nums = re.findall(r"(?<!\d)\d{2,3}(?!\d)", cleaned)
            nums = [int(n) for n in raw_nums if int(n) <= 100]
            m_reg = re.search(r"\b([3-8]\d{6})\b", l)
            if m_reg:
                found_reg_nos.append(m_reg.group(1))
            m_ses = re.search(r"\b(MARCH|APRIL|MAY|JUNE|JULY|SEPTEMBER|OCTOBER|MAR)\s*(20\d{2})\b", l, re.I)
            if m_ses:
                found_sessions.append((m_ses.group(1).upper(), int(m_ses.group(2))))
            if nums and (m_reg or len(nums) >= 2):
                marks_rows.append((j, nums))

        if len(unmatched_subs) == len(marks_rows):
            for (_, sub_key), (j, nums) in zip(unmatched_subs, marks_rows):
                claimed_lines.add(j)
                if len(nums) >= 4:
                    th, pr, int_m, tot = nums[0], nums[1], nums[2], nums[3]
                    results[f"{sub_key}_theory_marks"] = float(th)
                    results[f"{sub_key}_practical_marks"] = float(pr)
                    results[f"{sub_key}_internal_marks"] = float(int_m)
                    results[f"{sub_key}_marks"] = float(th + pr + int_m if abs(th + pr + int_m - tot) <= 1 else tot)
                elif len(nums) == 3:
                    th, int_m, tot = nums[0], nums[1], nums[2]
                    results[f"{sub_key}_theory_marks"] = float(th)
                    results[f"{sub_key}_internal_marks"] = float(int_m)
                    results[f"{sub_key}_marks"] = float(th + int_m if abs(th + int_m - tot) <= 1 else tot)
                elif len(nums) == 2:
                    results[f"{sub_key}_theory_marks"] = float(nums[0])
                    results[f"{sub_key}_marks"] = float(nums[1])

    # Pass 3: Check adjacent lines for 2 numbers if subject still has no marks
    for sub_key, rx in _HSS_SUBJECTS:
        if f"{sub_key}_marks" in results:
            continue
        for i, l in enumerate(lines):
            if re.search(rf"\b(?:{rx})\b", l, re.I) and not re.search(r"BOARD|GOVERNMENT|EXAMINATIONS\s+TAMIL", l, re.I):
                for offset in (-1, 0, 1, -2, 2):
                    ci = i + offset
                    if 0 <= ci < len(lines) and ci not in claimed_lines:
                        cleaned = re.sub(r"\b[oO](\d{2})\b", r"0\1", lines[ci])
                        raw_nums = re.findall(r"(?<!\d)\d{2,3}(?!\d)", cleaned)
                        nums = [int(n) for n in raw_nums if int(n) <= 100]
                        if len(nums) == 2 and nums[0] < nums[1] and nums[1] >= 35:
                            results[f"{sub_key}_theory_marks"] = float(nums[0])
                            results[f"{sub_key}_marks"] = float(nums[1])
                            claimed_lines.add(ci)
                            break
            if f"{sub_key}_marks" in results:
                break

    return results, found_reg_nos, found_sessions


def parse_state_11th(text: str) -> dict:
    """Parse TN State Board 11th Marksheet (HSE First Year). Returns valid columns for state_board_12th table."""
    h = _tn_header(text)
    d = {
        "certificate_name": "Higher Secondary Course - First Year Mark Certificate",
        "examination_board": h["examination_board"],
        "standard": "11th",
        "exam_year": h["exam_year"],
        "session": h["session"],
        "register_number": h["register_number"],
        "permanent_register_number": h["permanent_register_number"],
        "certificate_serial_number": h["certificate_serial_number"],
        "candidate_name": h["candidate_name"],
        "date_of_birth": h["date_of_birth"],
        "school_name": h["school_name"],
        "school_address": h["school_address"],
        "medium_of_instruction": h["medium_of_instruction"],
        "group_code": h.get("group_code"),
        "group_name": h.get("group_name"),
        "group_category": h.get("group_category"),
        "tmr_code_number": h.get("tmr_code_number"),
        "tmr_code_date": h.get("tmr_code_date"),
        "emis_id": h["emis_id"],
        "total_marks": None,
        "maximum_total_marks": 600.0,
        "percentage": None,
        "result": None,
    }
    if h.get("father_name"):
        d["father_name"] = h["father_name"]
    if h.get("mother_name"):
        d["mother_name"] = h["mother_name"]
    if h.get("roll_number"):
        d["roll_number"] = h["roll_number"]

    lines = _tn_lines(text)
    hse_marks, regs, sess = _parse_hse_marks(lines)

    # Detect which subjects appear in the document text or in extracted marks
    detected_subs = set()
    for sub, rx in _HSS_SUBJECTS:
        if re.search(rf"\b(?:{rx})\b", text, re.I):
            detected_subs.add(sub)
    for k in hse_marks:
        for sub, _ in _HSS_SUBJECTS:
            if k.startswith(sub):
                detected_subs.add(sub)

    for sub in detected_subs:
        d[f"{sub}_marks"] = None
        d[f"{sub}_theory_marks"] = None
        d[f"{sub}_internal_marks"] = None
        if sub in _PRACTICAL_SUBJECTS:
            d[f"{sub}_practical_marks"] = None

    d.update(hse_marks)

    if regs and not d.get("register_number"):
        d["register_number"] = regs[0]
    if sess and not d.get("session"):
        d["session"] = sess[0][0]
        d["exam_year"] = sess[0][1]

    # Calculate total marks from subject totals
    found_totals = [d[f"{sub}_marks"] for sub in detected_subs if d.get(f"{sub}_marks") is not None]
    m_tot = re.search(r"(?:TOTAL\s*MARKS|TOTAL)[^\d\n]*(\d{2,4})", text, re.I)
    if m_tot:
        d["total_marks"] = float(m_tot.group(1))
    elif found_totals:
        d["total_marks"] = float(sum(found_totals))

    return _finish_marksheet(d, text)


def parse_state_12th(text: str) -> dict:
    """Parse TN State Board 12th Marksheet (HSE Second Year). Returns valid columns for state_board_12th table."""
    h = _tn_header(text)
    d = {
        "certificate_name": "Higher Secondary Course - Second Year Mark Certificate",
        "examination_board": h["examination_board"],
        "standard": "12th",
        "exam_year": h["exam_year"],
        "session": h["session"],
        "register_number": h["register_number"],
        "permanent_register_number": h["permanent_register_number"],
        "certificate_serial_number": h["certificate_serial_number"],
        "candidate_name": h["candidate_name"],
        "date_of_birth": h["date_of_birth"],
        "school_name": h["school_name"],
        "school_address": h["school_address"],
        "medium_of_instruction": h["medium_of_instruction"],
        "group_code": h.get("group_code"),
        "group_name": h.get("group_name"),
        "group_category": h.get("group_category"),
        "tmr_code_number": h.get("tmr_code_number"),
        "tmr_code_date": h.get("tmr_code_date"),
        "emis_id": h["emis_id"],
        "total_marks": None,
        "maximum_total_marks": 600.0,
        "percentage": None,
        "result": None,
    }
    if h.get("father_name"):
        d["father_name"] = h["father_name"]
    if h.get("mother_name"):
        d["mother_name"] = h["mother_name"]
    if h.get("roll_number"):
        d["roll_number"] = h["roll_number"]

    lines = _tn_lines(text)
    hse_marks, regs, sess = _parse_hse_marks(lines)

    # Detect which subjects appear in the document text or in extracted marks
    detected_subs = set()
    for sub, rx in _HSS_SUBJECTS:
        if re.search(rf"\b(?:{rx})\b", text, re.I):
            detected_subs.add(sub)
    for k in hse_marks:
        for sub, _ in _HSS_SUBJECTS:
            if k.startswith(sub):
                detected_subs.add(sub)

    for sub in detected_subs:
        d[f"{sub}_marks"] = None
        d[f"{sub}_theory_marks"] = None
        d[f"{sub}_internal_marks"] = None
        if sub in _PRACTICAL_SUBJECTS:
            d[f"{sub}_practical_marks"] = None

    d.update(hse_marks)

    if regs and not d.get("register_number"):
        d["register_number"] = regs[0]
    if sess and not d.get("session"):
        d["session"] = sess[0][0]
        d["exam_year"] = sess[0][1]

    # Calculate total marks from subject totals
    found_totals = [d[f"{sub}_marks"] for sub in detected_subs if d.get(f"{sub}_marks") is not None]
    m_tot = re.search(r"(?:TOTAL\s*MARKS|TOTAL)[^\d\n]*(\d{2,4})", text, re.I)
    if m_tot:
        d["total_marks"] = float(m_tot.group(1))
    elif found_totals:
        d["total_marks"] = float(sum(found_totals))

    return _finish_marksheet(d, text)


# ----------------------------------------------------------------------------
# 5. CBSE Marksheets (10th & 12th)
# ----------------------------------------------------------------------------
_CBSE_10TH_SUBJECTS = {
    "english": r"ENGLISH",
    "hindi": r"HINDI",
    "tamil": r"TAMIL",
    "sanskrit": r"SANSKRIT",
    "mathematics": r"MATHEMATICS",
    "science": r"SCIENCE",
    "social_science": r"SOCIAL\s+SCIENCE",
    "information_technology": r"INFORMATION\s+TECHNOLOGY",
    "artificial_intelligence": r"ARTIFICIAL\s+INTELLIGENCE",
    "computer_applications": r"COMPUTER\s+APPLICATIONS",
}

_CBSE_12TH_SUBJECTS = {
    "english": r"ENGLISH",
    "hindi": r"HINDI",
    "tamil": r"TAMIL",
    "sanskrit": r"SANSKRIT",
    "mathematics": r"MATHEMATICS(?!\s+APPLIED)",
    "applied_mathematics": r"APPLIED\s+MATHEMATICS",
    "physics": r"PHYSICS",
    "chemistry": r"CHEMISTRY",
    "biology": r"BIOLOGY",
    "computer_science": r"COMPUTER\s+SCIENCE",
    "informatics_practices": r"INFORMATICS\s+PRACTICES",
    "biotechnology": r"BIOTECHNOLOGY",
    "accountancy": r"ACCOUNTANCY",
    "business_studies": r"BUSINESS\s+STUDIES",
    "economics": r"ECONOMICS",
    "entrepreneurship": r"ENTREPRENEURSHIP",
    "history": r"HISTORY",
    "geography": r"GEOGRAPHY",
    "political_science": r"POLITICAL\s+SCIENCE",
    "sociology": r"SOCIOLOGY",
    "psychology": r"PSYCHOLOGY",
    "philosophy": r"PHILOSOPHY",
    "physical_education": r"PHYSICAL\s+EDUCATION",
    "home_science": r"HOME\s+SCIENCE",
    "fine_arts": r"FINE\s+ARTS|PAINTING",
    "legal_studies": r"LEGAL\s+STUDIES",
}


def _parse_cbse(text: str, subjects_dict: dict, standard: str) -> dict:
    d = {
        "candidate_name": None,
        "roll_number": None,
        "mother_name": None,
        "father_name": None,
        "school_name": None,
        "school_affiliation_number": None,
        "date_of_birth": None,
        "examination_year": None,
        "examination_session": None,
        "total_marks": None,
        "maximum_marks": 500.0 if standard == "10th" else 500.0,
        "percentage": None,
        "overall_grade": None,
        "result": None,
    }
    if standard == "12th":
        d["stream"] = None

    # Header fields
    m_name = re.search(r"(?:Candidate(?:'s)?\s*Name|Name)[^\n:]*[:\-]\s*([A-Za-z\s.]+)", text, re.I)
    if m_name:
        d["candidate_name"] = clean_name(m_name.group(1))
    m_roll = re.search(r"(?:Roll\s*No|Roll\s*Number)[^\d\n]*(\d{6,10})", text, re.I)
    if m_roll:
        d["roll_number"] = m_roll.group(1)
    m_f = re.search(r"(?:Father(?:'s)?\s*Name)[^\n:]*[:\-]\s*([A-Za-z\s.]+)", text, re.I)
    if m_f:
        d["father_name"] = clean_name(m_f.group(1))
    m_m = re.search(r"(?:Mother(?:'s)?\s*Name)[^\n:]*[:\-]\s*([A-Za-z\s.]+)", text, re.I)
    if m_m:
        d["mother_name"] = clean_name(m_m.group(1))
    m_sch = re.search(r"(?:School\s*Name|School)[^\n:]*[:\-]\s*([A-Za-z0-9\s,.\-]+)", text, re.I)
    if m_sch:
        d["school_name"] = re.sub(r"\s+", " ", _strip_tamil(m_sch.group(1)).strip(" :.-"))
    m_aff = re.search(r"(?:Affiliation\s*No)[^\d\n]*(\d{4,10})", text, re.I)
    if m_aff:
        d["school_affiliation_number"] = m_aff.group(1)
    m_dob = re.search(r"(?:Date\s+of\s+Birth|DOB)[^\d\n]*(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{4})", text, re.I)
    if m_dob:
        d["date_of_birth"] = to_date(m_dob.group(1))
    m_yr = re.search(r"\b(20\d{2})\b", text)
    if m_yr:
        d["examination_year"] = int(m_yr.group(1))

    # Subject fields
    totals = []
    for sub, rx in subjects_dict.items():
        d[f"{sub}_marks"] = None
        d[f"{sub}_5_marks"] = None
        d[f"{sub}_grade"] = None
        for l in text.split("\n"):
            if re.search(rf"\b(?:{rx})\b", l, re.I):
                nums = _mark_tokens(l)
                if nums:
                    tot = float(nums[-1])
                    d[f"{sub}_marks"] = tot
                    d[f"{sub}_5_marks"] = tot
                    totals.append(tot)
                    m_gr = re.search(r"\b([A-E][12]?)\b", l)
                    if m_gr:
                        d[f"{sub}_grade"] = m_gr.group(1)
                    break

    if totals:
        d["total_marks"] = float(sum(totals[:5]))
        d["maximum_marks"] = 100.0 * min(5, len(totals))
        d["percentage"] = round(d["total_marks"] * 100 / d["maximum_marks"], 2)
    d["result"] = detect_result(text)
    return d


def parse_cbse_10th(text: str) -> dict:
    return _parse_cbse(text, _CBSE_10TH_SUBJECTS, "10th")


def parse_cbse_12th(text: str) -> dict:
    return _parse_cbse(text, _CBSE_12TH_SUBJECTS, "12th")


# ----------------------------------------------------------------------------
# 6. Transfer Certificates (State Board & CBSE)
# ----------------------------------------------------------------------------
def _make_tc_parser(standard: str, with_subjects: bool, with_education_type: bool, is_cbse: bool = False):
    def parser(text: str) -> dict:
        d = {
            "tc_number": None,
            "admission_number": None,
            "school_name": None,
            "school_address": None,
            "student_name": None,
            "father_name": None,
            "mother_name": None,
            "nationality": "Indian" if re.search(r"\bINDIAN\b", text, re.I) else None,
            "religion": None,
            "community": None,
            "gender": None,
            "date_of_birth": None,
            "identification_marks": [],
            "standard": standard,
            "date_of_admission": None,
            "scholarship_details": None,
            "medical_inspection": None,
            "date_of_leaving": None,
            "character_conduct": None,
            "date_of_tc": None,
            "course_of_study": None,
            "emis_number": None,
        }
        if is_cbse:
            d["school_affiliation_number"] = None
        if with_education_type:
            d["education_type"] = None
        if with_subjects:
            d["subjects_offered"] = []

        # School Name
        m_sch = re.search(r"(?:[ao]f\s+the\s+School|பள்ளியின்\s*பெயர்)[^\)\n]*\)?\s*([A-Za-z0-9\s,\.\-]+)", text, re.I)
        if m_sch:
            s = re.sub(r"[\u0B80-\u0BFF]", "", m_sch.group(1)).strip(" :.-_»«")
            s = re.sub(r"\s+[«<].*$", "", s)
            d["school_name"] = re.sub(r"\s+", " ", s.strip())

        # TC and Admission number
        m_adm = re.search(r"(?:Admission\s*(?:No|Number)|சேர்க்கை\s*எண்)[^\n:]*[:\-]\s*([A-Za-z0-9/\-_]+)", text, re.I)
        if m_adm:
            d["admission_number"] = m_adm.group(1).strip()
        m_tc = re.search(r"(?:T\.?C\.?\s*(?:No|Number)|Transfer\s+Certificate\s*(?:No|Number))[^\n:]*[:\-]\s*([A-Za-z0-9/\-_]+)", text, re.I)
        if m_tc:
            d["tc_number"] = m_tc.group(1).strip()

        # EMIS
        m_emis = re.search(r"(?:EMIS|எமிஸ்)[^\n\d]*(\d{10,20})", text, re.I)
        if m_emis:
            d["emis_number"] = m_emis.group(1)

        # Student Name
        m_name = re.search(r"(?:in\s+block\s+letters\)?|ஆங்கிலத்தில்)[^\n:]*[:\)]\s*([A-Za-z\s.]+)", text, re.I)
        if m_name:
            c = clean_name(m_name.group(1))
            if c and len(c) >= 3 and not re.search(r"\b(?:GOVERNMENT|SCHOOL|TAMIL|NADU|AS|IN|THE|LE)\b", c):
                d["student_name"] = c
        if not d["student_name"]:
            m_pupil = re.search(r"(?:Name\s+of\s+(?:the\s+)?(?:Pupil|Student)|மாணவரின்\s+பெயர்)[^\n:]*[:\)]\s*([A-Za-z\s.]+)", text, re.I)
            if m_pupil:
                c = clean_name(m_pupil.group(1))
                if c and len(c) >= 3 and not re.search(r"\b(?:GOVERNMENT|SCHOOL|TAMIL|NADU|AS|IN|THE|LE)\b", c):
                    d["student_name"] = c

        # Parents
        m_f = re.search(r"(?:Father(?:'?s)?\s*name|தந்தையின்\s*பெயர்)[^\)\n:]*[\):\-]\s*([A-Za-z\s.]+)", text, re.I)
        if m_f:
            d["father_name"] = clean_name(m_f.group(1))
        m_m = re.search(r"(?:Mother(?:'?s)?\s*name|தாயின்\s*பெயர்)[^\)\n:]*[\):\-]\s*([A-Za-z\s.]+)", text, re.I)
        if m_m:
            d["mother_name"] = clean_name(m_m.group(1))

        # Gender
        m_gline = re.search(r"(?:Gender|பாலினம்)[^\n]*", text, re.I)
        if m_gline:
            gl = m_gline.group(0)
            if re.search(r"\bFemale\b|பெண்", gl, re.I):
                d["gender"] = "Female"
            elif re.search(r"\bMale\b|ஆண்", gl, re.I):
                d["gender"] = "Male"
        if not d["gender"]:
            if re.search(r"\bFemale\b", text, re.I):
                d["gender"] = "Female"
            elif re.search(r"\bMale\b", text, re.I):
                d["gender"] = "Male"

        # DOB
        m_dob = re.search(r"(?:பிறந்த\s*CoP|Date\s+of\s+Birth|Date\s+of\s+N?e?t?h)[^\d\n]*(\d{1,2}[\-/]\d{1,2}[\-/]\d{4})", text, re.I)
        if m_dob:
            dt_val = m_dob.group(1)
            if "2084" in dt_val and "TWO THOUSAND FOUR" in text.upper():
                dt_val = dt_val.replace("2084", "2004")
            d["date_of_birth"] = to_date(dt_val)

        # Admission Date
        m_adm_d = re.search(r"(?:சேர்க்கப்பட்ட|Admission\s*Date|Admission\s*Mate)[^\d\n]*(\d{1,2}[\-/]\d{1,2}[\-/]\d{4})", text, re.I)
        if m_adm_d:
            d["date_of_admission"] = to_date(m_adm_d.group(1))

        # ID Marks
        marks = []
        for l in text.split("\n"):
            if re.search(r"MOL[EPIL]\.?\s*ON", l, re.I):
                m_mark = re.search(r"A\s*MOL[EPIL]\.?\s*ON\s+[A-Za-z\s]+", l, re.I)
                if m_mark:
                    clean_mark = re.sub(r"MOL[EPIL]\.?", "MOLE", m_mark.group(0), flags=re.I)
                    clean_mark = re.sub(r"\bTHR\b", "THE", clean_mark, flags=re.I)
                    clean_mark = re.sub(r"\bCLIN\b", "CHIN", clean_mark, flags=re.I)
                    clean_mark = re.sub(r"\bLEE\b", "LEFT", clean_mark, flags=re.I)
                    marks.append(re.sub(r"\s+", " ", clean_mark.strip()))
        d["identification_marks"] = marks

        # Education type
        if with_education_type:
            if re.search(r"General\s+Education", text, re.I):
                d["education_type"] = "General Education"
            elif re.search(r"Vocational\s+Education", text, re.I):
                d["education_type"] = "Vocational Education"

        # Subjects offered
        if with_subjects:
            m_sub = re.search(r"([A-Z]{4,}(?:[|/][A-Z]{4,}){2,})", text)
            if m_sub:
                subs = [s.strip() for s in re.split(r"[/|]", m_sub.group(1)) if len(s.strip()) > 2]
                d["subjects_offered"] = subs

        # Course of study
        m_cos = re.search(r"(?:Course\s+of\s+Study|பாடப்பிரிவு)[^\n:]*[:\-]\s*([A-Za-z0-9\s,\-]+)", text, re.I)
        if m_cos:
            d["course_of_study"] = re.sub(r"\s+", " ", m_cos.group(1).strip())

        return d

    return parser


parse_state_10th_tc = _make_tc_parser("10th", False, False, False)
parse_state_12th_tc = _make_tc_parser("12th", True, True, False)
parse_cbse_10th_tc = _make_tc_parser("10th", False, False, True)
parse_cbse_12th_tc = _make_tc_parser("12th", True, True, True)


# ----------------------------------------------------------------------------
# 7. Birth Certificate
# ----------------------------------------------------------------------------
def parse_birth_certificate(text: str) -> dict:
    """Parse Form 5 and standard bilingual Birth Certificates."""
    d = {
        "certificate_number": None,
        "student_name": None,
        "gender": None,
        "date_of_birth": None,
        "place_of_birth": None,
        "father_name": None,
        "mother_name": None,
        "permanent_address": None,
        "issuing_authority": None,
        "issue_date": None,
    }

    lines = [l.strip() for l in text.split("\n") if l.strip()]

    # Student Name
    m = re.search(r"(?:Name|பெயர்)\s*[:/]\s*(?:பெயர்\s*[:/]\s*)?([A-Za-z][A-Za-z .]{2,60})", text, re.I)
    if not m:
        m = re.search(r"Name\s*/\s*[^:\n]+:\s*([A-Za-z][A-Za-z .]{2,60})", text, re.I)
    if m:
        d["student_name"] = clean_name(m.group(1))

    # Registration / Certificate Number
    m_reg = re.search(r"(?:Registration\s*No|Certificate\s*No|பதிவு\s*எண்)[^\n\d]*[:\- ]\s*([A-Za-z0-9/_\- ]+)", text, re.I)
    if m_reg and re.search(r"\d", m_reg.group(1)):
        d["certificate_number"] = m_reg.group(1).strip()
    if not d["certificate_number"]:
        m_reg2 = re.search(r"(?<!\d)(\d{3,6}/\d{4}/\d{1,4})(?!\d)", text)
        if m_reg2:
            d["certificate_number"] = m_reg2.group(1)
        else:
            m_reg3 = re.search(r"(?<!\d)([A-Z]{1,3}/\d{4}/\d{4,8})(?!\d)", text)
            if m_reg3:
                d["certificate_number"] = m_reg3.group(1)

    # Gender
    if re.search(r"\bFEMALE\b|பெண்பால்", text, re.I):
        d["gender"] = "Female"
    elif re.search(r"\bMALE\b|ஆண்பால்", text, re.I):
        d["gender"] = "Male"

    # Dates
    dates = []
    for l in lines:
        dt = to_date(l)
        if dt:
            dates.append(dt)

    m_dob = re.search(r"(?:Date\s+of\s+Birth|பிறந்த\s+தேதி)[^\n\d]*[:\- ]\s*([0-9/\-\.]+)", text, re.I)
    if m_dob and to_date(m_dob.group(1)):
        d["date_of_birth"] = to_date(m_dob.group(1))

    # Parents
    m_f = re.search(r"(?:Name\s+of\s+Father|Father\s*(?:Name)?|தந்தையின்\s*பெயர்)[^\n:]*:\s*([A-Za-z][A-Za-z .]{2,50})", text, re.I)
    if m_f:
        d["father_name"] = clean_name(m_f.group(1))
    m_m = re.search(r"(?:Name\s+of\s+Mother|Mother\s*(?:Name)?|தாயின்\s*பெயர்)[^\n:]*:\s*([A-Za-z][A-Za-z .]{2,50})", text, re.I)
    if m_m:
        d["mother_name"] = clean_name(m_m.group(1))

    # Form 5 layout positional values block
    if not d["father_name"] or not d["mother_name"]:
        for l in lines:
            m_cand = re.search(r"^:\s*([A-Z]\.[A-Z\s]{3,40})$", l)
            if m_cand:
                val = clean_name(m_cand.group(1))
                if not d["father_name"] and val != d["student_name"]:
                    d["father_name"] = val
                elif d["father_name"] and not d["mother_name"] and val != d["student_name"] and val != d["father_name"]:
                    d["mother_name"] = val

    # Place of Birth
    m_pob = re.search(r"(?:Place\s+of\s+Birth|பிறந்த\s+இடம்)[^\n:]*:\s*([A-Za-z0-9 ,.\-]{3,60})", text, re.I)
    if m_pob and not re.search(r"[\u0B80-\u0BFF]", m_pob.group(1)):
        d["place_of_birth"] = m_pob.group(1).strip(" :.-")
    else:
        m_hosp = re.search(r":\s*([A-Z0-9\s,\.\-]+HOSPITAL[^\n]*)", text, re.I)
        if m_hosp:
            d["place_of_birth"] = m_hosp.group(1).strip(" :.-")

    # Permanent address
    m_addr = re.search(r":\s*(\d+[A-Za-z0-9\s,.\-]+(?:STREET|ROAD|NAGAR|T\.N\.|TAMILNADU|TAMIL\s+NADU)[^\n]*)", text, re.I)
    if m_addr:
        d["permanent_address"] = re.sub(r"\s+", " ", m_addr.group(1).strip(" :.-"))

    # Dates assignment
    if not d["date_of_birth"] and dates:
        d["date_of_birth"] = min(dates)
    if dates:
        later = [dt for dt in dates if dt != d["date_of_birth"]]
        if later:
            d["issue_date"] = max(later)

    # Authority
    m_auth = re.search(r"(?:Issuing\s+Authority|வழங்குபவர்)[^\n:]*:\s*([A-Za-z\s,.\-]+)", text, re.I)
    if m_auth:
        d["issuing_authority"] = re.sub(r"\s+", " ", m_auth.group(1).strip(" :.-"))
    elif re.search(r"MUNICIPAL\s+CORPORATION|Corporation|Municipality", text, re.I):
        m_corp = re.search(r"([A-Za-z\s]+(?:MUNICIPAL\s+CORPORATION|Corporation|Municipality))", text, re.I)
        if m_corp:
            d["issuing_authority"] = re.sub(r"\s+", " ", m_corp.group(1).strip())

    return d


# ----------------------------------------------------------------------------
# 8. Aadhaar Card
# ----------------------------------------------------------------------------
def parse_aadhaar(text: str) -> dict:
    """Parse Aadhaar card. Only last 4 digits are returned."""
    d = {
        "name_on_card": None,
        "date_of_birth": None,
        "father_name": None,
        "address": None,
        "aadhaar_last_four": None,
    }

    # Mask VID and get last 4 digits
    text_clean = re.sub(r"VID\s*:?\s*(?:\d{4}[ \t]?){4}", " ", text, flags=re.I)
    m_nums = re.findall(r"(?<!\d)\d{4}[ \t]?\d{4}[ \t]?(\d{4})(?!\d)", text_clean)
    if m_nums:
        d["aadhaar_last_four"] = m_nums[-1]

    # DOB
    m_dob = re.search(r"(?:DOB|Year\s+of\s+Birth|Date\s+of\s+Birth|பிறந்த\s+நாள்)\s*[:/]?\s*([0-9/\-\.]+)", text, re.I)
    if m_dob and to_date(m_dob.group(1)):
        d["date_of_birth"] = to_date(m_dob.group(1))

    # Father / Relative: D/O, S/O, W/O, C/O: <Name>
    m_rel = re.search(r"(?:[SDWC]/O|Father|Husband)\s*[:\-]?\s*([A-Za-z][A-Za-z .]{2,40})", text, re.I)
    if m_rel:
        d["father_name"] = clean_name(m_rel.group(1))

    # Name on card: Anchor to line before DOB or line before D/O
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    cand_names = []

    # Strategy 1: Prioritize line directly above DOB
    for i, l in enumerate(lines):
        if re.search(r"\b(?:DOB|Date\s+of\s+Birth|Year\s+of\s+Birth|பிறந்த\s+நாள்)\b", l, re.I):
            for offset in range(1, 4):
                if i - offset >= 0:
                    cand = _strip_tamil(lines[i - offset])
                    cand = re.sub(r"^[^A-Za-z]+|[^A-Za-z.]+$", "", cand).strip()
                    if cand and len(cand) >= 3 and not re.search(r"\b(?:Government|India|Unique|Identification|Enrollment|Aadhaar|Male|Female|Address|DOB)\b", cand, re.I):
                        c = clean_name(cand)
                        if c:
                            cand_names.append(c)
                            break

    # Strategy 2: Line directly above D/O, S/O
    for i, l in enumerate(lines):
        if re.search(r"\b[DSWC]/O\s*:", l, re.I):
            for offset in range(1, 3):
                if i - offset >= 0:
                    cand = _strip_tamil(lines[i - offset])
                    cand = re.sub(r"^[^A-Za-z]+|[^A-Za-z.]+$", "", cand).strip()
                    if cand and len(cand) >= 3 and not re.search(r"\b(?:Government|India|Unique|Identification|Enrollment|Aadhaar|Male|Female|Address|To)\b", cand, re.I):
                        c = clean_name(cand)
                        if c:
                            cand_names.append(c)
                            break

    if cand_names:
        d["name_on_card"] = cand_names[0]

    # Address: Either after D/O, S/O or "Address: ..." ending at 6-digit pincode
    addr_lines = []
    capturing = False
    for l in lines:
        if re.search(r"\b[DSWC]/O\s*:", l, re.I) or re.search(r"\b(?:Address|முகவரி)\b", l, re.I):
            capturing = True
            continue
        if capturing:
            clean = _strip_tamil(l)
            clean = re.sub(r"[^\x20-\x7E]", " ", clean)
            clean = re.sub(r"^[=\|<>\s\(\)\d/]+", "", clean).strip()
            clean = re.sub(r"[\(\)\|<>=]+$", "", clean).strip()
            if re.search(r"\b(?:Aadhaar|Unique|Government|India|DOB|Phone|Help)\b", clean, re.I):
                break
            if re.match(r"^\d{10}$", clean) or re.match(r"^\d{4}\s*\d{4}", clean):
                break
            if clean:
                addr_lines.append(clean)
            if re.search(r"\b\d{6}\b", l):
                break
    if addr_lines:
        d["address"] = ", ".join(addr_lines)

    return d


# ----------------------------------------------------------------------------
# 9. Revenue Certificates (Community, Income, Nativity)
# ----------------------------------------------------------------------------
def _label_value(text: str, labels) -> Optional[str]:
    """Read a value from common `Label: Value` certificate layouts."""
    for line in (text or "").splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        for label in labels:
            match = re.match(r"^\s*" + re.escape(label) + r"\s*(?:[:：=|/-])\s*(.*?)\s*$", line, re.I)
            if match:
                value = match.group(1).strip(" \t:：=|/-.,;")
                if value and value not in {"-", "—", "N/A", "NA"}:
                    return value
    return None


def _first_date(text: str, labels=None) -> Optional[str]:
    """Prefer dates attached to a matching label; otherwise use the existing date helper."""
    if labels:
        raw = _label_value(text, labels)
        if raw:
            date_match = re.search(r"\b(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{1,2}-\d{1,2})\b", raw)
            if date_match:
                parsed = to_date(date_match.group(0))
                if parsed:
                    return parsed
    for line in (text or "").splitlines():
        parsed = to_date(line)
        if parsed:
            return parsed
    return None


def parse_community_certificate(text: str) -> dict:
    """Parse label/value and narrative-style Tamil Nadu community certificates."""
    source = text or ""
    d = {
        "certificate_number": None,
        "candidate_name": None,
        "father_name": None,
        "community_name": None,
        "caste_name": None,
        "address": None,
        "issuing_authority": None,
        "issue_date": None,
    }

    # Explicit labels are the most reliable source for modern e-District layouts.
    d["certificate_number"] = _label_value(source, [
        "Certificate Number", "Certificate No", "Certificate No.", "Cert No",
        "Certificate ID", "Application Number", "Application No",
    ])
    d["candidate_name"] = _label_value(source, [
        "Name of the Candidate", "Candidate Name", "Applicant Name", "Name",
    ])
    d["father_name"] = _label_value(source, [
        "Father's Name", "Father’s Name", "Father Name", "Name of Father",
        "Father / Guardian Name", "Father/Guardian Name",
    ])
    d["community_name"] = _label_value(source, ["Community Name", "Community", "Category"])
    d["caste_name"] = _label_value(source, ["Caste Name", "Caste", "Sub Caste"])
    d["address"] = _label_value(source, [
        "Residential Address", "Permanent Address", "Address of Applicant", "Address", "Residence",
    ])
    d["issuing_authority"] = _label_value(source, [
        "Issuing Authority", "Issued By", "Competent Authority", "Designation", "Authority",
    ])
    d["issue_date"] = _first_date(source, ["Date of Issue", "Issue Date", "Date of Issuance", "Issued Date"])

    # Certificate numbers can contain slashes, hyphens, and year segments.
    if not d["certificate_number"]:
        m = re.search(r"(?i)(?:certificate\s*(?:no\.?|number|id)|application\s*(?:no\.?|number))\s*[:#/-]?\s*([A-Z0-9][A-Z0-9/_-]{4,30})", source)
        if m:
            d["certificate_number"] = m.group(1).strip(" /-")
        else:
            m = re.search(r"\b[A-Z]{1,5}/\d{4}/[A-Z0-9/-]{3,18}\b", source, re.I)
            if m:
                d["certificate_number"] = m.group(0)

    # Narrative-style certificate wording: “certify that ... son/daughter of ... residing ...”
    if not d["candidate_name"] or not d["father_name"]:
        m = re.search(
            r"certify\s+that\s+(?:(?:Thiru|Selvi|Selvan|Kumari|Mr|Mrs|Ms|Shri|Sri|Smt)\.?\s*)?"
            r"([A-Za-z][A-Za-z0-9 .]{1,60}?)\s+(?:son|daughter|wife|ward)\s+of\s+"
            r"(?:(?:Thiru|Selvi|Selvan|Kumari|Mr|Mrs|Ms|Shri|Sri|Smt)\.?\s*)?"
            r"([A-Za-z][A-Za-z0-9 .]{1,50}?)\s+(?:residing|ordinarily\s+reside)", source, re.I)
        if m:
            d["candidate_name"] = d["candidate_name"] or clean_name(m.group(1))
            d["father_name"] = d["father_name"] or clean_name(m.group(2))

    if not d["address"]:
        m = re.search(r"(?:residing|reside(?:s)?)\s+at\s+(.+?)(?=\s+belongs?\s+to\b|\n|$)", source, re.I | re.S)
        if m:
            d["address"] = re.sub(r"\s+", " ", m.group(1).strip(" ,.-"))

    if not d["caste_name"]:
        m = re.search(r"belongs?\s+to\s+([A-Za-z][A-Za-z0-9 .-]{1,60}?)\s+(?:Community|Caste)\b", source, re.I)
        if m:
            d["caste_name"] = re.sub(r"\s+", " ", m.group(1).strip(" ,.-"))

    # Normalize community category without overwriting a more specific labelled value.
    if d["community_name"]:
        category = d["community_name"].strip()
        if re.search(r"Most\s+Back\w*ard\s+Class|\bMBC\b", category, re.I):
            d["community_name"] = "Most Backward Class"
        elif re.search(r"Backward\s+Class|\bBC\b", category, re.I):
            d["community_name"] = "Backward Class"
        elif re.search(r"Scheduled\s+Caste|\bSC\b", category, re.I):
            d["community_name"] = "Scheduled Caste"
        elif re.search(r"Scheduled\s+Tribe|\bST\b", category, re.I):
            d["community_name"] = "Scheduled Tribe"
    elif re.search(r"Most\s+Back\w*ard\s+Class|\bMBC\b", source, re.I):
        d["community_name"] = "Most Backward Class"
    elif re.search(r"Backward\s+Class|\bBC\b", source, re.I):
        d["community_name"] = "Backward Class"
    elif re.search(r"Scheduled\s+Caste|\bSC\b", source, re.I):
        d["community_name"] = "Scheduled Caste"
    elif re.search(r"Scheduled\s+Tribe|\bST\b", source, re.I):
        d["community_name"] = "Scheduled Tribe"

    if not d["issuing_authority"]:
        m = re.search(r"(?im)^.*?\b(?:Tahsildar|Tehsildar|Revenue\s+Divisional\s+Officer|Deputy\s+Tahsildar)\b[^\n]*", source)
        if m:
            d["issuing_authority"] = re.sub(r"\s+", " ", m.group(0).strip(" :/-"))

    if not d["issue_date"]:
        d["issue_date"] = _first_date(source)

    if d["candidate_name"]:
        d["candidate_name"] = clean_name(d["candidate_name"])
    if d["father_name"]:
        d["father_name"] = clean_name(d["father_name"])
    return d


def parse_income_certificate(text: str) -> dict:
    """Parse common e-District income-certificate layouts and labelled OCR lines."""
    d = apply_specs(text, [
        ("certificate_number", TEXT, [r"Income\s+Certificate\s*(?:No|Number)"] + L_CERT_NAME),
        ("candidate_name", TEXT, L_NAME),
        ("annual_income", NUM, [r"Annual\s+(?:Family\s+)?Income", r"Family\s+Income", r"\bIncome\b"]),
        ("occupation", TEXT, [r"Occupation", r"Profession"]),
        ("address", TEXT, L_ADDRESS),
        ("issuing_authority", TEXT, L_ISSUER + [r"Tahsildar", r"Tehsildar"]),
        ("issue_date", DATE, L_ISSUE_DATE),
    ])
    fallbacks = {
        "certificate_number": ["Certificate Number", "Certificate No", "Certificate No.", "Application Number"],
        "candidate_name": ["Name of the Candidate", "Candidate Name", "Applicant Name", "Name"],
        "annual_income": ["Annual Family Income", "Annual Income", "Family Income", "Income"],
        "occupation": ["Occupation", "Profession"],
        "address": ["Permanent Address", "Residential Address", "Address"],
        "issuing_authority": ["Issuing Authority", "Issued By", "Competent Authority"],
    }
    for key, labels in fallbacks.items():
        if not d.get(key):
            value = _label_value(text, labels)
            if value:
                d[key] = value
    if d.get("candidate_name"):
        d["candidate_name"] = clean_name(d["candidate_name"])
    if d.get("annual_income") is not None:
        try:
            amount = re.sub(r"[^0-9.]", "", str(d["annual_income"]))
            d["annual_income"] = float(amount) if amount else None
        except (TypeError, ValueError):
            d["annual_income"] = None
    if not d.get("certificate_number"):
        m = re.search(r"(?i)(?:certificate\s*(?:no\.?|number)|application\s*(?:no\.?|number))\s*[:#/-]?\s*([A-Z0-9][A-Z0-9/_-]{4,30})", text or "")
        if m:
            d["certificate_number"] = m.group(1).strip(" /-")
    if not d.get("issue_date"):
        d["issue_date"] = _first_date(text, ["Date of Issue", "Issue Date", "Issued Date"])
    return d


def parse_nativity_certificate(text: str) -> dict:
    """Parse Nativity / domicile certificates with labelled and narrative layouts."""
    d = apply_specs(text, [
        ("certificate_number", TEXT, [r"Nativity\s+Certificate\s*(?:No|Number)"] + L_CERT_NAME),
        ("candidate_name", TEXT, L_NAME),
        ("address", TEXT, L_ADDRESS),
        ("issuing_authority", TEXT, L_ISSUER + [r"Tahsildar", r"Tehsildar"]),
        ("issue_date", DATE, L_ISSUE_DATE),
    ])
    fallbacks = {
        "certificate_number": ["Certificate Number", "Certificate No", "Certificate No.", "Application Number"],
        "candidate_name": ["Name of the Candidate", "Candidate Name", "Applicant Name", "Name"],
        "address": ["Permanent Address", "Residential Address", "Address"],
        "issuing_authority": ["Issuing Authority", "Issued By", "Competent Authority"],
    }
    for key, labels in fallbacks.items():
        if not d.get(key):
            d[key] = _label_value(text, labels)
    if d.get("candidate_name"):
        d["candidate_name"] = clean_name(d["candidate_name"])
    if not d.get("certificate_number"):
        m = re.search(r"(?i)(?:certificate\s*(?:no\.?|number)|application\s*(?:no\.?|number))\s*[:#/-]?\s*([A-Z0-9][A-Z0-9/_-]{4,30})", text or "")
        if m:
            d["certificate_number"] = m.group(1).strip(" /-")
    if not d.get("issue_date"):
        d["issue_date"] = _first_date(text, ["Date of Issue", "Issue Date", "Issued Date"])
    return d


# ----------------------------------------------------------------------------
# 10. Higher Education (Degree, Diploma Certificate, Diploma Semester)
# ----------------------------------------------------------------------------
def parse_degree_certificate(text: str) -> dict:
    """Parse Degree Certificate."""
    d = {
        "certificate_number": None,
        "college_name": None,
        "university_name": None,
        "candidate_name": None,
        "register_number": None,
        "date_of_birth": None,
        "degree_name": None,
        "department": None,
        "graduation_year": None,
        "classification": None,
        "result": None,
    }

    # University / College
    m_uni = re.search(r"([A-Za-z\s]+UNIVERSITY[^\n]*)", text, re.I)
    if m_uni:
        d["university_name"] = re.sub(r"\s+", " ", m_uni.group(1).strip())
    m_col = re.search(r"([A-Za-z\s.,\-]+(?:COLLEGE|INSTITUTE)[^\n]*)", text, re.I)
    if m_col:
        d["college_name"] = re.sub(r"\s+", " ", m_col.group(1).strip(" :.-"))

    # Candidate Name
    m_name = re.search(r"(?:certify\s+that|Name\s*[:\-])\s*([A-Za-z\s.]+?)(?:has\s+passed|having|Register|\n)", text, re.I)
    if m_name:
        d["candidate_name"] = clean_name(m_name.group(1))

    # Register Number
    m_reg = re.search(r"(?:Register\s*(?:No|Number)|Reg\s*No)[^\w\n]*([A-Za-z0-9]+)", text, re.I)
    if m_reg:
        d["register_number"] = m_reg.group(1)

    # Degree & Department
    m_deg = re.search(r"(?:degree\s+of\s+|DEGREE\s+OF\s+)?\b(B\.?E\.?|B\.?TECH\.?|M\.?E\.?|M\.?TECH\.?|B\.?SC\.?|M\.?SC\.?|B\.?A\.?|M\.?A\.?|B\.?COM\.?|M\.?COM\.?|B\.?C\.?A\.?|M\.?C\.?A\.?)\b[^\n,]*", text, re.I)
    if m_deg:
        d["degree_name"] = m_deg.group(0).strip()
    m_dept = re.search(r"(?:BRANCH|DEPARTMENT|IN)\s*[:\-]?\s*([A-Za-z\s]+(?:ENGINEERING|TECHNOLOGY|SCIENCE|ARTS|COMMERCE)[^\n,]*)", text, re.I)
    if m_dept:
        d["department"] = re.sub(r"\s+", " ", m_dept.group(1).strip())

    # Graduation Year
    m_yr = re.search(r"\b(20\d{2})\b", text)
    if m_yr:
        d["graduation_year"] = int(m_yr.group(1))

    # Classification
    if re.search(r"FIRST\s+CLASS\s+WITH\s+DISTINCTION", text, re.I):
        d["classification"] = "FIRST CLASS WITH DISTINCTION"
    elif re.search(r"FIRST\s+CLASS", text, re.I):
        d["classification"] = "FIRST CLASS"
    elif re.search(r"SECOND\s+CLASS", text, re.I):
        d["classification"] = "SECOND CLASS"

    d["result"] = detect_result(text)
    return d


def parse_diploma_certificate(text: str) -> dict:
    """Parse Diploma Certificate."""
    d = {
        "certificate_number": None,
        "college_name": None,
        "emis_number": None,
        "candidate_name": None,
        "register_number": None,
        "date_of_birth": None,
        "course_name": None,
        "department": None,
        "completion_year": None,
        "classification": None,
        "result": None,
    }

    m_col = re.search(r"([A-Za-z\s.,\-]+(?:POLYTECHNIC|COLLEGE)[^\n]*)", text, re.I)
    if m_col:
        d["college_name"] = re.sub(r"\s+", " ", m_col.group(1).strip(" :.-"))

    m_name = re.search(r"(?:certify\s+that|Name\s*[:\-])\s*([A-Za-z\s.]+?)(?:has\s+passed|having|Register|\n)", text, re.I)
    if m_name:
        d["candidate_name"] = clean_name(m_name.group(1))

    m_reg = re.search(r"(?:Register\s*(?:No|Number)|Reg\s*No)[^\w\n]*([A-Za-z0-9]+)", text, re.I)
    if m_reg:
        d["register_number"] = m_reg.group(1)

    m_crs = re.search(r"\bDIPLOMA\s+IN\s+([A-Za-z\s]+(?:ENGINEERING|TECHNOLOGY)[^\n,]*)", text, re.I)
    if m_crs:
        d["course_name"] = f"DIPLOMA IN {m_crs.group(1).strip()}"
        d["department"] = m_crs.group(1).strip()

    m_yr = re.search(r"\b(20\d{2})\b", text)
    if m_yr:
        d["completion_year"] = int(m_yr.group(1))

    if re.search(r"FIRST\s+CLASS\s+WITH\s+DISTINCTION", text, re.I):
        d["classification"] = "FIRST CLASS WITH DISTINCTION"
    elif re.search(r"FIRST\s+CLASS", text, re.I):
        d["classification"] = "FIRST CLASS"

    d["result"] = detect_result(text)
    return d


def parse_diploma_semester(text: str) -> dict:
    """Parse Diploma Semester Marksheet."""
    d = {
        "college_name": None,
        "emis_number": None,
        "candidate_name": None,
        "register_number": None,
        "date_of_birth": None,
        "course_name": None,
        "department": None,
        "semester": None,
        "exam_month": None,
        "exam_year": None,
        "total_marks": None,
        "maximum_marks": None,
        "percentage": None,
        "overall_grade": None,
        "result": None,
    }

    # Initialize 8 subjects
    for i in range(1, 9):
        d[f"subject_{i}_name"] = None
        d[f"subject_{i}_code"] = None
        d[f"subject_{i}_internal_marks"] = None
        d[f"subject_{i}_external_marks"] = None
        d[f"subject_{i}_practical_marks"] = None
        d[f"subject_{i}_total_marks"] = None
        d[f"subject_{i}_maximum_marks"] = 100.0
        d[f"subject_{i}_grade"] = None
        d[f"subject_{i}_credits"] = None

    m_name = re.search(r"(?:Candidate(?:'s)?\s*Name|Name)[^\n:]*[:\-]\s*([A-Za-z\s.]+)", text, re.I)
    if m_name:
        d["candidate_name"] = clean_name(m_name.group(1))
    m_reg = re.search(r"(?:Register\s*(?:No|Number)|Reg\s*No)[^\w\n]*([A-Za-z0-9]+)", text, re.I)
    if m_reg:
        d["register_number"] = m_reg.group(1)
    m_sem = re.search(r"(?:SEMESTER|SEM)[^\w\n]*([0-9IVX]+)", text, re.I)
    if m_sem:
        d["semester"] = str(m_sem.group(1))

    m_tot = re.search(r"(?:TOTAL\s*MARKS|TOTAL)[^\d\n]*(\d{2,4})", text, re.I)
    if m_tot:
        d["total_marks"] = float(m_tot.group(1))
    d["result"] = detect_result(text)
    return d


# ----------------------------------------------------------------------------
# 11. Validation
# ----------------------------------------------------------------------------
def validate_state_12th(d: dict, text: str = "") -> List[str]:
    warns = []
    if not d.get("candidate_name"):
        warns.append("Candidate name not found on marksheet.")
    if d.get("total_marks") is None:
        warns.append("Total marks could not be verified.")
    group = str(d.get("group_category") or "").strip().casefold()
    expected_by_group = {
        "biology": ("tamil", "english", "physics", "chemistry", "biology", "mathematics"),
        "computer science": ("tamil", "english", "physics", "chemistry", "computer_science", "mathematics"),
        "arts": ("tamil", "english", "economics", "commerce", "accountancy", "computer_applications"),
    }
    expected_subjects = expected_by_group.get(group, ())
    if expected_subjects:
        missing = [subject.replace("_", " ").title() for subject in expected_subjects if d.get(f"{subject}_marks") is None]
        if missing:
            warns.append("Could not confidently extract these group subjects: " + ", ".join(missing) + ". Review the marks table before saving.")
        subject_totals = [d.get(f"{subject}_marks") for subject in expected_subjects]
        if d.get("total_marks") is not None and not missing:
            calculated = sum(float(value) for value in subject_totals)
            if abs(float(d["total_marks"]) - calculated) > 1:
                warns.append(f"Subject marks total ({calculated:g}) does not match the printed total ({float(d['total_marks']):g}). Review the table before saving.")
    return warns


def validate_state_10th(d: dict, text: str = "") -> List[str]:
    """Validate TN SSLC fields and flag inconsistent values without guessing replacements."""
    warns = []
    if not d.get("candidate_name"):
        warns.append("Candidate name not found; verify against the original certificate.")
    marks = [d.get(f"subject_{i}_marks") for i in range(1, 6)]
    known = [float(v) for v in marks if v is not None]
    for i, value in enumerate(marks, 1):
        if value is not None and not 0 <= float(value) <= 100:
            warns.append(f"Subject {i} marks are outside the expected 0-100 range.")
    if len(known) < 5:
        missing = [str(i) for i, value in enumerate(marks, 1) if value is None]
        warns.append("Marks missing for subject(s): " + ", ".join(missing) + "; verify the scan.")
    if d.get("total_marks") is None:
        if len(known) == 5:
            calculated = sum(known)
            d["total_marks"] = float(calculated)
        else:
            warns.append("Total marks could not be verified because one or more subject marks are missing.")
    elif len(known) == 5 and abs(float(d["total_marks"]) - sum(known)) > 0.01:
        warns.append(f"Printed total ({d['total_marks']}) does not match the sum of extracted subject marks ({sum(known):g}). Verify the OCR and certificate.")
    if d.get("total_marks") is not None and not 0 <= float(d["total_marks"]) <= 500:
        warns.append("Total marks are outside the expected 0-500 range.")
    return warns

VALIDATORS = {
    "state_board_10th": validate_state_10th,
    "state_board_12th": validate_state_12th,
}


def validate_document(doc_type: str, fields: dict, text: str = "", saved_name=None, saved_dob=None) -> list:
    """Warnings for a parsed document."""
    fn = VALIDATORS.get(doc_type)
    warns = fn(fields, text) if fn else []
    if saved_name or saved_dob:
        warns += compare_with_record(fields, saved_name, saved_dob)
    return warns


# ----------------------------------------------------------------------------
# 12. Registry
# Exactly 16 document types mapping 1-to-1 to existing Supabase tables
# ----------------------------------------------------------------------------
def _cfg(table: str, parser, extracted_text: bool = True, verification: bool = True) -> dict:
    return {
        "table": table,
        "parser": parser,
        "extracted_text": extracted_text,
        "verification": verification,
    }


def detect_document_type(text: str) -> Optional[str]:
    """Detect document type based on layout, keywords, and examination board headers."""
    u = text.upper()

    # Aadhaar Card
    if re.search(r"\b(?:AADHAAR|UNIQUE\s+IDENTIFICATION|UIDAI|GOVERNMENT\s+OF\s+INDIA\s+MALE|GOVERNMENT\s+OF\s+INDIA\s+FEMALE)\b", u):
        return "aadhaar_card"

    # Transfer Certificates
    if re.search(r"\b(?:TRANSFER\s+CERTIFICATE|மாற்றுச்\s*சான்றிதழ்)\b", u):
        if "CBSE" in u:
            return "cbse_12th_tc" if re.search(r"\b(?:XII|12TH|SENIOR)\b", u) else "cbse_10th_tc"
        return "state_board_12th_tc" if re.search(r"\b(?:XII|12TH|SECOND\s+YEAR|HIGHER\s+SECONDARY)\b", u) else "state_board_10th_tc"

    # Community / Caste Certificate
    if re.search(r"\b(?:COMMUNITY\s+CERTIFICATE|CASTE\s+CERTIFICATE|சாதிச்\s*சான்றிதழ்)\b", u):
        return "community_certificate"

    # Income Certificate
    if re.search(r"\b(?:INCOME\s+CERTIFICATE|வருமானச்\s*சான்றிதழ்)\b", u):
        return "income_certificate"

    # Nativity / Residence Certificate
    if re.search(r"\b(?:NATIVITY\s+CERTIFICATE|RESIDENCE\s+CERTIFICATE|இருப்பிடச்\s*சான்றிதழ்)\b", u):
        return "nativity_certificate"

    # Birth Certificate
    if re.search(r"\b(?:BIRTH\s+CERTIFICATE|CERTIFICATE\s+OF\s+BIRTH|பிறப்புச்\s*சான்றிதழ்)\b", u):
        return "birth_certificate"

    # Tamil Nadu SSLC 10th Marksheet
    if re.search(r"\b(?:SECONDARY\s+SCHOOL\s+LEAVING\s+CERTIFICATE|X\s+STANDARD|பத்தாம்\s*வகுப்பு|SSLC)\b", u):
        return "state_board_10th"

    # Tamil Nadu Higher Secondary (+1 / 11th and +2 / 12th) Marksheets
    if re.search(r"\b(?:HIGHER\s+SECONDARY|FIRST\s+YEAR\s+MARK|SECOND\s+YEAR\s+MARK|மேல்நிலைப்\s*பள்ளி)\b", u) or re.search(r"\b(?:HSS|HSC)\b", u):
        if re.search(r"\b(?:FIRST\s+YEAR|முதலாமாண்டு|XI\s+STANDARD|\+1)\b", u) and not re.search(r"\bSECOND\s+YEAR|இரண்டாமாண்டு\b", u):
            return "state_board_11th"
        return "state_board_12th"

    # CBSE Marksheets
    if "CBSE" in u or "CENTRAL BOARD OF SECONDARY EDUCATION" in u:
        if re.search(r"\b(?:SENIOR\s+SCHOOL|CLASS\s+XII|CLASS\s+12)\b", u):
            return "cbse_12th_marksheet"
        return "cbse_10th_marksheet"

    # Degree & Diploma
    if re.search(r"\b(?:DEGREE\s+EXAMINATION|BACHELOR|MASTER\s+OF)\b", u):
        return "degree_certificate"
    if re.search(r"\b(?:DIPLOMA\s+COURSE|POLYTECHNIC|DIRECTORATE\s+OF\s+TECHNICAL\s+EDUCATION)\b", u):
        return "diploma_certificate"

    return None


def detect_scored(text: str) -> dict:
    """Backward-compatible scored detection result for API routes.

    This wraps the existing rule-based detector. Confidence is intentionally conservative;
    uncertain layouts should be reviewed rather than silently treated as certain.
    """
    detected = detect_document_type(text or "")
    if not detected:
        return {"type": None, "ok": False, "score": 0.0, "confidence": 0.0,
                "runner_up": None, "evidence": []}
    u = (text or "").upper()
    keywords = {
        "state_board_10th": ["SECONDARY SCHOOL LEAVING CERTIFICATE", "SSLC", "X STANDARD"],
        "state_board_12th": ["HIGHER SECONDARY", "HSC", "SECOND YEAR MARK"],
        "state_board_11th": ["FIRST YEAR MARK", "XI STANDARD"],
        "cbse_10th_marksheet": ["CBSE", "SECONDARY SCHOOL EXAMINATION", "CLASS X"],
        "cbse_12th_marksheet": ["CBSE", "SENIOR SCHOOL CERTIFICATE", "CLASS XII"],
        "state_board_10th_tc": ["TRANSFER CERTIFICATE", "SCHOOL"],
        "state_board_12th_tc": ["TRANSFER CERTIFICATE", "HIGHER SECONDARY"],
        "cbse_10th_tc": ["TRANSFER CERTIFICATE", "CBSE"],
        "cbse_12th_tc": ["TRANSFER CERTIFICATE", "CBSE", "CLASS XII"],
        "community_certificate": ["COMMUNITY CERTIFICATE", "COMMUNITY", "CASTE"],
        "income_certificate": ["INCOME CERTIFICATE", "ANNUAL INCOME"],
        "nativity_certificate": ["NATIVITY CERTIFICATE", "RESIDENCE CERTIFICATE"],
        "birth_certificate": ["BIRTH CERTIFICATE", "DATE OF BIRTH"],
        "aadhaar_card": ["AADHAAR", "UIDAI", "UNIQUE IDENTIFICATION"],
    }
    evidence = [word for word in keywords.get(detected, []) if word in u]
    score = float(max(1, len(evidence)))
    confidence = min(0.95, 0.45 + 0.1 * len(evidence))
    return {"type": detected, "ok": bool(evidence), "score": score,
            "confidence": round(confidence, 2), "runner_up": None, "evidence": evidence}


DOC_TYPES: Dict[str, Dict[str, Any]] = {
    # Marksheets
    "state_board_10th": _cfg("state_board_10th", parse_state_10th, False, False),
    "state_board_11th": _cfg("state_board_12th", parse_state_11th, False, False),
    "state_board_12th": _cfg("state_board_12th", parse_state_12th, False, False),
    "cbse_10th_marksheet": _cfg("cbse_10th_marksheet", parse_cbse_10th, False, True),
    "cbse_12th_marksheet": _cfg("cbse_12th_marksheet", parse_cbse_12th, False, True),
    "diploma_semester_marksheet": _cfg("diploma_semester_marksheet", parse_diploma_semester, False, True),
    # Transfer Certificates
    "state_board_10th_tc": _cfg("state_board_10th_tc", parse_state_10th_tc),
    "state_board_12th_tc": _cfg("state_board_12th_tc", parse_state_12th_tc),
    "cbse_10th_tc": _cfg("cbse_10th_tc", parse_cbse_10th_tc),
    "cbse_12th_tc": _cfg("cbse_12th_tc", parse_cbse_12th_tc),
    # Supporting Certificates
    "birth_certificate": _cfg("birth_certificate", parse_birth_certificate),
    "aadhaar_card": _cfg("aadhaar_card", parse_aadhaar),
    "community_certificate": _cfg("community_certificate", parse_community_certificate),
    "income_certificate": _cfg("income_certificate", parse_income_certificate),
    "nativity_certificate": _cfg("nativity_certificate", parse_nativity_certificate),
    "degree_certificate": _cfg("degree_certificate", parse_degree_certificate),
    "diploma_certificate": _cfg("diploma_certificate", parse_diploma_certificate),
}


def parse_document(doc_type: str, text: str) -> dict:
    """Execute parser for the requested document type and return structured fields."""
    canonical_type = doc_type

    # Dynamic layout adaptation for Tamil Nadu HSE Marksheets
    if canonical_type in ("state_board_11th", "state_board_12th"):
        u = text.upper()
        if re.search(r"\b(?:FIRST\s+YEAR|முதலாமாண்டு|XI\s+STANDARD|\+1)\b", u) and not re.search(r"\bSECOND\s+YEAR|இரண்டாமாண்டு\b", u):
            canonical_type = "state_board_11th"
        elif re.search(r"\b(?:SECOND\s+YEAR|இரண்டாமாண்டு|XII\s+STANDARD|\+2)\b", u):
            canonical_type = "state_board_12th"

    if canonical_type not in DOC_TYPES:
        detected = detect_document_type(text)
        if detected and detected in DOC_TYPES:
            canonical_type = detected
        else:
            raise ValueError(f"Unknown doc_type '{doc_type}'.")

    return copy.deepcopy(DOC_TYPES[canonical_type]["parser"](text))
