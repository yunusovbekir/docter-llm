"""Application settings: the single-row table and its API schemas.

The provider API key is never part of these models' persisted or returned data;
it lives only in the OS keyring (see ``backend.services.secrets``).
"""

from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)
from sqlalchemy import CheckConstraint
from sqlmodel import Field, SQLModel

from backend.config import Settings


def _check_http_url(value: str) -> str:
    Settings.HTTP_URL.validate_python(value)
    return value


def _normalize_provider(value: object) -> object:
    return value.strip().lower() if isinstance(value, str) else value


Provider = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$"),
    BeforeValidator(_normalize_provider),
]
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
BaseUrl = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=500), AfterValidator(_check_http_url)
]
Temperature = Annotated[float, Field(ge=0.0, le=2.0)]
MaxAgentSteps = Annotated[int, Field(ge=1, le=200)]


class AppSettings(SQLModel, table=True):
    """Persisted LLM configuration. Exactly one row (``id = 1``) may exist."""

    __tablename__ = "app_settings"
    __table_args__ = (CheckConstraint(f"id = {Settings.SETTINGS_ROW_ID}", name="single_row"),)

    id: int = Field(default=Settings.SETTINGS_ROW_ID, primary_key=True)
    provider: str = Settings.DEFAULT_PROVIDER
    model: str = Settings.DEFAULT_MODEL
    base_url: str | None = None
    embedding_model: str = Settings.DEFAULT_EMBEDDING_MODEL
    temperature: float = Settings.DEFAULT_TEMPERATURE
    max_agent_steps: int = Settings.DEFAULT_MAX_AGENT_STEPS


class SettingsRead(BaseModel):
    """Response body of ``GET``/``PUT /api/settings``."""

    provider: str
    model: str
    base_url: str | None
    embedding_model: str
    temperature: float
    max_agent_steps: int
    has_api_key: bool


class SettingsUpdate(BaseModel):
    """Request body of ``PUT /api/settings``.

    Omitted fields are left unchanged. ``base_url`` may be ``null`` or ``""`` to clear it.
    ``api_key``: omitted or ``null`` keeps the stored key, ``""`` deletes it, any other
    value replaces it.
    """

    model_config = ConfigDict(extra="forbid")

    provider: Provider | None = None
    model: NonEmptyStr | None = None
    base_url: BaseUrl | None = None
    embedding_model: NonEmptyStr | None = None
    temperature: Temperature | None = None
    max_agent_steps: MaxAgentSteps | None = None
    api_key: SecretStr | None = None

    @field_validator("base_url", mode="before")
    @classmethod
    def _blank_base_url_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def _reject_null_for_required_fields(self) -> Self:
        for name in Settings.NON_NULLABLE_SETTINGS_FIELDS:
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class ConnectionTestResult(BaseModel):
    """Response body of ``POST /api/settings/test``."""

    ok: bool
    latency_ms: int | None
    error: str | None
