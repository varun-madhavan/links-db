from pathlib import Path

import pytest

from links_db.ingest import extract_html_article, extract_metadata
from links_db.settings import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings()


def test_extract_metadata_from_fixture(settings: Settings) -> None:
    html = (Path(__file__).parent / "fixtures" / "sample.html").read_text(encoding="utf-8")
    title, summary = extract_metadata(html, "https://example.com/article")
    assert title is not None
    assert "Fixture" in title or "Title" in title
    assert summary is not None


def test_extract_html_article_fixture(settings: Settings) -> None:
    html = (Path(__file__).parent / "fixtures" / "sample.html").read_text(encoding="utf-8")
    body, plain = extract_html_article(html, settings)
    assert body is not None
    assert "SampleHeading" in body or "Lorem" in body
    assert plain is not None
    assert len(plain) >= settings.min_extracted_text_chars
