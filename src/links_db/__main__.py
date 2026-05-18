"""Run with: python -m links_db"""

import uvicorn

from links_db.settings import get_settings


def main() -> None:
    s = get_settings()
    uvicorn.run(
        "links_db.api:app",
        host=s.host,
        port=s.port,
        factory=False,
    )


if __name__ == "__main__":
    main()
