"""
ocr.py - SMART ERP
Text extraction (PDF / image) + certificate field parsing.
Every parser returns a dict whose keys match the columns in your SQL tables.
"""
import io
import os
import re
from datetime import datetime

import cv2
import fitz  # PyMuPDF
import numpy as np
import pytesseract
from PIL import Image, ImageOps

TESS_LANG = os.getenv("TESS_LANG", "eng")
TESS_CONFIG = "--oem 3 --psm 6"
MIN_PDF_TEXT_CHARS = 80
PDF_DPI = 300

# Windows: set full path to tesseract.exe in .env if it is not on PATH
if os.getenv("TESSERACT_CMD"):
    pytesseract.pytesseract.tesseract_cmd = os.getenv("TESSERACT_CMD")

TEXT, DATE, INT, NUM = "text", "date", "int", "num"


# ----------------------------------------------------------------------------
# 1. Text extraction
# ----------------------------------------------------------------------------
def _clean(text: str) -> str:
    text = text.replace("\r", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _preprocess(img: Image.Image) -> np.ndarray:
    """Grayscale -> upscale -> denoise -> adaptive threshold (OpenCV)."""
    img = ImageOps.exif_transpose(img).convert("L")
    arr = np.array(img)
    h, w = arr.shape
    if w < 1600:
        scale = 1600 / w
        arr = cv2.resize(arr, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    arr = cv2.medianBlur(arr, 3)
    arr = cv2.adaptiveThreshold(
        arr, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15
    )
    return arr


def _ocr_image(img: Image.Image) -> str:
    processed = _preprocess(img)
    return pytesseract.image_to_string(
        Image.fromarray(processed), lang=TESS_LANG, config=TESS_CONFIG
    )


def extract_text(content: bytes, filename: str) -> str:
    """PDF -> embedded text first (PyMuPDF), OCR fallback for scanned PDFs. Images -> OCR."""
    if filename.lower().endswith(".pdf"):
        doc = fitz.open(stream=content, filetype="pdf")
        try:
            text = "\n".join(page.get_text("text") for page in doc)
            if len(text.strip()) >= MIN_PDF_TEXT_CHARS:
                return _clean(text)

            # scanned PDF -> render each page to an image, then OCR
            zoom = PDF_DPI / 72
            parts = []
            for page in doc:
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY)
                img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
                parts.append(_ocr_image(img))
            return _clean("\n".join(parts))
        finally:
            doc.close()

    return _clean(_ocr_image(Image.open(io.BytesIO(content))))


def mask_sensitive(text: str) -> str:
    """Mask any 12-digit number (Aadhaar) so the full number is never stored."""
    return re.sub(r"\b(\d{4})\s?(\d{4})\s?(\d{4})\b", r"XXXX XXXX \3", text)


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


def _numbers(s, max_value=None):
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s)]
    return [n for n in nums if max_value is None or n <= max_value]


def subject_line_rest(text, name_rx):
    """Text on the line following a subject name at the start of a line."""
    m = re.search(
        rf"^[ \t]*(?:\d{{1,2}}[.)]?[ \t]+)?(?:{name_rx})\b(.*)$", text, re.I | re.M
    )
    return m.group(1) if m else None


def _split_list(items):
    joined = " ".join(items)
    parts = re.split(r"\s*(?:\(?[ivx]+\)|\(?\d\)|\d\.|;|,)\s*", joined)
    return [p.strip(" .") for p in parts if p and p.strip(" .")]


def _finish_marksheet(d, totals, default_max=None):
    """Fill total / max / percentage if the label search did not find them."""
    if d.get("total_marks") is None and totals:
        d["total_marks"] = float(sum(totals))
    if d.get("maximum_total_marks") is None and "maximum_total_marks" in d:
        d["maximum_total_marks"] = default_max or (100.0 * len(totals) if totals else None)
    mx = d.get("maximum_total_marks") if "maximum_total_marks" in d else d.get("maximum_marks")
    if d.get("percentage") is None and d.get("total_marks") and mx:
        d["percentage"] = round(d["total_marks"] * 100 / mx, 2)
    if d.get("result") is None:
        d["result"] = None
    return d


