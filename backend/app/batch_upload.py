"""
batch_upload.py - read many students' certificates in one request and get the details back per student.
PREVIEW ONLY: nothing is saved. To save a document use POST /upload/{doc_type} (it checks the student
and stores the file + row).

Add to app/main.py (two lines):
    from app.batch_upload import router as batch_router      # with the other imports
    app.include_router(batch_router)                          # right after app = FastAPI(...)

Request: POST /batch-extract  (multipart/form-data). The three lists line up by position, one entry per file:
    files=<file>   students=<student number or id>   doc_types=<a key of DOC_TYPES, see GET /doc-types>

Example (2 files):
    curl -F "files=@s1_aadhaar.jpg" -F "students=1" -F "doc_types=aadhaar_card" \
         -F "files=@s2_marksheet.pdf" -F "students=2" -F "doc_types=state_board_10th" \
         http://localhost:8000/batch-extract
"""
import os

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

try:
    from app import ocr_service          # project layout: app/main.py, app/ocr_service.py
except ImportError:                      # flat layout: files side by side
    import ocr_service

MAX_FILES = 30       # per request; OCR of images is slow, so keep batches modest
MAX_FILE_MB = 10     # same limit as main.py
ALLOWED_EXT = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}


def _check_file(filename, content):
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED_EXT:
        raise ValueError(f"unsupported file type '{ext}'; allowed: {sorted(ALLOWED_EXT)}")
    if not content:
        raise ValueError("empty file")
    if len(content) > MAX_FILE_MB * 1024 * 1024:
        raise ValueError(f"file larger than {MAX_FILE_MB} MB")


def process_batch(items, extract=None):
    """items: [{"student", "doc_type", "filename", "content"}]  (content = file bytes)

    Every file is handled on its own: one bad file never stops the others, and one student's
    data is never mixed with another's. Returns results grouped by student.
    """
    extract = extract or ocr_service.extract_text
    students, ok, failed = {}, 0, 0
    for it in items:
        student = str(it["student"]).strip() or "unassigned"
        entry = {"filename": it["filename"], "doc_type": it["doc_type"]}
        try:
            cfg = ocr_service.DOC_TYPES.get(it["doc_type"])
            if cfg is None:
                raise ValueError(
                    f"unknown doc_type '{it['doc_type']}'; valid types: {', '.join(sorted(ocr_service.DOC_TYPES))}"
                )
            _check_file(it["filename"], it["content"])
            text = extract(it["content"], it["filename"])
            if not text or not text.strip():
                raise ValueError("no text could be read from this file")

            if it["doc_type"] == "aadhaar_card":
                # one image/PDF may hold several people's cards -> one entry per person
                multi = ocr_service.process_multi_aadhaar(text)   # parses BEFORE masking
                if multi["cards"]:
                    entry["cards"] = multi["cards"]
                    entry["fields"] = multi["cards"][0]
                else:
                    entry["fields"] = ocr_service.parse_document("aadhaar_card", text)
            else:
                entry["fields"] = ocr_service.parse_document(it["doc_type"], text)  # parse BEFORE masking

            entry["table"] = cfg["table"]
            if cfg["extracted_text"]:
                entry["extracted_text"] = ocr_service.mask_sensitive(text)          # full ID numbers never returned
            entry["status"] = "ok"
            ok += 1
        except Exception as e:  # keep going with the remaining files
            entry["status"] = "failed"
            entry["error"] = f"{type(e).__name__}: {e}"
            failed += 1
        students.setdefault(student, []).append(entry)
    return {"ok": ok, "failed": failed, "students": students}


router = APIRouter()


@router.post("/batch-extract")
async def batch_extract(
    files: list[UploadFile] = File(...),
    students: list[str] = Form(...),
    doc_types: list[str] = Form(...),
):
    if not (len(files) == len(students) == len(doc_types)):
        raise HTTPException(
            400,
            f"got {len(files)} files, {len(students)} students, {len(doc_types)} doc_types - "
            "they must match one-to-one, in the same order",
        )
    if len(files) > MAX_FILES:
        raise HTTPException(400, f"at most {MAX_FILES} files per request")
    items = []
    for f, s, t in zip(files, students, doc_types):
        content = await f.read(MAX_FILE_MB * 1024 * 1024 + 1)   # never read more than the limit + 1 byte
        items.append({"student": s, "doc_type": t, "filename": f.filename or "", "content": content})
    return await run_in_threadpool(process_batch, items)