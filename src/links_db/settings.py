from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LINKS_", env_file=".env", extra="ignore")

    data_dir: Path = Path("./data")
    db_path: Path | None = None
    host: str = "127.0.0.1"
    port: int = 8765
    reader_base_url: str | None = None

    fetch_timeout_s: float = 15.0
    max_html_bytes: int = 5 * 1024 * 1024
    max_pdf_bytes: int = 50 * 1024 * 1024
    min_extracted_text_chars: int = 200

    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    def resolved_db_path(self) -> Path:
        if self.db_path is not None:
            return self.db_path
        return self.data_dir / "links.db"


def get_settings() -> Settings:
    return Settings()
