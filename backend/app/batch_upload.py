"""
batch_upload.py — batch OCR preview for EduTrio / SMART ERP.

POST /batch-extract with repeated multipart fields:
  files=<file>
  students=<student ID or number>
  doc_types=<key from GET /doc-types>

This endpoint previews extracted data only; it does not save files or rows.
"""
import logging
import os
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

try:
    from app import ocr_service
except ImportError:
    import ocr_service  # type: ignore

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Batch OCR"])

MAX_FILES = 30
MAX_FILE_MB = 10
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024
ALLOWED_EXT = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}


def _check_file(filename: str, content: bytes) -> None:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED_EXT:
        raise ValueError(
            f"Unsupported file type '{ext or '(no extension)'}'. "
            f"Allowed types: {', '.join(sorted(ALLOWED_EXT))}"
        )
    if not content:
        raise ValueError("The uploaded file is empty.")
    if len(content) > MAX_FILE_BYTES:
        raise ValueError(f"File exceeds the {MAX_FILE_MB} MB limit.")


def _parse_fields(doc_type: str, text: str) -> dict[str, Any]:
    """Parse fields without silently replacing uncertain OCR values."""
    fields = ocr_service.parse_document(doc_type, text)
    if not isinstance(fields, dict):
        raise ValueError("The parser did not return a fields object.")
    return fields


def process_batch(items: list[dict[str, Any]], extract=None) -> dict[str, Any]:
    """
    Process each uploaded file independently.
    Input item shape: student, doc_type, filename, content.
    One failed document does not stop other documents in the batch.
    """
    extract = extract or ocr_service.extract_text
    students: dict[str, list[dict[str, Any]]] = {}
    ok = 0
    failed = 0

    for item in items:
        student = str(item.get("student") or "").strip() or "unassigned"
        doc_type = str(item.get("doc_type") or "").strip()
        filename = str(item.get("filename") or "unnamed")
        content = item.get("content") or b""

        entry: dict[str, Any] = {
            "filename": filename,
            "doc_type": doc_type,
        }

        try:
            cfg = ocr_service.DOC_TYPES.get(doc_type)
            if not cfg:
                valid_types = ", ".join(sorted(ocr_service.DOC_TYPES))
                raise ValueError(
                    f"Unknown doc_type '{doc_type}'. Valid types: {valid_types}"
                )

            _check_file(filename, content)
            text = extract(content, filename)

            if not isinstance(text, str) or not text.strip():
                raise ValueError(
                    "No readable text was extracted. Try a clearer scan or PDF."
                )

            # Parse the original OCR text first; mask only the optional text returned.
            if doc_type == "aadhaar_card" and hasattr(
                ocr_service, "process_multi_aadhaar"
            ):
                multi = ocr_service.process_multi_aadhaar(text)
                cards = multi.get("cards", []) if isinstance(multi, dict) else []
                if cards:
                    entry["cards"] = cards
                    entry["fields"] = cards[0]
                else:
                    entry["fields"] = _parse_fields(doc_type, text)
            else:
                entry["fields"] = _parse_fields(doc_type, text)

            entry["table"] = cfg.get("table")
            if cfg.get("extracted_text"):
                mask = getattr(ocr_service, "mask_sensitive", None)
                entry["extracted_text"] = mask(text) if callable(mask) else text

            # Include validation output when the existing service supports it.
            validate = getattr(ocr_service, "validate_document", None)
            if callable(validate):
                try:
                    validation = validate(doc_type, entry["fields"])
                    if validation is not None:
                        entry["validation"] = validation
                except Exception:
                    logger.exception(
                        "Validation failed for %s (%s)", filename, doc_type
                    )
                    entry["validation_warning"] = (
                        "Fields were extracted, but validation could not complete."
                    )

            entry["status"] = "ok"
            ok += 1

        except Exception as exc:
            logger.exception(
                "Batch OCR failed for file=%s student=%s doc_type=%s",
                filename,
                student,
                doc_type,
            )
            entry["status"] = "failed"
            entry["error"] = f"{type(exc).__name__}: {exc}"
            failed += 1

        students.setdefault(student, []).append(entry)

    return {
        "ok": ok,
        "failed": failed,
        "total": len(items),
        "students": students,
    }


@router.post("/batch-extract")
async def batch_extract(
    files: list[UploadFile] = File(...),
    students: list[str] = Form(...),
    doc_types: list[str] = Form(...),
):
    if not (len(files) == len(students) == len(doc_types)):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Received {len(files)} files, {len(students)} students, "
                f"and {len(doc_types)} document types. These lists must have "
                "the same length and the same order."
            ),
        )

    if not files:
        raise HTTPException(status_code=400, detail="Upload at least one file.")
    if len(files) > MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"A maximum of {MAX_FILES} files is allowed per request.",
        )

    items: list[dict[str, Any]] = []
    for file, student, doc_type in zip(files, students, doc_types):
        # Read at most one byte over the limit to avoid loading huge uploads.
        content = await file.read(MAX_FILE_BYTES + 1)
        items.append(
            {
                "student": student,
                "doc_type": doc_type,
                "filename": file.filename or "",
                "content": content,
            }
        )
        await file.close()

    return await run_in_threadpool(process_batch, items)
