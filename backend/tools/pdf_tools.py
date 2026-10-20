"""
MeritMap PDF Tools — extract text from PDFs using pdfplumber with pypdf fallback.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("meritmap.tools.pdf")


def extract_pdf_text(file_path: str) -> str:
    """
    Extract text from a PDF file.
    Tries pdfplumber first, falls back to pypdf.
    Returns empty string if both fail.
    """
    if not file_path or not Path(file_path).exists():
        logger.warning(f"PDF file not found: {file_path}")
        return ""

    # Try pdfplumber
    text = _try_pdfplumber(file_path)
    if text and len(text.strip()) > 50:
        logger.info(f"pdfplumber extracted {len(text)} chars from {file_path}")
        return text

    # Fallback to pypdf
    text = _try_pypdf(file_path)
    if text and len(text.strip()) > 50:
        logger.info(f"pypdf extracted {len(text)} chars from {file_path}")
        return text

    logger.warning(f"Could not extract meaningful text from {file_path}")
    return ""


def _try_pdfplumber(file_path: str) -> str:
    """Extract text using pdfplumber."""
    try:
        import pdfplumber
        with pdfplumber.open(file_path) as pdf:
            pages = []
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages.append(text)
            return "\n\n".join(pages)
    except Exception as e:
        logger.warning(f"pdfplumber failed: {e}")
        return ""


def _try_pypdf(file_path: str) -> str:
    """Extract text using pypdf."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(file_path)
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        return "\n\n".join(pages)
    except Exception as e:
        logger.warning(f"pypdf failed: {e}")
        return ""


# Convenience aliases
extract_text_from_pdf = extract_pdf_text


def parse_academic_document(file_path: str) -> dict:
    """Parse academic document and return page count and text."""
    text = extract_pdf_text(file_path)
    count = 1
    try:
        from pypdf import PdfReader
        reader = PdfReader(file_path)
        count = len(reader.pages)
    except Exception:
        pass
    return {"text": text, "page_count": count}
