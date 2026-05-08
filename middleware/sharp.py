"""
SHARP Middleware — extracts FHIR context from incoming request headers.

In production, a SHARP (Substitutable Medical Applications, Reusable Technologies)
launch injects these headers. In dev mode, we fall back to .env values so you can
test locally without a live SMART-on-FHIR connection.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class SHARPContext:
    """Immutable FHIR session context extracted from SHARP headers or .env fallback."""

    patient_id: str
    fhir_base_url: str
    access_token: str
    encounter_id: Optional[str] = None

    @classmethod
    def from_headers(cls, headers: dict[str, str]) -> "SHARPContext":
        """
        Build context from HTTP request headers.

        Prompt Opinion platform headers (production):
            x-patient-id
            x-fhir-server-url
            x-fhir-access-token

        Legacy x-sharp-* headers are also accepted as fallback.
        Falls back to environment variables when no header is present (dev mode).
        """
        patient_id = (
            headers.get("x-patient-id")
            or headers.get("x-sharp-patient-id")
            or os.getenv("DEV_PATIENT_ID")
        )
        fhir_base_url = (
            headers.get("x-fhir-server-url")
            or headers.get("x-sharp-fhir-base-url")
            or os.getenv("FHIR_BASE_URL", "http://localhost:8080/fhir")
        )
        access_token = (
            headers.get("x-fhir-access-token")
            or headers.get("x-sharp-access-token")
            or os.getenv("DEV_ACCESS_TOKEN", "dev-token")
        )
        encounter_id = (
            headers.get("x-sharp-encounter-id")
            or os.getenv("DEV_ENCOUNTER_ID")
        )

        if not patient_id:
            raise ValueError(
                "Missing patient_id: provide x-patient-id header "
                "or set DEV_PATIENT_ID in .env"
            )
        if not fhir_base_url:
            raise ValueError(
                "Missing fhir_base_url: provide x-fhir-server-url header "
                "or set FHIR_BASE_URL in .env"
            )

        return cls(
            patient_id=patient_id,
            fhir_base_url=fhir_base_url.rstrip("/"),
            access_token=access_token,
            encounter_id=encounter_id,
        )

    @classmethod
    def dev(
        cls,
        patient_id: Optional[str] = None,
        fhir_base_url: Optional[str] = None,
        access_token: Optional[str] = None,
    ) -> "SHARPContext":
        """Convenience constructor for local development / tests."""
        return cls(
            patient_id=patient_id or os.getenv("DEV_PATIENT_ID", "test-patient-1"),
            fhir_base_url=(
                fhir_base_url
                or os.getenv("FHIR_BASE_URL", "http://localhost:8080/fhir")
            ).rstrip("/"),
            access_token=access_token or os.getenv("DEV_ACCESS_TOKEN", "dev-token"),
        )
