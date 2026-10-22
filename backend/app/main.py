
"""
main.py - SMART ERP Certificate Service
FastAPI + Supabase + OCR + AI Assistant

Run from backend:
    python -m uvicorn app.main:app --reload --reload-dir app --port 8000

API docs:
    http://localhost:8000/docs
"""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import Client, create_client

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.ocr_service import (
    DOC_TYPES,
    extract_text,
    mask_sensitive,
    parse_document,
    validate_document,
    detect_document_type,
)
from app.ai_routes import router as ai_router


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").strip()

SUPABASE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or os.getenv("SUPABASE_KEY")
    or ""
).strip()

BUCKET = (
    os.getenv("SUPABASE_STORAGE_BUCKET")
    or os.getenv("SUPABASE_BUCKET")
    or "certificates"
).strip()

MAX_FILE_MB = 10

ALLOWED_EXT = {
    ".pdf", ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"
}

DOC_TYPE_ALIASES = {
    "transfer_certificate": "state_board_10th_tc",
    "degree_marksheet": "degree_certificate",
    "11th_marksheet": "state_board_11th",
    "10th_marksheet": "state_board_10th",
    "12th_marksheet": "state_board_12th",
    "state_board_11th_marksheet": "state_board_11th",
    "state_board_10th_marksheet": "state_board_10th",
    "state_board_12th_marksheet": "state_board_12th",
}


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="SMART ERP - Certificate OCR",
    version="1.2.1",
    description=(
        "Certificate OCR, Supabase storage, student records, "
        "verification, and AI Assistant API."
    ),
)

app.include_router(ai_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Supabase connection
# ---------------------------------------------------------------------------

_sb: Optional[Client] = None


def get_sb() -> Client:
    """Create and reuse the Supabase client."""
    global _sb

    if _sb is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise HTTPException(
                status_code=500,
                detail=(
                    "Supabase configuration is missing. "
                    "Check SUPABASE_URL and backend Supabase key settings."
                ),
            )

        try:
            _sb = create_client(SUPABASE_URL, SUPABASE_KEY)
        except Exception:
            raise HTTPException(
                status_code=500,
                detail="Could not initialize Supabase.",
            )

    return _sb


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_cfg(doc_type: str) -> dict:
    """Return the configuration for a supported certificate type."""
    canonical_type = DOC_TYPE_ALIASES.get(doc_type, doc_type)
    cfg = DOC_TYPES.get(canonical_type)

    if not cfg:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown doc_type '{doc_type}'. See GET /doc-types.",
        )

    return cfg


def read_upload(file: UploadFile) -> bytes:
    """Read and validate an uploaded file."""
    filename = file.filename or ""
    ext = Path(filename).suffix.lower()

    if ext not in ALLOWED_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file extension: '{ext}'.",
        )

    content = file.file.read()

    if not content:
        raise HTTPException(
            status_code=400,
            detail="Uploaded file is empty.",
        )

    if len(content) > MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {MAX_FILE_MB} MB limit.",
        )

    return content


def run_ocr(doc_type: str, content: bytes, filename: str):
    """Extract text and parse certificate fields."""
    try:
        text = extract_text(content, filename)
    except Exception:
        raise HTTPException(
            status_code=422,
            detail="OCR failed. Check the file and OCR configuration.",
        )

    if not text or not text.strip():
        raise HTTPException(
            status_code=422,
            detail="No readable text was found in this file.",
        )

    try:
        parsed = parse_document(doc_type, text)
    except Exception:
        raise HTTPException(
            status_code=422,
            detail="Certificate parsing failed.",
        )

    if isinstance(parsed, dict) and isinstance(parsed.get("fields"), dict):
        fields = parsed["fields"]
    elif isinstance(parsed, dict):
        fields = parsed
    else:
        raise HTTPException(
            status_code=422,
            detail="The certificate parser returned an invalid result.",
        )

    return text, fields


