"""Schema-aware OCR extraction facade for the existing certificate OCR service.

OCR and layout handling live in :mod:`ocr_service`; this module guarantees the
database column set and presents a review-friendly JSON response.
"""
from __future__ import annotations

import re
import io
from typing import Any

from app import ocr_service as core
from PIL import Image, ImageOps
import pytesseract
import pymupdf as fitz


def _group(prefixes, suffixes):
    return [f"{p}_{s}" for p in prefixes for s in suffixes]


def _candidate_from_identity_crop(text: str) -> str | None:
    """Read a candidate name only from the focused certificate identity crop."""
    marker = "--- IDENTITY CROP ---"
    if marker not in text:
        return None
    lines = text.rsplit(marker, 1)[1].splitlines()
    label = re.compile(r"NAME\s+OF\s+(?:THE\s+)?CANDIDATE|CANDIDATE\s*NAME", re.I)
    label_index = next((i for i, line in enumerate(lines) if label.search(line)), None)
    dob_index = next((i for i, line in enumerate(lines) if re.search(r"DATE\s+OF\s+BIRTH", line, re.I)), None)
    if label_index is None:
        if dob_index is None:
            return None
        label_index = max(0, dob_index - 5)
    stop = next(
        (i for i in range(label_index + 1, len(lines)) if re.search(r"DATE\s+OF\s+BIRTH|FATHER|GUARDIAN|MOTHER|REGISTER\s+NUMBER", lines[i], re.I)),
        len(lines),
    )
    excluded = {
        "NAME", "CANDIDATE", "SESSION", "MAY", "MAR", "MARCH", "APRIL", "JUNE", "JULY",
        "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER", "DATE", "BIRTH", "FATHER",
        "MOTHER", "GUARDIAN", "REGISTER", "PERMANENT", "NUMBER", "STANDARD", "CLASS", "TAMIL",
        "NADU", "STATE", "BOARD", "SCHOOL", "EXAMINATION", "CERTIFICATE", "SECONDARY", "HIGHER",
    }
    candidates = []
    for line in lines[label_index + 1:stop]:
        value = re.sub(r"[^A-Za-z .'-]", " ", line).strip(" .'-")
        words = [word.strip(".'-") for word in value.split() if word.upper().rstrip(".") not in excluded]
        if not 1 <= len(words) <= 4 or not all(re.fullmatch(r"[A-Za-z][A-Za-z'.-]*", word) for word in words):
            continue
        letters = sum(len(word) for word in words)
        if letters < 4 or letters > 30:
            continue
        # Tamil labels can be transliterated into title-case English-looking
        # fragments by the Latin recognizer. TN certificate candidate names
        # are printed in uppercase, so prefer a clearly uppercase value when
        # both forms occur in this focused block.
        uppercase_bonus = 100 if all(word.isupper() for word in words) else 0
        normalized = [word.upper().rstrip(".") for word in words]
        if len(normalized) == 1 and len(normalized[0]) >= 6 and normalized[0][-1] in {"S", "V"}:
            normalized = [normalized[0][:-1], normalized[0][-1]]
        candidates.append((uppercase_bonus + sum(len(word) for word in normalized if len(word) > 1), " ".join(normalized)))
    return max(candidates, default=(0, None))[1]


STATE10 = ["certificate_serial_number", "examination_board", "certificate_name", "standard", "candidate_name", "date_of_birth", "roll_number", "register_number", "permanent_register_number", "medium_of_instruction", "session", "exam_year", "school_name", "school_address", "tmr_code_number", "tmr_code_date", "emis_id"] + _group([f"subject_{i}" for i in range(1, 6)], ["name", "marks"]) + ["total_marks", "maximum_total_marks", "percentage", "result", "certificate_file_path"]
STATE12_SUBJECTS = ["tamil", "english", "mathematics", "business_mathematics", "business_studies", "computer_science", "computer_applications", "political_science", "accountancy", "chemistry", "economics", "geography", "physics", "biology", "botany", "zoology", "commerce", "history"]
STATE12_PRACTICAL = {"computer_science", "computer_applications", "chemistry", "physics", "biology", "botany", "zoology"}
STATE12 = ["certificate_serial_number", "examination_board", "certificate_name", "standard", "candidate_name", "date_of_birth", "roll_number", "register_number", "permanent_register_number", "medium_of_instruction", "session", "exam_year", "school_name", "school_address", "emis_id", "group_code", "group_name", "group_category"] + [f"{p}_{suffix}" for p in STATE12_SUBJECTS for suffix in ((["theory_marks", "internal_marks", "practical_marks", "marks"]) if p in STATE12_PRACTICAL else ["theory_marks", "internal_marks", "marks"])] + ["total_marks", "maximum_total_marks", "percentage", "result", "certificate_file_path"]
CBSE10_SUBJECTS = ["english", "hindi", "tamil", "sanskrit", "mathematics", "science", "social_science", "information_technology", "computer_applications", "artificial_intelligence", "other_subject_1", "other_subject_2"]
CBSE12_SUBJECTS = ["english", "hindi", "tamil", "sanskrit", "mathematics", "applied_mathematics", "physics", "chemistry", "biology", "computer_science", "informatics_practices", "biotechnology", "accountancy", "business_studies", "economics", "entrepreneurship", "history", "geography", "political_science", "sociology", "psychology", "philosophy", "physical_education", "home_science", "fine_arts", "legal_studies", "other_subject_1", "other_subject_2"]
CBSE_COMMON = ["candidate_name", "father_name", "mother_name", "roll_number", "school_name", "school_affiliation_number", "date_of_birth", "examination_year", "examination_session"]
def _cbse(subjects, include_stream=False):
    cols = CBSE_COMMON + (["stream"] if include_stream else [])
    for p in subjects:
        if p.startswith("other_subject_"):
            cols.append(f"{p}_name")
        cols.extend((f"{p}_marks", f"{p}_5_marks", f"{p}_grade"))
    return cols + ["total_marks", "maximum_marks", "percentage", "overall_grade", "result", "certificate_file_path", "verification_status", "verified_by", "verified_at"]

TC_BASE = ["tc_number", "admission_number", "school_name", "school_affiliation_number", "school_address", "student_name", "father_name", "mother_name", "nationality", "religion", "community", "gender", "date_of_birth", "identification_marks", "standard", "date_of_admission", "scholarship_details", "medical_inspection", "date_of_leaving", "character_conduct", "date_of_tc", "course_of_study"]
def _tc(state=False, senior=False):
    cols = TC_BASE.copy()
    if state: cols.insert(1, "emis_number")
    cols += (["education_type", "subjects_offered"] if senior else []) + ["certificate_file_path", "extracted_text", "verification_status", "verified_by", "verified_at"]
    return cols