# ----------------------------------------------------------------------------
# 3. Shared label sets
# ----------------------------------------------------------------------------
L_NAME = [
    r"Name\s+of\s+the\s+(?:Candidate|Student|Pupil)",
    r"(?:Candidate|Student|Pupil)'?s?\s+Name",
    r"\bName\b(?!\s+of)",
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
L_COLLEGE = [r"Name\s+of\s+(?:the\s+)?(?:College|Institution)", r"College\s+Name", r"College"]


# ----------------------------------------------------------------------------
# 4. State Board marksheets (Tamil Nadu)
# ----------------------------------------------------------------------------
STATE_10_SPECS = [
    ("certificate_serial_number", TEXT, L_CERT_NO),
    ("candidate_name", TEXT, L_NAME),
    ("date_of_birth", DATE, L_DOB),
    ("roll_number", TEXT, L_ROLL),
    ("register_number", TEXT, [r"(?<!Permanent )Register\s*(?:No|Number)", r"Reg\.?\s*No"]),
    ("permanent_register_number", TEXT, [r"Permanent\s+Register\s*(?:No|Number)", r"\bPRN\b"]),
    ("medium_of_instruction", TEXT, [r"Medium(?:\s+of\s+Instruction)?"]),
    ("session", TEXT, [r"Month\s+and\s+Year\s+of\s+(?:the\s+)?Examination", r"Session"]),
    ("school_name", TEXT, L_SCHOOL),
    ("school_address", TEXT, [r"School\s+Address"]),
    ("tmr_code_number", TEXT, [r"TMR\s*Code\s*(?:No|Number)?"]),
    ("tmr_code_date", DATE, [r"TMR\s*Code\s*Date"]),
    ("emis_id", TEXT, [r"EMIS\s*(?:ID|No|Number)?"]),
    ("total_marks", NUM, [r"Grand\s+Total", r"Total\s+Marks"]),
    ("maximum_total_marks", NUM, [r"Maximum\s+(?:Total\s+)?Marks"]),
    ("percentage", NUM, [r"Percentage"]),
]
STATE_10_SUBJECTS = [
    ("subject_1", r"Tamil"),
    ("subject_2", r"English"),
    ("subject_3", r"Mathematics|Maths"),
    ("subject_4", r"Science"),
    ("subject_5", r"Social\s+Science"),
]


def parse_state_10th(text):
    d = apply_specs(text, STATE_10_SPECS)
    d["examination_board"] = (
        "Directorate of Government Examinations, Tamil Nadu"
        if re.search(r"Government\s+Examinations|Tamil\s*Nadu", text, re.I) else None
    )
    d["certificate_name"] = "Statement of Marks" if re.search(r"Statement\s+of\s+Marks", text, re.I) else None
    d["exam_year"] = to_year(d.get("session")) or to_year(text)
    totals = []
    for prefix, rx in STATE_10_SUBJECTS:
        rest = subject_line_rest(text, rx)
        nums = _numbers(rest or "", max_value=100)
        d[f"{prefix}_marks"] = nums[0] if nums else None
        if nums:
            totals.append(nums[0])
    d["result"] = detect_result(text)
    return _finish_marksheet(d, totals, default_max=500.0)


STATE_12_SPECS = [
    ("certificate_serial_number", TEXT, L_CERT_NO),
    ("candidate_name", TEXT, L_NAME),
    ("date_of_birth", DATE, L_DOB),
    ("roll_number", TEXT, L_ROLL),
    ("register_number", TEXT, [r"(?<!Permanent )Register\s*(?:No|Number)", r"Reg\.?\s*No"]),
    ("permanent_register_number", TEXT, [r"Permanent\s+Register\s*(?:No|Number)", r"\bPRN\b"]),
    ("medium_of_instruction", TEXT, [r"Medium(?:\s+of\s+Instruction)?"]),
    ("session", TEXT, [r"Month\s+and\s+Year\s+of\s+(?:the\s+)?Examination", r"Session"]),
    ("school_name", TEXT, L_SCHOOL),
    ("school_address", TEXT, [r"School\s+Address"]),
    ("emis_id", TEXT, [r"EMIS\s*(?:ID|No|Number)?"]),
    ("group_code", TEXT, [r"Group\s*Code"]),
    ("group_name", TEXT, [r"Group\s*Name", r"Name\s+of\s+the\s+Group"]),
    ("total_marks", NUM, [r"Grand\s+Total", r"Total\s+Marks"]),
    ("maximum_total_marks", NUM, [r"Maximum\s+(?:Total\s+)?Marks"]),
    ("percentage", NUM, [r"Percentage"]),
]
# (column prefix, regex, has practical)
STATE_12_SUBJECTS = [
    ("tamil", r"Tamil", False),
    ("english", r"English", False),
    ("mathematics", r"Mathematics", False),
    ("business_mathematics", r"Business\s+Mathematics(?:\s+and\s+Statistics)?", False),
    ("business_studies", r"Business\s+Studies", False),
    ("computer_science", r"Computer\s+Science", True),
    ("political_science", r"Political\s+Science", False),
    ("accountancy", r"Accountancy", False),
    ("chemistry", r"Chemistry", True),
    ("economics", r"Economics", False),
    ("geography", r"Geography", False),
    ("physics", r"Physics", True),
    ("biology", r"Biology", True),
    ("botany", r"Botany", True),
    ("zoology", r"Zoology", True),
    ("commerce", r"Commerce", False),
    ("history", r"History", False),
]


def _assign_12th(prefix, nums, practical):
    """Heuristic column mapping - tune against your real sample marksheets."""
    d = {}
    n = len(nums)
    if n == 0:
        return d
    d[f"{prefix}_marks"] = nums[-1]
    if n >= 2:
        d[f"{prefix}_theory_marks"] = nums[0]
    if practical:
        if n >= 4:
            d[f"{prefix}_practical_marks"] = nums[1]
            d[f"{prefix}_internal_marks"] = nums[2]
        elif n == 3:
            d[f"{prefix}_practical_marks"] = nums[1]
    elif n >= 3:
        d[f"{prefix}_internal_marks"] = nums[1]
    return d


def parse_state_12th(text):
    d = apply_specs(text, STATE_12_SPECS)
    d["examination_board"] = (
        "Directorate of Government Examinations, Tamil Nadu"
        if re.search(r"Government\s+Examinations|Tamil\s*Nadu", text, re.I) else None
    )
    d["certificate_name"] = "Statement of Marks" if re.search(r"Statement\s+of\s+Marks", text, re.I) else None
    d["exam_year"] = to_year(d.get("session")) or to_year(text)
    totals = []
    for prefix, rx, practical in STATE_12_SUBJECTS:
        rest = subject_line_rest(text, rx)
        if rest is None:
            continue
        sub = _assign_12th(prefix, _numbers(rest, max_value=100), practical)
        d.update(sub)
        if f"{prefix}_marks" in sub:
            totals.append(sub[f"{prefix}_marks"])
    d["result"] = detect_result(text)
    return _finish_marksheet(d, totals)


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
    g = re.search(r"\b([A-E][12]?)\s*$", rest.strip(), re.I)
    return marks, (g.group(1).upper() if g else None)


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
    ("emis_number", TEXT, [r"EMIS\s*(?:ID|No|Number)?"]),
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
def parse_birth_certificate(text):
    return apply_specs(text, [
        ("certificate_number", TEXT, [r"Registration\s*(?:No|Number)"] + L_CERT_NO),
        ("student_name", TEXT, [r"Name\s+of\s+(?:the\s+)?(?:Child|Person)", r"Child'?s?\s+Name"] + L_NAME),
        ("gender", TEXT, [r"\bSex\b", r"Gender"]),
        ("date_of_birth", DATE, L_DOB),
        ("place_of_birth", TEXT, [r"Place\s+of\s+Birth"]),
        ("father_name", TEXT, L_FATHER),
        ("mother_name", TEXT, L_MOTHER),
        ("permanent_address", TEXT, L_ADDRESS),
        ("issuing_authority", TEXT, L_ISSUER + [r"Registrar"]),
        ("issue_date", DATE, L_ISSUE_DATE),
    ])


def parse_aadhaar(text):
    """Only the LAST 4 digits are ever returned - the full number is discarded."""
    d = {"name_on_card": None, "date_of_birth": None, "father_name": None,
         "address": None, "aadhaar_last_four": None}
    m = re.search(r"\b\d{4}\s?\d{4}\s?(\d{4})\b", text)
    d["aadhaar_last_four"] = m.group(1) if m else None

    lines = [l.strip() for l in text.split("\n") if l.strip()]
    for i, line in enumerate(lines):
        if re.search(r"\bDOB\b|Date\s+of\s+Birth|Year\s+of\s+Birth", line, re.I):
            d["date_of_birth"] = to_date(line)
            for j in range(i - 1, -1, -1):
                if re.fullmatch(r"[A-Za-z .]{3,}", lines[j]) and not re.search(
                    r"government|india|authority|unique|aadhaar", lines[j], re.I
                ):
                    d["name_on_card"] = lines[j]
                    break
            break
    rel = find_field(text, [r"S/O", r"C/O", r"D/O", r"W/O"])
    d["father_name"] = rel.strip(" ,:") if rel else None
    addr = block_after(text, r"Address", 4)
    d["address"] = re.sub(r"\s+", " ", " ".join(addr)).strip()[:500] or None
    return d


def parse_community_certificate(text):
    return apply_specs(text, [
        ("certificate_number", TEXT, [r"Community\s+Certificate\s*(?:No|Number)"] + L_CERT_NO),
        ("candidate_name", TEXT, L_NAME),
        ("father_name", TEXT, L_FATHER),
        ("community_name", TEXT, [r"Community"]),
        ("caste_name", TEXT, [r"Caste"]),
        ("address", TEXT, L_ADDRESS),
        ("issuing_authority", TEXT, L_ISSUER + [r"Tahsildar", r"Tehsildar"]),
        ("issue_date", DATE, L_ISSUE_DATE),
    ])


def parse_income_certificate(text):
    return apply_specs(text, [
        ("certificate_number", TEXT, L_CERT_NO),
        ("candidate_name", TEXT, L_NAME),
        ("annual_income", NUM, [r"Annual\s+(?:Family\s+)?Income", r"Income"]),
        ("occupation", TEXT, [r"Occupation"]),
        ("address", TEXT, L_ADDRESS),
        ("issuing_authority", TEXT, L_ISSUER + [r"Tahsildar", r"Tehsildar"]),
        ("issue_date", DATE, L_ISSUE_DATE),
    ])


def parse_nativity_certificate(text):
    return apply_specs(text, [
        ("certificate_number", TEXT, L_CERT_NO),
        ("candidate_name", TEXT, L_NAME),
        ("address", TEXT, L_ADDRESS),
        ("issuing_authority", TEXT, L_ISSUER + [r"Tahsildar", r"Tehsildar"]),
        ("issue_date", DATE, L_ISSUE_DATE),
    ])


def parse_degree_certificate(text):
    d = apply_specs(text, [
        ("certificate_number", TEXT, L_CERT_NO),
        ("college_name", TEXT, L_COLLEGE),
        ("university_name", TEXT, [r"University"]),
        ("candidate_name", TEXT, L_NAME),
        ("register_number", TEXT, [r"Register\s*(?:No|Number)", r"Reg\.?\s*No"]),
        ("date_of_birth", DATE, L_DOB),
        ("degree_name", TEXT, [r"Degree(?:\s+of)?", r"Degree\s+Name"]),
        ("department", TEXT, [r"Department", r"Branch", r"Subject"]),
        ("graduation_year", INT, [r"Year\s+of\s+(?:Passing|Graduation)", r"Month\s+and\s+Year"]),
        ("classification", TEXT, [r"Classification", r"Class\s+Obtained", r"Division"]),
    ])
    d["result"] = detect_result(text)
    return d


def parse_diploma_certificate(text):
    d = apply_specs(text, [
        ("certificate_number", TEXT, L_CERT_NO),
        ("college_name", TEXT, L_COLLEGE),
        ("emis_number", TEXT, [r"EMIS\s*(?:ID|No|Number)?"]),
        ("candidate_name", TEXT, L_NAME),
        ("register_number", TEXT, [r"Register\s*(?:No|Number)", r"Reg\.?\s*No"]),
        ("date_of_birth", DATE, L_DOB),
        ("course_name", TEXT, [r"Course(?:\s+Name)?", r"Diploma\s+in"]),
        ("department", TEXT, [r"Department", r"Branch"]),
        ("completion_year", INT, [r"Year\s+of\s+(?:Passing|Completion)", r"Month\s+and\s+Year"]),
        ("classification", TEXT, [r"Classification", r"Class\s+Obtained", r"Division"]),
    ])
    d["result"] = detect_result(text)
    return d


_SEM_LINE = re.compile(
    r"^[ \t]*(?P<code>[A-Z]{0,3}\d{3,6}[A-Z]?)[ \t]+(?P<name>[A-Za-z&,\-()/ ]+?)[ \t]+"
    r"(?P<nums>(?:\d{1,3}(?:\.\d+)?[ \t]*)+?)(?P<grade>[A-Z]\+?)?[ \t]*$",
    re.M,
)


def parse_diploma_semester(text):
    d = apply_specs(text, [
        ("college_name", TEXT, L_COLLEGE),
        ("emis_number", TEXT, [r"EMIS\s*(?:ID|No|Number)?"]),
        ("candidate_name", TEXT, L_NAME),
        ("register_number", TEXT, [r"Register\s*(?:No|Number)", r"Reg\.?\s*No"]),
        ("date_of_birth", DATE, L_DOB),
        ("course_name", TEXT, [r"Course(?:\s+Name)?", r"Diploma\s+in"]),
        ("department", TEXT, [r"Department", r"Branch"]),
        ("semester", INT, [r"Semester"]),
        ("exam_month", TEXT, [r"Month\s+of\s+Examination", r"Exam(?:ination)?\s+Month"]),
        ("exam_year", INT, [r"Year\s+of\s+Examination", r"Exam(?:ination)?\s+Year", r"Month\s+and\s+Year"]),
        ("total_marks", NUM, [r"Grand\s+Total", r"Total\s+Marks"]),
        ("maximum_marks", NUM, [r"Maximum\s+Marks"]),
        ("percentage", NUM, [r"Percentage"]),
        ("overall_grade", TEXT, [r"Overall\s+Grade", r"\bCGPA\b"]),
    ])
    if d.get("semester") and d["semester"] > 12:  # a year was captured by mistake
        d["semester"] = None
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
# 8. Registry
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