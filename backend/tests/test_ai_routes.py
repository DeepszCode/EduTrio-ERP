"""Focused tests for deterministic AI certificate response formatting.

Live Supabase access is intentionally not mocked into a purported integration
test; deployment data isolation needs to be verified against a configured
Supabase project.
"""

import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.ai_routes import answer_from_certificates, authenticate_user, certificate_fields, fetch_user_certificates, get_user_database_client, _record_summary


def certificate(name="Student A", register="R-100", marks=80):
    return {
        "id": f"certificate-{register}",
        "student_name": name,
        "certificate_type": "state_board_10th",
        "date_extracted": "2025-06-10",
        "ocr_result": {
            "fields": {
                "school_name": "Example Higher Secondary School",
                "register_number": register,
                "date_of_birth": "01/01/2008",
                "total_marks": marks,
                "maximum_total_marks": 500,
                "subject_1_name": "Tamil",
                "subject_1_marks": marks,
                "percentage": marks / 5,
            }
        },
    }


class CertificateAssistantTests(unittest.TestCase):
    def test_extracts_nested_fields_and_summary_without_guessing(self):
        row = certificate(marks=420)
        self.assertEqual(certificate_fields(row)["school_name"], "Example Higher Secondary School")
        summary = _record_summary(row)
        self.assertEqual(summary["total_marks"], 420)
        self.assertEqual(summary["maximum_marks"], 500)
        self.assertEqual(summary["percentage"], 84)

    def test_student_marks_answer_is_structured_and_uses_matching_record(self):
        response = answer_from_certificates(
            "Show Student A 10th marks",
            [certificate("Student A", "R-100", 420), certificate("Student B", "R-200", 390)],
            2,
            False,
        )
        self.assertEqual(response["source"], "supabase")
        details = response["data"]["tables"][0]
        self.assertEqual(details["rows"][0][0], "Student A")
        self.assertEqual(details["rows"][0][5], 420)
        self.assertNotIn("Student B", str(details["rows"]))

    def test_collection_results_support_pages_and_report_completeness(self):
        rows = [certificate(f"Student {index}", f"R-{index}", 400 + index) for index in range(4)]
        first = answer_from_certificates("Show all 10th records", rows, 4, False, 0, 2)
        second = answer_from_certificates("Show all 10th records", rows, 4, False, 2, 2)
        first_rows = first["data"]["tables"][0]["rows"]
        second_rows = second["data"]["tables"][0]["rows"]
        self.assertEqual(len(first_rows), 2)
        self.assertEqual(first["data"]["next_offset"], 2)
        self.assertTrue(first["data"]["has_more"])
        self.assertEqual(len(second_rows), 2)
        self.assertFalse(second["data"]["has_more"])

    def test_chart_uses_record_counts(self):
        rows = [certificate("Student A"), {**certificate("Student B"), "certificate_type": "cbse_10th_marksheet"}]
        response = answer_from_certificates("Show a pie chart of certificates by type", rows, 2, False)
        chart = response["data"]["chart"]
        self.assertEqual(chart["type"], "pie")
        self.assertEqual(sum(chart["values"]), 2)

    def test_highest_marks_and_threshold_queries_filter_records(self):
        rows = [certificate("Student A", "R-1", 420), certificate("Student B", "R-2", 390)]
        highest = answer_from_certificates("Who scored the highest marks?", rows, 2, False)
        self.assertEqual(highest["data"]["tables"][0]["rows"][0][0], "Student A")
        above = answer_from_certificates("Show students with marks above 400", rows, 2, False)
        self.assertEqual(len(above["data"]["tables"][0]["rows"]), 1)
        self.assertEqual(above["data"]["tables"][0]["rows"][0][0], "Student A")

    def test_database_fetch_is_scoped_to_supplied_user(self):
        class Query:
            def __init__(self, expected_filter):
                self.user_filter = None
                self.expected_filter = expected_filter
                self.selected_columns = None

            def select(self, columns, **_kwargs):
                self.selected_columns = columns
                return self
            def eq(self, column, value):
                self.user_filter = (column, value)
                return self
            def order(self, *_args, **_kwargs): return self
            def range(self, *_args, **_kwargs): return self
            def execute(self):
                self.assert_filter()
                return type("Result", (), {"data": [{"student_name": "Owned"}], "count": 1})()
            def assert_filter(self):
                if self.user_filter != self.expected_filter:
                    raise AssertionError(f"Unexpected user filter: {self.user_filter}")

        class Supabase:
            def __init__(self, expected_filter): self.query = Query(expected_filter)
            def table(self, table_name):
                if table_name != "certificates": raise AssertionError(table_name)
                return self.query

        rows, count, truncated = fetch_user_certificates(
            Supabase(("student_user_id", "auth-user-123")), "auth-user-123", "student"
        )
        self.assertEqual(rows[0]["student_name"], "Owned")
        self.assertEqual(count, 1)
        self.assertFalse(truncated)

    def test_staff_query_does_not_require_student_link_column(self):
        class Query:
            selected_columns = None
            def select(self, columns, **_kwargs):
                self.selected_columns = columns
                return self
            def order(self, *_args, **_kwargs): return self
            def range(self, *_args, **_kwargs): return self
            def execute(self):
                self.assertNotIn("student_user_id", self.selected_columns)
                return type("Result", (), {"data": [], "count": 0})()

        class Supabase:
            query = Query()
            def table(self, _name): return self.query

        fetch_user_certificates(Supabase(), "staff-user", "staff")

    def test_student_query_requires_link_column_and_filters_to_own_id(self):
        class Query:
            selected_columns = None
            filters = []
            def select(self, columns, **_kwargs):
                self.selected_columns = columns
                return self
            def eq(self, column, value):
                self.filters.append((column, value))
                return self
            def order(self, *_args, **_kwargs): return self
            def range(self, *_args, **_kwargs): return self
            def execute(self):
                self.assertIn("student_user_id", self.selected_columns)
                self.assertEqual(self.filters, [("student_user_id", "student-user")])
                return type("Result", (), {"data": [], "count": 0})()

        class Supabase:
            query = Query()
            def table(self, _name): return self.query

        fetch_user_certificates(Supabase(), "student-user", "student")

    def test_certificate_schema_error_gets_safe_migration_guidance(self):
        from app.ai_routes import _certificate_query_error_detail

        error = type("PostgrestError", (Exception,), {"code": "PGRST204"})("missing field")
        detail = _certificate_query_error_detail(error)
        self.assertIn("Apply the SMART ERP Supabase migrations", detail)
        self.assertNotIn("missing field", detail)


