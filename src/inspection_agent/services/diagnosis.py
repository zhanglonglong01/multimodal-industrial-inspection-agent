"""Diagnosis-provider boundary with deterministic and real-API implementations."""

from __future__ import annotations

import hashlib
import json
from typing import Protocol, runtime_checkable

from pydantic import Field

from ..analysis_schemas import EvidenceKind, EvidenceRef
from ..config import Settings
from ..schemas import StrictModel
from ..workflow_schemas import (
    DiagnosisProviderInput,
    DiagnosisReport,
    EvidenceStrength,
    validate_diagnosis_evidence,
)


@runtime_checkable
class DiagnosisProvider(Protocol):
    """A provider may reason only over the evidence bundle supplied here."""

    provider_name: str

    def diagnose(self, request: DiagnosisProviderInput) -> DiagnosisReport:
        """Rank candidates and produce a structured, evidence-linked explanation."""


def _evidence_strength(request: DiagnosisProviderInput) -> EvidenceStrength:
    kinds = {
        item.kind
        for item in [
            *request.vision_evidence,
            *request.sensor_evidence,
            *request.knowledge_evidence,
        ]
    }
    if {EvidenceKind.VISION, EvidenceKind.SENSOR, EvidenceKind.KNOWLEDGE} <= kinds:
        return EvidenceStrength.STRONG
    if EvidenceKind.KNOWLEDGE in kinds and kinds & {
        EvidenceKind.VISION,
        EvidenceKind.SENSOR,
    }:
        return EvidenceStrength.MODERATE
    return EvidenceStrength.WEAK


class FixtureDiagnosisProvider:
    """Offline deterministic synthesis; never reads scenario ground truth."""

    provider_name = "fixture_diagnosis"

    def diagnose(self, request: DiagnosisProviderInput) -> DiagnosisReport:
        available = [
            *request.vision_evidence,
            *request.sensor_evidence,
            *request.knowledge_evidence,
        ]
        ranked = list(request.failure_mode_candidates)
        primary = ranked[0] if ranked else None
        content_hash = hashlib.sha256(
            json.dumps(
                request.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:16].upper()

        if primary is None:
            explanation = (
                "Available evidence does not sufficiently support an actionable "
                "maintenance fault; continue routine monitoring."
            )
            causes: list[str] = []
            actions = ["Continue routine inspection and retain the evidence record."]
            uncertainties = ["This result is based on synthetic fixture evidence."]
        else:
            explanation = (
                f"{primary.name} is ranked first because the controlled candidate "
                "matches the observed vision and/or sensor evidence."
            )
            causes = primary.possible_causes
            actions = primary.recommended_checks
            uncertainties = [
                "Fixture synthesis cannot establish physical causality or replace a qualified inspection."
            ]

        report = DiagnosisReport(
            diagnosis_id=f"DIAG-{content_hash}",
            primary_fault_candidate=primary.mode_id if primary else None,
            actionable=primary is not None,
            alternative_candidates=[mode.mode_id for mode in ranked[1:]],
            possible_causes=causes,
            supporting_evidence_ids=[item.evidence_id for item in available],
            contradicting_evidence_ids=[],
            recommended_actions=actions,
            missing_evidence=(
                []
                if request.vision_evidence and request.sensor_evidence
                else ["One observational modality is unavailable."]
            ),
            uncertainties=uncertainties,
            evidence_strength=_evidence_strength(request),
            explanation=explanation,
            provider=self.provider_name,
            fixture=True,
        )
        return validate_diagnosis_evidence(report, available)


class _OpenAIDiagnosisOutput(StrictModel):
    primary_fault_candidate: str | None
    actionable: bool
    alternative_candidates: list[str] = Field(default_factory=list)
    possible_causes: list[str] = Field(default_factory=list)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    evidence_strength: EvidenceStrength
    explanation: str


class OpenAIDiagnosisProvider:
    """Optional real diagnosis adapter; fixture mode remains the offline default."""

    provider_name = "openai_responses"

    def __init__(self, settings: Settings) -> None:
        if settings.openai_api_key is None:
            raise ValueError("INSPECTION_OPENAI_API_KEY is required")
        from openai import OpenAI

        self.model = settings.openai_diagnosis_model
        self.client = OpenAI(api_key=settings.openai_api_key.get_secret_value())

    def diagnose(self, request: DiagnosisProviderInput) -> DiagnosisReport:
        payload = request.model_dump(mode="json")
        response = self.client.responses.parse(
            model=self.model,
            input=[
                {
                    "role": "system",
                    "content": (
                        "Rank only the supplied failure-mode candidates. Cite only "
                        "supplied evidence IDs. State uncertainty and never invent data."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                },
            ],
            text_format=_OpenAIDiagnosisOutput,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise ValueError("diagnosis provider returned no parsed structured output")
        report_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16].upper()
        report = DiagnosisReport(
            diagnosis_id=f"DIAG-{report_hash}",
            **parsed.model_dump(),
            provider=self.provider_name,
            fixture=False,
        )
        candidates = {item.mode_id for item in request.failure_mode_candidates}
        returned = {
            *(report.alternative_candidates),
            *([report.primary_fault_candidate] if report.primary_fault_candidate else []),
        }
        unknown_candidates = sorted(returned - candidates)
        if unknown_candidates:
            raise ValueError(
                f"diagnosis referenced unavailable failure modes: {unknown_candidates}"
            )
        return validate_diagnosis_evidence(
            report,
            [
                *request.vision_evidence,
                *request.sensor_evidence,
                *request.knowledge_evidence,
            ],
        )
