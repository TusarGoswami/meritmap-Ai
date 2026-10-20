"""
Tests for PDF text and table extraction using pdf_tools.
"""
from pathlib import Path
import pytest
from backend.tools.pdf_tools import extract_text_from_pdf, parse_academic_document


def test_sample_pdf_text_extraction():
    resume_path = Path("sample_data/rahul_resume.pdf")
    assert resume_path.exists(), "Sample resume PDF must exist"

    text = extract_text_from_pdf(str(resume_path))
    assert "RAHUL SHARMA" in text
    assert "8.20" in text or "8.2" in text
    assert "Computer Science" in text
    assert "MedScan AI" in text


def test_sample_marksheet_extraction():
    marksheet_path = Path("sample_data/rahul_marksheet.pdf")
    assert marksheet_path.exists(), "Sample marksheet PDF must exist"

    doc_info = parse_academic_document(str(marksheet_path))
    assert doc_info["page_count"] >= 1
    assert "NATIONAL INSTITUTE OF TECHNOLOGY" in doc_info["text"]
    assert "8.20" in doc_info["text"]
