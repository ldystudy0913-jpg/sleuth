"""Request / response / domain models."""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class Phase(str, Enum):
    CHECK = "CHECK"
    RECHECK = "RECHECK"
    BATCH_RECHECK = "BATCH_RECHECK"


class CustType(str, Enum):
    PRIVATE = "PRIVATE"
    CORPORATE = "CORPORATE"
    OTHER = "OTHER"


class Severity(str, Enum):
    INFO = "info"
    WARN = "warn"
    FAIL = "fail"


class FindingStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


class CheckRequest(BaseModel):
    reportId: str = ""
    investId: str = ""
    result: str = ""
    question: str = ""
    busCode: str = ""
    busCodeDesc: str = ""
    currentDateTime: str = Field(default="", alias="currentDateTime")
    currentDate: str = ""
    custType: str = ""
    approveData: str = ""
    phase: str = "CHECK"
    bankId: str = ""

    model_config = {"populate_by_name": True}

    def effective_datetime(self) -> str:
        return (self.currentDateTime or self.currentDate or "").strip()


class BatchCheckRequest(BaseModel):
    items: List[CheckRequest]
    phase: str = "BATCH_RECHECK"
    question: str = ""


class Finding(BaseModel):
    dimension: str
    status: FindingStatus
    severity: Severity = Severity.WARN
    message: str
    evidence: str = ""
    suggestion: str = ""
    field_code: str = ""


class DimensionScore(BaseModel):
    dimension: str
    status: FindingStatus
    weight: float
    contribution: float


class CheckResult(BaseModel):
    reportId: str
    investId: str
    busCode: str
    custType: str
    phase: str
    strategy_id: str
    enabled_dimensions: List[str]
    findings: List[Finding]
    score: float
    grade: str
    dimension_scores: List[DimensionScore]
    summary: str
    skipped_attachments: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ReportSection(BaseModel):
    label: str = ""
    code: str = ""
    type: str = ""
    value: Any = None


class ReportFacts(BaseModel):
    """Normalized facts extracted from the form JSON."""

    sections: List[ReportSection] = Field(default_factory=list)
    fields: Dict[str, str] = Field(default_factory=dict)
    tables: Dict[str, List[Dict[str, str]]] = Field(default_factory=dict)
    checkboxes: Dict[str, List[Dict[str, str]]] = Field(default_factory=dict)
    narrative_codes: List[str] = Field(default_factory=list)
    raw_section_count: int = 0