def _recover_state10_header(fields: dict, text: str) -> None:
    """Recover common TN SSLC header values when a label is split by OCR."""
    focused_candidate = _candidate_from_identity_crop(text)
    if focused_candidate:
        fields["candidate_name"] = focused_candidate
    if fields.get("candidate_name") and re.search(r"\b(?:FATHER|MOTHER|GUARDIAN|CANDIDATE|STUDENT|PUPIL|NAME|SESSION|STANDARD|CLASS|TOTAL|MARKS|OBTAINED|PASS)\b", str(fields["candidate_name"]), re.I):
        fields["candidate_name"] = None
    if not fields.get("candidate_name"):
        excluded = {"TAMIL", "NADU", "GOVERNMENT", "CERTIFICATE", "SECONDARY", "SCHOOL", "LEAVING", "EXAMINATION", "EXAMINATIONS", "CANDIDATE", "SESSION", "STANDARD", "MEDIUM", "INSTRUCTION", "MARKS", "OBTAINED", "ROLL", "NUMBER", "ENGLISH", "SCIENCE", "SOCIAL", "MATHEMATICS", "MAY", "MARCH", "APRIL", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"}
        candidates = []
        lines = text.splitlines()
        identity_marker = "--- IDENTITY CROP ---"
        if identity_marker in text:
            crop_lines = text.rsplit(identity_marker, 1)[1].splitlines()
            crop_label = next((i for i, line in enumerate(crop_lines) if re.search(r"NAME\s+OF\s+THE\s+CANDIDATE|CANDIDATE\s*NAME", line, re.I)), None)
            crop_end = next((i for i, line in enumerate(crop_lines) if re.search(r"FATHER|GUARDIAN|DATE\s+OF\s+BIRTH", line, re.I)), len(crop_lines))
            if crop_label is not None:
                for line in crop_lines[crop_label + 1:crop_end]:
                    value = re.sub(r"[^A-Za-z .'-]", " ", line).strip(" .'-")
                    words = [word for word in value.split() if word.upper().rstrip(".") not in excluded]
                    if words and words[-1].lower() in {"is", "|"}:
                        words.pop()
                    if not 1 <= len(words) <= 3 or any(len(word) < 1 for word in words):
                        continue
                    letters = sum(len(word) for word in words if len(word) > 1)
                    if letters < 5 or letters > 24:
                        continue
                    normalized = [word.upper().rstrip(".") for word in words]
                    # TN SSLC often prints a final initial immediately after
                    # the name; OCR can join it to the name in the isolated crop.
                    if len(normalized) == 1 and len(normalized[0]) >= 6:
                        normalized = [normalized[0][:-1], normalized[0][-1]]
                    fields["candidate_name"] = " ".join(normalized)
                    return
        label_idx = next((i for i, line in enumerate(lines) if re.search(r"NAME\s+OF\s+THE\s+CANDIDATE|CANDIDATE\s*NAME", line, re.I)), None)
        end_idx = next((i for i, line in enumerate(lines) if re.search(r"FATHER|GUARDIAN", line, re.I)), len(lines))
        # Candidate identity is in the block between the candidate heading
        # and parent headings. Never scan the marks table or the rest of page.
        identity_lines = lines[label_idx:min(end_idx, label_idx + 12)] if label_idx is not None and label_idx < end_idx else []
        for line in identity_lines:
            value = re.sub(r"[^A-Za-z .'-]", " ", line).strip(" .'-")
            # Tesseract often appends the OCR word "is" to a name, or puts
            # the final initial on a separate token (e.g. "MANIMEGALAI S").
            # The candidate value can share a line with the adjacent session
            # text (e.g. "DHIVYA S MAY 2022"). Remove known headings/months
            # before judging the remaining name tokens.
            words = [
                word for word in value.split()
                if word.upper().rstrip(".") not in excluded
            ]
            if words and words[-1].lower() in {"is", "|"}:
                words.pop()
            upper_words = [w.upper().rstrip(".") for w in words]
            if not 1 <= len(words) <= 3 or any(w in excluded for w in upper_words):
                continue
            if any(len(w) < 1 for w in words) or not all(re.fullmatch(r"[A-Za-z][A-Za-z'.-]*", w) for w in words):
                continue
            # A strong candidate line has a long name token and may have one
            # or two initials. Compare candidates so short OCR fragments do
            # not beat the actual name line.
            letters = sum(len(w) for w in words if len(w) > 1)
            if all(w.upper() == w for w in words) and letters >= 5 and letters <= 24:
                candidates.append((letters, " ".join(w.upper().rstrip(".") for w in words)))
        if candidates:
            fields["candidate_name"] = max(candidates, key=lambda item: item[0])[1]
    if not fields.get("roll_number"):
        m = re.search(r"ROLL\s*(?:NO\.?|NUMBER)\b[^\n]{0,100}\n\s*(\d{6,10})\b", text, re.I)
        if not m:
            m = re.search(r"\b(\d{6,10})\s+ENGLISH\b", text, re.I)
        if m:
            fields["roll_number"] = m.group(1)
    if not fields.get("tmr_code_number") or not fields.get("tmr_code_date"):
        m = re.search(r"\b(\d{7,8})\s+(\d{1,2}[./-]\d{1,2}[./-]\d{4})\b", text)
        if m:
            fields["tmr_code_number"] = fields.get("tmr_code_number") or m.group(1)
            fields["tmr_code_date"] = fields.get("tmr_code_date") or core.to_date(m.group(2))


def _secondary_ocr(content: bytes, filename: str) -> str:
    """Try an alternate page-segmentation pass when primary OCR missed fields."""
    images = []
    if filename.lower().endswith(".pdf"):
        doc = fitz.open(stream=content, filetype="pdf")
        try:
            for page in list(doc)[:5]:
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), colorspace=fitz.csRGB)
                images.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
        finally:
            doc.close()
    else:
        images.append(Image.open(io.BytesIO(content)))
    lang = core._effective_ocr_lang()
    result = []
    for img in images:
        img = ImageOps.exif_transpose(img).convert("L")
        img = ImageOps.autocontrast(img)
        if max(img.size) < 1800:
            scale = 1800 / max(img.size)
            img = img.resize((int(img.width * scale), int(img.height * scale)), Image.Resampling.LANCZOS)
        # PSM 11 is better at finding sparse labels and separate value blocks.
        for psm in (11, 6):
            try:
                value = pytesseract.image_to_string(img, lang=lang, config=f"--oem 3 --psm {psm}")
                if value.strip(): result.append(value)
            except Exception:
                continue
    return "\n".join(result)

