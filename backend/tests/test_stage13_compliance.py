from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.assistant import get_intent_routing_service
from app.db.session import get_db_session
from app.evaluation.loader import load_eval_dataset
from app.main import app
from app.security.identity import AuthenticatedIdentity, get_current_identity
from app.services.bailian_answer_provider import get_answer_provider
from app.services.compliance_graph import ComplianceCheckResult, ComplianceCheckService
from app.services.embeddings import get_embedding_provider
from app.services.intent_routing import (
    IntentRoutingService,
    RoutingResult,
    RuleBasedIntentClassifier,
)
from app.services.reimbursement_detail import (
    ReimbursementDetailResult,
    ReimbursementDetailTool,
    ReimbursementDetailToolError,
)

EMPLOYEE_ID = UUID("11111111-1111-1111-1111-111111111111")
KB_ID = UUID("33333333-3333-3333-3333-333333333333")
STAGE13_DATASET = (
    Path(__file__).resolve().parents[2] / "data" / "evaluation" / "stage13_compliance_v0.json"
)


def identity() -> AuthenticatedIdentity:
    return AuthenticatedIdentity(
        user_id=EMPLOYEE_ID,
        scopes=frozenset({"all_employees", "finance_only"}),
        roles=frozenset({"employee"}),
    )


def detail_result(*, status: str = "under_review") -> ReimbursementDetailResult:
    return ReimbursementDetailResult(
        claim_number="BX-2026-0001",
        expense_date=date(2026, 5, 1),
        amount=720.0,
        currency="CNY",
        status=status,
        current_step="财务审核" if status != "not_found" else None,
        return_reason=None,
        message="detail",
    )


def policy_result(*, citations: tuple[object, ...]) -> SimpleNamespace:
    return SimpleNamespace(
        status="answered" if citations else "refused",
        answer="符合制度上限，但仍需人工复核附件。" if citations else "没有足够制度证据",
        citations=citations,
        missing_information=(),
    )


def test_stage13_dataset_is_reviewed_and_matches_route_baseline() -> None:
    dataset = load_eval_dataset(STAGE13_DATASET)
    assert dataset.dataset_version == "stage13-compliance-v0"
    assert dataset.annotation_status == "reviewed"
    assert len(dataset.cases) == 6
    classifier = RuleBasedIntentClassifier()
    for case in dataset.cases:
        assert classifier.classify(case.question).capability == case.expected_route


