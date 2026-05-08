"""
SHARP Middleware — extracts FHIR context from incoming request headers.

Prompt Opinion platform sends SHARP-on-MCP headers:
    X-FHIR-Server-URL, X-FHIR-Access-Token, X-Patient-ID

In dev mode, we fall back to .env values so you can test locally
without a live SMART-on-FHIR connection.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("denialgpt.sharp")


@dataclass(frozen=True)
class SHARPContext:
    """Immutable FHIR session context extracted from SHARP headers or .env fallback."""

    patient_id: str
    fhir_base_url: str
    access_token: str
    encounter_id: Optional[str] = None
    _from_headers: bool = field(default=False, repr=False)

    def is_live(self) -> bool:
        """True if all 3 core values came from real SHARP headers (not .env fallback)."""
        return self._from_headers

    @classmethod
    def from_headers(cls, headers: dict[str, str]) -> "SHARPContext":
        """
        Build context from HTTP request headers.

        Priority order (first non-None wins):
          patient_id:    X-Patient-ID → X-SHARP-Patient-ID → DEV_PATIENT_ID env var
          fhir_base_url: X-FHIR-Server-URL → X-SHARP-FHIR-Base-URL → FHIR_BASE_URL env var
          access_token:  X-FHIR-Access-Token → X-SHARP-Access-Token → DEV_ACCESS_TOKEN env var

        Falls back to environment variables when no header is present (dev mode).
        """
        # --- patient_id ---
        pid_header = (
            headers.get("x-patient-id")
            or headers.get("x-sharp-patient-id")
        )
        patient_id = pid_header or os.getenv("DEV_PATIENT_ID")

        # --- fhir_base_url ---
        url_header = (
            headers.get("x-fhir-server-url")
            or headers.get("x-sharp-fhir-base-url")
        )
        fhir_base_url = url_header or os.getenv("FHIR_BASE_URL", "http://localhost:8080/fhir")

        # --- access_token ---
        token_header = (
            headers.get("x-fhir-access-token")
            or headers.get("x-sharp-access-token")
        )
        access_token = token_header or os.getenv("DEV_ACCESS_TOKEN", "dev-token")

        # --- encounter_id (optional) ---
        encounter_id = (
            headers.get("x-sharp-encounter-id")
            or os.getenv("DEV_ENCOUNTER_ID")
        )

        # --- validation ---
        if not patient_id:
            raise ValueError(
                "Missing patient_id: provide X-Patient-ID header "
                "or set DEV_PATIENT_ID in .env"
            )
        if not fhir_base_url:
            raise ValueError(
                "Missing fhir_base_url: provide X-FHIR-Server-URL header "
                "or set FHIR_BASE_URL in .env"
            )

        # Determine if context came from real headers vs env fallback
        from_headers = bool(pid_header and url_header and token_header)

        if not from_headers:
            missing = []
            if not pid_header:
                missing.append("X-Patient-ID")
            if not url_header:
                missing.append("X-FHIR-Server-URL")
            if not token_header:
                missing.append("X-FHIR-Access-Token")
            logger.warning(
                "SHARP running in DEV FALLBACK mode — missing headers: %s. "
                "Using .env values instead.",
                ", ".join(missing),
            )

        return cls(
            patient_id=patient_id,
            fhir_base_url=fhir_base_url.rstrip("/"),
            access_token=access_token,
            encounter_id=encounter_id,
            _from_headers=from_headers,
        )

    @classmethod
    def dev(
        cls,
        patient_id: Optional[str] = None,
        fhir_base_url: Optional[str] = None,
        access_token: Optional[str] = None,
    ) -> "SHARPContext":
        """Convenience constructor for local development / tests."""
        logger.info("SHARPContext created in DEV mode (no SHARP headers).")
        return cls(
            patient_id=patient_id or os.getenv("DEV_PATIENT_ID", "test-patient-1"),
            fhir_base_url=(
                fhir_base_url
                or os.getenv("FHIR_BASE_URL", "http://localhost:8080/fhir")
            ).rstrip("/"),
            access_token=access_token or os.getenv("DEV_ACCESS_TOKEN", "dev-token"),
            _from_headers=False,
        )
