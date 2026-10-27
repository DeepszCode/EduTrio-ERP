
import asyncio
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

import httpx
from dotenv import load_dotenv
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["AI Assistant"])

OLLAMA_URL = os.getenv(
    "OLLAMA_URL",
    "http://127.0.0.1:11434/api/chat",
)

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")

STUDENT_TABLE = "student_identification"

# Configure these UUIDs in backend/.env to restrict student lookups.
# Use the authenticated user's Supabase Auth UUID, not student_id.
ALLOWED_LOOKUP_USER_IDS = {
    value.strip().lower()
    for value in os.getenv("ERP_LOOKUP_ALLOWED_USER_IDS", "").split(",")
    if value.strip()
}

# Only these known certificate tables can be queried.
CERTIFICATE_TABLES = [
    "state_board_10th",
    "state_board_12th",
    "cbse_10th_marksheet",
    "cbse_12th_marksheet",
    "state_board_10th_tc",
    "state_board_12th_tc",
    "cbse_10th_tc",
    "cbse_12th_tc",
    "birth_certificate",
    "aadhaar_card",
    "community_certificate",
    "income_certificate",
    "nativity_certificate",
    "degree_certificate",
    "diploma_certificate",
    "diploma_semester_marksheet",
]

SYSTEM_PROMPT = """
You are the SMART ERP AI Assistant.

Help users understand student records and certificate information
returned by the application.

Rules:
- Never invent student details, marks, certificate records, or dates.
- For database questions, use only the records supplied by the backend.
- If no database records are supplied, clearly say that you cannot
  confirm the requested information.
- Use Markdown tables when useful.
- Explain academic marks only when the actual values are provided.
- Do not claim that you searched a database unless the application
  explicitly returned database results.
- Do not reveal passwords, access tokens, service keys, or secrets.
- Keep answers clear and concise.
"""


# ---------------------------------------------------------
# REQUEST / RESPONSE MODELS
# ---------------------------------------------------------

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[dict[str, Any]] = Field(default_factory=list)
    offset: int = Field(default=0, ge=0, le=50000)
    page_size: int = Field(default=50, ge=1, le=100)


def make_response(
    reply: str,
    source: str = "assistant",
    format: str = "text",
    data: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Response format expected by AIAssistant.jsx."""
    return {
        "reply": reply,
        "response": reply,
        "answer": reply,
        "source": source,
        "format": format,
        "data": data,
    }


# ---------------------------------------------------------
# SUPABASE CLIENT
# ---------------------------------------------------------

def get_supabase_client():
    """
    Reuse the Supabase client configured by app.main.
    Do not create a new client with a different key here.
    """
    try:
        from app.main import get_sb

        client = get_sb()

        if client is None:
            raise HTTPException(
                status_code=503,
                detail="Supabase client is not configured.",
            )

        return client

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception("Unable to initialize Supabase client")
        raise HTTPException(
            status_code=503,
            detail="Supabase is not configured correctly. Check backend settings.",
        ) from exc


def get_user_database_client(access_token: str):
    """Create an isolated PostgREST client that evaluates this user's RLS policies."""
    try:
        from app.main import SUPABASE_KEY, SUPABASE_URL
        from supabase import create_client

        if not SUPABASE_URL or not SUPABASE_KEY:
            raise HTTPException(status_code=503, detail="Supabase client is not configured.")
        client = create_client(SUPABASE_URL, SUPABASE_KEY)
        # Keep the server key in the apikey header only. PostgREST must receive
        # the verified user's JWT as Authorization so PostgreSQL applies RLS.
        client.postgrest.auth(access_token)
        return client
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Unable to initialize the user-scoped Supabase client")
        raise HTTPException(status_code=503, detail="Could not initialize your database session.") from exc


# ---------------------------------------------------------
# AUTHENTICATION
# ---------------------------------------------------------

async def authorize_student_lookup(
    authorization: Optional[str],
):
    """
    Verify the user's actual Supabase access token.
    Student data is not queried until authentication succeeds.
    """
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Authorization header is missing. Please sign in again.",
        )

    scheme, separator, token = authorization.partition(" ")

    if (
        not separator
        or scheme.lower() != "bearer"
        or not token.strip()
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid authorization header. Please sign in again.",
        )

    # Do not allow database lookups when the administrator has
    # not configured which authenticated users may perform them.
    if not ALLOWED_LOOKUP_USER_IDS:
        raise HTTPException(
            status_code=503,
            detail=(
                "Student lookup is not configured. "
                "Set ERP_LOOKUP_ALLOWED_USER_IDS in backend/.env "
                "to an authorized Supabase Auth user UUID."
            ),
        )

    sb = get_supabase_client()

    try:
        # The Supabase Python client's network request is synchronous.
        # Run it in a worker thread so it does not block FastAPI's loop.
        auth_result = await asyncio.to_thread(
            sb.auth.get_user,
            token.strip(),
        )

    except Exception as exc:
        logger.exception("Supabase access-token verification failed")

        if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
            raise HTTPException(
                status_code=503,
                detail=(
                    "Supabase authentication timed out. "
                    "Please try again in a moment."
                ),
            ) from exc

        if isinstance(exc, httpx.ConnectError):
            raise HTTPException(
                status_code=503,
                detail=(
                    "Could not connect to Supabase authentication. "
                    "Check your network and Supabase project URL."
                ),
            ) from exc

        raise HTTPException(
            status_code=503,
            detail="Unable to verify the Supabase session right now.",
        ) from exc

    user = getattr(auth_result, "user", None)

    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Your session is invalid or expired. Please sign in again.",
        )

    user_id = str(getattr(user, "id", "")).lower()

    user_sb = get_user_database_client(token.strip())

    try:
        role_result = await asyncio.to_thread(
            lambda: user_sb.table("user_roles").select("role").eq("user_id", user_id).maybe_single().execute()
        )
        assigned_role = (role_result.data or {}).get("role")
    except Exception as exc:
        logger.exception("Could not verify account role for student lookup")
        raise HTTPException(status_code=503, detail="Could not verify your account permissions.") from exc

    if assigned_role not in {"admin", "staff"}:
        raise HTTPException(status_code=403, detail="Cross-student searches require an authorized Staff or Admin account.")

    try:
        # Also validate configured UUIDs, avoiding malformed config.
        UUID(user_id)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(
            status_code=401,
            detail="Supabase returned an invalid user identity.",
        )

    if user_id not in ALLOWED_LOOKUP_USER_IDS:
        raise HTTPException(
            status_code=403,
            detail="Your account is not authorized to search student records.",
        )

    return user_sb, user


