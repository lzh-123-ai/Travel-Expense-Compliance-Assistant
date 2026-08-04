from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExpectedSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_key: str = Field(min_length=1)
    version_label: str = Field(min_length=1)
    relevant_sections: list[str] = Field(min_length=1)
    retrieval_allowed: bool = True


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^TRAVEL-EVAL-\d{3}$")
    category: Literal[
        "direct_fact",
        "multi_condition",
        "cross_document",
        "version_conflict",
        "no_answer",
        "permission",
    ]
    question: str = Field(min_length=4)
    role: Literal["employee", "finance_reviewer", "policy_admin"]
    expense_date: date | None = None
    expected_sources: list[ExpectedSource] = Field(default_factory=list)
    acceptable_answer_points: list[str] = Field(default_factory=list)
    should_refuse: bool = False
    clarification_required: bool = False
    failure_labels: list[str] = Field(default_factory=list)
    expected_route: str | None = None
    expected_tool: str | None = None
    expected_arguments: dict[str, object] | None = None
    execution_allowed: bool | None = None

    @model_validator(mode="after")
    def validate_ground_truth(self) -> EvalCase:
        if not self.should_refuse and not self.expected_sources:
            raise ValueError("Answerable cases require at least one expected source")
        if not self.should_refuse and not self.acceptable_answer_points:
            raise ValueError("Answerable cases require acceptable answer points")
        if self.clarification_required and not self.should_refuse:
            raise ValueError("Clarification cases must refuse an unsupported direct answer")
        if any(not label.strip() for label in self.failure_labels):
            raise ValueError("Failure labels must not be empty")
        return self


class EvalDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_version: str = Field(pattern=r"^stage8-v\d+$")
    annotation_status: Literal["draft_needs_human_review", "reviewed"]
    description: str = Field(min_length=10)
    cases: list[EvalCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_case_ids(self) -> EvalDataset:
        case_ids = [case.id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("Evaluation case IDs must be unique")
        return self


class EvaluationRunConfig(BaseModel):
    """Stage 11 执行模型评测时复用；Stage 8 只冻结可复现字段。"""

    model_config = ConfigDict(extra="forbid")

    dataset_version: str
    retrieval_config_version: str
    prompt_version: str
    model_provider: str
    model_name: str
    framework_versions: dict[str, str] = Field(default_factory=dict)
    temperature: float = Field(ge=0, le=2)
    random_seed: int | None = None


class EvalCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    retrieved_chunk_ids: list[str] = Field(default_factory=list)
    answer: str | None = None
    passed: bool | None = None
    failure_labels: list[str] = Field(default_factory=list)
    latency_ms: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0)


class EvaluationRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    started_at: datetime
    config: EvaluationRunConfig
    results: list[EvalCaseResult] = Field(default_factory=list)
