import httpx

from links_db.ingest import is_pdf_content_type, is_pdf_magic, is_pdf_path


def test_is_pdf_path_suffix():
    assert is_pdf_path("https://example.com/paper.pdf")
    assert is_pdf_path("https://example.com/paper.PDF?x=1")
    assert not is_pdf_path("https://example.com/page.html")


def test_is_pdf_content_type():
    assert is_pdf_content_type("application/pdf")
    assert is_pdf_content_type("application/pdf; charset=binary")
    assert not is_pdf_content_type("text/html")


def test_is_pdf_magic():
    assert is_pdf_magic(b"%PDF-1.4\n")
    assert not is_pdf_magic(b"<!DOCTYPE")
