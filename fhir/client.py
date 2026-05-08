"""
Async FHIR R4 Client — wraps httpx for querying a FHIR sandbox.

All methods accept a patient_id and return parsed JSON (dicts/lists).
Auth via Bearer token from SHARPContext.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx
from dotenv import load_dotenv

load_dotenv()


class FHIRClient:
    """Async FHIR R4 client backed by httpx."""

    def __init__(
        self,
        base_url: str | None = None,
        access_token: str | None = None,
        dev_mode: bool | None = None,
        timeout: float = 30.0,
    ):
        self.dev_mode = (
            dev_mode
            if dev_mode is not None
            else os.getenv("DEV_MODE", "false").lower() == "true"
        )
        self.base_url = (
            base_url
            or os.getenv("FHIR_BASE_URL", "http://localhost:8080/fhir")
        ).rstrip("/")
        self.access_token = (
            access_token or os.getenv("DEV_ACCESS_TOKEN", "dev-token")
        )
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    # -- lifecycle -----------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Accept": "application/fhir+json",
                    "Content-Type": "application/fhir+json",
                },
                timeout=self.timeout,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> "FHIRClient":
        await self._get_client()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # -- helpers -------------------------------------------------------------

    async def _get(self, path: str, params: dict | None = None) -> dict:
        """GET a FHIR resource path and return parsed JSON."""
        client = await self._get_client()
        resp = await client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    def _bundle_entries(self, bundle: dict) -> list[dict]:
        """Extract resource list from a FHIR Bundle."""
        return [
            entry["resource"]
            for entry in bundle.get("entry", [])
            if "resource" in entry
        ]

    # -- FHIR resource queries -----------------------------------------------

    async def get_patient(self, patient_id: str) -> dict:
        """Fetch a single Patient resource by ID."""
        return await self._get(f"/Patient/{patient_id}")

    async def get_conditions(
        self, patient_id: str, clinical_status: str | None = None
    ) -> list[dict]:
        """Search Condition resources for a patient."""
        params: dict[str, str] = {"patient": patient_id}
        if clinical_status:
            params["clinical-status"] = clinical_status
        bundle = await self._get("/Condition", params=params)
        return self._bundle_entries(bundle)

    async def get_procedures(
        self,
        patient_id: str,
        date_range: Optional[tuple[str, str]] = None,
    ) -> list[dict]:
        """
        Search Procedure resources for a patient.

        date_range: optional (start, end) ISO date strings for filtering.
        """
        params: dict[str, str] = {"patient": patient_id}
        if date_range:
            params["date"] = f"ge{date_range[0]}"
            params["date"] = f"le{date_range[1]}"  # FHIR supports repeated params
        bundle = await self._get("/Procedure", params=params)
        return self._bundle_entries(bundle)

    async def get_medications(self, patient_id: str) -> list[dict]:
        """Search MedicationRequest resources for a patient."""
        bundle = await self._get(
            "/MedicationRequest", params={"patient": patient_id}
        )
        return self._bundle_entries(bundle)

    async def get_documents(
        self, patient_id: str, category: str | None = None
    ) -> list[dict]:
        """Search DocumentReference resources for a patient."""
        params: dict[str, str] = {"patient": patient_id}
        if category:
            params["category"] = category
        bundle = await self._get("/DocumentReference", params=params)
        return self._bundle_entries(bundle)

    async def get_eob(self, patient_id: str) -> list[dict]:
        """Search ExplanationOfBenefit resources for a patient."""
        bundle = await self._get(
            "/ExplanationOfBenefit", params={"patient": patient_id}
        )
        return self._bundle_entries(bundle)

    async def get_observations(
        self,
        patient_id: str,
        category: str | None = None,
        code: str | None = None,
    ) -> list[dict]:
        """Search Observation resources for a patient."""
        params: dict[str, str] = {"patient": patient_id}
        if category:
            params["category"] = category
        if code:
            params["code"] = code
        bundle = await self._get("/Observation", params=params)
        return self._bundle_entries(bundle)

    # -- bulk fetch for gap analysis -----------------------------------------

    async def fetch_all_for_denial(
        self,
        patient_id: str,
        resource_types: list[str],
        date_range: Optional[tuple[str, str]] = None,
    ) -> dict[str, list[dict]]:
        """
        Fetch multiple resource types in parallel for a given patient.

        resource_types: list of FHIR resource type names, e.g.
            ["Condition", "Procedure", "MedicationRequest"]

        Returns a dict keyed by resource type → list of resources.
        """
        import asyncio

        dispatch = {
            "Condition": lambda: self.get_conditions(patient_id),
            "Procedure": lambda: self.get_procedures(patient_id, date_range),
            "MedicationRequest": lambda: self.get_medications(patient_id),
            "DocumentReference": lambda: self.get_documents(patient_id),
            "ExplanationOfBenefit": lambda: self.get_eob(patient_id),
            "Observation": lambda: self.get_observations(patient_id),
        }

        tasks = {}
        for rt in resource_types:
            if rt in dispatch:
                tasks[rt] = asyncio.create_task(dispatch[rt]())

        results: dict[str, list[dict]] = {}
        for rt, task in tasks.items():
            try:
                results[rt] = await task
            except httpx.HTTPStatusError:
                results[rt] = []  # graceful degradation

        return results
