import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.assistant import get_intent_routing_service
from app.db.session import get_db_session
from app.evaluation.loader import load_eval_dataset
from app.main import app
from app.security.identity import (
    AuthenticatedIdentity,
    IdentityTokenError,
    decode_access_token,
    encode_access_token,
    get_current_identity,
)
from app.services.bailian_answer_provider import get_answer_provider
from app.services.bailian_tool_provider import (
    BailianIntentClassifier,
    ToolRoutingProviderError,
)
from app.services.embeddings import get_embedding_provider
from app.services.intent_routing import (
    IntentRoutingService,
    RoutingResult,
    RuleBasedIntentClassifier,
)
from app.services.reimbursement_status import (
    ReimbursementStatusResult,
    ReimbursementStatusTool,
)
from app.services.tool_contracts import (
    REIMBURSEMENT_STATUS_TOOL_SCHEMA,
    ReimbursementStatusToolArguments,
)

EMPLOYEE_ID = UUID("11111111-1111-1111-1111-111111111111")
OTHER_EMPLOYEE_ID = UUID("22222222-2222-2222-2222-222222222222")
KB_ID = UUID("33333333-3333-3333-3333-333333333333")
STAGE12_DATASET = (
    Path(__file__).resolve().parents[2] / "data" / "evaluation" / "stage12_tool_routing_v0.json"
)


def identity(user_id: UUID = EMPLOYEE_ID) -> AuthenticatedIdentity:
    return AuthenticatedIdentity(
        user_id=user_id,
        scopes=frozenset({"all_employees", "finance_only"}),
        roles=frozenset({"employee"}),
    )


def test_jwt_round_trip_rejects_expired_token() -> None:
    token = encode_access_token(identity(), secret="stage12-test-secret")
    decoded = decode_access_token(token, secret="stage12-test-secret")
    assert decoded == identity()

    expired = encode_access_token(
        identity(), secret="stage12-test-secret", expires_in=timedelta(seconds=-1)
    )
    with pytest.raises(IdentityTokenError, match="expired"):
        decode_access_token(expired, secret="stage12-test-secret")


def test_tool_schema_forbids_client_identity_and_arbitrary_arguments() -> None:
    arguments = ReimbursementStatusToolArguments(reimbursement_number="BX-2026-0001")
    assert arguments.reimbursement_number == "BX-2026-0001"
    with pytest.raises(ValidationError):
        ReimbursementStatusToolArguments.model_validate(
            {"reimbursement_number": "BX-2026-0001", "user_id": str(EMPLOYEE_ID)}
        )
    parameters = REIMBURSEMENT_STATUS_TOOL_SCHEMA["function"]["parameters"]
    assert parameters["additionalProperties"] is False
    assert "user_id" not in parameters["properties"]


class _FakeToolModel:
    def __init__(self, tool_calls: list[dict[str, object]]) -> None:
        self.tool_calls = tool_calls

    async def ainvoke(self, messages: list[object]):
        return SimpleNamespace(tool_calls=self.tool_calls)


@pytest.mark.asyncio
async def test_bailian_tool_classifier_converts_status_and_detail_calls() -> None:
    status_classifier = BailianIntentClassifier(
        model_name="qwen-plus",
        base_url="https://example.invalid",
        api_key=None,
        temperature=0,
        timeout_seconds=1,
        max_retries=0,
        model=_FakeToolModel(
            [
                {
                    "name": "get_my_reimbursement_status",
                    "args": {"reimbursement_number": "BX-2026-0001"},
                }
            ]
        ),
    )
    detail_classifier = BailianIntentClassifier(
        model_name="qwen-plus",
        base_url="https://example.invalid",
        api_key=None,
        temperature=0,
        timeout_seconds=1,
        max_retries=0,
        model=_FakeToolModel(
            [
                {
                    "name": "get_my_reimbursement_detail",
                    "args": {"reimbursement_number": "BX-2026-0001"},
                }
            ]
        ),
    )

    status = await status_classifier.classify("我的报销单 BX-2026-0001 到哪一步了？")
    detail = await detail_classifier.classify("我的报销单 BX-2026-0001 是否合规？")
    assert status.capability == "reimbursement_status"
    assert detail.capability == "compliance_precheck"
    assert status.arguments is not None
    assert detail.arguments is not None


