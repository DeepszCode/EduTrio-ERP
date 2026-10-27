"""Regression checks for TN HSE marksheet subject-row ordering."""

import unittest

from app.ocr_engine import _repair_state12_table
from app.ocr_service import validate_state_12th


class TamilNaduHseLayoutTests(unittest.TestCase):
    def assert_group_rows(self, group, rows, expected):
        text = "--- TABLE CROP ---\nTHEORY PRACTICAL INTERNAL MARKS\n" + "\n".join(rows) + "\nTOTAL MARKS 999"
        fields = {"group_category": group, "total_marks": sum(expected.values())}
        _repair_state12_table(fields, text)
        actual = {
            key.removesuffix("_marks"): value
            for key, value in fields.items()
            if key.endswith("_marks") and key != "total_marks"
            and not key.endswith(("_theory_marks", "_practical_marks", "_internal_marks"))
        }
        self.assertEqual(actual, expected)

    def test_computer_science_group_uses_printed_six_subject_order(self):
        self.assert_group_rows(
            "Computer Science",
            [
                "TAMIL 080 010 090", "ENGLISH 063 010 073", "PHYSICS 027 020 010 057",
                "CHEMISTRY 038 020 010 068", "COMPUTER SCIENCE 062 020 010 092", "MATHEMATICS 047 010 057",
            ],
            {"tamil": 90, "english": 73, "physics": 57, "chemistry": 68, "computer_science": 92, "mathematics": 57},
        )

    def test_biology_group_uses_printed_six_subject_order(self):
        self.assert_group_rows(
            "Biology",
            [
                "TAMIL 086 010 096", "ENGLISH 065 010 075", "PHYSICS 041 020 010 071",
                "CHEMISTRY 060 020 010 090", "BIOLOGY 056 020 010 086", "MATHEMATICS 065 010 075",
            ],
            {"tamil": 96, "english": 75, "physics": 71, "chemistry": 90, "biology": 86, "mathematics": 75},
        )

    def test_arts_group_recovers_a_missing_subject_label_from_order(self):
        fields = {"group_category": "Arts", "total_marks": 357}
        text = """--- TABLE CROP ---
THEORY PRACTICAL INTERNAL MARKS
058 010 068
061 010 071
042 010 052
027 010 037
049 010 059
040 010 070
TOTAL MARKS 0357
"""
        _repair_state12_table(fields, text)
        expected = {"tamil": 68, "english": 71, "economics": 52, "commerce": 37, "accountancy": 59, "computer_applications": 70}
        actual = {
            key.removesuffix("_marks"): value
            for key, value in fields.items()
            if key.endswith("_marks") and key != "total_marks"
            and not key.endswith(("_theory_marks", "_practical_marks", "_internal_marks"))
        }
        self.assertEqual(actual, expected)

    def test_incomplete_group_subjects_are_flagged_for_review(self):
        warnings = validate_state_12th({"group_category": "Computer Science", "total_marks": 437, "tamil_marks": 90})
        self.assertTrue(any("Could not confidently extract" in warning for warning in warnings))


if __name__ == "__main__":
    unittest.main()