SIMPLE = {
 "birth_certificate": "certificate_number student_name gender date_of_birth place_of_birth father_name mother_name permanent_address issuing_authority issue_date certificate_file_path extracted_text verification_status verified_by verified_at",
 "aadhaar_card": "name_on_card date_of_birth father_name address aadhaar_last_four certificate_file_path extracted_text verification_status verified_by verified_at",
 "community_certificate": "certificate_number candidate_name father_name community_name caste_name address issuing_authority issue_date certificate_file_path extracted_text verification_status verified_by verified_at",
 "income_certificate": "certificate_number candidate_name annual_income occupation address issuing_authority issue_date certificate_file_path extracted_text verification_status verified_by verified_at",
 "nativity_certificate": "certificate_number candidate_name address issuing_authority issue_date certificate_file_path extracted_text verification_status verified_by verified_at",
 "degree_certificate": "certificate_number college_name university_name candidate_name register_number date_of_birth degree_name department graduation_year classification result certificate_file_path extracted_text verification_status verified_by verified_at",
 "diploma_certificate": "certificate_number college_name emis_number candidate_name register_number date_of_birth course_name department completion_year classification result certificate_file_path extracted_text verification_status verified_by verified_at",
}
SEM = "college_name emis_number candidate_name register_number date_of_birth course_name department semester exam_month exam_year".split() + _group([f"subject_{i}" for i in range(1, 9)], ["name", "code", "internal_marks", "external_marks", "practical_marks", "total_marks", "maximum_marks", "grade", "credits"]) + "total_marks maximum_marks percentage overall_grade result certificate_file_path verification_status verified_by verified_at".split()
SCHEMAS = {
 "state_board_10th": STATE10, "state_board_12th": STATE12,
 "cbse_10th_marksheet": _cbse(CBSE10_SUBJECTS), "cbse_12th_marksheet": _cbse(CBSE12_SUBJECTS, True),
 "state_board_10th_tc": _tc(True), "state_board_12th_tc": _tc(True, True), "cbse_10th_tc": _tc(False), "cbse_12th_tc": _tc(False, True),
 "diploma_semester_marksheet": SEM,
 **{k: v.split() for k, v in SIMPLE.items()},
}

ALIASES = {
    "state_board_10th_marksheet": "state_board_10th", "state_board_10th_marksheets": "state_board_10th",
    "10th_marksheet": "state_board_10th", "10th_state_board": "state_board_10th", "10th_state_board_marksheet": "state_board_10th", "10th_state_board_marksheets": "state_board_10th",
    "state_board_12th_marksheet": "state_board_12th", "state_board_12th_marksheets": "state_board_12th",
    "12th_marksheet": "state_board_12th", "12th_state_board": "state_board_12th", "12th_state_board_marksheet": "state_board_12th", "12th_state_board_marksheets": "state_board_12th",
    "cbse_10th": "cbse_10th_marksheet", "cbse_10th_marksheets": "cbse_10th_marksheet",
    "cbse_12th": "cbse_12th_marksheet", "cbse_12th_marksheets": "cbse_12th_marksheet",
    "transfer_certificate": "state_board_10th_tc",
}


def _after_label(text: str, label_rx: str, max_chars: int = 180) -> str | None:
    """Read a label's same-line value or nearby following OCR line."""
    match = re.search(rf"(?:{label_rx})\s*[:#-]?\s*([^\n]*)", text, re.I)
    if not match:
        return None
    value = match.group(1).strip(" :#-\t")
    if value and not re.search(r"\b(?:NAME|NUMBER|DATE|SCHOOL|ROLL|REGISTER|DOB)\b", value, re.I):
        return value[:max_chars].strip()
    rest = text[match.end():match.end() + max_chars]
    for line in rest.splitlines():
        line = line.strip(" :#-\t")
        if not line:
            continue
        if re.search(r"\b(?:DATE\s+OF\s+BIRTH|ROLL\s*(?:NO|NUMBER)|SCHOOL|REGISTER|NAME\s+OF)\b", line, re.I):
            break
        return line
    return None


def _state_board_fallback(fields: dict, text: str) -> None:
    """Extract identity rows whose labels and values are split by layout/OCR."""
    name_labels = r"NAME\s+OF\s+THE\s+CANDIDATE|CANDIDATE\s*NAME"
    if not fields.get("candidate_name") or re.search(r"CERTIFY|FATHER|GUARDIAN|MOTHER|CANDIDATE|NAME", str(fields.get("candidate_name") or ""), re.I):
        candidate = _after_label(text, name_labels)
        if candidate:
            candidate = re.sub(r"\b(?:SESSION|MAY|MARCH|APRIL|JUNE|JULY|20\d{2})\b.*$", "", candidate, flags=re.I).strip()
            clean = core.clean_name(candidate)
            if clean and not re.search(r"CERTIFY|FATHER|GUARDIAN|MOTHER|CANDIDATE|NAME", clean, re.I):
                fields["candidate_name"] = clean
    if not fields.get("date_of_birth"):
        match = re.search(r"DATE\s+OF\s+BIRTH[^\d\n]{0,50}(\d{1,2}[./-]\d{1,2}[./-]\d{4})", text, re.I)
        if not match:
            match = re.search(r"DATE\s+OF\s+BIRTH.{0,120}?(\d{1,2}[./-]\d{1,2}[./-]\d{4})", text, re.I | re.S)
        if match:
            fields["date_of_birth"] = core.to_date(match.group(1))
    if not fields.get("school_name"):
        matches = list(re.finditer(r"(?:NAME\s+OF\s+THE\s+SCHOOL|SCHOOL\s+NAME)\s*[:#-]?\s*([^\n]*)", text, re.I))
        candidates = [m.group(1).strip(" :#-") for m in matches[::-1]]
        candidates.extend(line.strip() for line in text.splitlines()[::-1] if re.search(r"\b(?:GOVT\.?|GOVERNMENT|MATRIC|HIGH\s+SCHOOL|HR\.?\s*SEC)\b", line, re.I))
        for school in candidates:
            school = re.sub(r"\s*\(\s*\d{4,8}\s*\)\s*$", "", school).strip()
            latin = re.sub(r"[^A-Za-z]", "", school)
            if len(latin) > 8 and not re.search(r"CERTIFICATE|EXAMINATION|DATE|NAME\s+OF", school, re.I):
                fields["school_name"] = re.sub(r"\s+", " ", school).upper()
                break
    if not fields.get("permanent_register_number"):
        match = re.search(r"PERMANENT\s+REGISTER\s*(?:NO\.?|NUMBER)?[^\n]{0,100}\n?\s*([A-Z]{1,3}\d[A-Z0-9]{8,18})\b", text, re.I)
        if match:
            fields["permanent_register_number"] = match.group(1).upper()
    if not fields.get("tmr_code_number") or not fields.get("tmr_code_date"):
        match = re.search(r"\b([A-Z]?\d{7,8})\s+(\d{1,2}[./-]\d{1,2}[./-]\d{4})\b", text, re.I)
        if match:
            fields["tmr_code_number"] = fields.get("tmr_code_number") or match.group(1).upper()
            fields["tmr_code_date"] = fields.get("tmr_code_date") or core.to_date(match.group(2))


