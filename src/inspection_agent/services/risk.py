"""Versioned deterministic risk policy; the LLM has no authority here."""

from __future__ import annotations

from ..analysis_schemas import Severity
from ..schemas import Criticality
from ..workflow_schemas import (
    EvidenceStrength,
    RiskAssessment,
    RiskInputs,
    RiskLevel,
)

_SEVERITY_ORDER = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
}
_BASE_RISK_MATRIX = {
    Criticality.MEDIUM: {
        Severity.INFO: RiskLevel.LOW,
        Severity.LOW: RiskLevel.LOW,
        Severity.MEDIUM: RiskLevel.MEDIUM,
        Severity.HIGH: RiskLevel.HIGH,
    },
    Criticality.HIGH: {
        Severity.INFO: RiskLevel.LOW,
        Severity.LOW: RiskLevel.MEDIUM,
        Severity.MEDIUM: RiskLevel.HIGH,
        Severity.HIGH: RiskLevel.CRITICAL,
    },
}
_RISK_ORDER = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}


class DeterministicRiskPolicy:
    """Combine controlled severity, evidence, criticality, and sensor severity."""

    version = "risk-policy-1.0"

    def assess(self, inputs: RiskInputs) -> RiskAssessment:
        combined_severity = max(
            (inputs.fault_severity, inputs.sensor_severity),
            key=_SEVERITY_ORDER.__getitem__,
        )
        risk = _BASE_RISK_MATRIX[inputs.asset_criticality][combined_severity]

        cap = {
            EvidenceStrength.WEAK: RiskLevel.MEDIUM,
            EvidenceStrength.MODERATE: RiskLevel.HIGH,
            EvidenceStrength.STRONG: RiskLevel.CRITICAL,
        }[inputs.evidence_strength]
        if _RISK_ORDER[risk] > _RISK_ORDER[cap]:
            risk = cap

        return RiskAssessment(
            policy_version=self.version,
            risk_level=risk,
            inputs=inputs,
            explanation=(
                f"Controlled fault severity={inputs.fault_severity.value}, sensor "
                f"severity={inputs.sensor_severity.value}, asset criticality="
                f"{inputs.asset_criticality.value}; evidence strength="
                f"{inputs.evidence_strength.value} caps risk at {cap.value}."
            ),
        )
