from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://localhost/edgecast"
    admin_username: str = "admin"
    admin_password: str = "changeme"
    secret_key: str = "change-this-secret-key-in-production-minimum-32-chars"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 480  # 8 hours

    # External API base URLs (configurable for testing)
    kalshi_base_url: str = "https://api.elections.kalshi.com/trade-api/v2"
    openmeteo_base_url: str = "https://api.open-meteo.com/v1"

    # NOAA Climate Data Online (CDO) API token.
    # Free registration at https://www.ncdc.noaa.gov/cdo-web/token
    # When absent the forecast verifier falls back to Open-Meteo ERA5 reanalysis
    # for all cities (same behaviour as before this setting existed).
    noaa_cdo_token: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}

    def get_async_db_url(self) -> tuple[str, dict]:
        """Return (async_url, connect_args) suitable for asyncpg.

        asyncpg does not accept sslmode as a query parameter; we strip all
        SSL-related query params and translate them into connect_args instead.
        """
        from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

        url = self.database_url
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://") and "+asyncpg" not in url:
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

        parsed = urlparse(url)
        params = parse_qs(parsed.query, keep_blank_values=True)

        # Pull out SSL-related params that asyncpg won't accept
        sslmode = params.pop("sslmode", ["disable"])[0]
        params.pop("sslcert", None)
        params.pop("sslkey", None)
        params.pop("sslrootcert", None)

        new_query = urlencode({k: v[0] for k, v in params.items()})
        clean_url = urlunparse(parsed._replace(query=new_query))

        connect_args: dict = {}
        if sslmode in ("require", "verify-ca", "verify-full"):
            connect_args["ssl"] = True
        elif sslmode == "prefer":
            connect_args["ssl"] = False  # safe default for local Replit DB

        return clean_url, connect_args


@lru_cache()
def get_settings() -> Settings:
    return Settings()
