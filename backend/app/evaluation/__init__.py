"""Versioned evaluation dataset and run contracts."""

from app.evaluation.answer_metrics import (
    AnswerAggregateMetrics,
    AnswerCaseMetric,
    aggregate_answer_metrics,
    expected_answer_status,
    score_answer_case,
)
from app.evaluation.contracts import (
    EvalCase,
    EvalCaseResult,
    EvalDataset,
    EvaluationRun,
    EvaluationRunConfig,
    ExpectedSource,
    RetrievalEvalCase,
    RetrievalEvalDataset,
)
from app.evaluation.loader import load_eval_dataset, load_retrieval_eval_dataset
from app.evaluation.retrieval_metrics import (
    RetrievalAggregateMetrics,
    RetrievalCaseMetric,
    aggregate_retrieval_metrics,
    score_retrieval_case,
)

__all__ = [
    "AnswerAggregateMetrics",
    "AnswerCaseMetric",
    "EvalCase",
    "EvalCaseResult",
    "EvalDataset",
    "EvaluationRun",
    "EvaluationRunConfig",
    "ExpectedSource",
    "RetrievalEvalCase",
    "RetrievalEvalDataset",
    "load_eval_dataset",
    "load_retrieval_eval_dataset",
    "RetrievalAggregateMetrics",
    "RetrievalCaseMetric",
    "aggregate_retrieval_metrics",
    "aggregate_answer_metrics",
    "expected_answer_status",
    "score_answer_case",
    "score_retrieval_case",
]
