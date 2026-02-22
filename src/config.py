from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str  # async (asyncpg) connection string
    database_url_sync: str = ""  # sync (psycopg2) connection string; derived if empty

    # Encryption
    encryption_key: str  # 32-byte hex-encoded AES-256 key

    # JWT
    jwt_secret_key: str = ""

    # GitHub OAuth (user login)
    github_oauth_client_id: str = ""
    github_oauth_client_secret: str = ""

    # Linear OAuth
    linear_client_id: str = ""
    linear_client_secret: str = ""
    linear_redirect_uri: str = ""

    # GitHub App
    github_app_id: str = ""
    github_app_private_key: str = ""  # PEM contents
    github_webhook_secret: str = ""

    # Slack
    slack_client_id: str = ""
    slack_client_secret: str = ""
    slack_redirect_uri: str = ""

    # Anthropic
    anthropic_api_key: str = ""

    # Stripe
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_publishable_key: str = ""

    # Frontend
    frontend_url: str = "http://localhost:3000"

    # AWS SQS
    sqs_queue_url: str = ""

    # AWS region
    aws_region: str = "us-east-1"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @property
    def sync_database_url(self) -> str:
        if self.database_url_sync:
            return self.database_url_sync
        # Convert asyncpg URL to psycopg2 URL
        return self.database_url.replace(
            "postgresql+asyncpg://", "postgresql+psycopg2://"
        ).replace("postgresql://", "postgresql+psycopg2://")

    @property
    def async_database_url(self) -> str:
        url = self.database_url
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url


settings = Settings()  # type: ignore[call-arg]