def _recover_state12_candidate_name(fields: dict, text: str) -> None:
    """Recover the HSE candidate name from the certificate identity block."""
    focused_candidate = _candidate_from_identity_crop(text)
    if focused_candidate:
        fields["candidate_name"] = focused_candidate
    current = str(fields.get("candidate_name") or "").strip()
    invalid_name = re.search(
        r"CERTIFY|FATHER|GUARDIAN|MOTHER|CANDIDATE|STUDENT|PUPIL|SESSION|STANDARD|CLASS|TOTAL|MARKS|OBTAINED|PASS",
        current,
        re.I,
    )
    current_tokens = re.findall(r"[A-Za-z]+", current)
    ambiguous_current = bool(current) and not any(len(token) >= 3 for token in current_tokens)
    if current and not invalid_name and not ambiguous_current:
        joined_initials = re.fullmatch(r"([A-Z]{4,})VS", current, re.I)
        separated_initials = re.fullmatch(r"(.+\s+)VS", current, re.I)
        if joined_initials:
            fields["candidate_name"] = f"{joined_initials.group(1).upper()} V S"
        elif separated_initials:
            fields["candidate_name"] = f"{separated_initials.group(1).strip().upper()} V S"
        return

    identity_marker = "--- IDENTITY CROP ---"
    source_text = text.rsplit(identity_marker, 1)[1] if identity_marker in text else text
    lines = [re.sub(r"\s+", " ", line).strip(" :|\t") for line in source_text.splitlines()]
    name_label = re.compile(
        r"NAME\s+OF\s+(?:THE\s+)?CANDIDATE|CANDIDATE\s*NAME|தேர்வரின்\s*பெயர்",
        re.I,
    )
    dob_label = re.compile(r"DATE\s+OF\s+BIRTH|பிறந்த\s+தேதி", re.I)
    stop_label = re.compile(
        r"FATHER|GUARDIAN|MOTHER|PERMANENT\s+REGISTER|REGISTER\s+NUMBER|ROLL\s+NO|SESSION|DATE\s+OF\s+BIRTH",
        re.I,
    )
    starts = [i for i, line in enumerate(lines) if name_label.search(line)]

    # The HSE format places the candidate identity immediately before the DOB
    # row. If OCR lost the label, inspect only that small bounded area.
    if not starts:
        dob_index = next((i for i, line in enumerate(lines) if dob_label.search(line)), None)
        if dob_index is None:
            return
        starts = [max(0, dob_index - 4)]

    excluded = re.compile(
        r"STATE|BOARD|SCHOOL|EXAMINATION|CERTIFICATE|HIGHER|SECONDARY|COURSE|SESSION|STANDARD|CLASS|DATE|BIRTH|REGISTER|NUMBER|ROLL|PERMANENT|FATHER|MOTHER|GUARDIAN|NAME|CANDIDATE|TAMIL|NADU|MARCH|MAY|202[0-9]",
        re.I,
    )
    for start in starts:
        end = next(
            (i for i in range(start + 1, min(start + 8, len(lines))) if stop_label.search(lines[i])),
            min(start + 8, len(lines)),
        )
        for line in lines[start:end]:
            # Strip labels and OCR spillover from adjacent fields on the same line.
            value = name_label.sub(" ", line)
            value = re.split(r"\b(?:MARKS?|CERTIFI\w*|CERTIFICATE|DATE|PERMANENT|SESSION)\b", value, maxsplit=1, flags=re.I)[0]
            value = re.sub(r"[^A-Za-z .'-]", " ", value)
            words = [word.strip(".'-") for word in value.split() if word.strip(".'-")]
            if not 1 <= len(words) <= 4:
                continue
            if excluded.search(" ".join(words)) or not all(re.fullmatch(r"[A-Za-z][A-Za-z'.-]*", word) for word in words):
                continue
            # On TN certificates OCR sometimes joins the printed initials to
            # the surname (for example, DEEPIKAVS).
            if len(words) == 1:
                joined_initials = re.fullmatch(r"([A-Z]{4,})(VS)", words[0], re.I)
                if joined_initials:
                    words = [joined_initials.group(1), "V", "S"]
            letters = sum(len(word) for word in words if len(word) > 1)
            if letters < 4 or letters > 30:
                continue
            fields["candidate_name"] = " ".join(word.upper().rstrip(".") for word in words)
            return


def _needs_secondary_pass(kind: str, fields: dict) -> bool:
    """Retry scans with missing or internally suspicious identity/marks fields."""
    name = str(fields.get("candidate_name") or "")
    bad_name = bool(re.search(r"CERTIFY|FATHER|GUARDIAN|MOTHER|CANDIDATE|STUDENT|PUPIL|SESSION|STANDARD|CLASS|TOTAL|MARKS|OBTAINED|PASS", name, re.I))
    if kind in {"state_board_10th", "state_board_12th", "cbse_10th_marksheet", "cbse_12th_marksheet"}:
        if not name or bad_name or not fields.get("date_of_birth") or not fields.get("school_name"):
            return True
    year_key = "examination_year" if kind.startswith("cbse_") else "exam_year"
    year = fields.get(year_key)
    if year is not None and (not isinstance(year, (int, float)) or not 1900 <= int(year) <= 2050):
        return True
    if kind == "state_board_10th":
        marks = [fields.get(f"subject_{i}_marks") for i in range(1, 6)]
        if all(isinstance(v, (int, float)) for v in marks) and fields.get("total_marks") is not None:
            if abs(sum(marks) - float(fields["total_marks"])) > 2:
                return True
    if kind in {"cbse_10th_marksheet", "cbse_12th_marksheet"}:
        candidates = [fields.get(f"{s}_marks") for s in (CBSE10_SUBJECTS if kind == "cbse_10th_marksheet" else CBSE12_SUBJECTS) if fields.get(f"{s}_marks") is not None]
        if fields.get("total_marks") is not None and len(candidates) >= 5 and fields.get("maximum_marks") and float(fields["total_marks"]) > float(fields["maximum_marks"]):
            return True
    return False