def signed_url(path: Optional[str], expires: int = 3600):
    """Create a temporary URL for a stored certificate."""
    if not path:
        return None

    try:
        result = get_sb().storage.from_(BUCKET).create_signed_url(
            path, expires
        )
        return result.get("signedURL") or result.get("signedUrl")
    except Exception:
        return None


def known_names(student_id: int) -> list:
    """Get previously saved candidate names for a student."""
    names = []

    for table in ("state_board_10th", "state_board_12th"):
        try:
            result = (
                get_sb()
                .table(table)
                .select("candidate_name")
                .eq("student_id", student_id)
                .execute()
            )
            names.extend(
                row["candidate_name"]
                for row in (result.data or [])
                if row.get("candidate_name")
            )
        except Exception:
            continue

    return names


def _norm_name(value: str) -> str:
    return re.sub(r"[\s.]", "", value or "").upper()


# ---------------------------------------------------------------------------
# Health endpoints
# ---------------------------------------------------------------------------

@app.get("/health", tags=["Health"])
def health():
    return {
        "status": "ok",
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/health/supabase", tags=["Health"])
def health_supabase():
    """Check database and storage connectivity."""
    result = {
        "url_set": bool(SUPABASE_URL),
        "key_set": bool(SUPABASE_KEY),
        "bucket": BUCKET,
        "database": "not_checked",
        "storage": "not_checked",
    }

    sb = get_sb()

    try:
        (
            sb.table("student_identification")
            .select("student_id")
            .limit(1)
            .execute()
        )
        result["database"] = "ok"
    except Exception:
        result["database"] = "error"

    try:
        sb.storage.from_(BUCKET).list()
        result["storage"] = "ok"
    except Exception:
        result["storage"] = "error"

    return result


@app.get("/debug/student/{student_id}", tags=["Health"])
def debug_student(student_id: int):
    """Temporary diagnostic endpoint."""
    try:
        result = (
            get_sb()
            .table("student_identification")
            .select("student_id")
            .eq("student_id", student_id)
            .limit(1)
            .execute()
        )
        return {
            "requested_student_id": student_id,
            "found": bool(result.data),
            "matching_rows": result.data or [],
        }
    except Exception:
        raise HTTPException(
            status_code=502,
            detail="Student lookup failed.",
        )


# ---------------------------------------------------------------------------
# Supported certificate types
# ---------------------------------------------------------------------------

@app.get("/doc-types", tags=["Certificates"])
def doc_types():
    return {
        key: {
            "table": value["table"],
            "has_verification": value["verification"],
        }
        for key, value in DOC_TYPES.items()
    }


# ---------------------------------------------------------------------------
# OCR preview - does not save the file or data
# ---------------------------------------------------------------------------

@app.post("/extract/{doc_type}", tags=["OCR"])
def extract_only(
    doc_type: str,
    file: UploadFile = File(...),
    student_id: Optional[int] = Form(None),
):
    content = read_upload(file)

    if doc_type.lower() in ("auto", "detect"):
        try:
            text = extract_text(content, file.filename or "")
        except Exception:
            raise HTTPException(
                status_code=422,
                detail="OCR failed. Check the file and OCR configuration.",
            )

        if not text or not text.strip():
            raise HTTPException(
                status_code=422,
                detail="No readable text was found in this file.",
            )

        canonical_type = detect_document_type(text)

        if not canonical_type or canonical_type not in DOC_TYPES:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Could not identify the certificate type. "
                    "Choose its type manually and try again."
                ),
            )

        parsed = parse_document(canonical_type, text)
        fields = (
            parsed.get("fields", parsed)
            if isinstance(parsed, dict)
            else {}
        )
    else:
        canonical_type = DOC_TYPE_ALIASES.get(doc_type, doc_type)
        get_cfg(canonical_type)
        text, fields = run_ocr(
            canonical_type, content, file.filename or ""
        )

    if (
        fields.get("standard") == "11th"
        and canonical_type == "state_board_12th"
        and "state_board_11th" in DOC_TYPES
    ):
        canonical_type = "state_board_11th"
    elif (
        fields.get("standard") == "12th"
        and canonical_type == "state_board_11th"
        and "state_board_12th" in DOC_TYPES
    ):
        canonical_type = "state_board_12th"

    warnings = validate_document(canonical_type, fields, text)

    candidate_name = (
        fields.get("candidate_name")
        or fields.get("student_name")
        or fields.get("name_on_card")
    )

    if student_id and candidate_name:
        for saved_name in known_names(student_id):
            if (
                saved_name != candidate_name
                and _norm_name(saved_name) == _norm_name(candidate_name)
            ):
                warnings.append(
                    f"Name spelling differs from saved record '{saved_name}'."
                )
                break

    return {
        "doc_type": canonical_type,
        "fields": fields,
        "warnings": warnings,
        "extracted_text": mask_sensitive(text),
    }


