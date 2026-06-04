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

    # --- Anthropic (direct API) ---
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"

    # NL2SQL model provider: "claudecode" (local CLI / Max sub, no key) or "anthropic" (API key)
    nl2sql_provider: str = "claudecode"

    # --- NL2SQL SQLite databases (seed: python scripts/seed_security.py) ---
    incidents_db: str = "data/incidents.db"
    assets_db: str = "data/assets.db"

    # --- Neo4j ---
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"

    # --- Other sources ---
    elastic_url: str = "http://localhost:9200"

    # --- Trino ---
    trino_host: str = "localhost"
    trino_port: int = 8080
    trino_user: str = "studio"
    trino_catalog: str = "hive"
    trino_scheme: str = "https"

    # --- MCP (Model Context Protocol) ---
    # Servers are an ADMIN ALLOW-LIST (governance): only listed servers can be
    # reached, and only their listed tools. Set via PLEXUS_MCP_SERVERS as a JSON
    # object {serverId: {transport: "stdio"|"sse", command|url, args, allowed_tools}}.
    # A built-in "demo" server always exists so MCP cards run with no dependency.
    mcp_enabled: bool = True
    mcp_servers: dict = {}


@lru_cache
def get_settings() -> Settings:
    return Settings()