def _cbse_fallback(fields: dict, text: str, is_senior: bool) -> None:
    """Recover CBSE identity fields and named additional subjects."""
    bad_name = re.search(r"CERTIFY|THIS\s+IS|GUARDIAN|CANDIDATE|NAME", str(fields.get("candidate_name") or ""), re.I)
    if not fields.get("candidate_name") or bad_name:
        match = re.search(r"(?:THIS\s+IS\s+TO.{0,18}?THAT|CERTIFY\s+THAT)\s+([A-Z][A-Z .'-]{2,50})", text, re.I)
        if match:
            fields["candidate_name"] = core.clean_name(match.group(1))
    for col, lab in (("father_name", r"FATHER(?:'S|S)?(?:\s*/\s*GUARDIAN(?:'S|S)?)?\s+NAME"), ("mother_name", r"MOTHER(?:'S|S)?\s+NAME")):
        if not fields.get(col):
            val = _after_label(text, lab, 100)
            if val:
                val = re.split(r"\b(?:MOTHER(?:'S)?\s+NAME|FATHER(?:'S)?|DATE\s+OF\s+BIRTH|SCHOOL)\b", val, maxsplit=1, flags=re.I)[0]
                name = core.clean_name(val)
                if name: fields[col] = name
    if not fields.get("school_name") or len(str(fields.get("school_name") or "")) < 20:
        school_candidates = []
        for line in text.splitlines():
            m = re.search(r"\bSCHOOL\b\s*[:#-]?\s*(.+)$", line, re.I)
            if m:
                val = m.group(1).strip()
                if not re.match(r"(?:EXAMINATION|CERTIFICATE|AFFILIATION|CODE)\b", val, re.I):
                    school_candidates.append(val)
        if school_candidates:
            val = max(school_candidates, key=lambda item: len(re.sub(r"[^A-Za-z]", "", item)))
            val = re.sub(r"\s*\(\s*\d{4,8}\s*\)\s*$", "", val).strip()
            if len(re.sub(r"[^A-Za-z]", "", val)) > 5: fields["school_name"] = val.upper()
    if not fields.get("roll_number"):
        m = re.search(r"ROLL\s*(?:NO\.?|NUMBER)[^\d\n]{0,50}(\d{6,10})", text, re.I)
        if m: fields["roll_number"] = m.group(1)
    if not fields.get("school_affiliation_number"):
        m = re.search(r"AFFILIATION\s*(?:NO\.?|NUMBER)[^\d\n]{0,30}(\d{4,10})", text, re.I)
        if m: fields["school_affiliation_number"] = m.group(1)
    if not isinstance(fields.get("examination_year"), int) or not 1900 <= fields["examination_year"] <= 2050:
        m = re.search(r"(?:SECONDARY|SENIOR)\s+SCHOOL\s+EXAMINATION[^\n]{0,60}\b(19\d{2}|20\d{2})\b", text, re.I)
        if not m:
            m = re.search(r"(?:EXAMINATION\s+YEAR|YEAR\s+OF\s+EXAMINATION)[^\d\n]{0,30}(19\d{2}|20\d{2})", text, re.I)
        if m: fields["examination_year"] = int(m.group(1))
    if not fields.get("examination_session"):
        m = re.search(r"\b(MAIN|ANNUAL|COMPARTMENT|SUPPLEMENTARY|IMPROVEMENT)\b", text, re.I)
        if m: fields["examination_session"] = m.group(1).title()
    if is_senior and not fields.get("stream"):
        m = re.search(r"\b(?:STREAM|STREAM OF STUDY)\s*[:\-]?\s*([A-Z][A-Z &/-]{2,40})", text, re.I)
        if m: fields["stream"] = m.group(1).strip()
    # Preserve unfamiliar CBSE rows in the two schema-provided "other" slots.
    known = ("ENGLISH", "HINDI", "TAMIL", "SANSKRIT", "MATHEMATICS", "SCIENCE", "SOCIAL SCIENCE", "INFORMATION TECHNOLOGY", "ARTIFICIAL INTELLIGENCE", "COMPUTER APPLICATIONS", "PHYSICS", "CHEMISTRY", "BIOLOGY", "COMPUTER SCIENCE", "INFORMATICS PRACTICES", "BIOTECHNOLOGY", "ACCOUNTANCY", "BUSINESS STUDIES", "ECONOMICS", "ENTREPRENEURSHIP", "HISTORY", "GEOGRAPHY", "POLITICAL SCIENCE", "SOCIOLOGY", "PSYCHOLOGY", "PHILOSOPHY", "PHYSICAL EDUCATION", "HOME SCIENCE", "FINE ARTS", "LEGAL STUDIES", "APPLIED MATHEMATICS")
    extra_rows = []
    for line in text.splitlines():
        m = re.match(r"^\s*\d{3}\s+([A-Z][A-Z &.-]{2,40}?)\s+\d{2,3}\s+\d{2,3}\s+\d{2,3}\b", line, re.I)
        if m:
            subject = re.sub(r"\s+", " ", m.group(1)).strip(" .")
            if not any(k in subject.upper() for k in known):
                extra_rows.append((subject, line))
    other_n = 1
    for subject, line in extra_rows:
        if other_n > 2: break
        prefix = f"other_subject_{other_n}"
        fields[f"{prefix}_name"] = subject.title()
        marks = re.findall(r"(?<!\d)(\d{1,3})(?!\d)", line)
        if marks:
            fields[f"{prefix}_marks"] = float(marks[-1])
            fields[f"{prefix}_5_marks"] = round(float(marks[-1]) / 20, 2)
        grade = re.search(r"\b([A-E][1-2]?)\b", line, re.I)
        if grade: fields[f"{prefix}_grade"] = grade.group(1).upper()
        other_n += 1