# ---------------------------------------------------------------------------
# Upload certificate - saves original file and extracted fields
# ---------------------------------------------------------------------------

@app.post("/upload/{doc_type}", tags=["Certificates"])
def upload_certificate(
    doc_type: str,
    student_id: int = Form(...),
    file: UploadFile = File(...),
    overrides: Optional[str] = Form(None),
):
    cfg = get_cfg(doc_type)
    canonical_type = DOC_TYPE_ALIASES.get(doc_type, doc_type)
    sb = get_sb()

    # 1. Verify that the student exists.
    try:
        student_result = (
            sb.table("student_identification")
            .select("student_id")
            .eq("student_id", student_id)
            .limit(1)
            .execute()
        )
    except Exception:
        raise HTTPException(
            status_code=502,
            detail="Could not check the student record in Supabase.",
        )

    if not student_result.data:
        raise HTTPException(
            status_code=404,
            detail=f"student_id {student_id} was not found.",
        )

    # 2. Read the uploaded file.
    content = read_upload(file)

    # 3. Run OCR and parse the fields.
    text, fields = run_ocr(
        canonical_type, content, file.filename or ""
    )

    # 4. Apply manually corrected fields, if supplied.
    if overrides:
        try:
            manual_fields = json.loads(overrides)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=400,
                detail="overrides must contain valid JSON.",
            )

        if not isinstance(manual_fields, dict):
            raise HTTPException(
                status_code=400,
                detail="overrides must be a JSON object.",
            )

        unknown_fields = [key for key in manual_fields if key not in fields]
        if unknown_fields:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown fields in overrides: {unknown_fields}",
            )

        fields.update(manual_fields)

    # 5. Upload the original file to Supabase Storage.
    filename = file.filename or "certificate"
    ext = Path(filename).suffix.lower()
    storage_path = (
        f"{canonical_type}/{student_id}/{uuid.uuid4().hex}{ext}"
    )

    try:
        sb.storage.from_(BUCKET).upload(
            storage_path,
            content,
            {
                "content-type": (
                    file.content_type or "application/octet-stream"
                ),
                "upsert": "false",
            },
        )
    except Exception:
        raise HTTPException(
            status_code=502,
            detail="Certificate file upload to Supabase Storage failed.",
        )

    # 6. Prepare and insert the database row.
    row = {
        key: value
        for key, value in fields.items()
        if value is not None
    }
    row["student_id"] = student_id
    row["certificate_file_path"] = storage_path

    if cfg.get("extracted_text"):
        row["extracted_text"] = mask_sensitive(text)

    try:
        result = sb.table(cfg["table"]).insert(row).execute()
    except Exception:
        # Avoid leaving an orphaned file when the database insert fails.
        try:
            sb.storage.from_(BUCKET).remove([storage_path])
        except Exception:
            pass

        raise HTTPException(
            status_code=400,
            detail=(
                "Database insert failed. Check the target table's "
                "column names, required fields, and data types."
            ),
        )

    saved_record = result.data[0] if result.data else row

    return {
        "saved": True,
        "doc_type": canonical_type,
        "record": saved_record,
    }


# ---------------------------------------------------------------------------
# List all certificates - My Certificates
# ---------------------------------------------------------------------------

