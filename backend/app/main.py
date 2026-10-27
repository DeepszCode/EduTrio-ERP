
"""
main.py - SMART ERP Certificate Service
FastAPI + Supabase + OCR + AI Assistant

Run from backend:
    python -m uvicorn app.main:app --reload --reload-dir app --port 8000

API docs:
    http://localhost:8000/docs
"""

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.concurrency import run_in_threadpool
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
from app.ocr_engine import extract_document
from app.ai_routes import router as ai_router

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").strip()

SUPABASE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or os.getenv("SUPABASE_KEY")
    or ""
).strip()
SUPABASE_SERVICE_ROLE_KEY = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
SMART_ERP_FRONTEND_URL = (os.getenv("SMART_ERP_FRONTEND_URL") or "http://localhost:5173").strip().rstrip("/")

BUCKET = (
    os.getenv("SUPABASE_STORAGE_BUCKET")
    or os.getenv("SUPABASE_BUCKET")
    or "certificates"
).strip()

MAX_FILE_MB = 10
MAX_BATCH_FILES = 30

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
    allow_origins=list(dict.fromkeys([
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        SMART_ERP_FRONTEND_URL,
    ])),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def enforce_api_role(request: Request, call_next):
    """Protect all service-role-backed endpoints with a verified user role."""
    path = request.url.path
    if request.method == "OPTIONS" or path in {"/health", "/doc-types", "/openapi.json", "/docs", "/redoc"}:
        return await call_next(request)

    # These endpoints establish roles only after independently validating the
    # Supabase Auth user, invitation/allowlist state, and email confirmation.
    if path in {"/api/auth/activate-staff", "/api/invitations/students/accept"}:
        return await call_next(request)

    # The AI router performs token verification itself and applies role checks
    # before any cross-student legacy lookup.
    if path.startswith("/api/ai/"):
        return await call_next(request)

    # Diagnostic endpoints reveal administrative data and are Admin-only.
    admin_only = path.startswith("/debug/") or path.startswith("/api/admin/") or path == "/health/supabase" or (
        request.method == "DELETE" and path.startswith("/certificates/")
    )
    authorization = request.headers.get("authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        return JSONResponse(status_code=401, content={"detail": "Sign in to use this endpoint."})

    try:
        sb = get_sb()
        auth_result = await run_in_threadpool(sb.auth.get_user, token.strip())
        authenticated_user = getattr(auth_result, "user", None)
        if not authenticated_user or not authenticated_user.id:
            return JSONResponse(status_code=401, content={"detail": "Your session is invalid or expired."})
        role_result = await run_in_threadpool(
            lambda: sb.table("user_roles").select("role").eq("user_id", str(authenticated_user.id)).maybe_single().execute()
        )
        role = (role_result.data or {}).get("role")
    except Exception:
        return JSONResponse(status_code=503, content={"detail": "Unable to verify your account role."})

    if role not in {"admin", "staff", "student"}:
        return JSONResponse(status_code=403, content={"detail": "Your account has no authorized role."})
    if role == "student":
        return JSONResponse(status_code=403, content={"detail": "This endpoint is available to Staff and Admin accounts."})
    if admin_only and role != "admin":
        return JSONResponse(status_code=403, content={"detail": "This action requires an Admin account."})

    request.state.user_id = str(authenticated_user.id)
    request.state.user_role = role
    return await call_next(request)


# ---------------------------------------------------------------------------
# Supabase connection
# ---------------------------------------------------------------------------

_sb: Optional[Client] = None
_admin_sb: Optional[Client] = None


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


def get_admin_sb() -> Client:
    """Return the server-only Supabase client used for Auth Admin operations."""
    global _admin_sb
    if _admin_sb is None:
        if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
            raise HTTPException(
                status_code=503,
                detail="Admin management and invitations require SUPABASE_SERVICE_ROLE_KEY in backend/.env.",
            )
        try:
            _admin_sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Could not initialize the server-side Supabase Auth client.") from exc
    return _admin_sb


async def get_verified_request_user(request: Request):
    authorization = request.headers.get("authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    try:
        auth_result = await run_in_threadpool(get_admin_sb().auth.get_user, token.strip())
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Your Supabase session is invalid or expired.") from exc
    user = getattr(auth_result, "user", None)
    if not user or not getattr(user, "id", None) or not getattr(user, "email", None):
        raise HTTPException(status_code=401, detail="Your Supabase session has no verified account identity.")
    return user


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

class StudentInvitationRequest(BaseModel):
    email: str


class AdminRoleUpdate(BaseModel):
    role: str


class StaffApprovalRequest(BaseModel):
    email: str


def require_admin(request: Request):
    if getattr(request.state, "user_role", None) != "admin":
        raise HTTPException(status_code=403, detail="This action requires an Admin account.")


@app.get("/api/admin/users", tags=["Administration"])
async def admin_list_users(request: Request):
    require_admin(request)
    try:
        result = await run_in_threadpool(
            lambda: get_admin_sb().table("user_roles")
            .select("id,user_id,role,created_at,updated_at")
            .order("created_at", desc=True)
            .execute()
        )
        return {"users": result.data or []}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Admin could not list assigned user roles (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Could not load user roles.") from exc


@app.patch("/api/admin/users/{user_id}/role", tags=["Administration"])
async def admin_update_user_role(user_id: str, payload: AdminRoleUpdate, request: Request):
    require_admin(request)
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", user_id):
        raise HTTPException(status_code=422, detail="Invalid user ID.")
    role = payload.role.strip().lower()
    if role not in {"admin", "staff", "student"}:
        raise HTTPException(status_code=422, detail="Role must be Admin, Staff, or Student.")
    if user_id.lower() == str(request.state.user_id).lower() and role != "admin":
        raise HTTPException(status_code=409, detail="You cannot remove your own Admin access.")
    try:
        target = await run_in_threadpool(
            lambda: get_admin_sb().auth.admin.get_user_by_id(user_id)
        )
        if not getattr(target, "user", None):
            raise HTTPException(status_code=404, detail="Auth user was not found.")
        result = await run_in_threadpool(
            lambda: get_admin_sb().rpc(
                "assign_user_role",
                {"target_user_id": user_id, "new_role": role},
            ).execute()
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.info("Admin role update rejected", exc_info=True)
        raise HTTPException(status_code=403, detail="Role update was rejected. Admin assignment is limited to the three approved accounts.") from exc
    return {"user_id": user_id, "role": role, "updated": True, "result": result.data}


@app.get("/api/admin/staff-approvals", tags=["Administration"])
async def admin_list_staff_approvals(request: Request):
    require_admin(request)
    try:
        result = await run_in_threadpool(
            lambda: get_admin_sb().table("staff_registration_allowlist")
            .select("email,enabled,added_by,created_at")
            .order("created_at", desc=True)
            .execute()
        )
        return {"approvals": result.data or []}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Admin could not list Staff approvals (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Could not load Staff approvals.") from exc


@app.post("/api/admin/staff-approvals", tags=["Administration"])
async def admin_approve_staff(payload: StaffApprovalRequest, request: Request):
    require_admin(request)
    email = payload.email.strip().lower()
    if len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(status_code=422, detail="Enter a valid Staff email address.")
    try:
        result = await run_in_threadpool(
            lambda: get_admin_sb().table("staff_registration_allowlist")
            .upsert({"email": email, "enabled": True, "added_by": request.state.user_id}, on_conflict="email")
            .execute()
        )
        return {"email": email, "enabled": True, "approval": (result.data or [None])[0]}
    except Exception as exc:
        logger.exception("Admin could not approve Staff registration")
        raise HTTPException(status_code=503, detail="Could not save Staff approval.") from exc


@app.delete("/api/admin/staff-approvals/{email}", tags=["Administration"])
async def admin_revoke_staff_approval(email: str, request: Request):
    require_admin(request)
    normalized_email = email.strip().lower()
    try:
        result = await run_in_threadpool(
            lambda: get_admin_sb().table("staff_registration_allowlist")
            .update({"enabled": False})
            .eq("email", normalized_email)
            .execute()
        )
        if not result.data:
            raise HTTPException(status_code=404, detail="Staff approval not found.")
        return {"email": normalized_email, "enabled": False}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Admin could not revoke Staff approval")
        raise HTTPException(status_code=503, detail="Could not revoke Staff approval.") from exc


@app.post("/api/invitations/students", tags=["Invitations"])
async def invite_student(payload: StudentInvitationRequest, request: Request):
    """Create an expiring invitation and send the Supabase Auth invite email."""
    if getattr(request.state, "user_role", None) not in {"admin", "staff"}:
        raise HTTPException(status_code=403, detail="Only Staff or Admin accounts can invite students.")

    email = payload.email.strip().lower()
    if len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(status_code=422, detail="Enter a valid student email address.")

    admin_sb = get_admin_sb()
    try:
        await run_in_threadpool(
            lambda: admin_sb.table("student_invitations")
            .update({"status": "expired"})
            .eq("email", email)
            .eq("status", "pending")
            .lt("expires_at", datetime.now(timezone.utc).isoformat())
            .execute()
        )
        inserted = await run_in_threadpool(
            lambda: admin_sb.table("student_invitations").insert({
                "email": email,
                "invited_by": request.state.user_id,
                "status": "pending",
            }).select("id,email,status,expires_at").execute()
        )
        invitation = (inserted.data or [None])[0]
        if not invitation:
            raise RuntimeError("The invitation row was not returned.")
    except Exception as exc:
        logger.info("Could not create student invitation", exc_info=True)
        raise HTTPException(
            status_code=409,
            detail="An active invitation may already exist for this email, or the invitation table migration is not applied.",
        ) from exc

    try:
        invite_result = await run_in_threadpool(
            lambda: admin_sb.auth.admin.invite_user_by_email(
                email,
                {"redirect_to": f"{SMART_ERP_FRONTEND_URL}/?flow=student-invite"},
            )
        )
        invited_user = getattr(invite_result, "user", None)
        if not invited_user:
            raise RuntimeError("Supabase Auth did not return the invited user.")
    except Exception as exc:
        logger.info("Supabase Auth student invite failed", exc_info=True)
        try:
            await run_in_threadpool(
                lambda: admin_sb.table("student_invitations")
                .update({"status": "failed"})
                .eq("id", invitation["id"])
                .execute()
            )
        except Exception:
            logger.exception("Could not mark failed student invitation")
        raise HTTPException(status_code=502, detail="Supabase could not send the invitation email. Check Auth email settings and the backend service key.") from exc

    try:
        await run_in_threadpool(
            lambda: admin_sb.table("student_invitations")
            .update({"auth_invitation_sent_at": datetime.now(timezone.utc).isoformat()})
            .eq("id", invitation["id"])
            .execute()
        )
    except Exception:
        # Email delivery succeeded and the pending row remains usable. This
        # audit timestamp is useful, but it must not invalidate the invite.
        logger.exception("Invitation was sent but its sent timestamp could not be saved")

    return {
        "invitation_id": invitation["id"],
        "email": email,
        "status": "pending",
        "expires_at": invitation.get("expires_at"),
        "message": "Invitation email sent. The student must open it and set a password before access is enabled.",
    }


@app.get("/api/invitations/students", tags=["Invitations"])
async def list_student_invitations(request: Request):
    """List all invitations for Admin, or only the caller's invitations for Staff."""
    role = getattr(request.state, "user_role", None)
    if role not in {"admin", "staff"}:
        raise HTTPException(status_code=403, detail="Only Staff or Admin accounts can view student invitations.")
    try:
        query = get_admin_sb().table("student_invitations").select(
            "id,email,invited_by,status,created_at,expires_at,accepted_at,revoked_at,auth_invitation_sent_at"
        )
        if role != "admin":
            query = query.eq("invited_by", request.state.user_id)
        result = await run_in_threadpool(
            lambda: query.order("created_at", desc=True).limit(200).execute()
        )
        return {"invitations": result.data or []}
    except Exception as exc:
        logger.exception("Could not list student invitations")
        raise HTTPException(status_code=503, detail="Could not load student invitations.") from exc


@app.post("/api/auth/activate-staff", tags=["Authentication"])
async def activate_staff_account(request: Request):
    """Assign Staff only to an email approved in the server-owned allowlist."""
    user = await get_verified_request_user(request)
    email = str(user.email).strip().lower()
    if not getattr(user, "email_confirmed_at", None):
        raise HTTPException(status_code=403, detail="Confirm your email before Staff access is activated.")

    admin_sb = get_admin_sb()
    try:
        approval = await run_in_threadpool(
            lambda: admin_sb.table("staff_registration_allowlist")
            .select("email")
            .eq("email", email)
            .eq("enabled", True)
            .maybe_single()
            .execute()
        )
        if not approval.data:
            raise HTTPException(status_code=403, detail="This email is not approved for Staff registration. Ask an Admin to approve it.")

        current = await run_in_threadpool(
            lambda: admin_sb.table("user_roles").select("role").eq("user_id", str(user.id)).maybe_single().execute()
        )
        if current.data and current.data.get("role") != "staff":
            raise HTTPException(status_code=403, detail="This account already has a different role. Contact an Admin.")
        await run_in_threadpool(
            lambda: admin_sb.table("user_roles")
            .upsert({"user_id": str(user.id), "role": "staff", "updated_at": datetime.now(timezone.utc).isoformat()}, on_conflict="user_id")
            .execute()
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Staff role activation failed")
        raise HTTPException(status_code=503, detail="Could not verify Staff approval. Check the role migration and server configuration.") from exc

    return {"role": "staff", "message": "Staff access is active."}


@app.post("/api/invitations/students/accept", tags=["Invitations"])
async def accept_student_invitation(request: Request):
    """Atomically accept one live invite for the signed-in, verified email."""
    user = await get_verified_request_user(request)
    email = str(user.email).strip().lower()
    if not getattr(user, "email_confirmed_at", None):
        raise HTTPException(status_code=403, detail="Verify the invited email address before accepting the invitation.")

    try:
        result = await run_in_threadpool(
            lambda: get_admin_sb().rpc(
                "accept_student_invitation",
                {"target_user_id": str(user.id), "verified_email": email},
            ).execute()
        )
    except Exception as exc:
        logger.info("Student invitation acceptance rejected", exc_info=True)
        raise HTTPException(status_code=403, detail="This invitation is missing, expired, revoked, already used, or does not match the verified email.") from exc

    return {"role": "student", "invitation_id": result.data, "message": "Invitation accepted."}


@app.post("/api/invitations/students/{invitation_id}/revoke", tags=["Invitations"])
async def revoke_student_invitation(invitation_id: int, request: Request):
    """Allow the inviter or an Admin to revoke an unused invitation."""
    if getattr(request.state, "user_role", None) not in {"admin", "staff"}:
        raise HTTPException(status_code=403, detail="Only Staff or Admin accounts can revoke invitations.")
    admin_sb = get_admin_sb()
    try:
        lookup = await run_in_threadpool(
            lambda: admin_sb.table("student_invitations").select("id,invited_by,status").eq("id", invitation_id).maybe_single().execute()
        )
        row = lookup.data
        if not row:
            raise HTTPException(status_code=404, detail="Invitation not found.")
        if request.state.user_role != "admin" and str(row.get("invited_by")) != str(request.state.user_id):
            raise HTTPException(status_code=403, detail="You can revoke only invitations you sent.")
        if row.get("status") != "pending":
            raise HTTPException(status_code=409, detail="Only a pending invitation can be revoked.")
        revoked = await run_in_threadpool(
            lambda: admin_sb.table("student_invitations")
            .update({"status": "revoked", "revoked_at": datetime.now(timezone.utc).isoformat()})
            .eq("id", invitation_id)
            .eq("status", "pending")
            .select("id")
            .execute()
        )
        if not revoked.data:
            raise HTTPException(status_code=409, detail="The invitation was accepted or changed before it could be revoked.")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Invitation revocation failed")
        raise HTTPException(status_code=503, detail="Could not update the invitation status.") from exc
    return {"invitation_id": invitation_id, "status": "revoked"}


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
    try:
        result = extract_document(content, file.filename or "document", doc_type)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=422, detail="OCR failed. Check that Tesseract and the required language data are installed, then try a clearer image or PDF.")

    fields = result["data"]
    candidate_name = fields.get("candidate_name") or fields.get("student_name") or fields.get("name_on_card")

    if student_id and candidate_name:
        for saved_name in known_names(student_id):
            if (
                saved_name != candidate_name
                and _norm_name(saved_name) == _norm_name(candidate_name)
            ):
                result["warnings"].append(
                    f"Name spelling differs from saved record '{saved_name}'."
                )
                break
    if result["warnings"]:
        result["status"] = "review_required"
    result["doc_type"] = result["certificate_type"]
    return result


# ---------------------------------------------------------------------------
# Batch OCR preview for multiple students and certificate types
# ---------------------------------------------------------------------------

@app.post("/batch-extract", tags=["OCR"])
async def batch_extract(
    files: list[UploadFile] = File(...),
    students: list[str] = Form(...),
    doc_types: list[str] = Form(...),
):
    """Extract a group of certificates, associating each file to its student.

    Send repeated multipart values in the same order: files, students, doc_types.
    Use `auto` as a doc_type to identify each certificate from its contents.
    This endpoint previews data and does not save or verify database records.
    """
    if not files:
        raise HTTPException(status_code=400, detail="Upload at least one file.")
    if len(files) > MAX_BATCH_FILES:
        raise HTTPException(status_code=400, detail=f"A maximum of {MAX_BATCH_FILES} files is allowed per request.")
    if not (len(files) == len(students) == len(doc_types)):
        raise HTTPException(
            status_code=400,
            detail=f"Received {len(files)} files, {len(students)} students, and {len(doc_types)} document types; these lists must have the same length and order.",
        )

    grouped: dict[str, list[dict]] = {}
    succeeded = failed = 0
    for uploaded, student, doc_type in zip(files, students, doc_types):
        filename = uploaded.filename or "document"
        student = (student or "").strip()
        entry = {"filename": filename, "student_id": student or None}
        try:
            if not student:
                raise ValueError("A student identifier is required for every file.")
            content = await uploaded.read(MAX_FILE_MB * 1024 * 1024 + 1)
            ext = Path(filename).suffix.lower()
            if ext not in ALLOWED_EXT:
                raise ValueError(f"Unsupported file extension: '{ext}'.")
            if not content:
                raise ValueError("Uploaded file is empty.")
            if len(content) > MAX_FILE_MB * 1024 * 1024:
                raise ValueError(f"File exceeds the {MAX_FILE_MB} MB limit.")
            result = await run_in_threadpool(
                extract_document, content, filename, (doc_type or "auto").strip()
            )
            entry.update(result)
            entry["status"] = result.get("status", "review_required")
            succeeded += 1
        except ValueError as exc:
            entry.update({"status": "failed", "error": str(exc)})
            failed += 1
        except Exception:
            entry.update({"status": "failed", "error": "OCR failed for this file. Check the file and OCR configuration."})
            failed += 1
        finally:
            await uploaded.close()
        grouped.setdefault(student or "unassigned", []).append(entry)

    return {
        "total": len(files),
        "succeeded": succeeded,
        "failed": failed,
        "students": grouped,
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