def _repair_state12_table(fields: dict, text: str) -> None:
    """Rebuild Tamil Nadu HSE marks from the subject-to-next-subject rows."""
    table_marker = "--- TABLE CROP ---"
    if table_marker in text:
        table_text = text.rsplit(table_marker, 1)[1]
        secondary_marker = "--- SECONDARY OCR ---"
        if secondary_marker in table_text:
            table_text = table_text.split(secondary_marker, 1)[0]
        lines = table_text.splitlines()
    else:
        lines = text.splitlines()
    table_start = next((i for i, line in enumerate(lines) if re.search(r"\bTHEORY\b", line, re.I)), 0)
    table_end = next((i for i in range(table_start + 1, len(lines)) if re.search(r"TOTAL\s+MARKS", lines[i], re.I)), len(lines))
    lines = lines[table_start:table_end]
    subjects = [
        ("computer_science", r"COMPUTER\s+SCIENCE", True),
        ("computer_applications", r"COMPUTER\s+APPLICATIONS", True),
        ("business_mathematics", r"BUSINESS\s+MATHEMATICS", False),
        ("business_studies", r"BUSINESS\s+STUDIES", False),
        ("political_science", r"POLITICAL\s+SCIENCE", False),
        ("mathematics", r"MATHEMATICS", False),
        ("accountancy", r"ACCOUNTANCY", False),
        ("chemistry", r"CHEMISTRY", True),
        ("economics", r"ECONOMICS", False),
        ("geography", r"GEOGRAPHY", False),
        ("physics", r"PHYSICS", True),
        ("biology", r"BIOLOGY", True),
        ("botany", r"BOTANY", True),
        ("zoology", r"ZOOLOGY", True),
        ("commerce", r"COMMERCE", False),
        ("history", r"HISTORY", False),
        ("tamil", r"TAMIL", False),
        ("english", r"ENGLISH", False),
    ]
    starts = []
    for i, line in enumerate(lines):
        upper = line.upper()
        for key, rx, practical in subjects:
            if re.search(rf"\b(?:{rx})\b", upper):
                if key == "mathematics" and "BUSINESS" in upper:
                    continue
                if key == "english" and "MEDIUM" in upper:
                    continue
                starts.append((i, key, practical))
                break
    starts.sort()
    unique = []
    seen = set()
    for row in starts:
        if row[1] not in seen:
            unique.append(row)
            seen.add(row[1])
    def score_values(line: str, has_practical: bool) -> list[int] | None:
        if re.search(r"THEORY|PRACTICAL|INTERNAL|OBTAINED|ROLL\s+NO|SUBJECT", line, re.I):
            return None
        cleaned = re.sub(r"\b[oO](\d{2})\b", r"0\1", line)
        nums = [int(n) for n in re.findall(r"(?<!\d)(\d{2,3})(?!\d)", cleaned)]
        nums = [number for number in nums if 0 <= number <= 100]
        if has_practical and len(nums) >= 4:
            for offset in range(len(nums) - 3):
                theory, practical, internal, total = nums[offset:offset + 4]
                calculated_total = theory + practical + internal
                # Some photographed Tamil Nadu marksheets confuse a 9 with 3
                # in the printed total (for example 090 -> 030). Keep the
                # three component columns and derive the total only when they
                # form a valid score out of 100.
                if calculated_total <= 100:
                    return [theory, practical, internal, calculated_total]
        elif has_practical and len(nums) == 3:
            # The practical score is occasionally missed by OCR while the
            # internal and printed total remain legible (e.g. 040 010 070).
            # Recover that component only when the visible middle value fits
            # the template's internal-mark column and the remainder is a
            # plausible practical score.
            theory, internal, total = nums
            practical = total - theory - internal
            if internal <= 25 and 0 <= practical <= 75 and total <= 100:
                return [theory, practical, internal, total]
        elif not has_practical and len(nums) >= 3:
            for offset in range(len(nums) - 2):
                theory, internal, total = nums[offset:offset + 3]
                calculated_total = theory + internal
                if calculated_total <= 100:
                    return [theory, internal, calculated_total]
        return None

    claimed_rows: set[int] = set()
    # OCR may emit a marks row immediately before its subject name when it
    # reads the certificate's separate columns. Search both sides of each
    # label and accept a row only when its marks add up to its printed total.
    for start, key, has_practical in unique:
        values = None
        row_index = None
        for offset in (0, -1, -2, -3, 1, 2, 3):
            candidate_index = start + offset
            if not (0 <= candidate_index < len(lines)) or candidate_index in claimed_rows:
                continue
            candidate_values = score_values(lines[candidate_index], has_practical)
            if candidate_values:
                values = candidate_values
                row_index = candidate_index
                break
        if not values:
            continue
        claimed_rows.add(row_index)
        prefix = f"{key}_"
        if has_practical:
            theory, practical, internal, total = values
            fields[prefix + "theory_marks"] = theory
            fields[prefix + "practical_marks"] = practical
            fields[prefix + "internal_marks"] = internal
            fields[prefix + "marks"] = total
        else:
            theory, internal, total = values
            fields[prefix + "theory_marks"] = theory
            fields[prefix + "internal_marks"] = internal
            fields[prefix + "marks"] = total

    # The HSE certificate uses a fixed subject order within each group. OCR
    # sometimes recognizes the score row but loses the subject word (often
    # English or Tamil). When all six arithmetic-valid score rows are present,
    # align them to the printed group sequence as a whole instead of guessing
    # each row from nearby OCR lines independently.
    group_sequences = {
        "biology": ("tamil", "english", "physics", "chemistry", "biology", "mathematics"),
        "computer science": ("tamil", "english", "physics", "chemistry", "computer_science", "mathematics"),
        "arts": ("tamil", "english", "economics", "commerce", "accountancy", "computer_applications"),
    }
    category = str(fields.get("group_category") or "").strip().casefold()
    sequence = group_sequences.get(category, ())
    practical_subjects = {"physics", "chemistry", "biology", "computer_science", "computer_applications", "botany", "zoology"}
    score_rows = []
    for index, line in enumerate(lines):
        if score_values(line, False) or score_values(line, True):
            score_rows.append(index)
    if sequence and len(score_rows) == len(sequence):
        for subject, row_index in zip(sequence, score_rows):
            values = score_values(lines[row_index], subject in practical_subjects)
            if not values:
                continue
            prefix = f"{subject}_"
            if subject in practical_subjects:
                theory, practical, internal, total = values
                fields[prefix + "theory_marks"] = theory
                fields[prefix + "practical_marks"] = practical
                fields[prefix + "internal_marks"] = internal
            else:
                theory, internal, total = values
                fields[prefix + "theory_marks"] = theory
                fields[prefix + "internal_marks"] = internal
            fields[prefix + "marks"] = total

    # Tamil is the first subject on Tamil Nadu HSE certificates. On some
    # rotated/low-contrast scans its label is lost while its score row remains
    # readable immediately before the English row. Recover only that bounded
    # row and only when its components add to the printed total.
    if not fields.get("tamil_marks") and unique:
        first_subject_index = min(start for start, _, _ in unique)
        first_row = None
        first_row_index = None
        for index in range(max(table_start, first_subject_index - 6), first_subject_index):
            values = score_values(lines[index], False)
            if values:
                first_row, first_row_index = values, index
        if first_row and first_row_index not in claimed_rows:
            theory, internal, total = first_row
            fields["tamil_theory_marks"] = theory
            fields["tamil_internal_marks"] = internal
            fields["tamil_marks"] = total