class AIAuthenticationTests(unittest.IsolatedAsyncioTestCase):
    def test_database_client_forwards_verified_jwt_for_rls(self):
        from unittest.mock import Mock

        client = Mock()
        with patch("app.main.SUPABASE_URL", "https://example.supabase.co"), patch(
            "app.main.SUPABASE_KEY", "server-side-key"
        ), patch("supabase.create_client", return_value=client) as create_client:
            self.assertIs(get_user_database_client("verified-user-jwt"), client)

        create_client.assert_called_once_with("https://example.supabase.co", "server-side-key")
        client.postgrest.auth.assert_called_once_with("verified-user-jwt")

    async def test_invalid_supabase_token_is_a_refreshable_401(self):
        class AuthError(Exception):
            status = "401"

        class Auth:
            def get_user(self, token):
                self.token = token
                raise AuthError("request rejected")

        class Supabase:
            auth = Auth()

        with patch("app.ai_routes.get_supabase_client", return_value=Supabase()):
            with self.assertRaises(HTTPException) as raised:
                await authenticate_user("Bearer expired-token")

        self.assertEqual(raised.exception.status_code, 401)
        self.assertIn("Refresh your session", raised.exception.detail)

    async def test_auth_service_failure_is_not_reported_as_expired_token(self):
        class AuthError(Exception):
            status = 503

        class Auth:
            def get_user(self, _token):
                raise AuthError("temporarily unavailable")

        class Supabase:
            auth = Auth()

        with patch("app.ai_routes.get_supabase_client", return_value=Supabase()):
            with self.assertRaises(HTTPException) as raised:
                await authenticate_user("Bearer token")

        self.assertEqual(raised.exception.status_code, 503)

    async def test_missing_or_malformed_bearer_token_is_rejected(self):
        for authorization in (None, "Basic token", "Bearer "):
            with self.subTest(authorization=authorization):
                with self.assertRaises(HTTPException) as raised:
                    await authenticate_user(authorization)
                self.assertEqual(raised.exception.status_code, 401)

    def test_staff_certificate_fetch_uses_staff_permissions_without_student_filter(self):
        from app.ai_routes import fetch_user_certificates

        class Query:
            def __init__(self): self.filters = []
            def select(self, *_args, **_kwargs): return self
            def eq(self, column, value):
                self.filters.append((column, value))
                return self
            def order(self, *_args, **_kwargs): return self
            def range(self, *_args, **_kwargs): return self
            def execute(self):
                if self.filters:
                    raise AssertionError(f"Staff query should be authorized by role/RLS, got filters: {self.filters}")
                return type("Result", (), {"data": [{"student_name": "Visible to staff"}], "count": 1})()

        class Supabase:
            def __init__(self): self.query = Query()
            def table(self, name):
                if name != "certificates": raise AssertionError(name)
                return self.query

        rows, count, truncated = fetch_user_certificates(Supabase(), "staff-user", "staff")
        self.assertEqual(rows[0]["student_name"], "Visible to staff")
        self.assertEqual(count, 1)
        self.assertFalse(truncated)


if __name__ == "__main__":
    unittest.main()
