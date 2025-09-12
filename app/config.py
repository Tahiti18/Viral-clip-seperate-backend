import os

class Settings:
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/unitylab")
    API_PREFIX: str = "/v1"
    API_KEY: str = os.getenv("API_KEY", "devkey")
    THROUGHPUT_P0: float = float(os.getenv("THROUGHPUT_P0", "1.6"))
    THROUGHPUT_P1: float = float(os.getenv("THROUGHPUT_P1", "1.2"))
    THROUGHPUT_P2: float = float(os.getenv("THROUGHPUT_P2", "1.0"))
    AGING_MINUTES: int = int(os.getenv("AGING_MINUTES", "10"))

settings = Settings()