def _repair_cbse_table(fields: dict, text: str, is_senior: bool) -> None:
    """Read CBSE total marks and grades from subject rows, overriding noisy parser values."""
    source = text.rsplit("--- SECONDARY OCR ---", 1)[-1] if "--- SECONDARY OCR ---" in text else text
    lines = source.splitlines()
    subject_map = [
        ("social_science", r"SOCIAL\s+SCIENCE"), ("information_technology", r"INFORMATION\s+TECHNOLOGY"),
        ("computer_applications", r"COMPUTER\s+APPLICATIONS"), ("artificial_intelligence", r"ARTIFICIAL\s+INTELLIGENCE"),
        ("applied_mathematics", r"APPLIED\s+MATHEMATICS"), ("informatics_practices", r"INFORMATICS\s+PRACTICES"),
        ("physical_education", r"PHYSICAL\s+EDUCATION"), ("business_studies", r"BUSINESS\s+STUDIES"),
        ("fine_arts", r"FINE\s+ARTS|PAINTING"), ("home_science", r"HOME\s+SCIENCE"), ("political_science", r"POLITICAL\s+SCIENCE"),
    ]
    keys = CBSE12_SUBJECTS if is_senior else CBSE10_SUBJECTS
    for key in keys:
        if key in {name for name, _ in subject_map}:
            continue
        subject_map.append((key, key.replace("_", r"\s+")))
    starts = []
    for i, line in enumerate(lines):
        upper = line.upper()
        for key, pattern in subject_map:
            if re.search(rf"\b(?:{pattern})\b", upper):
                if key == "science" and "SOCIAL" in upper:
                    continue
                starts.append((i, key))
                break
    starts.sort()
    unique = []
    seen = set()
    for row in starts:
        if row[1] not in seen:
            unique.append(row)
            seen.add(row[1])

    word_values = {"ZERO": 0, "ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5, "SIX": 6, "SEVEN": 7, "EIGHT": 8, "NINE": 9,
                   "TEN": 10, "TWENTY": 20, "THIRTY": 30, "FORTY": 40, "FIFTY": 50, "SIXTY": 60, "SEVENTY": 70, "EIGHTY": 80, "NINETY": 90}
    for idx, (start, key) in enumerate(unique):
        end = unique[idx + 1][0] if idx + 1 < len(unique) else len(lines)
        block = " ".join(lines[start:end])
        # Discard grade tokens before reading marks; otherwise C2 can be read as 2 marks.
        no_grade = re.sub(r"\b[A-E][1-2]\b", " ", block, flags=re.I)
        phrases = re.findall(r"\b(?:ZERO|ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN|TWENTY|THIRTY|FORTY|FIFTY|SIXTY|SEVENTY|EIGHTY|NINETY)(?:\s+(?:ZERO|ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE))?\b", no_grade.upper())
        word_total = None
        if phrases:
            parts = phrases[-1].split()
            if len(parts) == 1:
                word_total = word_values[parts[0]]
            elif len(parts) == 2:
                word_total = word_values[parts[0]] + word_values[parts[1]]
        nums = [int(n) for n in re.findall(r"(?<!\d)(\d{2,3})(?!\d)", no_grade)]
        nums = [n for n in nums if 0 <= n <= 100]
        total = word_total if word_total is not None and 0 <= word_total <= 100 else (nums[-1] if nums else None)
        grade_matches = re.findall(r"\b([A-E][1-2]?)\b", block, re.I)
        if total is not None:
            fields[f"{key}_marks"] = total
            # The source sheet does not present a separate "out of 5" score.
            fields[f"{key}_5_marks"] = None
        if grade_matches:
            fields[f"{key}_grade"] = grade_matches[-1].upper()

    # The ordinary CBSE total is the five main subjects; additional subjects
    # remain separate. Recalculate only when exactly five main scores were read.
    main_keys = ["english", "hindi", "tamil", "sanskrit", "mathematics", "applied_mathematics", "physics", "chemistry", "biology", "computer_science", "informatics_practices", "accountancy", "business_studies", "economics", "history", "geography", "political_science", "sociology", "psychology", "philosophy", "physical_education", "home_science", "fine_arts", "legal_studies"]
    scores = [fields.get(f"{key}_marks") for key in main_keys if fields.get(f"{key}_marks") is not None]
    if len(scores) == 5:
        fields["total_marks"] = sum(scores)
        fields["maximum_marks"] = 500
        fields["percentage"] = round(sum(scores) / 5, 2)


def _keep_school_address_separate(fields: dict) -> bool:
    """Clear an address value that is only a duplicate of the school name."""
    address = fields.get("school_address")
    school = fields.get("school_name")
    if not address or not school:
        return False
    normalize = lambda value: re.sub(r"[^A-Z0-9]", "", str(value).upper())
    if normalize(address) == normalize(school):
        fields["school_address"] = None
        return True
    return False
def detect_document_type(text: str) -> str | None:
    found = core.detect_document_type(text)
    if found in SCHEMAS: return found
    u = (text or "").upper()
    if "AADHAAR" in u or "AADHAR" in u: return "aadhaar_card"
    if "BIRTH CERTIFICATE" in u or "REGISTRAR OF BIRTH" in u: return "birth_certificate"
    if "DIPLOMA" in u and "SEMESTER" in u: return "diploma_semester_marksheet"
    if "TRANSFER CERTIFICATE" in u or "SCHOOL LEAVING" in u:
        return "cbse_12th_tc" if "CBSE" in u and re.search(r"XII|12TH",u) else "cbse_10th_tc" if "CBSE" in u else "state_board_12th_tc" if re.search(r"XII|12TH",u) else "state_board_10th_tc"
    if "CBSE" in u and ("MARKS STATEMENT" in u or "MARKSHEET" in u): return "cbse_12th_marksheet" if re.search(r"CLASS XII|XII|12TH",u) else "cbse_10th_marksheet"
    for key, terms in (("income_certificate",("INCOME CERTIFICATE",)),("community_certificate",("COMMUNITY CERTIFICATE",)),("nativity_certificate",("NATIVITY CERTIFICATE",)),("degree_certificate",("DEGREE CERTIFICATE",)),("diploma_certificate",("DIPLOMA CERTIFICATE",))):
        if any(t in u for t in terms): return key
    if "HIGHER SECONDARY" in u or "HSS" in u: return "state_board_12th"
    if "SECONDARY SCHOOL" in u or "X STANDARD" in u: return "state_board_10th"
    return found if found in SCHEMAS else None

