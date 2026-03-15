from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)


class Settings(BaseSettings):
    openai_api_key: str = Field(..., env="OPENAI_API_KEY")

    # Google OAuth client secrets file (downloaded from Google Cloud console)
    google_client_secret_file: Path = Field(
        default=DATA_DIR / "google_client_secret.json"
    )

    # Where we persist Google user tokens
    google_token_file: Path = Field(default=DATA_DIR / "google_token.json")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()  # type: ignore[arg-type]