# ---------------------------------------------------------
# STUDENT SEARCH
# ---------------------------------------------------------

def find_students(sb, search_value: str, search_by: str):
    """
    Search student_identification using the known project schema.
    student_id is bigint; admission_number and student_name are text.
    """
    query = sb.table(STUDENT_TABLE).select(
        "student_id, admission_number, student_name, "
        "date_of_birth, gender, college_name, department, "
        "course_name, admission_year, email, phone_number, created_at"
    )

    if search_by == "admission":
        query = query.eq("admission_number", search_value)
    else:
        # Escape PostgREST filter wildcard characters.
        safe_name = (
            search_value.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        query = query.ilike("student_name", f"%{safe_name}%")

    result = query.limit(20).execute()
    return result.data or []


# ---------------------------------------------------------
# CERTIFICATE SEARCH
# ---------------------------------------------------------

def lookup_certificates(sb, student_id: Any):
    """
    Query only allowlisted certificate tables.
    A table is included only when its query succeeds.
    """
    certificates: dict[str, list[dict[str, Any]]] = {}

    for table_name in CERTIFICATE_TABLES:
        try:
            result = (
                sb.table(table_name)
                .select("*")
                .eq("student_id", student_id)
                .limit(100)
                .execute()
            )

            records = result.data or []

            if records:
                certificates[table_name] = records

        except Exception:
            # A certificate table may not exist in every deployment,
            # or it may use a different schema. Log this explicitly.
            logger.warning(
                "Certificate lookup failed for table %s",
                table_name,
                exc_info=True,
            )

    return certificates


# ---------------------------------------------------------
# SEARCH INTENT PARSING
# ---------------------------------------------------------

def parse_lookup(message: str):
    """
    Supported examples:
      Find student by admission number: ADM001
      Search student by admission: ADM001
      Find student by name: Arun Kumar
      Search student by name: Arun Kumar
    """
    text = message.strip()

    admission_patterns = [
        r"^(?:find|search)\s+student\s+by\s+admission\s+number\s*:\s*(.+)$",
        r"^(?:find|search)\s+student\s+by\s+admission\s*:\s*(.+)$",
        r"^admission\s+number\s*:\s*(.+)$",
    ]

    name_patterns = [
        r"^(?:find|search)\s+student\s+by\s+name\s*:\s*(.+)$",
        r"^student\s+name\s*:\s*(.+)$",
    ]

    for pattern in admission_patterns:
        match = re.match(pattern, text, re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            return ("admission", value) if value else None

    for pattern in name_patterns:
        match = re.match(pattern, text, re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            return ("name", value) if value else None

    return None


# ---------------------------------------------------------
# STUDENT RECORD FORMATTING
# ---------------------------------------------------------

def format_student(student: dict[str, Any]) -> str:
    """Format the student and certificate data without inventing values."""
    fields = [
        ("Student ID", "student_id"),
        ("Student Name", "student_name"),
        ("Admission Number", "admission_number"),
        ("Date of Birth", "date_of_birth"),
        ("Gender", "gender"),
        ("College", "college_name"),
        ("Department", "department"),
        ("Course", "course_name"),
        ("Admission Year", "admission_year"),
        ("Email", "email"),
        ("Phone Number", "phone_number"),
    ]

    lines = [
        "### Student Details",
        "",
        "| Field | Value |",
        "|---|---|",
    ]

    for label, key in fields:
        value = student.get(key)

        if value is not None and str(value).strip():
            # Prevent a database value from breaking the Markdown table.
            display_value = str(value).replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {label} | {display_value} |")

    certificates = student.get("certificates", {})

    lines.extend(["", "### Certificate Records", ""])

    if not certificates:
        lines.append(
            "No matching certificate records were returned from the "
            "configured certificate tables."
        )
        return "\n".join(lines)

    for table_name, records in certificates.items():
        lines.append(f"#### {table_name.replace('_', ' ').title()}")
        lines.append("")

        if not records:
            lines.append("No records found.")
            lines.append("")
            continue

        # Use the keys actually returned by Supabase.
        columns = list(records[0].keys())[:12]

        lines.append("| " + " | ".join(columns) + " |")
        lines.append("| " + " | ".join(["---"] * len(columns)) + " |")

        for record in records[:20]:
            values = []

            for column in columns:
                value = record.get(column, "")
                display_value = (
                    "" if value is None else str(value)
                ).replace("|", "\\|").replace("\n", " ")

                values.append(display_value)

            lines.append("| " + " | ".join(values) + " |")

        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------
# AUTHENTICATED, USER-SCOPED CERTIFICATE DATA
# ---------------------------------------------------------

async def authenticate_user(authorization: Optional[str]):
    """Validate a Supabase bearer token and return the service client and user."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is missing. Please sign in again.")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Invalid authorization header. Please sign in again.")

    sb = get_supabase_client()
    try:
        auth_result = await asyncio.to_thread(sb.auth.get_user, token.strip())
    except Exception as exc:
        # GoTrue can vary the error message between versions. An HTTP 4xx from
        # get_user means the supplied bearer token was rejected; do not rely
        # on parsing its wording to decide whether the client should refresh.
        raw_auth_status = (
            getattr(exc, "status", None)
            or getattr(exc, "status_code", None)
            or getattr(getattr(exc, "response", None), "status_code", None)
        )
        try:
            auth_status = int(raw_auth_status) if raw_auth_status is not None else None
        except (TypeError, ValueError):
            auth_status = None
        auth_message = str(exc).casefold()
        if auth_status in {400, 401, 403} or any(
            marker in auth_message
            for marker in (
                "jwt", "access token", "token expired", "expired token",
                "invalid token", "session expired",
            )
        ):
            raise HTTPException(
                status_code=401,
                detail="Your Supabase access token is invalid or expired. Refresh your session and try again.",
            ) from exc
        logger.warning(
            "AI assistant could not verify Supabase session (error_type=%s, status=%s)",
            type(exc).__name__,
            auth_status,
        )
        raise HTTPException(status_code=503, detail="Unable to verify your Supabase session right now.") from exc

    user = getattr(auth_result, "user", None)
    user_id = str(getattr(user, "id", "")).lower()
    try:
        UUID(user_id)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=401, detail="Your session is invalid or expired. Please sign in again.")
    user_sb = get_user_database_client(token.strip())
    try:
        role_result = await asyncio.to_thread(
            lambda: user_sb.table("user_roles").select("role").eq("user_id", user_id).maybe_single().execute()
        )
    except Exception as exc:
        logger.exception("AI assistant could not verify assigned role")
        raise HTTPException(status_code=503, detail="Could not verify your account permissions.") from exc
    assigned_role = (role_result.data or {}).get("role")
    if assigned_role not in {"admin", "staff", "student"}:
        raise HTTPException(status_code=403, detail="Your account has no authorized role.")
    return user_sb, user, assigned_role


def fetch_user_certificates(sb, user_id: str, role: str = "student", max_records: int = 50000):
    """Fetch records permitted by the role: linked student rows or staff/admin database."""
    if role not in {"admin", "staff", "student"}:
        raise HTTPException(status_code=403, detail="Your account has no authorized role.")
    page_size = 500
    records: list[dict[str, Any]] = []
    exact_count: Optional[int] = None
    while len(records) < max_records:
        start = len(records)
        # Only Student access needs the ownership column. Keeping it out of
        # Staff/Admin selects supports older certificate schemas while those
        # accounts remain subject to the JWT-backed PostgREST RLS policies.
        selected_columns = "id,user_id,student_name,certificate_type,certificate_name,date_extracted,status,ocr_result"
        if role == "student":
            selected_columns = "id,user_id,student_user_id,student_name,certificate_type,certificate_name,date_extracted,status,ocr_result"
        query = (
            sb.table("certificates")
            .select(selected_columns, count="exact")
        )
        if role == "student":
            query = query.eq("student_user_id", user_id)
        result = query.order("date_extracted", desc=True).range(start, min(start + page_size, max_records) - 1).execute()
        page = result.data or []
        if exact_count is None:
            exact_count = getattr(result, "count", None)
        records.extend(page)
        if len(page) < page_size:
            break
    total = exact_count if exact_count is not None else len(records)
    return records, total, len(records) >= max_records and total > len(records)


def _certificate_query_error_detail(exc: Exception) -> str:
    """Return safe setup guidance without exposing Supabase response data."""
    code = str(getattr(exc, "code", "") or "").upper()
    message = str(getattr(exc, "message", "") or exc).casefold()
    if code in {"42703", "PGRST204", "PGRST200"} or any(
        marker in message
        for marker in ("column", "schema cache", "could not find the")
    ):
        return (
            "The certificate database schema is missing a required field. "
            "Apply the SMART ERP Supabase migrations, then try again."
        )
    if code in {"42501", "PGRST301", "PGRST302"} or any(
        marker in message
        for marker in ("row-level security", "permission denied", "not allowed")
    ):
        return (
            "Supabase denied access to certificate records. Verify the role "
            "and certificate RLS policies in the SMART ERP migrations."
        )
    return "Could not retrieve saved certificate records. Check the Supabase connection and certificate table setup."


def _normalized(value: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", str(value or "").casefold())).strip()


def _flatten_ocr(value: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten OCR JSON while supporting both object and [{key,value}] formats."""
    result: dict[str, Any] = {}
    if isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, dict) and ("value" in item):
                key = item.get("key") or item.get("name") or item.get("label") or f"field_{index + 1}"
                if item.get("value") not in (None, ""):
                    result[str(key)] = item["value"]
            elif isinstance(item, dict):
                result.update(_flatten_ocr(item, prefix))
        return result
    if not isinstance(value, dict):
        return result
    if "value" in value and len(value) <= 4:
        key = value.get("key") or value.get("name") or value.get("label") or prefix
        if key and value.get("value") not in (None, ""):
            result[str(key)] = value["value"]
        return result
    for key, child in value.items():
        if key in {"raw_text", "text", "extracted_text", "certificate_file_path", "file_path", "storage_path"}:
            continue
        clean_key = f"{prefix}_{key}" if prefix else str(key)
        if isinstance(child, (dict, list)):
            result.update(_flatten_ocr(child, clean_key))
        elif child not in (None, ""):
            result[clean_key] = child
    return result


def certificate_fields(record: dict[str, Any]) -> dict[str, Any]:
    ocr = record.get("ocr_result")
    if isinstance(ocr, str):
        try:
            import json
            ocr = json.loads(ocr)
        except (TypeError, ValueError):
            ocr = {}
    if not isinstance(ocr, dict):
        return {}
    source = ocr.get("fields") or ocr.get("extracted_fields") or ocr.get("data") or {}
    fields = _flatten_ocr(source)
    for key, value in ocr.items():
        if key not in {"fields", "extracted_fields", "data", "raw_text", "text", "extracted_text"} and not isinstance(value, (dict, list)) and value not in (None, ""):
            fields.setdefault(key, value)
    return fields


def _field(fields: dict[str, Any], *names: str) -> Any:
    normalized = {_normalized(key).replace(" ", "_"): value for key, value in fields.items()}
    for name in names:
        value = normalized.get(name)
        if value not in (None, ""):
            return value
    return None


def _student_name(record: dict[str, Any]) -> str:
    fields = certificate_fields(record)
    return str(record.get("student_name") or _field(fields, "student_name", "candidate_name", "name") or "Unknown student").strip()


def _certificate_type(record: dict[str, Any]) -> str:
    fields = certificate_fields(record)
    return str(record.get("certificate_type") or _field(fields, "certificate_type", "doc_type", "document_type") or record.get("certificate_name") or "Certificate").replace("_", " ").strip()


def _certificate_group(record: dict[str, Any]) -> str:
    value = _normalized(_certificate_type(record))
    if "transfer" in value or re.search(r"\btc\b", value): return "Transfer Certificate"
    if "birth" in value: return "Birth Certificate"
    if "aadhaar" in value or "aadhar" in value: return "Aadhaar Card"
    if "income" in value: return "Income Certificate"
    if "community" in value: return "Community Certificate"
    if "diploma" in value: return "Diploma Certificate"
    if "degree" in value: return "Degree Certificate"
    if "cbse" in value and ("10th" in value or "class x" in value): return "CBSE 10th Marksheet"
    if "cbse" in value and ("12th" in value or "class xii" in value): return "CBSE 12th Marksheet"
    if ("state board" in value or "state_board" in value or "tamil nadu" in value) and ("10th" in value or "sslc" in value or "class x" in value): return "State Board 10th Marksheet"
    if ("state board" in value or "state_board" in value or "tamil nadu" in value) and ("12th" in value or "hsc" in value or "class xii" in value): return "State Board 12th Marksheet"
    if "10th" in value or "sslc" in value: return "10th Marksheet"
    if "12th" in value or "hsc" in value: return "12th Marksheet"
    return _certificate_type(record)


def _category_filter(message: str) -> Optional[str]:
    query = _normalized(message)
    if "transfer certificate" in query or re.search(r"\btc\b", message.casefold()): return "Transfer Certificate"
    if "birth certificate" in query: return "Birth Certificate"
    if "aadhaar" in query or "aadhar" in query: return "Aadhaar Card"
    if "income certificate" in query: return "Income Certificate"
    if "community certificate" in query: return "Community Certificate"
    if "diploma" in query: return "Diploma Certificate"
    if "degree" in query: return "Degree Certificate"
    if "cbse" in query and ("10th" in query or "class x" in query): return "CBSE 10th Marksheet"
    if "cbse" in query and ("12th" in query or "class xii" in query): return "CBSE 12th Marksheet"
    if ("state board" in query or "state_board" in query or "tamil nadu" in query) and ("10th" in query or "sslc" in query): return "State Board 10th Marksheet"
    if ("state board" in query or "state_board" in query or "tamil nadu" in query) and ("12th" in query or "hsc" in query): return "State Board 12th Marksheet"
    if "10th" in query or "sslc" in query: return "10th Marksheet"
    if "12th" in query or "hsc" in query: return "12th Marksheet"
    return None


def _is_project_question(message: str) -> bool:
    query = _normalized(message)
    database_request = re.search(
        r"\b(?:show|list|find|search|count|display|retrieve|fetch|compare|summari[sz]e)\b.*\b(?:student|certificate|mark|score|database|record)\b",
        query,
    ) or any(
        phrase in query
        for phrase in (
            "certificate database", "student database", "my certificates",
            "my certificate records", "my student records", "saved certificate records",
        )
    )
    if database_request:
        return False

    cues = (
        "what is smart erp", "what is the objective", "technologies used", "what technologies",
        "role of react", "how does fastapi", "how does react", "how are records saved",
        "how does ocr work", "what is tesseract", "benefits of", "advantages of",
        "limitations of ocr", "future enhancement", "project demonstration", "viva",
        "how certificate searching works", "how does certificate search",
    )
    return any(cue in query for cue in cues) or ("project" in query and not any(word in query for word in ("student", "marks", "uploads", "database records")))


def _category_matches(record: dict[str, Any], group: Optional[str]) -> bool:
    if not group:
        return True
    actual = _certificate_group(record)
    if group == "10th Marksheet":
        return "10th" in _normalized(actual) or "sslc" in _normalized(actual)
    if group == "12th Marksheet":
        return "12th" in _normalized(actual) or "hsc" in _normalized(actual)
    return actual == group


def _is_database_question(message: str) -> bool:
    query = _normalized(message)
    if _is_project_question(message):
        return False
    words = ("student", "mark", "score", "who scored", "details", "school name", "roll number", "register number", "upload", "record", "database", "birth certificate", "transfer certificate", "aadhaar", "income certificate", "community certificate", "diploma", "degree", "10th", "12th", "ella students", "evlo", "marks table")
    if any(word in query for word in words):
        return True
    if re.search(r"\btc\b", message.casefold()):
        return True
    return "certificate" in query and any(word in query for word in ("show", "list", "count", "how many", "details", "distribution", "all", "recent", "type"))


def _numeric(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(str(value).replace(",", "").strip())
        return number if number >= 0 else None
    except (TypeError, ValueError):
        return None


def _subject_marks(record: dict[str, Any], fields: dict[str, Any]) -> list[dict[str, Any]]:
    subjects: dict[str, dict[str, Any]] = {}
    for key, value in fields.items():
        normalized = _normalized(key).replace(" ", "_")
        numeric = _numeric(value)
        if numeric is None:
            continue
        named = re.match(r"subject_(\d+)_marks$", normalized)
        if named:
            subject_no = named.group(1)
            name = _field(fields, f"subject_{subject_no}_name") or f"Subject {subject_no}"
            subjects[str(name)] = {"subject": str(name), "marks": numeric, "maximum": None}
            continue
        if not normalized.endswith("_marks") or any(part in normalized for part in ("theory", "practical", "internal", "maximum", "total", "subject_")):
            continue
        name = normalized[:-6].replace("_", " ").strip()
        if name:
            subjects[name.title()] = {"subject": name.title(), "marks": numeric, "maximum": None}

    for subject in subjects.values():
        max_value = _field(fields, f"{_normalized(subject['subject']).replace(' ', '_')}_maximum_marks", f"{_normalized(subject['subject']).replace(' ', '_')}_max_marks")
        subject["maximum"] = _numeric(max_value)
    return list(subjects.values())


def _record_summary(record: dict[str, Any]) -> dict[str, Any]:
    fields = certificate_fields(record)
    name = _student_name(record)
    certificate_type = _certificate_type(record)
    school = _field(fields, "school_name", "school", "college_name")
    register = _field(fields, "register_number", "roll_number", "roll_no", "permanent_register_number", "admission_number")
    year = _field(fields, "exam_year", "year", "session") or record.get("date_extracted")
    total = _numeric(_field(fields, "total_marks", "total_score", "marks_obtained", "total"))
    maximum = _numeric(_field(fields, "maximum_total_marks", "maximum_marks", "max_marks", "total_maximum_marks"))
    subjects = _subject_marks(record, fields)
    normalized_type = _normalized(certificate_type)
    expected = 6 if ("state board" in normalized_type or "state_board" in normalized_type) and ("12th" in normalized_type or "hsc" in normalized_type) else 5 if any(token in normalized_type for token in ("10th", "12th", "sslc", "hsc", "class x")) else None
    if total is None and expected and len(subjects) == expected and all(subject.get("marks") is not None for subject in subjects):
        total = sum(subject["marks"] for subject in subjects)
    percentage = _numeric(_field(fields, "percentage", "percent"))
    if percentage is None and total is not None and maximum:
        percentage = round(total * 100 / maximum, 2)
    sensitive = {}
    for key, value in fields.items():
        if re.search(r"aadhaar|aadhar", key, re.I) and re.search(r"number|no", key, re.I):
            text = re.sub(r"\D", "", str(value))
            sensitive[key] = f"XXXX XXXX {text[-4:]}" if len(text) >= 4 else "Masked"
        elif value is not None and not isinstance(value, (dict, list)):
            sensitive[key.replace("_", " ").title()] = value
    return {
        "student_name": name,
        "school_name": school,
        "register_number": register,
        "certificate_type": certificate_type,
        "exam_year": year,
        "total_marks": total,
        "maximum_marks": maximum,
        "percentage": percentage,
        "result": _field(fields, "result", "pass_fail", "overall_result"),
        "date_extracted": record.get("date_extracted") or record.get("created_at"),
        "status": record.get("status"),
        "subjects": subjects,
        "fields": sensitive,
    }


def _unique_student_key(record: dict[str, Any]) -> str:
    fields = certificate_fields(record)
    name = _normalized(_student_name(record))
    dob = _normalized(_field(fields, "date_of_birth", "dob"))
    stable_id = _normalized(_field(fields, "student_id", "admission_number", "permanent_student_id"))
    return f"{stable_id}" if stable_id else f"{name}|{dob}"


def _match_student_records(message: str, records: list[dict[str, Any]]) -> Optional[list[dict[str, Any]]]:
    query = f" {_normalized(message)} "
    names = sorted({_normalized(_student_name(record)) for record in records if _student_name(record) != "Unknown student"}, key=len, reverse=True)
    matched = [name for name in names if len(name) >= 3 and f" {name} " in query]
    if not matched:
        tokens = [token for token in query.strip().split() if len(token) >= 4 and token not in {"marks", "mark", "show", "school", "name", "total", "details", "certificate", "student", "records", "please", "give", "find", "tell", "about", "evlo", "sollu", "oda", "marksheet", "result", "table", "format", "all", "every", "students"}]
        matched = [name for name in names if any(token in name.split() for token in tokens)]
    if not matched:
        return None
    longest = max(len(name.split()) for name in matched)
    matched = [name for name in matched if len(name.split()) == longest]
    return [record for record in records if _normalized(_student_name(record)) in matched]


def _table(title: str, columns: list[str], rows: list[list[Any]]) -> dict[str, Any]:
    return {"title": title, "columns": columns, "rows": rows}


def answer_from_certificates(message: str, records: list[dict[str, Any]], total_records: int, truncated: bool, offset: int = 0, page_size: int = 50) -> Optional[dict[str, Any]]:
    """Answer database questions using only the caller's fetched certificate rows."""
    if not _is_database_question(message):
        return None
    query = _normalized(message)
    group = _category_filter(message)
    comparing_grades = "10th" in query and "12th" in query and any(term in query for term in ("compare", "comparison", "versus", " vs "))
    if comparing_grades:
        group = None
    filtered = [record for record in records if _category_matches(record, group)]
    matches = _match_student_records(message, filtered)
    asks_chart = any(word in query for word in ("chart", "graph", "visualization", "pie", "bar chart"))
    asks_count = any(word in query for word in ("how many", "count", "number of", "total students", "total certificates", "statistics", "distribution"))
    asks_recent = any(word in query for word in ("recent upload", "recent certificate", "latest upload", "uploaded recently", "monthly", "upload trend"))
    asks_marks = any(word in query for word in ("mark", "score", "percentage", "highest", "compare"))
    asks_detail = any(word in query for word in ("details", "school name", "school", "register number", "roll number", "certificate number", "date of birth", "parent"))
    offset = min(offset, max(0, len(filtered)))

    if matches is not None:
        selected = matches
        if group:
            selected = [record for record in selected if _category_matches(record, group)]
        if len({_unique_student_key(record) for record in selected}) > 1 and len({_normalized(_student_name(record)) for record in selected}) == 1:
            choices = []
            seen = set()
            for record in selected:
                summary = _record_summary(record)
                key = (_unique_student_key(record), summary["certificate_type"], summary["register_number"])
                if key in seen: continue
                seen.add(key)
                choices.append([summary["student_name"], summary["school_name"], summary["register_number"], summary["certificate_type"], summary["exam_year"]])
            return make_response("Several students with that name match your records. Use the school, register number, or date of birth to identify the correct person.", source="supabase", format="table", data={"response_type": "table", "tables": [_table("Matching students", ["Student Name", "School", "Register Number", "Certificate", "Exam Year"], choices)], "record_count": len(choices), "quick_replies": ["Show all certificates for this student"]})
        if not selected:
            return make_response(f"No {group or 'certificate'} records were found for that student.", source="supabase", format="text", data={"response_type": "empty", "record_count": 0})
        summaries = [_record_summary(record) for record in selected]
        summary_rows = [[item["student_name"], item["school_name"], item["register_number"], item["certificate_type"], item["exam_year"], item["total_marks"], item["maximum_marks"], item["percentage"], item["result"]] for item in summaries]
        tables = [_table("Student certificate details", ["Student Name", "School", "Register Number", "Certificate Type", "Exam Year", "Total Marks", "Maximum Marks", "Percentage", "Result"], summary_rows)]
        if asks_marks:
            subject_rows = []
            for item in summaries:
                for subject in item["subjects"]:
                    subject_rows.append([item["student_name"], item["certificate_type"], subject["subject"], subject["marks"], subject["maximum"]])
            if subject_rows:
                tables.append(_table("Subject-wise marks", ["Student", "Certificate", "Subject", "Marks Obtained", "Maximum Marks"], subject_rows))
        if asks_detail:
            field_rows = [[item["student_name"], item["certificate_type"], key, value] for item in summaries for key, value in item["fields"].items()]
            if field_rows:
                tables.append(_table("Extracted certificate fields", ["Student", "Certificate", "Field", "Value"], field_rows))
        return make_response(f"Found {len(selected)} matching certificate record(s) for {summaries[0]['student_name']}. Values are shown only where present in the saved record.", source="supabase", format="table", data={"response_type": "table", "tables": tables, "record_count": len(selected), "quick_replies": ["Show 10th marks", "Show 12th marks", "Find school name"]})

    if asks_chart:
        groups: dict[str, float] = {}
        if any(word in query for word in ("monthly", "upload trend", "over time", "by month")):
            chart_type = "line"
            for record in filtered:
                raw_date = record.get("date_extracted") or record.get("created_at")
                try:
                    parsed_date = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
                    label = parsed_date.strftime("%Y-%m")
                    groups[label] = groups.get(label, 0) + 1
                except (TypeError, ValueError):
                    continue
        elif comparing_grades:
            chart_type = "bar"
            groups = {"10th Marksheet": float(sum(1 for record in filtered if "10th" in _normalized(_certificate_type(record)) or "sslc" in _normalized(_certificate_type(record)))), "12th Marksheet": float(sum(1 for record in filtered if "12th" in _normalized(_certificate_type(record)) or "hsc" in _normalized(_certificate_type(record))))}
        elif "school" in query and not asks_marks:
            chart_type = "bar"
            for record in filtered:
                school = _field(certificate_fields(record), "school_name", "school", "college_name")
                if school:
                    groups[str(school)] = groups.get(str(school), 0) + 1
        elif asks_marks:
            chart_type = "bar"
            for record in filtered:
                summary = _record_summary(record)
                if summary["total_marks"] is not None:
                    label = f"{summary['student_name']} · {summary['certificate_type']}"
                    groups[label] = summary["total_marks"]
        else:
            chart_type = "pie" if any(word in query for word in ("pie", "distribution", "by type", "certificate types")) else "bar"
            for record in filtered:
                label = _certificate_group(record)
                groups[label] = groups.get(label, 0) + 1
        sorted_groups = sorted(groups.items(), key=lambda pair: pair[0].casefold()) if chart_type == "line" else sorted(groups.items(), key=lambda pair: pair[1], reverse=True)
        if not sorted_groups:
            return make_response("There are no matching saved records to chart.", source="supabase", format="text", data={"response_type": "empty", "record_count": 0})
        chart_title = "Monthly certificate uploads" if chart_type == "line" else "10th vs 12th certificate records" if comparing_grades else "Student total marks" if asks_marks else "Certificate counts by school" if "school" in query else "Certificates by type" if chart_type == "pie" else "Certificate record comparison"
        chart = {"type": chart_type, "title": chart_title, "labels": [item[0] for item in sorted_groups], "values": [item[1] for item in sorted_groups]}
        note = " The configured record safety cap was reached, so this chart may be incomplete." if truncated else ""
        return make_response(f"This chart uses {len(filtered)} saved record(s) from your account.{note}", source="supabase", format="chart", data={"response_type": "chart", "chart": chart, "record_count": len(filtered), "complete": not truncated})

    if asks_count:
        unique_students = len({_unique_student_key(record) for record in records})
        by_type: dict[str, int] = {}
        for record in records:
            label = _certificate_group(record)
            by_type[label] = by_type.get(label, 0) + 1
        cards = [{"label": "Students", "value": unique_students}, {"label": "Certificates", "value": total_records}]
        if group:
            cards.append({"label": group, "value": len(filtered)})
        if "school" in query:
            school_counts: dict[str, set[str]] = {}
            for record in records:
                school = _field(certificate_fields(record), "school_name", "school", "college_name")
                if school:
                    school_counts.setdefault(str(school), set()).add(_unique_student_key(record))
            school_rows = [[school, len(student_keys)] for school, student_keys in sorted(school_counts.items(), key=lambda item: item[0].casefold())]
            return make_response(f"Found {len(school_rows)} school(s) in your saved certificate records.", source="supabase", format="table", data={"response_type": "summary", "summary_cards": cards, "tables": [_table("School-wise student count", ["School", "Students"], school_rows)], "record_count": total_records, "complete": not truncated})
        table = _table("Certificate counts by type", ["Certificate Type", "Records"], [[key, value] for key, value in sorted(by_type.items(), key=lambda item: item[0].casefold())])
        return make_response(f"Your account has {unique_students} unique student(s) and {total_records} certificate record(s).", source="supabase", format="table", data={"response_type": "summary", "summary_cards": cards, "tables": [table], "record_count": total_records, "complete": not truncated})

    if not filtered:
        return make_response(f"No saved {group or 'certificate'} records were found in your account.", source="supabase", format="text", data={"response_type": "empty", "record_count": 0})

    threshold_match = re.search(r"\b(?:above|over|greater than|more than)\s+(\d+(?:\.\d+)?)\b", query)
    if threshold_match and asks_marks:
        threshold = float(threshold_match.group(1))
        filtered = [record for record in filtered if (_record_summary(record)["total_marks"] or -1) > threshold]
        filtered.sort(key=lambda record: _record_summary(record)["total_marks"] or -1, reverse=True)
    if any(term in query for term in ("highest", "top scorer", "highest scoring")) and asks_marks:
        marked_records = [record for record in filtered if _record_summary(record)["total_marks"] is not None]
        if not marked_records:
            return make_response("No saved total marks are available to identify the highest score.", source="supabase", format="text", data={"response_type": "empty", "record_count": 0})
        high_score = max(_record_summary(record)["total_marks"] for record in marked_records)
        filtered = [record for record in marked_records if _record_summary(record)["total_marks"] == high_score]

    if asks_recent:
        filtered = sorted(filtered, key=lambda record: str(record.get("date_extracted") or record.get("created_at") or ""), reverse=True)
    elif asks_marks:
        filtered = [record for record in filtered if _record_summary(record)["total_marks"] is not None or _record_summary(record)["subjects"]]
        filtered.sort(key=lambda record: _record_summary(record)["total_marks"] or -1, reverse=True)

    page = filtered[offset:offset + page_size]
    summaries = [_record_summary(record) for record in page]
    if asks_recent:
        title = "Recent certificate uploads"
    elif any(term in query for term in ("highest", "top scorer", "highest scoring")) and asks_marks:
        title = "Highest marks"
    elif threshold_match and asks_marks:
        title = f"Marks above {threshold_match.group(1)}"
    elif asks_marks or "marks" in query:
        title = "Student marks records"
    else:
        title = f"{group or 'Certificate'} records"
    columns = ["Student Name", "School", "Register Number", "Certificate Type", "Exam Year", "Total Marks", "Maximum Marks", "Percentage", "Result"]
    rows = [[item["student_name"], item["school_name"], item["register_number"], item["certificate_type"], item["exam_year"], item["total_marks"], item["maximum_marks"], item["percentage"], item["result"]] for item in summaries]
    tables = [_table(title, columns, rows)]
    if asks_detail and group in {"Birth Certificate", "Transfer Certificate", "Aadhaar Card", "Income Certificate", "Community Certificate", "Diploma Certificate", "Degree Certificate"}:
        detail_rows = []
        for record, summary in zip(page, summaries):
            detail_rows.extend([[summary["student_name"], summary["certificate_type"], key, value] for key, value in summary["fields"].items()])
        if detail_rows:
            tables.append(_table("Available certificate fields", ["Student", "Certificate", "Field", "Value"], detail_rows))
    # Rows beyond the configured 50k cap are deliberately not fetchable from
    # this request path. Report the cap in `complete`, but don't offer a
    # pagination button that would loop over an empty page forever.
    has_more = offset + len(page) < len(filtered)
    next_offset = offset + len(page) if has_more else None
    if asks_recent:
        reply = f"Showing {offset + 1}–{offset + len(page)} of {total_records} saved certificate records, newest first."
    elif asks_marks or "marks" in query:
        reply = f"Showing {offset + 1}–{offset + len(page)} of {len(filtered)} academic record(s) with marks available. Missing totals are left blank."
    else:
        reply = f"Showing {offset + 1}–{offset + len(page)} of {len(filtered)} matching certificate record(s)."
    if truncated and not has_more:
        reply += " The database query reached its safety cap; this result may be incomplete."
    return make_response(reply, source="supabase", format="table", data={"response_type": "table", "tables": tables, "record_count": len(filtered), "total_count": total_records, "offset": offset, "page_size": page_size, "has_more": has_more, "next_offset": next_offset, "query": message, "complete": not truncated})


PROJECT_CONTEXT = """
Verified source-code facts: SMART ERP uses React + Vite, FastAPI, Supabase Auth/PostgreSQL/Storage, and local Ollama configured by `OLLAMA_MODEL` (default `llama3.2`). OCR uses Tesseract through pytesseract, with English and Tamil configured by default, plus certificate-specific parsing. The backend exposes `/extract/{doc_type}` and `/batch-extract`; the Certificate Database reads signed-in-user rows from `certificates`, including structured OCR JSON in `ocr_result`. The backend also contains allowlisted legacy student and certificate-table lookups. No verified product roadmap is present in the inspected code. Explain implemented behavior from these facts, call out limits, and label future ideas as suggestions rather than current features.
"""


# ---------------------------------------------------------
# LOCAL OLLAMA CHAT
# ---------------------------------------------------------

async def ask_ollama(
    message: str,
    history: Optional[list[dict[str, Any]]] = None,
) -> str:
    messages = [
        {
            "role": "system",
            "content": f"{SYSTEM_PROMPT}\n\nVerified project context:\n{PROJECT_CONTEXT}",
        }
    ]

    # Accept only standard chat roles and string content.
    for item in (history or [])[-10:]:
        role = item.get("role")
        content = item.get("content")

        if (
            role in {"user", "assistant"}
            and isinstance(content, str)
            and content.strip()
        ):
            messages.append(
                {
                    "role": role,
                    "content": content[:4000],
                }
            )

    messages.append({"role": "user", "content": message})

    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
    }

    try:
        timeout = httpx.Timeout(90.0, connect=5.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(OLLAMA_URL, json=payload)
            response.raise_for_status()
            result = response.json()

        answer = result.get("message", {}).get("content", "")

        if not isinstance(answer, str) or not answer.strip():
            raise HTTPException(
                status_code=502,
                detail="Ollama returned an empty response.",
            )

        return answer.strip()

    except HTTPException:
        raise

    except httpx.TimeoutException as exc:
        logger.warning("Ollama request timed out")
        raise HTTPException(
            status_code=504,
            detail="Local AI timed out. Check that Ollama is running.",
        ) from exc

    except httpx.ConnectError as exc:
        logger.warning("Could not connect to Ollama at %s", OLLAMA_URL)
        raise HTTPException(
            status_code=503,
            detail=(
                "Cannot connect to local Ollama. "
                "Start Ollama and check the configured model."
            ),
        ) from exc

    except httpx.HTTPStatusError as exc:
        logger.exception("Ollama returned an HTTP error")
        raise HTTPException(
            status_code=502,
            detail=(
                "Ollama returned an error. "
                "Check the model name and Ollama terminal."
            ),
        ) from exc

    except Exception as exc:
        logger.exception("Unexpected Ollama error")
        raise HTTPException(
            status_code=502,
            detail="The local AI service could not complete the request.",
        ) from exc


# ---------------------------------------------------------
# CHAT ENDPOINT
# ---------------------------------------------------------

@router.post("/chat")
async def chat(
    request: ChatRequest,
    authorization: Optional[str] = Header(default=None),
):
    message = request.message.strip()

    if not message:
        raise HTTPException(
            status_code=400,
            detail="Please enter a message.",
        )

    sb, authenticated_user, assigned_role = await authenticate_user(authorization)
    user_id = str(authenticated_user.id).lower()
    lookup = parse_lookup(message)

    if _is_database_question(message):
        try:
            records, total_records, truncated = await asyncio.to_thread(
                fetch_user_certificates, sb, user_id, assigned_role
            )
        except Exception as exc:
            error_code = getattr(exc, "code", None)
            error_status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
            logger.error(
                "User-scoped certificate query failed (type=%s, code=%s, status=%s)",
                type(exc).__name__,
                error_code,
                error_status,
            )
            raise HTTPException(
                status_code=503,
                detail=_certificate_query_error_detail(exc),
            ) from exc

        database_answer = answer_from_certificates(
            message,
            records,
            total_records,
            truncated,
            request.offset,
            request.page_size,
        )
        # Preserve configured legacy lookups if this account is explicitly
        # authorized and no matching user-owned certificate row was found.
        answer_data = database_answer.get("data") if database_answer else None
        is_empty_result = isinstance(answer_data, dict) and answer_data.get("response_type") == "empty"
        if database_answer and not (lookup and is_empty_result and user_id in ALLOWED_LOOKUP_USER_IDS):
            return database_answer

        if not lookup:
            return database_answer or make_response(
                "No saved certificate records were found for your account.",
                source="supabase",
                format="text",
                data={"response_type": "empty", "record_count": 0},
            )

    # Explicit student searches require verified authentication.
    if lookup:
        search_by, search_value = lookup

        if len(search_value) > 200:
            raise HTTPException(
                status_code=400,
                detail="The search value is too long.",
            )

        sb, user = await authorize_student_lookup(authorization)

        try:
            students = await asyncio.to_thread(
                find_students,
                sb,
                search_value,
                search_by,
            )

        except HTTPException:
            raise

        except Exception as exc:
            logger.exception("Student lookup failed")
            raise HTTPException(
                status_code=502,
                detail="Could not retrieve student records from Supabase.",
            ) from exc

        if not students:
            return make_response(
                "No matching student was found. Check the spelling or "
                "admission number and try again.",
                source="supabase",
                format="text",
                data={"students": [], "certificates": {}},
            )

        # Avoid disclosing details when a name matches multiple students.
        if len(students) > 1:
            lines = [
                "Multiple students match this search. "
                "Please search using an exact admission number.",
                "",
                "| Admission Number | Student Name | College |",
                "|---|---|---|",
            ]

            for student in students[:20]:
                admission = str(
                    student.get("admission_number") or ""
                ).replace("|", "\\|")

                name = str(
                    student.get("student_name") or ""
                ).replace("|", "\\|")

                college = str(
                    student.get("college_name") or ""
                ).replace("|", "\\|")

                lines.append(f"| {admission} | {name} | {college} |")

            return make_response(
                "\n".join(lines),
                source="supabase",
                format="text",
                data={"students": students},
            )

        student = students[0]

        try:
            certificates = await asyncio.to_thread(
                lookup_certificates,
                sb,
                student["student_id"],
            )

        except Exception as exc:
            logger.exception("Certificate lookup failed")
            raise HTTPException(
                status_code=502,
                detail="Could not retrieve certificate records.",
            ) from exc

        student_with_certificates = {
            **student,
            "certificates": certificates,
        }

        return make_response(
            format_student(student_with_certificates),
            source="supabase",
            format="text",
            data={
                "student": student,
                "certificates": certificates,
            },
        )

    # General project questions are answered separately from private DB facts.
    answer = await ask_ollama(
        message,
        request.history,
    )

    return make_response(
        answer,
        source="ollama",
        format="text",
    )