def extract_document(content: bytes, filename: str, doc_type: str | None = None) -> dict:
    """OCR one PDF/image and return normalized schema keys plus confidence metadata."""
    text = core.extract_text(content, filename)
    if not text.strip(): raise ValueError("No readable text was found in the document.")
    requested_type = re.sub(r"\s+", "_", (doc_type or "").strip().lower())
    kind = ALIASES.get(requested_type, requested_type) if requested_type and requested_type not in ("auto", "detect") else detect_document_type(text)
    if kind not in SCHEMAS: raise ValueError("Could not identify the certificate type; specify doc_type.")
    if kind not in core.DOC_TYPES: raise ValueError(f"Unsupported certificate type: {kind}")
    raw = core.parse_document(kind, text)
    raw = raw.get("fields", raw) if isinstance(raw, dict) else {}
    source_keys = set(raw)
    fields = {key: raw.get(key) for key in SCHEMAS[kind]}
    # When important identity fields are absent, use a second OCR segmentation
    # pass and reparse the combined text. Keep first-pass values unless missing.
    retry_keys = {
        "state_board_10th": ("candidate_name", "date_of_birth", "roll_number", "school_name"),
        "state_board_12th": ("candidate_name", "date_of_birth", "register_number", "school_name"),
        "cbse_10th_marksheet": ("candidate_name", "date_of_birth", "roll_number", "school_name"),
        "cbse_12th_marksheet": ("candidate_name", "date_of_birth", "roll_number", "school_name"),
    }.get(kind, ())
    if any(fields.get(key) is None for key in retry_keys) or _needs_secondary_pass(kind, fields):
        try:
            extra_text = _secondary_ocr(content, filename)
        except Exception:
            extra_text = ""
        if extra_text:
            combined_text = f"{text}\n--- SECONDARY OCR ---\n{extra_text}"
            try:
                retry_raw = core.parse_document(kind, combined_text)
                retry_raw = retry_raw.get("fields", retry_raw) if isinstance(retry_raw, dict) else {}
                source_keys.update(retry_raw)
                for key in fields:
                    if fields[key] is None and retry_raw.get(key) is not None:
                        fields[key] = retry_raw[key]
                text = combined_text
            except Exception:
                pass
    if kind == "state_board_10th":
        _recover_state10_header(fields, text)
        _state_board_fallback(fields, text)
    elif kind == "state_board_12th":
        _state_board_fallback(fields, text)
        _recover_state12_candidate_name(fields, text)
        _repair_state12_table(fields, text)
        # TN HSE tables label this value "Roll No. of Passing". Older parser
        # logic stored the repeated table value under register_number.
        if re.search(r"ROLL\s*NO\.?\s+OF\s+PASSING", text, re.I):
            if not fields.get("roll_number"):
                fields["roll_number"] = fields.get("register_number")
            if fields.get("register_number") == fields.get("roll_number") and not re.search(r"REGISTER\s*(?:NO|NUMBER)", text, re.I):
                fields["register_number"] = None
        serial = str(fields.get("certificate_serial_number") or "")
        serial_digits = re.search(r"\d{7,8}", serial)
        if serial_digits and not re.search(r"\bHSS\b", serial, re.I):
            fields["certificate_serial_number"] = f"HSS {serial_digits.group(0)}"
    elif kind in ("cbse_10th_marksheet", "cbse_12th_marksheet"):
        _cbse_fallback(fields, text, kind == "cbse_12th_marksheet")
        _repair_cbse_table(fields, text, kind == "cbse_12th_marksheet")
        for key in fields:
            if key.endswith("_5_marks"):
                fields[key] = None
    if kind == "state_board_12th":
        # Only return subject columns the State Board parser actually found in
        # this certificate. The database schema has columns for many possible
        # groups, but showing all of them creates dozens of empty review fields.
        detected_subjects = {
            subject
            for subject in STATE12_SUBJECTS
            if any(re.match(rf"^{re.escape(subject)}_(?:theory_marks|internal_marks|practical_marks|marks)$", key) for key in source_keys)
        }
        detected_subjects.update(
            subject
            for subject in STATE12_SUBJECTS
            if any(
                fields.get(f"{subject}_{suffix}") is not None
                for suffix in ("theory_marks", "internal_marks", "practical_marks", "marks")
            )
        )
        group_subjects = {
            "biology": {"tamil", "english", "physics", "chemistry", "biology", "botany", "zoology", "mathematics"},
            "computer science": {"tamil", "english", "physics", "chemistry", "computer_science", "computer_applications", "mathematics"},
            "arts": {"tamil", "english", "economics", "commerce", "accountancy", "business_mathematics", "business_studies", "history", "geography", "political_science", "computer_applications"},
        }
        category = str(fields.get("group_category") or "").strip().casefold()
        allowed_subjects = group_subjects.get(category)
        if allowed_subjects:
            detected_subjects &= allowed_subjects
        fields = {
            key: value
            for key, value in fields.items()
            if not any(key.startswith(f"{subject}_") for subject in STATE12_SUBJECTS)
            or any(key.startswith(f"{subject}_") for subject in detected_subjects)
        }
    _keep_school_address_separate(fields)
    inferred_school_address = False
    if kind in {"state_board_10th", "state_board_12th"}:
        # TN State Board certificates may label the same permanent register
        # identifier inconsistently. Keep roll_number distinct, but populate
        # the related register fields from one another when either is missing.
        if not fields.get("register_number") and fields.get("permanent_register_number"):
            fields["register_number"] = fields["permanent_register_number"]
        if not fields.get("permanent_register_number") and fields.get("register_number"):
            fields["permanent_register_number"] = fields["register_number"]

        # Some marksheets print the school location only as the final word of
        # the school name. Use that as a fallback only when OCR found no
        # separate address (including when it returned the whole school name).
        if not fields.get("school_address") and fields.get("school_name"):
            school_words = str(fields["school_name"]).strip().split()
            if school_words:
                address_candidate = school_words[-1].strip(" ,.;:-()[]")
                if address_candidate.casefold() not in {"school", "secondary", "higher", "matric", "government", "govt"}:
                    fields["school_address"] = address_candidate or None
                    inferred_school_address = bool(fields["school_address"])
    # OCR frequently merges the candidate-name row with the adjacent
    # father/guardian heading. Never surface a heading as a person's name.
    name_key = "candidate_name" if "candidate_name" in fields else "student_name" if "student_name" in fields else "name_on_card" if "name_on_card" in fields else None
    name_value = fields.get(name_key) if name_key else None
    name_is_heading = bool(name_value and re.search(r"\b(?:FATHER|MOTHER|GUARDIAN|CANDIDATE|STUDENT|PUPIL|NAME|SESSION|STANDARD|CLASS|TOTAL|MARKS|OBTAINED|PASS)\b", str(name_value), re.I))
    name_is_ambiguous = False
    if name_value and kind in {"state_board_10th", "state_board_12th"}:
        name_tokens = re.findall(r"[A-Za-z]+", str(name_value))
        # A valid OCR name should contain at least one substantive name token;
        # a few short fragments are usually the candidate label or image noise.
        name_is_ambiguous = not any(len(token) >= 3 for token in name_tokens)
    if name_is_heading or name_is_ambiguous:
        fields[name_key] = None
    # Preserve the Aadhaar suffix only; never expose full number through fields or preview text.
    if kind == "aadhaar_card":
        match = re.search(r"(?<!\d)(?:\d{4}[ -]?){2}(\d{4})(?!\d)", text)
        fields["aadhaar_last_four"] = match.group(1) if match else fields.get("aadhaar_last_four")
    extracted_data = {
        key: value
        for key, value in fields.items()
        if value is not None and not (isinstance(value, str) and not value.strip())
    }
    labels = {k: k.replace("_", " ").title() for k in extracted_data}
    entries = []
    for key, value in extracted_data.items():
        conf = 0.0 if value is None else (0.84 if re.search(re.escape(str(value)), text, re.I) else 0.65)
        # Text occurrence alone is not field-level verification. Keep OCR
        # predictions reviewable until a person or a source-specific validator
        # confirms them.
        entries.append({"field": key, "label": labels[key], "value": value, "confidence": conf, "status": "missing" if value is None else "review"})
    warnings = core.validate_document(kind, fields, text)
    if inferred_school_address:
        warnings.append("School address was populated from the final word of the school name because no separate address was detected. Review it before saving.")
    if name_is_heading and name_key:
        warnings.append("Candidate name OCR matched a field heading; review the document and enter the name manually.")
    elif name_is_ambiguous and name_key:
        warnings.append("Candidate name OCR was ambiguous; review the name against the original certificate.")
    return {"certificate_type": kind, "table_name": core.DOC_TYPES[kind]["table"], "status": "verified" if all(x["status"] != "review" for x in entries) and not warnings else "review_required", "fields": entries, "data": extracted_data, "warnings": warnings, "extracted_text": core.mask_sensitive(text)}