@pytest.mark.asyncio
async def test_bailian_tool_classifier_uses_no_call_for_policy_and_rejects_tampering() -> None:
    policy_classifier = BailianIntentClassifier(
        model_name="qwen-plus",
        base_url="https://example.invalid",
        api_key=None,
        temperature=0,
        timeout_seconds=1,
        max_retries=0,
        model=_FakeToolModel([]),
    )
    tampered_classifier = BailianIntentClassifier(
        model_name="qwen-plus",
        base_url="https://example.invalid",
        api_key=None,
        temperature=0,
        timeout_seconds=1,
        max_retries=0,
        model=_FakeToolModel(
            [
                {
                    "name": "get_my_reimbursement_status",
                    "args": {
                        "reimbursement_number": "BX-2026-0001",
                        "user_id": str(EMPLOYEE_ID),
                    },
                }
            ]
        ),
    )

    policy = await policy_classifier.classify("上海住宿费标准是多少？")
    assert policy.capability == "policy_rag"
    with pytest.raises(ToolRoutingProviderError, match="invalid tool arguments"):
        await tampered_classifier.classify("查我的报销单")


@pytest.mark.asyncio
async def test_bailian_tool_classifier_falls_back_to_policy_for_spurious_tool_call() -> None:
    classifier = BailianIntentClassifier(
        model_name="qwen-plus",
        base_url="https://example.invalid",
        api_key=None,
        temperature=0,
        timeout_seconds=1,
        max_retries=0,
        model=_FakeToolModel(
            [
                {
                    "name": "get_my_reimbursement_status",
                    "args": {"reimbursement_number": "BX-2026-0001"},
                }
            ]
        ),
    )

    decision = await classifier.classify("上海住宿费标准是多少？")

    assert decision.capability == "policy_rag"
    assert decision.arguments is None


@pytest.mark.asyncio
async def test_bailian_tool_classifier_keeps_missing_number_as_server_side_clarification() -> None:
    status_classifier = BailianIntentClassifier(
        model_name="qwen-plus",
        base_url="https://example.invalid",
        api_key=None,
        temperature=0,
        timeout_seconds=1,
        max_retries=0,
        model=_FakeToolModel([]),
    )
    compliance_classifier = BailianIntentClassifier(
        model_name="qwen-plus",
        base_url="https://example.invalid",
        api_key=None,
        temperature=0,
        timeout_seconds=1,
        max_retries=0,
        model=_FakeToolModel([]),
    )

    status = await status_classifier.classify("我的报销审批状态是什么？")
    compliance = await compliance_classifier.classify("我的报销单是否合规？")

    assert status.capability == "reimbursement_status"
    assert status.arguments is None
    assert compliance.capability == "compliance_precheck"
    assert compliance.arguments is None


@pytest.mark.asyncio
async def test_bailian_tool_classifier_rejects_invented_or_mismatched_claim_number() -> None:
    invented_classifier = BailianIntentClassifier(
        model_name="qwen-plus",
        base_url="https://example.invalid",
        api_key=None,
        temperature=0,
        timeout_seconds=1,
        max_retries=0,
        model=_FakeToolModel(
            [
                {
                    "name": "get_my_reimbursement_detail",
                    "args": {"reimbursement_number": "BX-2026-0001"},
                }
            ]
        ),
    )
    mismatched_classifier = BailianIntentClassifier(
        model_name="qwen-plus",
        base_url="https://example.invalid",
        api_key=None,
        temperature=0,
        timeout_seconds=1,
        max_retries=0,
        model=_FakeToolModel(
            [
                {
                    "name": "get_my_reimbursement_status",
                    "args": {"reimbursement_number": "BX-2026-9999"},
                }
            ]
        ),
    )

    with pytest.raises(ToolRoutingProviderError, match="not present"):
        await invented_classifier.classify("我的报销单是否合规？")
    with pytest.raises(ToolRoutingProviderError, match="does not match"):
        await mismatched_classifier.classify("查询报销单 BX-2026-0001 的状态")


def test_rule_classifier_routes_status_without_requiring_expense_date() -> None:
    decision = RuleBasedIntentClassifier().classify("我的报销单 BX-2026-0001 到哪一步了？")
    assert decision.capability == "reimbursement_status"
    assert decision.arguments is not None
    assert decision.arguments.reimbursement_number == "BX-2026-0001"

    policy = RuleBasedIntentClassifier().classify("上海住宿费标准是多少？")
    assert policy.capability == "policy_rag"


