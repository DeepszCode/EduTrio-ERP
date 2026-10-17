"""
ocr_service.py - SMART ERP
Text extraction (PDF / image) + certificate field parsing + validation.
Every parser returns a dict whose keys match the columns in your SQL tables.
"""
import io
import os
import re
from datetime import datetime

import cv2
import numpy as np
import pymupdf as fitz  # PyMuPDF
import pytesseract
from dotenv import load_dotenv
from PIL import Image, ImageOps

load_dotenv()  # must run before os.getenv() below

TESS_LANG = (os.getenv("OCR_LANGUAGES") or os.getenv("TESS_LANG") or "eng").replace(",", "+").replace(" ", "")
MIN_PDF_TEXT_CHARS = 80
PDF_DPI = 300

# Tesseract path: .env value first, else the default Windows install location
_DEFAULT_TESS = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
_tess_cmd = (os.getenv("TESSERACT_CMD") or "").strip().strip('"')
if not _tess_cmd and os.path.exists(_DEFAULT_TESS):
    _tess_cmd = _DEFAULT_TESS
if _tess_cmd:
    pytesseract.pytesseract.tesseract_cmd = _tess_cmd

TEXT, DATE, INT, NUM = "text", "date", "int", "num"


# ----------------------------------------------------------------------------
# 1. Text extraction
# ----------------------------------------------------------------------------
def _clean(text: str) -> str:
    text = text.replace("\r", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _variants(img: Image.Image) -> dict:
    """Preprocessed versions of the page. ALL arrays share the same size/geometry, so a box found
    on one can be cropped from the others.
      plain : grayscale, normalised (no binarisation)
      otsu  : global threshold
      ink   : min(R,G,B) - makes red/coloured ink (e.g. the serial number) as dark as black ink
    """
    img = ImageOps.exif_transpose(img).convert("RGB")
    rgb = np.array(img)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    ink = np.ascontiguousarray(np.min(rgb, axis=2))

    h, w = gray.shape
    if w < 2000:  # upscale small images only
        scale = 2000 / w
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        ink = cv2.resize(ink, (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_CUBIC)

    plain = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
    ink = cv2.normalize(ink, None, 0, 255, cv2.NORM_MINMAX)
    _, otsu = cv2.threshold(cv2.GaussianBlur(gray, (3, 3), 0), 0, 255,
                            cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # white margin (keeps edge characters from being dropped)
    pad = lambda a: cv2.copyMakeBorder(a, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)
    return {"plain": pad(plain), "otsu": pad(otsu), "ink": pad(ink)}


# Serial-number re-read. Used when the normal OCR text has no clean "HSS #######".
_SERIAL_OK = re.compile(r"CERTIFICATE\s+S[LtI1l]\.?\s*NO\.?\s*[:\-]?\s*[A-Z]{2,5}\s*\d{7,8}\b")
_TN_CERT_HINT = re.compile(r"CERTIFICATE\s+S[LtI1l]|SCHOOL\s+EXAMINATIONS|GOVERNMENT\s+EXAMINATIONS", re.I)
_SERIAL_PATTERNS = [
    (re.compile(r"H[S5]{2}(\d{7})(?!\d)"), "HSS"),   # 12th: HSS + 7 digits
    (re.compile(r"SEC(\d{8})(?!\d)"), "SEC"),        # 10th: SEC + 8 digits
]


def _serial_from_raw(raw: str):
    s = re.sub(r"[^A-Z0-9]", "", (raw or "").upper())
    for rx, prefix in _SERIAL_PATTERNS:
        m = rx.search(s)
        if m:
            return f"{prefix} {m.group(1)}"
    return None


def _serial_crop_ocr(anchor_arr, arrays):
    """Re-read the serial number: anchor on 'HSS'/'SEC' or 'SL.NO:' in the top of the page, crop that line
    from every array (same geometry), try several scales / thresholds / psm modes and accept only a result
    that >= 2 attempts agree on and that beats the runner-up."""
    h, w = anchor_arr.shape
    top = anchor_arr[: int(h * 0.35), :]
    d = pytesseract.image_to_data(
        Image.fromarray(top), lang="eng", config="--oem 3 --psm 6",
        output_type=pytesseract.Output.DICT,
    )
    anchor = None
    for i, t in enumerate(d["text"]):
        t = t.strip()
        if re.match(r"(?:H[S5]{2}|SEC)", t):
            anchor = (d["left"][i] - 12, i)
            break
        if anchor is None and re.search(r"N[O0][.:]*$", t, re.I) and re.search(r"S[LI1]", t, re.I):
            anchor = (d["left"][i] + d["width"][i], i)    # start right after "SL.NO:"
    if anchor is None:
        return None

    x0, i = anchor
    y, hh = d["top"][i], d["height"][i]
    pad = int(hh * 0.8) + 10
    y0, y1 = max(0, y - pad), y + hh + pad
    x0, x1 = max(0, x0), min(w, x0 + int(w * 0.35))
    if y1 <= y0 or x1 <= x0:
        return None

    votes = {}
    for arr in arrays:
        strip = arr[y0:y1, x0:x1]
        if strip.size == 0:
            continue
        for scale in (2, 3):
            s = cv2.resize(strip, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            _, otsu = cv2.threshold(s, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            for img in (s, otsu):
                for psm in (7, 8):
                    raw = pytesseract.image_to_string(
                        Image.fromarray(img), lang="eng",
                        config=f"--oem 3 --psm {psm} -c tessedit_char_whitelist=HSEC0123456789",
                    )
                    if os.getenv("DEBUG_OCR") == "1":
                        print("serial attempt:", scale, psm, repr(raw))
                    serial = _serial_from_raw(raw)
                    if serial:
                        votes[serial] = votes.get(serial, 0) + 1

    if os.getenv("DEBUG_OCR") == "1":
        cv2.imwrite("debug_serial.png", arrays[0][y0:y1, x0:x1])
    if votes:
        ranked = sorted(votes.items(), key=lambda kv: kv[1], reverse=True)
        best, n = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else 0
        if n >= 2 and n > runner_up:
            return best
    return None


def _ocr_image(img: Image.Image) -> str:
    variants = _variants(img)
    best = (-1, None, None)  # (score, variant name, config)
    for name in ("plain", "otsu"):
        arr = variants[name]
        for psm in (6, 3):
            cfg = f"--oem 3 --psm {psm}"
            data = pytesseract.image_to_data(
                Image.fromarray(arr), lang=TESS_LANG, config=cfg,
                output_type=pytesseract.Output.DICT,
            )
            # score = total letters in confidently-read words
            score = sum(
                len(w) for w, c in zip(data["text"], data["conf"])
                if w.strip() and float(c) >= 60
            )
            if score > best[0]:
                best = (score, name, cfg)

    _, name, cfg = best
    arr = variants[name]
    if os.getenv("DEBUG_OCR") == "1":
        cv2.imwrite("debug_ocr.png", arr)  # saved in the folder you run uvicorn from

    text = pytesseract.image_to_string(Image.fromarray(arr), lang=TESS_LANG, config=cfg)

    # Only for Tamil Nadu board sheets: if the serial number was not read cleanly, re-read just that line
    if _TN_CERT_HINT.search(text) and not _SERIAL_OK.search(text):
        serial = _serial_crop_ocr(arr, [variants["plain"], variants["ink"]])
        if serial:
            text += f"\nSERIAL_HINT: {serial}"
    return text


def extract_text(content: bytes, filename: str) -> str:
    """PDF -> embedded text first (PyMuPDF), OCR fallback for scanned PDFs. Images -> OCR."""
    if filename.lower().endswith(".pdf"):
        doc = fitz.open(stream=content, filetype="pdf")
        try:
            text = "\n".join(page.get_text("text") for page in doc)
            if len(text.strip()) >= MIN_PDF_TEXT_CHARS:
                return _clean(text)

            # scanned PDF -> render each page to an RGB image (colour kept for the serial re-read), then OCR
            zoom = PDF_DPI / 72
            parts = []
            for page in doc:
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csRGB)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                parts.append(_ocr_image(img))
            return _clean("\n".join(parts))
        finally:
            doc.close()

    return _clean(_ocr_image(Image.open(io.BytesIO(content))))


def mask_sensitive(text: str) -> str:
    """Mask Aadhaar (12 digits, keep last 4) and VID (16 digits, mask all) so full numbers are never stored.
    Lines that mention EMIS are left alone (a 16-digit EMIS ID is not a VID)."""
    out = []
    for line in text.split("\n"):
        if re.search(r"\bEMIS\b", line, re.I):
            out.append(line)
            continue
        line = VID_RE.sub("VID: XXXX XXXX XXXX XXXX", line)
        line = re.sub(r"(?<!\d)\d{4}[ \t]?\d{4}[ \t]?\d{4}[ \t]?\d{4}(?!\d)", "XXXX XXXX XXXX XXXX", line)
        line = re.sub(r"(?<!\d)\d{4}[ \t]?\d{4}[ \t]?(\d{4})(?![ \t]?\d)", r"XXXX XXXX \1", line)
        out.append(line)
    return "\n".join(out)


# ----------------------------------------------------------------------------
# 2. Generic helpers
# ----------------------------------------------------------------------------
_DATE_FORMATS = [
    "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d",
    "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y",
    "%d/%m/%y", "%d-%m-%y", "%d.%m.%y",
]
_DATE_RX = re.compile(
    r"\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}|\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}\s+[A-Za-z]{3,9},?\s+\d{4}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}"
)


def to_date(s):
    if not s:
        return None
    m = _DATE_RX.search(s)
    if not m:
        return None
    cand = re.sub(r"\s+", " ", m.group(0).replace(",", "")).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cand, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def to_num(s):
    if not s:
        return None
    m = re.search(r"\d[\d,]*\.?\d*", s)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def to_year(s):
    if not s:
        return None
    m = re.search(r"(?:19|20)\d{2}", s)
    return int(m.group(0)) if m else None


def normalize_name(s):
    """Letters only, upper-case: 'DEEPIKA V S' / 'Deepika V.S.' / 'DEEPIKA VS' -> 'DEEPIKAVS'."""
    return re.sub(r"[^A-Z]", "", (s or "").upper())


def names_match(a, b):
    """True when two names differ only in spacing, punctuation or case."""
    na, nb = normalize_name(a), normalize_name(b)
    return bool(na) and na == nb


def compare_with_record(fields: dict, saved_name=None, saved_dob=None) -> list:
    """Warnings when a parsed sheet disagrees with the saved student record.
    Spacing/punctuation-only name differences ('V S' vs 'VS') are NOT reported."""
    warns = []
    name = fields.get("candidate_name") or fields.get("student_name") or fields.get("name_on_card")
    if saved_name and name and not names_match(name, saved_name):
        warns.append(f"Name differs from the saved record: '{saved_name}' (this sheet reads '{name}')")
    if saved_dob and fields.get("date_of_birth") and str(saved_dob) != str(fields["date_of_birth"]):
        warns.append(f"Date of birth differs from the saved record: {saved_dob} vs {fields['date_of_birth']}")
    return warns


def find_field(text, labels):
    """Return the text after the first matching label (same line, else next line)."""
    lines = text.split("\n")
    for lab in labels:
        rx = re.compile(rf"(?:{lab})[ \t]*[:\-–—.]*[ \t]*(.*)", re.I)
        for i, line in enumerate(lines):
            m = rx.search(line)
            if not m:
                continue
            val = m.group(1).strip(" :-–—.\t")
            if not val and i + 1 < len(lines):
                val = lines[i + 1].strip(" :-–—.\t")
            if val:
                return val
    return None


def block_after(text, label_rx, max_lines=1):
    """Lines starting at a label: its remainder + following lines until the next 'Label:' line."""
    lines = text.split("\n")
    rx = re.compile(rf"(?:{label_rx})[ \t]*[:\-–—.]*[ \t]*(.*)", re.I)
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


def _convert(raw, kind):
    if raw is None:
        return None
    if kind == DATE:
        return to_date(raw)
    if kind == INT:
        return to_year(raw)
    if kind == NUM:
        return to_num(raw)
    return re.sub(r"\s+", " ", raw).strip()[:255] or None


def apply_specs(text, specs):
    return {col: _convert(find_field(text, labels), kind) for col, kind, labels in specs}


def detect_result(text):
    m = re.search(r"\b(PASS(?:ED)?|FAIL(?:ED)?|PROMOTED|ABSENT|COMPARTMENT)\b", text, re.I)
    return m.group(1).upper() if m else None


def _split_list(items):
    joined = " ".join(items)
    parts = re.split(r"\s*(?:\(?[ivx]+\)|\(?\d\)|\d\.|;|,)\s*", joined)
    return [p.strip(" .") for p in parts if p and p.strip(" .")]


def _finish_marksheet(d, totals, default_max=None):
    """Fill total / max / percentage if they were not read from the sheet."""
    if d.get("total_marks") is None and totals:
        d["total_marks"] = float(sum(totals))
    if d.get("maximum_total_marks") is None and "maximum_total_marks" in d:
        d["maximum_total_marks"] = default_max or (100.0 * len(totals) if totals else None)
    mx = d.get("maximum_total_marks") if "maximum_total_marks" in d else d.get("maximum_marks")
    if d.get("percentage") is None and d.get("total_marks") and mx:
        d["percentage"] = round(d["total_marks"] * 100 / mx, 2)
    return d


# ----------------------------------------------------------------------------
# 3. Shared label sets
# ----------------------------------------------------------------------------
L_NAME = [
    r"Name\s+of\s+the\s+(?:Candidate|Student|Pupil)",
    r"(?:Candidate|Student|Pupil)'?s?\s+Name",
    r"(?<!College )(?<!School )(?<!Course )(?<!Degree )(?<!University )(?<!'s )\bName\b(?!\s+of)",
]
L_FATHER = [
    r"Father'?s?\s*(?:/\s*Guardian'?s?)?\s*Name",
    r"Name\s+of\s+(?:the\s+)?Father(?:\s*/\s*Guardian)?",
    r"Father",
]
L_MOTHER = [r"Mother'?s?\s*Name", r"Name\s+of\s+(?:the\s+)?Mother", r"Mother"]
L_DOB = [r"Date\s+of\s+Birth", r"\bD\.?O\.?B\.?\b"]
L_ISSUER = [r"Issuing\s+Authority", r"Issued\s+By"]
L_ISSUE_DATE = [r"Date\s+of\s+Issue", r"Issue\s+Date", r"\bDate\b"]
L_CERT_NO = [r"Certificate\s*(?:No|Number)", r"Serial\s*(?:No|Number)", r"Reference\s*(?:No|Number)"]
L_ADDRESS = [r"Permanent\s+Address", r"Address"]
L_ROLL = [r"Roll\s*(?:No|Number)"]
L_SCHOOL = [r"Name\s+of\s+(?:the\s+)?School", r"School\s+Name", r"School"]
L_COLLEGE = [r"Name\s+of\s+(?:the\s+)?(?:College|Institution)", r"College\s+Name"]
L_EMIS = [r"\bEMIS\s*(?:ID|No|Number)?"]


# ----------------------------------------------------------------------------
# 4. State Board marksheets (Tamil Nadu)
# ----------------------------------------------------------------------------
_MONTH = r"(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*"
_LANGS = r"(?:ENGLISH|TAMIL|HINDI|TELUGU|MALAYALAM|KANNADA|URDU)"
_REG_SESSION = re.compile(rf"\b\d{{7}}\b\s*{_MONTH}\s*\d{{4}}")
_MONTH_YEAR = re.compile(rf"\b{_MONTH}\s+\d{{4}}\b")


def _tn_lines(text):
    return [l.strip() for l in text.split("\n") if l.strip()]


def _tn_region(lines):
    """Lines of the subject table: after 'Certified that' and before 'TOTAL MARKS'."""
    start, end = 0, len(lines)
    for i, l in enumerate(lines):
        if re.search(r"Certified\s+that", l, re.I):
            start = i + 1
            break
    for i in range(start, len(lines)):
        if re.search(r"TOTAL\s+MARKS", lines[i], re.I):
            end = i
            break
    return lines[start:end]


def _mark_tokens(line):
    """2-3 digit mark values on a line (register number / month-year / fractions removed)."""
    if re.search(r"\d\s*/\s*\d", line):  # header row like 70/90 20/75
        return []
    line = _REG_SESSION.sub(" ", line)
    line = _MONTH_YEAR.sub(" ", line)
    return [int(x) for x in re.findall(r"(?<![\d.])\d{2,3}(?![\d.])", line) if int(x) <= 100]


def _subject_numbers(lines, rx, claimed):
    """Marks for one subject. The numbers sit on the subject-name line or on the line above."""
    pat = re.compile(rf"\b(?:{rx})\b", re.I)
    for i, line in enumerate(lines):
        if i in claimed or not pat.search(line):
            continue
        nums, src = _mark_tokens(line), i
        if len(nums) < 2 and i > 0 and (i - 1) not in claimed:
            nums, src = _mark_tokens(lines[i - 1]), i - 1
        if len(nums) >= 2:
            claimed.update({i, src})
            return nums
    return None


def _tn_name(lines, text):
    for l in lines:
        m = re.search(r"(?:^|\s)([A-Z][A-Z .]{1,40}?)\s+MARK\s+CERTIFICATE\s*/", l)
        if m:
            return m.group(1).strip()
    val = find_field(text, L_NAME)  # fallback, accepted only if it looks like a name
    if val and re.fullmatch(r"[A-Za-z .]{3,40}", val.strip()):
        return val.strip()
    return None


def _tn_school(lines):
    """School name = longest all-caps, multi-word run in the 3 lines after 'NAME OF THE SCHOOL'."""
    for i, l in enumerate(lines):
        if re.search(r"NAME\s+OF\s+THE\s+SCHOOL", l, re.I):
            best = ""
            for nxt in lines[i + 1: i + 4]:
                for m in re.finditer(r"[A-Z][A-Z0-9.,&()\-! ]{7,}", nxt):
                    cand = m.group(0).strip()
                    if " " in cand and len(cand) > len(best):
                        best = cand
            if best:
                # "!" -> "I", then drop 1-2 letter OCR noise at the end ("... PUDUPPAI SS")
                # and at the start ("NY GNANASAMBANDAR ..."); "ST" is kept (e.g. "ST JOSEPH")
                name = re.sub(r"!$", "I", best.strip(" -.,"))
                name = re.sub(r"(?:\s+[A-Z]{1,2})+$", "", name)
                name = re.sub(r"^(?:(?!ST\b)[A-Z]{1,2}\s+)+", "", name)
                return name.strip(" -.,")
    return None


def _tn_header(text):
    """Fields shared by the 10th and 12th Tamil Nadu sheets."""
    lines = _tn_lines(text)
    d = {
        "certificate_serial_number": None, "examination_board": None, "certificate_name": None,
        "candidate_name": _tn_name(lines, text), "date_of_birth": None,
        "roll_number": None, "register_number": None, "permanent_register_number": None,
        "medium_of_instruction": None, "session": None, "exam_year": None,
        "school_name": _tn_school(lines), "school_address": None, "emis_id": None,
        "total_marks": None, "maximum_total_marks": None, "percentage": None, "result": None,
    }

    # "CERTIFICATE SL.NO: HSS 3203002" (OCR sometimes reads "SL." as "St.")
    m = re.search(r"CERTIFICATE\s+S[LtI1l]\.?\s*NO\.?\s*[:\-]?\s*([A-Z]{2,5}\s*\d{5,10})", text)
    if m:
        d["certificate_serial_number"] = re.sub(r"\s+", " ", m.group(1))

    # fall back to the re-read serial from _serial_crop_ocr
    if not d["certificate_serial_number"]:
        m = re.search(r"SERIAL_HINT:\s*([A-Z]{2,5} \d{7,8})", text)
        if m:
            d["certificate_serial_number"] = m.group(1)

    if re.search(r"Government\s+Examinations|Tamil\s*Nadu", text, re.I):
        d["examination_board"] = "Directorate of Government Examinations, Tamil Nadu"
    if re.search(r"SECOND\s+YEAR\s+MARK\s+CERTIFICATE", text):
        d["certificate_name"] = "Higher Secondary Second Year Mark Certificate"
    elif re.search(r"SECONDARY\s+SCHOOL\s+LEAVING\s+CERTIFICATE", text):
        d["certificate_name"] = "Secondary School Leaving Certificate"
    elif re.search(r"Statement\s+of\s+Marks", text, re.I):
        d["certificate_name"] = "Statement of Marks"
    elif re.search(r"MARK\s+CERTIFICATE", text):
        d["certificate_name"] = "Mark Certificate"

    # "25/11/2006 2313218290 MAR 2024"  -> DOB, permanent register no., session
    m = re.search(rf"(\d{{2}}[/.\-]\d{{2}}[/.\-]\d{{4}})\s+(\d{{8,12}})\s+({_MONTH}\s+\d{{4}})", text)
    if m:
        d["date_of_birth"] = to_date(m.group(1))
        d["permanent_register_number"] = m.group(2)
        d["session"] = m.group(3)
    else:
        d["date_of_birth"] = to_date(find_field(text, L_DOB))
        ym = _MONTH_YEAR.search(text)
        d["session"] = ym.group(0) if ym else None
    d["exam_year"] = to_year(d["session"])

    m = re.search(rf"\b(\d{{7}})\s+{_MONTH}\s+\d{{4}}", text)  # "6218315 MAR 2024"
    if m:
        d["register_number"] = m.group(1)
        d["roll_number"] = m.group(1)  # TN sheets have no separate roll number

    m = re.search(r"EMIS\s*ID\s*No\.?\s*[:\-]?\s*(\d{6,})", text, re.I)
    if m:
        d["emis_id"] = m.group(1)

    m = re.search(r"TOTAL\s+MARKS\s*[:\-]?\s*0*(\d{2,4})", text, re.I)
    if m:
        d["total_marks"] = float(m.group(1))

    d["result"] = detect_result(text)
    return d


# ---- 12th (HSS) -------------------------------------------------------------
_HSS_SUBJECTS = [
    ("tamil", r"TAMIL"),
    ("english", r"ENGLISH"),
    ("physics", r"PHYSICS"),
    ("chemistry", r"CHEMISTRY"),
    ("computer_science", r"COMPUTER\s+SCIENCE"),
    # the scan often garbles this name (e.g. "watuemarics"), so a row-based fallback exists below
    ("mathematics", r"MATH\w*|\w*(?:EMATICS|EMARICS)"),
]


def _hss_assign(d, prefix, nums):
    """Row layouts: theory/practical/internal/total (4), theory/internal/total (3), or just the total."""
    d[f"{prefix}_marks"] = nums[-1]
    if len(nums) >= 4:
        d[f"{prefix}_theory_marks"] = nums[0]
        d[f"{prefix}_practical_marks"] = nums[1]
        d[f"{prefix}_internal_marks"] = nums[2]
    elif len(nums) == 3:
        d[f"{prefix}_theory_marks"] = nums[0]
        d[f"{prefix}_internal_marks"] = nums[1]


def _tn_group(lines):
    """(group_code, group_name, medium) from the lines right after 'GROUP CODE AND NAME'."""
    code = name = medium = None
    for i, l in enumerate(lines):
        if re.search(r"GROUP\s+CODE", l, re.I):
            for b in lines[i + 1: i + 4]:
                m = re.search(rf"/\s*([A-Z][A-Z ]+?)\s+({_LANGS})\b", b)
                if m and name is None:
                    name, medium = m.group(1).strip(), m.group(2)
                m = re.search(r"(?<!\d)(\d{4})(?!\d)", b)
                if m and code is None:
                    code = m.group(1)
            break
    return code, name, medium


def parse_state_12th(text):
    lines = _tn_lines(text)
    d = _tn_header(text)
    d["group_code"], d["group_name"], d["medium_of_instruction"] = _tn_group(lines)
    for prefix, _ in _HSS_SUBJECTS:
        d[f"{prefix}_marks"] = None

    region = _tn_region(lines)
    claimed, totals = set(), []
    for prefix, rx in _HSS_SUBJECTS:
        nums = _subject_numbers(region, rx, claimed)
        if nums:
            _hss_assign(d, prefix, nums)
            totals.append(nums[-1])

    # Fallback: a mark row whose subject name was too garbled to match (usually Mathematics)
    if d.get("mathematics_marks") is None:
        for i, l in enumerate(region):
            if i not in claimed and _REG_SESSION.search(l):
                nums = _mark_tokens(l)
                if len(nums) >= 2:
                    claimed.add(i)
                    _hss_assign(d, "mathematics", nums)
                    totals.append(nums[-1])
                    break

    return _finish_marksheet(d, totals, default_max=600.0)  # 6 papers x 100


# ---- 10th (SSLC) ------------------------------------------------------------
_NUM_WORDS = r"(?:ZERO|ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|HUNDRED)"
_SSLC_ORDER = 5  # first language, English, Mathematics, Science, Social Science


def parse_state_10th(text):
    lines = _tn_lines(text)
    d = _tn_header(text)
    d["tmr_code_number"] = None
    d["tmr_code_date"] = None
    for i in range(1, _SSLC_ORDER + 1):
        d[f"subject_{i}_name"] = None
        d[f"subject_{i}_marks"] = None

    # candidate name = capitals just before the session ("DEEPIKAV S MAY 2022")
    for l in lines:
        m = re.search(rf"(?:^|[\s)])([A-Z][A-Z .]{{2,40}}?)\s+{_MONTH}\s+\d{{4}}\b", l)
        if m:
            d["candidate_name"] = m.group(1).strip()
            break

    # "25/11/2006 1265535 ENGLISH" -> date of birth, roll number, medium
    m = re.search(rf"(\d{{2}}[/.\-]\d{{2}}[/.\-]\d{{4}})\s+(\d{{6,8}})\s+({_LANGS})\b", text)
    if m:
        d["date_of_birth"] = to_date(m.group(1))
        d["roll_number"] = d["register_number"] = m.group(2)
        d["medium_of_instruction"] = m.group(3)

    m = re.search(r"\bXM\d{2}R\d+\b", text)
    if m:
        d["permanent_register_number"] = m.group(0)

    if not d["certificate_serial_number"]:
        m = re.search(r"\bSEC\s*(\d{8})\b", text)
        if m:
            d["certificate_serial_number"] = f"SEC {m.group(1)}"

    m = re.search(r"\b([A-Z]\d{7})\s+(\d{2})\.(\d{2})\.(\d{4})\b", text)
    if m:
        d["tmr_code_number"] = m.group(1)
        d["tmr_code_date"] = f"{m.group(4)}-{m.group(3)}-{m.group(2)}"

    # Each subject row is "<marks...> <total in words>", e.g. "083 ZERO EIGHT THREE" or
    # "052 025 077 ZERO SEVEN SEVEN" (theory, internal, total). The last number is the total.
    totals = []
    for l in lines:
        if re.search(r"TOTAL\s+MARKS", l, re.I):
            continue
        m = re.search(rf"((?:(?<!\d)\d{{3}}(?!\d)[ \t]+){{1,3}}){_NUM_WORDS}\b", l)
        if m:
            totals.append(int(re.findall(r"\d{3}", m.group(1))[-1]))

    # SSLC order is fixed; the first language is read from the text (Tamil, Hindi, ...)
    names = ["Tamil", "English", "Mathematics", "Science", "Social Science"]
    region_text = " ".join(_tn_region(lines))
    m = re.search(r"\b(TAMIL|HINDI|TELUGU|MALAYALAM|KANNADA|URDU|FRENCH|SANSKRIT)\b", region_text)
    if m:
        names[0] = m.group(1).title()

    for i, t in enumerate(totals[:_SSLC_ORDER], start=1):
        d[f"subject_{i}_marks"] = t
        d[f"subject_{i}_name"] = names[i - 1]

    return _finish_marksheet(d, totals[:_SSLC_ORDER], default_max=500.0)


# ----------------------------------------------------------------------------
# 5. CBSE marksheets
# ----------------------------------------------------------------------------
CBSE_COMMON = [
    ("candidate_name", TEXT, L_NAME),
    ("father_name", TEXT, L_FATHER),
    ("mother_name", TEXT, L_MOTHER),
    ("roll_number", TEXT, L_ROLL),
    ("school_name", TEXT, L_SCHOOL),
    ("school_affiliation_number", TEXT, [r"Affiliation\s*(?:No|Number)"]),
    ("date_of_birth", DATE, L_DOB),
    ("examination_year", INT, [r"Examination\s*Year", r"Year\s+of\s+Examination", r"Examination"]),
    ("total_marks", NUM, [r"Total\s*Marks", r"Grand\s*Total"]),
    ("maximum_marks", NUM, [r"Maximum\s*Marks"]),
    ("percentage", NUM, [r"Percentage"]),
    ("overall_grade", TEXT, [r"Overall\s+Grade"]),
]
CBSE_10_SUBJECTS = [
    ("english", r"English"), ("hindi", r"Hindi"), ("tamil", r"Tamil"),
    ("sanskrit", r"Sanskrit"), ("mathematics", r"Mathematics"),
    ("science", r"Science"), ("social_science", r"Social\s+Science"),
    ("information_technology", r"Information\s+Technology"),
    ("computer_applications", r"Computer\s+Applications"),
    ("artificial_intelligence", r"Artificial\s+Intelligence"),
]
CBSE_12_SUBJECTS = CBSE_10_SUBJECTS[:5] + [
    ("applied_mathematics", r"Applied\s+Mathematics"),
    ("physics", r"Physics"), ("chemistry", r"Chemistry"), ("biology", r"Biology"),
    ("computer_science", r"Computer\s+Science"),
    ("informatics_practices", r"Informatics\s+Practices"),
    ("biotechnology", r"Biotechnology"), ("accountancy", r"Accountancy"),
    ("business_studies", r"Business\s+Studies"), ("economics", r"Economics"),
    ("entrepreneurship", r"Entrepreneurship"), ("history", r"History"),
    ("geography", r"Geography"), ("political_science", r"Political\s+Science"),
    ("sociology", r"Sociology"), ("psychology", r"Psychology"),
    ("philosophy", r"Philosophy"), ("physical_education", r"Physical\s+Education"),
    ("home_science", r"Home\s+Science"), ("fine_arts", r"Fine\s+Arts"),
    ("legal_studies", r"Legal\s+Studies"),
]


def _cbse_subject(text, name_rx):
    """CBSE line e.g. '041 ENGLISH LNG & LIT 085 EIGHTY FIVE A1' -> (85.0, 'A1')."""
    m = re.search(
        rf"^[ \t]*(?P<code>\d{{3}}[ \t]+)?(?:{name_rx})\b(?P<rest>.*)$", text, re.I | re.M
    )
    if not m:
        return None, None
    rest = m.group("rest")
    nums = re.findall(r"\b\d{2,3}\b", rest)
    if not nums:
        return None, None
    idx = 0 if (m.group("code") or len(nums) == 1) else 1
    marks = float(nums[idx])
    if marks > 100:
        return None, None
    # OCR often reads the digit in A1/B1/C1 as l, I, i, | or !  -> normalise to 1
    g = re.search(r"\b([A-E])([12lLiI|!]?)\s*$", rest.strip(), re.I)
    grade = None
    if g:
        dg = g.group(2)
        grade = g.group(1).upper() + ("" if not dg else ("2" if dg == "2" else "1"))
    return marks, grade


def _parse_cbse(text, subjects, with_stream):
    d = apply_specs(text, CBSE_COMMON)
    s = re.search(r"\b(Main|Supplementary|Compartment|Improvement)\b", text, re.I)
    d["examination_session"] = s.group(1).title() if s else None
    if with_stream:
        d["stream"] = _convert(find_field(text, [r"Stream"]), TEXT)
    for prefix, rx in subjects:
        marks, grade = _cbse_subject(text, rx)
        d[f"{prefix}_marks"] = marks
        d[f"{prefix}_grade"] = grade
        # "<subject>_5_marks": no clear source on the CBSE sheet - left NULL, fill manually.
        d[f"{prefix}_5_marks"] = None
    d["result"] = detect_result(text)
    if d.get("percentage") is None and d.get("total_marks") and d.get("maximum_marks"):
        d["percentage"] = round(d["total_marks"] * 100 / d["maximum_marks"], 2)
    return d


def parse_cbse_10th(text):
    return _parse_cbse(text, CBSE_10_SUBJECTS, with_stream=False)


def parse_cbse_12th(text):
    return _parse_cbse(text, CBSE_12_SUBJECTS, with_stream=True)


# ----------------------------------------------------------------------------
# 6. Transfer certificates
# ----------------------------------------------------------------------------
TC_SPECS = [
    ("tc_number", TEXT, [r"T\.?\s?C\.?\s*(?:No|Number)", r"Transfer\s+Certificate\s*(?:No|Number)", r"Serial\s*(?:No|Number)"]),
    ("emis_number", TEXT, L_EMIS),
    ("admission_number", TEXT, [r"Admission\s*(?:No|Number)"]),
    ("school_name", TEXT, L_SCHOOL),
    ("school_affiliation_number", TEXT, [r"Affiliation\s*(?:No|Number)"]),
    ("school_address", TEXT, [r"School\s+Address"]),
    ("student_name", TEXT, L_NAME),
    ("father_name", TEXT, L_FATHER),
    ("mother_name", TEXT, L_MOTHER),
    ("nationality", TEXT, [r"Nationality"]),
    ("religion", TEXT, [r"Religion"]),
    ("community", TEXT, [r"Community(?:\s*(?:&|and)\s*Caste)?", r"Caste"]),
    ("gender", TEXT, [r"\bSex\b", r"Gender"]),
    ("date_of_birth", DATE, L_DOB),
    ("date_of_admission", DATE, [r"Date\s+of\s+Admission"]),
    ("scholarship_details", TEXT, [r"Scholarship"]),
    ("medical_inspection", TEXT, [r"Medical\s+Inspection"]),
    ("date_of_leaving", DATE, [r"Date\s+of\s+Leaving"]),
    ("character_conduct", TEXT, [r"(?:Character|Conduct)(?:\s*(?:and|&)\s*(?:Character|Conduct))?"]),
    ("date_of_tc", DATE, [r"Date\s+of\s+(?:issue\s+of\s+)?T\.?C\.?", r"Date\s+of\s+Issue"]),
    ("course_of_study", TEXT, [r"Course\s+of\s+Study", r"Group"]),
]


def _make_tc_parser(standard, with_subjects, with_education_type):
    def parser(text):
        d = apply_specs(text, TC_SPECS)
        d["standard"] = standard
        marks = block_after(text, r"Identification\s+Marks?(?:\s+of\s+the\s+(?:Pupil|Student))?", 2)
        d["identification_marks"] = _split_list(marks)
        if with_education_type:
            d["education_type"] = _convert(find_field(text, [r"Education\s+Type", r"Type\s+of\s+Education"]), TEXT)
        if with_subjects:
            subs = block_after(text, r"Subjects?\s*(?:Studied|Offered)", 2)
            d["subjects_offered"] = _split_list(subs)
        return d
    return parser


parse_state_10th_tc = _make_tc_parser("10th", False, False)
parse_state_12th_tc = _make_tc_parser("12th", True, True)
parse_cbse_10th_tc = _make_tc_parser("10th", False, False)
parse_cbse_12th_tc = _make_tc_parser("12th", True, True)


# ----------------------------------------------------------------------------
# 7. Supporting certificates
# ----------------------------------------------------------------------------
# (label regex, column, kind) - order matters: first match wins
_BC_LABELS = [
    (r"Father",                                                        "father_name", TEXT),
    (r"Mother",                                                        "mother_name", TEXT),
    (r"(?:Registration|Certificate|Serial|Reference)\s*(?:No|Number)", "certificate_number", TEXT),
    (r"\b(?:Sex|Gender)\b",                                            "gender", TEXT),
    (r"Date\s+of\s+Birth|\bD\.?O\.?B\b",                               "date_of_birth", DATE),
    (r"Place\s+of\s+Birth",                                            "place_of_birth", TEXT),
    (r"Date\s+of\s+Issue|Issue\s+Date|\bDate\b",                       "issue_date", DATE),
    (r"Issuing\s+Authority|Issued\s+By|\bRegistrar\b",                 "issuing_authority", TEXT),
    (r"Address",                                                       "permanent_address", TEXT),
    (r"\bName\b",                                                      "student_name", TEXT),
]
_BC_COLS = [c for _, c, _ in _BC_LABELS]
_BC_KIND = {c: k for _, c, k in _BC_LABELS}
_LABEL_ONLY = re.compile(r"^[A-Za-z' /.()\-]{2,40}?\s*:\s*$")
_LABEL_INLINE = re.compile(r"^([A-Za-z' /.()\-]{2,40}?)\s*:\s*(\S.*)$")


def _bc_column(label):
    for rx, col, _ in _BC_LABELS:
        if re.search(rx, label, re.I):
            return col
    return None


def parse_birth_certificate(text):
    """Handles 'Label : value' lines AND the layout where all labels come first,
    followed by all values in the same order."""
    d = {c: None for c in _BC_COLS}
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    junk = re.compile(r"SAMPLE DOCUMENT|DUMMY DATA|FOR SOFTWARE TESTING", re.I)

    pending, inline_idx, last_label = [], set(), -1
    for i, l in enumerate(lines):
        if _LABEL_ONLY.match(l):
            col = _bc_column(l.rstrip(" :"))
            if col:
                pending.append(col)
                last_label = i
            continue
        m = _LABEL_INLINE.match(l)
        if m and _bc_column(m.group(1)):
            col = _bc_column(m.group(1))
            if d[col] is None:
                d[col] = m.group(2)
            inline_idx.add(i)

    # value lines = everything after the last label-only line that is not a label/junk
    values = [l for i, l in enumerate(lines)
              if i > last_label and i not in inline_idx and not junk.search(l)
              and not _LABEL_ONLY.match(l)]

    if pending and len(values) == len(pending):          # clean positional match
        for col, val in zip(pending, values):
            if d[col] is None:
                d[col] = val
    elif pending:                                        # counts differ: only certain types
        for v in values:
            if d["certificate_number"] is None and re.fullmatch(r"[A-Z]{0,3}[/\- ]?\d{2,4}[/\-]\d{3,8}", v):
                d["certificate_number"] = v
            elif d["gender"] is None and re.fullmatch(r"(?i)male|female|transgender|other", v):
                d["gender"] = v
        dates = [v for v in values if to_date(v)]
        if dates and d["date_of_birth"] is None:
            d["date_of_birth"] = dates[0]
        if len(dates) > 1 and d["issue_date"] is None:
            d["issue_date"] = dates[-1]

    return {c: _convert(d[c], _BC_KIND[c]) for c in _BC_COLS}


# ----------------------------------------------------------------------------
# Aadhaar: one card, or several people's cards in one upload
# ----------------------------------------------------------------------------
VID_RE = re.compile(r"VID\s*:?\s*(?:\d{4}[ \t]?){4}", re.I)
NUM_RE = re.compile(r"(?<!\d)(?<!\d[ \t])\d{4}[ \t]?\d{4}[ \t]?\d{4}(?![ \t]?\d)")


def parse_aadhaar(text):
    """Only the LAST 4 digits are ever returned - the full number is discarded everywhere."""
    d = {"name_on_card": None, "date_of_birth": None, "father_name": None,
         "address": None, "aadhaar_last_four": None}

    text = VID_RE.sub(" ", text)  # a labelled 16-digit VID must never be read as the Aadhaar number
    m = NUM_RE.search(text)
    d["aadhaar_last_four"] = re.sub(r"\D", "", m.group())[-4:] if m else None

    lines = [l.strip() for l in text.split("\n") if l.strip()]
    for i, line in enumerate(lines):
        if re.search(r"\bDOB\b|Date\s+of\s+Birth|Year\s+of\s+Birth", line, re.I):
            d["date_of_birth"] = to_date(line)
            for j in range(i - 1, -1, -1):
                if re.fullmatch(r"[A-Za-z .]{3,}", lines[j]) and not re.search(
                    r"government|india|authority|unique|aadhaar|sample|photo", lines[j], re.I
                ):
                    d["name_on_card"] = lines[j]
                    break
            break

    rel = find_field(text, [r"S/O", r"C/O", r"D/O", r"W/O"])
    d["father_name"] = rel.strip(" ,:") if rel else None

    addr = " ".join(block_after(text, r"Address", 4))
    # the address ends where the 12-digit ID number starts (and the number must never be kept)
    addr = re.split(r"\b\d{4}\s?\d{4}\s?\d{4}\b", addr)[0]
    addr = re.split(r"SAMPLE DOCUMENT|VID\s*:", addr, flags=re.I)[0]
    d["address"] = re.sub(r"\s+", " ", addr).strip(" ,-")[:500] or None
    return d


def split_cards(text):
    """One block per Aadhaar number (each block ends at its number). Returns [(block_text, full_number)];
    the full number is used only to group front/back of the same person in memory - never returned."""
    text = VID_RE.sub(" ", text)
    blocks, start = [], 0
    for m in NUM_RE.finditer(text):
        blocks.append([text[start:m.end()], re.sub(r"\s", "", m.group())])
        start = m.end()
    if blocks and text[start:].strip():
        blocks[-1][0] += "\n" + text[start:]      # trailing footer text stays with the last card
    return [tuple(b) for b in blocks]


def process_multi_aadhaar(text):
    """Several people's Aadhaar cards in one upload -> {'count': n, 'cards': [one dict per person]}."""
    groups = {}
    for block, num in split_cards(text):          # front + back of the same person share a number
        groups.setdefault(num, []).append(block)

    cards = []
    for blocks in groups.values():
        merged = {}
        for b in blocks:
            for k, v in parse_aadhaar(b).items():  # parse BEFORE masking
                if v and not merged.get(k):
                    merged[k] = v
        cards.append(merged)

    return {
        "doc_type": "aadhaar_card",
        "count": len(cards),
        "cards": cards,
        "extracted_text": mask_sensitive(text),
    }


# ---- narrative-style revenue certificates ----
_TITLES = r"(?:Thiru|Thirumathi|Tmt|Selvi|Selvan|Kumari|Kum|Mr|Mrs|Ms|Shri|Sri|Smt)"
_COMM_CODE = re.compile(r"\(\s*(MBC/DNC|MBC|BCM|DNC|BC|SCA|SC|ST|OC)\s*\)")
_COMM_PHRASE = re.compile(
    r"\b(Most\s+Backward\s+Class|Backward\s+Class|Scheduled\s+Caste|Scheduled\s+Tribe|Denotified\s+Communit(?:y|ies))\b", re.I
)


def _narrative(text):
    """Name / parent / address / caste / community read from sentence-style certificates.
    Every value is None when the sentence is not there (labelled layouts are untouched)."""
    flat = re.sub(r"\s+", " ", text)
    out = {"name": None, "father": None, "address": None, "caste": None, "community": None}

    m = re.search(rf"certify\s+that\s+(?:{_TITLES}\.?\s+)?([A-Za-z][A-Za-z .]{{1,60}}?)\s*,?\s+(?:son|daughter|wife|ward)\s+of\b",
                  flat, re.I)
    if m:
        out["name"] = m.group(1).strip(" ,.")
    m = re.search(rf"(?:son|daughter|wife|ward)\s+of\s+(?:{_TITLES}\.?\s+)?([A-Za-z][A-Za-z .]{{1,60}}?)\s*,", flat, re.I)
    if m:
        out["father"] = m.group(1).strip(" ,.")
    m = re.search(
        r"(?:residing|resident|resides)\s+(?:at|in|of)\s+(.+?),?\s+"
        r"(?:belongs?\s+to|belonging\s+to|and\s+belongs?|is\s+a\s+(?:member|native)|has\b|having\b)", flat, re.I)
    if m:
        out["address"] = m.group(1).strip(" ,.")
    m = re.search(r"belongs?\s+to\s+(?:the\s+)?([A-Za-z][A-Za-z .]{1,40}?)\s+(?:caste|community)\b", flat, re.I)
    if m:
        out["caste"] = m.group(1).strip(" ,.")
    m = _COMM_CODE.search(flat)
    if m:
        out["community"] = m.group(1)
    else:
        m = _COMM_PHRASE.search(flat)
        out["community"] = re.sub(r"\s+", " ", m.group(1)) if m else None
    return out


def _merge_narrative(d, text, mapping):
    """Sentence-style values replace the label-based ones (which are unreliable on such layouts)."""
    n = _narrative(text)
    for key, col in mapping.items():
        if n.get(key) and col in d:
            d[col] = n[key]
    cur = d.get("issuing_authority")
    if cur is None or str(cur).startswith(","):
        m = re.search(r"^\W*((?:Tahsildar|Tehsildar)\b[^\n]*)", text, re.I | re.M)
        if m:
            d["issuing_authority"] = re.sub(r"\s+", " ", m.group(1)).strip(" ,.")
    return d


def parse_community_certificate(text):
    d = apply_specs(text, [
        ("certificate_number", TEXT, [r"Community\s+Certificate\s*(?:No|Number)"] + L_CERT_NO),
        ("candidate_name", TEXT, L_NAME),
        ("father_name", TEXT, L_FATHER),
        ("community_name", TEXT, [r"Community(?!\s+Certificate)"]),
        ("caste_name", TEXT, [r"Caste"]),
        ("address", TEXT, L_ADDRESS),
        ("issuing_authority", TEXT, L_ISSUER + [r"Tahsildar", r"Tehsildar"]),
        ("issue_date", DATE, L_ISSUE_DATE),
    ])
    return _merge_narrative(d, text, {"name": "candidate_name", "father": "father_name", "address": "address",
                                      "caste": "caste_name", "community": "community_name"})


def parse_income_certificate(text):
    d = apply_specs(text, [
        ("certificate_number", TEXT, L_CERT_NO),
        ("candidate_name", TEXT, L_NAME),
        ("annual_income", NUM, [r"Annual\s+(?:Family\s+)?Income", r"Income"]),
        ("occupation", TEXT, [r"Occupation"]),
        ("address", TEXT, L_ADDRESS),
        ("issuing_authority", TEXT, L_ISSUER + [r"Tahsildar", r"Tehsildar"]),
        ("issue_date", DATE, L_ISSUE_DATE),
    ])
    return _merge_narrative(d, text, {"name": "candidate_name", "address": "address"})


def parse_nativity_certificate(text):
    d = apply_specs(text, [
        ("certificate_number", TEXT, L_CERT_NO),
        ("candidate_name", TEXT, L_NAME),
        ("address", TEXT, L_ADDRESS),
        ("issuing_authority", TEXT, L_ISSUER + [r"Tahsildar", r"Tehsildar"]),
        ("issue_date", DATE, L_ISSUE_DATE),
    ])
    return _merge_narrative(d, text, {"name": "candidate_name", "address": "address"})


def _first_token(v):
    """'7376211CS101, has passed the ...' -> '7376211CS101'."""
    if not v:
        return v
    m = re.match(r"[A-Za-z0-9][A-Za-z0-9/\-]*", v.strip())
    return m.group(0) if m else v


def _header_line(text, keyword_rx):
    """First short heading-style line containing the keyword (e.g. 'ANNA UNIVERSITY, CHENNAI')."""
    for l in text.split("\n"):
        l = l.strip()
        if len(l) <= 80 and re.search(keyword_rx, l, re.I) and not re.search(r"certify|:", l, re.I):
            return re.sub(r"\s+", " ", l).strip(" ,.")
    return None


_STOP = r"(?=\s+held\b|\s+with\b|\s+examination\b|\s+on\b|\s+and\s+is\b|,|\.\s|\.$|$)"


def _academic_narrative(text):
    """'This is to certify that X, Register No. N, has passed ... degree of B.E. in CSE held in April 2024 ...'
    Every value is None unless the text is written as such a sentence."""
    flat = re.sub(r"\s+", " ", text)
    out = dict.fromkeys(["name", "register", "degree", "department", "course", "year", "classification"])
    if not re.search(r"certify\s+that", flat, re.I):
        return out
    m = re.search(rf"certify\s+that\s+(?:{_TITLES}\.?\s+)?([A-Za-z][A-Za-z .]{{1,60}}?)\s*[,(]", flat, re.I)
    if m:
        out["name"] = m.group(1).strip(" ,.")
    m = re.search(r"Reg(?:ister|\.)?\s*(?:No|Number)\.?\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9/\-]{3,})", flat, re.I)
    if m:
        out["register"] = m.group(1)
    m = re.search(r"degree\s+of\s+((?:Bachelor|Master|Doctor)[A-Za-z .&()]*?)(?=\s+in\s|\s+held\b|\s+with\b|\s+on\b|,|\.\s|\.$|$)",
                  flat, re.I)
    if m:
        out["degree"] = m.group(1).strip(" ,.")
    m = re.search(rf"degree\s+of\s+(?:Bachelor|Master|Doctor)[A-Za-z .&()]*?\s+in\s+([A-Za-z .&()]+?){_STOP}", flat, re.I)
    if m:
        out["department"] = m.group(1).strip(" ,.")
    m = re.search(rf"\b(diploma\s+in\s+[A-Za-z .&()]+?){_STOP}", flat, re.I)
    if m:
        out["course"] = m.group(1).strip(" ,.")
    m = re.search(r"held\s+in\s+(?:[A-Za-z]+\s+)?((?:19|20)\d{2})", flat, re.I)
    if m:
        out["year"] = int(m.group(1))
    m = re.search(r"(?:placed|declared|awarded|secured|passed)\s+(?:in\s+|the\s+|with\s+)?"
                  r"((?:First|Second|Third)\s+Class(?:\s+with\s+(?:Distinction|Honou?rs))?|Distinction|Pass\s+Class)",
                  flat, re.I)
    if m:
        out["classification"] = m.group(1).strip()
    return out


def _finish_academic(d, text, mapping):
    n = _academic_narrative(text)                      # sentence-style values replace the label-based ones
    for key, col in mapping.items():
        if n.get(key) is not None and col in d:
            d[col] = n[key]
    d["register_number"] = _first_token(d.get("register_number"))
    if not d.get("college_name"):                      # college / university often appear only as a heading line
        d["college_name"] = _header_line(text, r"\b(?:College|Institute|Polytechnic)\b")
    if "university_name" in d and not d.get("university_name"):
        d["university_name"] = _header_line(text, r"\bUniversity\b")
    d["result"] = detect_result(text)
    return d


def parse_degree_certificate(text):
    d = apply_specs(text, [
        ("certificate_number", TEXT, L_CERT_NO),
        ("college_name", TEXT, L_COLLEGE),
        ("university_name", TEXT, [r"University\s+Name", r"University(?=\s*:)"]),
        ("candidate_name", TEXT, L_NAME),
        ("register_number", TEXT, [r"Register\s*(?:No|Number)", r"Reg\.?\s*No"]),
        ("date_of_birth", DATE, L_DOB),
        ("degree_name", TEXT, [r"Degree\s+Name", r"Degree(?=\s*[:\-–—])",
                               r"Degree\s+of\s+(?=(?:Bachelor|Master|Doctor|B\.|M\.))"]),
        ("department", TEXT, [r"Department", r"Branch", r"Subject"]),
        ("graduation_year", INT, [r"Year\s+of\s+(?:Passing|Graduation)", r"Month\s+and\s+Year"]),
        ("classification", TEXT, [r"Classification", r"Class\s+Obtained", r"Division"]),
    ])
    return _finish_academic(d, text, {"name": "candidate_name", "register": "register_number", "degree": "degree_name",
                                      "department": "department", "year": "graduation_year",
                                      "classification": "classification"})


def parse_diploma_certificate(text):
    d = apply_specs(text, [
        ("certificate_number", TEXT, L_CERT_NO),
        ("college_name", TEXT, L_COLLEGE),
        ("emis_number", TEXT, L_EMIS),
        ("candidate_name", TEXT, L_NAME),
        ("register_number", TEXT, [r"Register\s*(?:No|Number)", r"Reg\.?\s*No"]),
        ("date_of_birth", DATE, L_DOB),
        ("course_name", TEXT, [r"Course(?:\s+Name)?", r"Diploma\s+in"]),
        ("department", TEXT, [r"Department", r"Branch"]),
        ("completion_year", INT, [r"Year\s+of\s+(?:Passing|Completion)", r"Month\s+and\s+Year"]),
        ("classification", TEXT, [r"Classification", r"Class\s+Obtained", r"Division"]),
    ])
    return _finish_academic(d, text, {"name": "candidate_name", "register": "register_number", "course": "course_name",
                                      "year": "completion_year", "classification": "classification"})


_SEM_LINE = re.compile(
    r"^[ \t]*(?P<code>[A-Z]{0,3}\d{3,6}[A-Z]?)[ \t]+(?P<name>[A-Za-z&,\-()/ ]+?)[ \t]+"
    r"(?P<nums>(?:\d{1,3}(?:\.\d+)?[ \t]*)+?)(?P<grade>[A-Z]\+?)?[ \t]*$",
    re.M,
)

_ROMAN_SEM = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8}


def to_semester(s):
    """'4', '4th', 'IV', 'Semester 4' -> 4. Anything else (e.g. an OCR-garbled 'A') -> None."""
    if not s:
        return None
    m = re.search(r"(?<!\d)(\d{1,2})(?!\d)", s)
    if m:
        n = int(m.group(1))
        return n if 1 <= n <= 12 else None
    m = re.fullmatch(r"\W*([IVX]{1,4})\W*", s.strip(), re.I)
    return _ROMAN_SEM.get(m.group(1).upper()) if m else None


def parse_diploma_semester(text):
    d = apply_specs(text, [
        ("college_name", TEXT, L_COLLEGE),
        ("emis_number", TEXT, L_EMIS),
        ("candidate_name", TEXT, L_NAME),
        ("register_number", TEXT, [r"Register\s*(?:No|Number)", r"Reg\.?\s*No"]),
        ("date_of_birth", DATE, L_DOB),
        ("course_name", TEXT, [r"Course(?:\s+Name)?", r"Diploma\s+in"]),
        ("department", TEXT, [r"Department", r"Branch"]),
        ("semester", TEXT, [r"Semester(?=\s*:)",
                            r"\bSemester\s+(?:No\.?\s*)?(?=(?:\d{1,2}|[IVX]{1,4})\b)"]),
        ("exam_month", TEXT, [r"Month\s+of\s+Examination", r"Exam(?:ination)?\s+Month"]),
        ("exam_year", INT, [r"Year\s+of\s+Examination", r"Exam(?:ination)?\s+Year", r"Month\s+and\s+Year"]),
        ("total_marks", NUM, [r"Grand\s+Total", r"Total\s+Marks"]),
        ("maximum_marks", NUM, [r"Maximum\s+Marks"]),
        ("percentage", NUM, [r"Percentage"]),
        ("overall_grade", TEXT, [r"Overall\s+Grade", r"\bCGPA\b"]),
    ])
    d["register_number"] = _first_token(d.get("register_number"))
    if not d.get("college_name"):
        d["college_name"] = _header_line(text, r"\b(?:College|Institute|Polytechnic)\b")
    d["semester"] = to_semester(d["semester"])
    totals = []
    for i, m in enumerate(_SEM_LINE.finditer(text), start=1):
        if i > 8:
            break
        nums = [float(x) for x in m.group("nums").split()]
        p = f"subject_{i}"
        d[f"{p}_name"] = m.group("name").strip()
        d[f"{p}_code"] = m.group("code")
        d[f"{p}_total_marks"] = nums[-1]
        if len(nums) >= 3:
            d[f"{p}_internal_marks"] = nums[0]
            d[f"{p}_external_marks"] = nums[1]
        if len(nums) >= 4:
            d[f"{p}_practical_marks"] = nums[2]
        d[f"{p}_maximum_marks"] = 100.0
        d[f"{p}_grade"] = m.group("grade")
        totals.append(nums[-1])
    if d.get("total_marks") is None and totals:
        d["total_marks"] = float(sum(totals))
        d["maximum_marks"] = d.get("maximum_marks") or 100.0 * len(totals)
    if d.get("percentage") is None and d.get("total_marks") and d.get("maximum_marks"):
        d["percentage"] = round(d["total_marks"] * 100 / d["maximum_marks"], 2)
    d["result"] = detect_result(text)
    return d


# ----------------------------------------------------------------------------
# 8. Validation - returns warnings only, never changes the parsed dict
# ----------------------------------------------------------------------------
_WORD_DIGIT = {"ZERO": "0", "ONE": "1", "TWO": "2", "THREE": "3", "FOUR": "4",
               "FIVE": "5", "SIX": "6", "SEVEN": "7", "EIGHT": "8", "NINE": "9"}
_WORD_RX = re.compile("|".join(_WORD_DIGIT))
_DIGIT_WORDS = r"(?:ZERO|ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE)"


def _words_val(s):
    """'ZERO FOUR THREE SEVEN' / 'THREE FIVENINE' -> 437 / 359 (tolerates fused words)."""
    digits = "".join(_WORD_DIGIT[w] for w in _WORD_RX.findall((s or "").upper()))
    return int(digits) if digits else None


def validate_state_12th(d, text=""):
    warns = []
    sub_totals = []
    for prefix, _ in _HSS_SUBJECTS:
        total = d.get(f"{prefix}_marks")
        if total is None:
            warns.append(f"{prefix}: marks not found")
            continue
        sub_totals.append(total)
        parts = [d.get(f"{prefix}_{k}_marks") or 0 for k in ("theory", "practical", "internal")]
        if sum(parts) != total:
            warns.append(f"{prefix}: components {parts} do not add up to {total}")

    if d.get("total_marks") is not None and sub_totals and sum(sub_totals) != d["total_marks"]:
        warns.append(f"subject totals sum to {sum(sub_totals)}, but total_marks is {d['total_marks']}")

    mx, tm, pct = d.get("maximum_total_marks"), d.get("total_marks"), d.get("percentage")
    if mx and tm and pct is not None and abs(round(tm * 100 / mx, 2) - pct) > 0.01:
        warns.append("percentage does not match total/maximum")

    if not re.fullmatch(r"\d{10}", d.get("permanent_register_number") or ""):
        warns.append("permanent register number is not 10 digits")
    if not d.get("date_of_birth"):
        warns.append("date of birth missing or invalid")
    serial = d.get("certificate_serial_number")
    if not serial:
        warns.append("certificate serial number not read")
    elif not re.fullmatch(r"HSS \d{7}", serial):
        warns.append(f"serial number '{serial}' not in 'HSS #######' format")

    m = re.search(rf"TOTAL\s+MARKS\s*[:\-]?\s*\d+\s+((?:{_DIGIT_WORDS}\s*)+)", text, re.I)
    if m and d.get("total_marks") is not None:
        w = _words_val(m.group(1))
        if w is not None and w != int(d["total_marks"]):
            warns.append(f"total in words ({w}) differs from total_marks ({int(d['total_marks'])})")
    return warns


def validate_state_10th(d, text=""):
    warns = []
    marks = [d.get(f"subject_{i}_marks") for i in range(1, _SSLC_ORDER + 1)]
    got = [m for m in marks if m is not None]
    if len(got) < _SSLC_ORDER:
        warns.append(f"only {len(got)} of {_SSLC_ORDER} subject rows read")
    if d.get("total_marks") is not None and got and sum(got) != d["total_marks"]:
        warns.append(f"subject marks sum to {sum(got)}, but total_marks is {d['total_marks']}")
    mx, tm, pct = d.get("maximum_total_marks"), d.get("total_marks"), d.get("percentage")
    if mx and tm and pct is not None and abs(round(tm * 100 / mx, 2) - pct) > 0.01:
        warns.append("percentage does not match total/maximum")

    # each subject row: the last number must equal the marks written in words
    row_rx = re.compile(rf"((?:(?<!\d)\d{{3}}(?!\d)[ \t]+){{1,3}})((?:{_NUM_WORDS}[ \t]*)+)")
    for l in text.split("\n"):
        if re.search(r"TOTAL\s+MARKS|HUNDRED", l, re.I):
            continue
        m = row_rx.search(l)
        if m:
            n = int(re.findall(r"\d{3}", m.group(1))[-1])
            w = _words_val(m.group(2))
            if w is not None and w != n:
                warns.append(f"row {n}: words say {w}")

    m = re.search(rf"TOTAL\s+MARKS\s*[:\-]?\s*(\d{{2,4}})\s+((?:{_DIGIT_WORDS}\s*)+)", text, re.I)
    if m and _words_val(m.group(2)) not in (None, int(m.group(1))):
        warns.append("total in words differs from the total in digits")

    if not re.fullmatch(r"SEC \d{8}", d.get("certificate_serial_number") or ""):
        warns.append("serial number not in 'SEC ########' format")
    prn, roll = d.get("permanent_register_number") or "", d.get("roll_number") or ""
    if not re.fullmatch(r"XM\d{2}R\d+", prn):
        warns.append("permanent register number format unexpected")
    elif not prn.endswith(roll):
        warns.append("roll number does not match the end of the permanent register number")
    if not d.get("date_of_birth"):
        warns.append("date of birth missing or invalid")
    return warns


VALIDATORS = {
    "state_board_10th": validate_state_10th,
    "state_board_12th": validate_state_12th,
}


def validate_document(doc_type: str, fields: dict, text: str = "", saved_name=None, saved_dob=None) -> list:
    """Warnings for a parsed document ([] = all checks passed or no validator for this type).
    If saved_name / saved_dob are given, the sheet is also compared with the saved record
    (name differences that are only spacing/punctuation, e.g. 'V S' vs 'VS', are ignored)."""
    fn = VALIDATORS.get(doc_type)
    warns = fn(fields, text) if fn else []
    if saved_name or saved_dob:
        warns += compare_with_record(fields, saved_name, saved_dob)
    return warns


# ----------------------------------------------------------------------------
# 9. Registry
# ----------------------------------------------------------------------------
def _cfg(table, parser, extracted_text=True, verification=True):
    return {"table": table, "parser": parser,
            "extracted_text": extracted_text, "verification": verification}


DOC_TYPES = {
    # marksheets (state-board tables have neither extracted_text nor verification columns)
    "state_board_10th": _cfg("state_board_10th", parse_state_10th, False, False),
    "state_board_12th": _cfg("state_board_12th", parse_state_12th, False, False),
    "cbse_10th_marksheet": _cfg("cbse_10th_marksheet", parse_cbse_10th, False, True),
    "cbse_12th_marksheet": _cfg("cbse_12th_marksheet", parse_cbse_12th, False, True),
    "diploma_semester_marksheet": _cfg("diploma_semester_marksheet", parse_diploma_semester, False, True),
    # transfer certificates
    "state_board_10th_tc": _cfg("state_board_10th_tc", parse_state_10th_tc),
    "state_board_12th_tc": _cfg("state_board_12th_tc", parse_state_12th_tc),
    "cbse_10th_tc": _cfg("cbse_10th_tc", parse_cbse_10th_tc),
    "cbse_12th_tc": _cfg("cbse_12th_tc", parse_cbse_12th_tc),
    # supporting certificates
    "birth_certificate": _cfg("birth_certificate", parse_birth_certificate),
    "aadhaar_card": _cfg("aadhaar_card", parse_aadhaar),
    "community_certificate": _cfg("community_certificate", parse_community_certificate),
    "income_certificate": _cfg("income_certificate", parse_income_certificate),
    "nativity_certificate": _cfg("nativity_certificate", parse_nativity_certificate),
    "degree_certificate": _cfg("degree_certificate", parse_degree_certificate),
    "diploma_certificate": _cfg("diploma_certificate", parse_diploma_certificate),
}


def parse_document(doc_type: str, text: str) -> dict:
    return DOC_TYPES[doc_type]["parser"](text)