@pytest.mark.asyncio
async def test_detail_tool_adds_server_identity_filter_and_audit() -> None:
    reimbursement = SimpleNamespace(
        claim_number="BX-2026-0001",
        expense_date=date(2026, 5, 1),
        amount=720,
        currency="CNY",
        status="under_review",
        current_step="财务审核",
        return_reason=None,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = reimbursement
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = result

    output = await ReimbursementDetailTool().execute(
        session,
        identity(),
        reimbursement_number="BX-2026-0001",
    )

    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "reimbursements.employee_id" in sql
    assert output.amount == 720.0
    session.commit.assert_awaited_once()
    assert session.add.call_args.args[0].tool_name == "get_my_reimbursement_detail"


@pytest.mark.asyncio
async def test_compliance_graph_normal_path_keeps_fact_policy_and_trace_separate() -> None:
    detail_tool = AsyncMock()
    detail_tool.execute.return_value = detail_result()
    answer_service = AsyncMock()
    answer_service.answer.return_value = policy_result(citations=(object(),))
    service = ComplianceCheckService(answer_service=answer_service, detail_tool=detail_tool)

    result = await service.check(
        AsyncMock(spec=AsyncSession),
        identity(),
        MagicMock(),
        MagicMock(),
        knowledge_base_id=KB_ID,
        question="报销单 BX-2026-0001 是否符合住宿制度？",
        reimbursement_number="BX-2026-0001",
    )

    assert result.branch == "normal"
    assert result.status == "answered"
    assert result.detail is not None and result.detail.amount == 720.0
    assert result.policy_result is not None
    assert result.recommendation.startswith("制度证据已返回")
    assert result.trace == (
        "graph_started",
        "detail_lookup_started",
        "detail_loaded",
        "policy_lookup_completed",
        "evidence_verified",
        "result_finalized",
    )
    answer_service.answer.assert_awaited_once()
    assert answer_service.answer.await_args.kwargs["expense_date"] == date(2026, 5, 1)
    assert answer_service.answer.await_args.kwargs["allowed_scopes"] == identity().scopes


@pytest.mark.asyncio
async def test_compliance_graph_rejects_cross_user_or_missing_detail_without_policy_call() -> None:
    detail_tool = AsyncMock()
    detail_tool.execute.return_value = detail_result(status="not_found")
    answer_service = AsyncMock()
    service = ComplianceCheckService(answer_service=answer_service, detail_tool=detail_tool)

    result = await service.check(
        AsyncMock(spec=AsyncSession),
        identity(),
        MagicMock(),
        MagicMock(),
        knowledge_base_id=KB_ID,
        question="报销单 BX-2026-0001 是否合规？",
        reimbursement_number="BX-2026-0001",
    )

    assert result.branch == "not_found"
    assert result.status == "refused"
    answer_service.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_compliance_graph_refuses_when_policy_has_no_citations() -> None:
    detail_tool = AsyncMock()
    detail_tool.execute.return_value = detail_result()
    answer_service = AsyncMock()
    answer_service.answer.return_value = policy_result(citations=())
    service = ComplianceCheckService(answer_service=answer_service, detail_tool=detail_tool)

    result = await service.check(
        AsyncMock(spec=AsyncSession),
        identity(),
        MagicMock(),
        MagicMock(),
        knowledge_base_id=KB_ID,
        question="报销单 BX-2026-0001 是否合规？",
        reimbursement_number="BX-2026-0001",
    )

    assert result.branch == "evidence_insufficient"
    assert result.status == "refused"
    assert "没有足够" in result.answer


@pytest.mark.asyncio
async def test_compliance_graph_preserves_tool_failure_branch() -> None:
    detail_tool = AsyncMock()
    detail_tool.execute.side_effect = ReimbursementDetailToolError("database down")
    service = ComplianceCheckService(detail_tool=detail_tool)

    result = await service.check(
        AsyncMock(spec=AsyncSession),
        identity(),
        MagicMock(),
        MagicMock(),
        knowledge_base_id=KB_ID,
        question="报销单 BX-2026-0001 是否合规？",
        reimbursement_number="BX-2026-0001",
    )

    assert result.branch == "tool_failed"
    assert result.status == "failed"
    assert "没有生成合规结论" in result.answer


def test_rule_classifier_prioritizes_compliance_over_status_for_mixed_question() -> None:
    decision = RuleBasedIntentClassifier().classify(
        "我的报销单 BX-2026-0001 是否符合规定，能不能报销？"
    )
    assert decision.capability == "compliance_precheck"
    assert decision.arguments is not None
    assert decision.arguments.reimbursement_number == "BX-2026-0001"


@pytest.mark.asyncio
async def test_routing_compliance_does_not_put_identity_in_tool_arguments() -> None:
    compliance_service = AsyncMock()
    compliance_service.check.return_value = ComplianceCheckResult(
        branch="evidence_insufficient",
        status="refused",
        answer="没有足够制度证据",
        recommendation="补充制度材料",
        trace=("graph_started", "evidence_insufficient"),
    )
    service = IntentRoutingService(compliance_service=compliance_service)

    result = await service.route(
        AsyncMock(spec=AsyncSession),
        identity(),
        MagicMock(),
        MagicMock(),
        knowledge_base_id=KB_ID,
        question="报销单 BX-2026-0001 是否合规？",
        expense_date=None,
        top_k=5,
    )

    assert result.route == "compliance_precheck"
    assert result.compliance_result is not None
    assert result.tool_arguments == {"reimbursement_number": "BX-2026-0001"}
    assert "user_id" not in result.tool_arguments
    compliance_service.check.assert_awaited_once()


def test_assistant_api_returns_structured_compliance_branch() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = MagicMock()
    service = AsyncMock(spec=IntentRoutingService)
    service.route.return_value = RoutingResult(
        route="compliance_precheck",
        status="refused",
        answer="当前制度证据不足",
        compliance_result=ComplianceCheckResult(
            branch="evidence_insufficient",
            status="refused",
            answer="当前制度证据不足",
            recommendation="补充制度材料",
            detail=detail_result(),
            trace=("graph_started", "evidence_insufficient"),
        ),
        tool_name="get_my_reimbursement_detail",
        tool_arguments={"reimbursement_number": "BX-2026-0001"},
        tool_outcome="success",
    )

    async def override_db():
        yield session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_current_identity] = lambda: identity()
    app.dependency_overrides[get_embedding_provider] = lambda: MagicMock()
    app.dependency_overrides[get_answer_provider] = lambda: MagicMock()
    app.dependency_overrides[get_intent_routing_service] = lambda: service
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/knowledge-bases/{KB_ID}/assistant",
                json={"question": "报销单 BX-2026-0001 是否合规？"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["route"] == "compliance_precheck"
    assert payload["compliance"]["branch"] == "evidence_insufficient"
    assert payload["reimbursement"]["amount"] == 720.0
    assert payload["tool_call"]["arguments"] == {"reimbursement_number": "BX-2026-0001"}