def test_stage12_routing_dataset_is_versioned_and_matches_rule_baseline() -> None:
    dataset = load_eval_dataset(STAGE12_DATASET)
    assert dataset.dataset_version == "stage12-tool-v0"
    assert dataset.annotation_status == "reviewed"
    assert len(dataset.cases) == 6
    classifier = RuleBasedIntentClassifier()
    for case in dataset.cases:
        assert classifier.classify(case.question).capability == case.expected_route
    assert json.loads(STAGE12_DATASET.read_text(encoding="utf-8"))["cases"][5][
        "expected_arguments"
    ] == {"reimbursement_number": "BX-2026-0001"}


@pytest.mark.asyncio
async def test_status_tool_adds_server_identity_filter_and_audit() -> None:
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

    output = await ReimbursementStatusTool().execute(
        session,
        identity(),
        reimbursement_number="BX-2026-0001",
    )

    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "reimbursements.employee_id" in sql
    assert output.status == "under_review"
    assert output.current_step == "财务审核"
    session.commit.assert_awaited_once()
    audit = session.add.call_args.args[0]
    assert audit.actor_id == EMPLOYEE_ID
    assert audit.input_summary == {"reimbursement_number": "BX-2026-0001"}


@pytest.mark.asyncio
async def test_status_tool_does_not_reveal_another_users_claim() -> None:
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = result

    output = await ReimbursementStatusTool().execute(
        session,
        identity(OTHER_EMPLOYEE_ID),
        reimbursement_number="BX-2026-0001",
    )

    assert output.status == "not_found"
    assert "当前登录用户" in output.message
    assert session.commit.await_count == 1


@pytest.mark.asyncio
async def test_routing_status_skips_answer_service_and_date_gate() -> None:
    answer_service = AsyncMock()
    tool = AsyncMock()
    tool.name = "get_my_reimbursement_status"
    tool.execute.return_value = ReimbursementStatusResult(
        claim_number="BX-2026-0001",
        status="under_review",
        message="报销单正在财务审核",
        current_step="财务审核",
    )
    service = IntentRoutingService(answer_service=answer_service, reimbursement_tool=tool)

    result = await service.route(
        AsyncMock(spec=AsyncSession),
        identity(),
        MagicMock(),
        MagicMock(),
        knowledge_base_id=KB_ID,
        question="我的报销单 BX-2026-0001 到哪一步了？",
        expense_date=None,
        top_k=5,
    )

    assert result.route == "reimbursement_status"
    assert result.status == "answered"
    answer_service.answer.assert_not_awaited()
    tool.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_routing_policy_passes_server_scopes_to_answer_service() -> None:
    answer_service = AsyncMock()
    answer_service.answer.return_value = SimpleNamespace(
        status="needs_clarification",
        answer="请提供费用发生日期",
        missing_information=("expense_date",),
        citations=(),
        warnings=(),
        version_conflicts=(),
    )
    service = IntentRoutingService(answer_service=answer_service)

    result = await service.route(
        AsyncMock(spec=AsyncSession),
        identity(),
        MagicMock(),
        MagicMock(),
        knowledge_base_id=KB_ID,
        question="上海住宿费标准是多少？",
        expense_date=None,
        top_k=5,
    )

    assert result.route == "policy_rag"
    assert answer_service.answer.await_args.kwargs["allowed_scopes"] == identity().scopes


def test_assistant_api_exposes_route_and_passes_identity_context() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = MagicMock()
    service = AsyncMock(spec=IntentRoutingService)
    service.route.return_value = RoutingResult(
        route="reimbursement_status",
        status="answered",
        answer="报销单正在财务审核",
        reimbursement_result=ReimbursementStatusResult(
            claim_number="BX-2026-0001",
            status="under_review",
            message="报销单正在财务审核",
            current_step="财务审核",
        ),
        tool_name="get_my_reimbursement_status",
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
                json={"question": "我的报销单 BX-2026-0001 到哪一步了？"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["route"] == "reimbursement_status"
    assert service.route.await_args.args[1].user_id == EMPLOYEE_ID