@app.get("/certificates/", tags=["Certificates"])
def list_all_certificates():
    sb = get_sb()
    all_items = []

    for doc_type, cfg in DOC_TYPES.items():
        table_name = cfg["table"]

        try:
            result = sb.table(table_name).select("*").execute()
        except Exception:
            raise HTTPException(
                status_code=502,
                detail=f"Could not retrieve records from '{table_name}'.",
            )

        for row in result.data or []:
            item = dict(row)
            item["doc_type"] = doc_type
            item["file_url"] = signed_url(
                item.get("certificate_file_path")
            )
            all_items.append(item)

    all_items.sort(
        key=lambda item: str(item.get("created_at") or ""),
        reverse=True,
    )

    return {"count": len(all_items), "items": all_items}


# ---------------------------------------------------------------------------
# List certificates for one student and type
# ---------------------------------------------------------------------------

@app.get("/certificates/{doc_type}/{student_id}", tags=["Certificates"])
def list_certificates(doc_type: str, student_id: int):
    cfg = get_cfg(doc_type)

    try:
        result = (
            get_sb()
            .table(cfg["table"])
            .select("*")
            .eq("student_id", student_id)
            .order("created_at", desc=True)
            .execute()
        )
    except Exception:
        raise HTTPException(
            status_code=502,
            detail="Could not retrieve certificates from Supabase.",
        )

    rows = result.data or []

    for row in rows:
        row["file_url"] = signed_url(
            row.get("certificate_file_path")
        )

    return {"count": len(rows), "items": rows}


# ---------------------------------------------------------------------------
# Certificate verification
# ---------------------------------------------------------------------------

class VerifyBody(BaseModel):
    verified_by: str
    status: str = "verified"


@app.patch("/verify/{doc_type}/{record_id}", tags=["Verification"])
def verify_certificate(
    doc_type: str,
    record_id: int,
    body: VerifyBody,
):
    cfg = get_cfg(doc_type)

    if not cfg["verification"]:
        raise HTTPException(
            status_code=400,
            detail=f"'{doc_type}' table has no verification columns.",
        )

    if body.status not in {"verified", "rejected", "pending"}:
        raise HTTPException(
            status_code=400,
            detail="status must be verified, rejected, or pending.",
        )

    try:
        result = (
            get_sb()
            .table(cfg["table"])
            .update({
                "verification_status": body.status,
                "verified_by": body.verified_by,
                "verified_at": datetime.now(timezone.utc).isoformat(),
            })
            .eq("id", record_id)
            .execute()
        )
    except Exception:
        raise HTTPException(
            status_code=502,
            detail="Certificate verification update failed.",
        )

    if not result.data:
        raise HTTPException(
            status_code=404,
            detail="Certificate record was not found.",
        )

    return {"updated": True, "record": result.data[0]}


# ---------------------------------------------------------------------------
# Delete certificate
# ---------------------------------------------------------------------------

@app.delete("/certificates/{doc_type}/{record_id}", tags=["Certificates"])
def delete_certificate(doc_type: str, record_id: int):
    cfg = get_cfg(doc_type)
    sb = get_sb()

    try:
        found = (
            sb.table(cfg["table"])
            .select("certificate_file_path")
            .eq("id", record_id)
            .execute()
        )
    except Exception:
        raise HTTPException(
            status_code=502,
            detail="Could not find the certificate record.",
        )

    if not found.data:
        raise HTTPException(
            status_code=404,
            detail="Certificate record was not found.",
        )

    storage_path = found.data[0].get("certificate_file_path")

    try:
        (
            sb.table(cfg["table"])
            .delete()
            .eq("id", record_id)
            .execute()
        )
    except Exception:
        raise HTTPException(
            status_code=502,
            detail="Could not delete the certificate record.",
        )

    if storage_path:
        try:
            sb.storage.from_(BUCKET).remove([storage_path])
        except Exception:
            pass

    return {"deleted": True, "record_id": record_id}
