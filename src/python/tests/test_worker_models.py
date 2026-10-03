from __future__ import annotations

import unittest

from schemas.models import OcrResult


class WorkerModelTests(unittest.TestCase):
    def test_ocr_result_serializes_zero_based_page_rotations(self) -> None:
        result = OcrResult(
            job_id="job-1",
            status="success",
            pdf_base64="JVBERg==",
            page_rotations={3: 270, 0: 90},
        ).to_dict()

        self.assertEqual(result["page_rotations"], {"0": 90, "3": 270})

if __name__ == "__main__":
    unittest.main()
