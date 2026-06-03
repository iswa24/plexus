"""Runtime configuration, loaded from environment / .env (prefix PLEXUS_)."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="PLEXUS_", extra="ignore"
    )

    # When true, connectors return realistic sample data and require no
    # credentials. Flip to false (and fill the sections below) to hit the
    # real Bedrock / Neo4j / Trino.
    demo_mode: bool = True

    db_path: str = "studio.db"
    cors_origins: str = "*"

    # --- Bedrock ---
    aws_region: str = "us-east-1"
    bedrock_default_model: str = "anthropic.claude-3-5-sonnet-20241022-v2:0"

    # --- Neo4j ---
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"

    # --- Trino ---
    trino_host: str = "localhost"
    trino_port: int = 8080
    trino_user: str = "studio"
    trino_catalog: str = "hive"
    trino_scheme: str = "https"


@lru_cache
def get_settings() -> Settings:
    return Settings()
