"""阿里云百炼 Function Calling 的路由适配器。

本模块只负责把模型返回的 ``tool_calls`` 转换成稳定的 ``ToolCallDecision``。
身份、权限、SQL 和工具执行仍在 ``IntentRoutingService`` 及领域工具中完成。
"""

from __future__ import annotations

import json
from typing import Any, Final, Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import SecretStr, ValidationError

from app.core.observability import estimated_model_cost, read_token_usage, trace_stage
from app.services.tool_contracts import (
    REIMBURSEMENT_DETAIL_TOOL_SCHEMA,
    REIMBURSEMENT_STATUS_TOOL_SCHEMA,
    ReimbursementDetailToolArguments,
    ReimbursementStatusToolArguments,
    ToolCallDecision,
    extract_reimbursement_number,
)


class ToolRoutingProviderError(RuntimeError):
    """模型工具调用不可用或返回了不可信的工具请求。"""


class ToolRoutingModel(Protocol):
    async def ainvoke(self, messages: list[Any]) -> Any: ...


class BailianIntentClassifier:
    """使用百炼模型选择能力，但不让模型执行工具或决定访问范围。"""

    _system_prompt = """你是企业差旅报销助手的能力选择器。
只能在以下规则中选择：
1. 用户询问制度、标准、材料或政策时，不调用工具，直接返回空 tool_calls。
2. 用户询问本人报销单进度时，调用 get_my_reimbursement_status。
3. 用户询问本人报销单是否合规、能否报销时，调用 get_my_reimbursement_detail。
工具参数只能填写用户明确提供的 reimbursement_number。
不要生成 user_id、SQL、权限、角色或其他参数。"""
    _status_markers: Final[tuple[str, ...]] = (
        "我的报销",
        "报销单状态",
        "审批状态",
        "审批进度",
        "到哪一步",
    )
    _compliance_markers: Final[tuple[str, ...]] = (
        "是否合规",
        "合规吗",
        "能不能报销",
        "是否可以报销",
        "符合规定",
        "符合制度",
    )

    def __init__(
        self,
        *,
        model_name: str,
        base_url: str,
        api_key: SecretStr | None,
        temperature: float,
        timeout_seconds: float,
        max_retries: int,
        model: ToolRoutingModel | None = None,
    ) -> None:
        self.model_name = model_name
        self.base_url = base_url
        self._api_key = api_key
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._model = model

    async def classify(self, question: str) -> ToolCallDecision:
        """将模型工具调用转成受限决策；无工具调用代表制度 RAG。"""

        try:
            with trace_stage(
                "model_request", provider="bailian", model=self.model_name
            ) as model_trace:
                response = await self._get_model().ainvoke(
                    [
                        SystemMessage(content=self._system_prompt),
                        HumanMessage(content=question),
                    ]
                )
                input_tokens, output_tokens = read_token_usage(response)
                model_trace["input_tokens"] = input_tokens
                model_trace["output_tokens"] = output_tokens
                model_trace["estimated_cost_usd"] = estimated_model_cost(
                    input_tokens, output_tokens
                )
            calls = list(getattr(response, "tool_calls", None) or [])
            if not calls:
                return self._fallback_for_missing_number(question)
            if len(calls) != 1:
                raise ToolRoutingProviderError("Model returned multiple tools")
            return self._decision_from_call(calls[0], question=question)
        except ToolRoutingProviderError:
            raise
        except (ValidationError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ToolRoutingProviderError("Model returned invalid tool arguments") from exc
        except Exception as exc:
            raise ToolRoutingProviderError("Tool routing model request failed") from exc

    @classmethod
    def _decision_from_call(cls, call: Any, *, question: str) -> ToolCallDecision:
        name = call.get("name") if isinstance(call, dict) else getattr(call, "name", None)
        raw_arguments = (
            call.get("args", {})
            if isinstance(call, dict)
            else getattr(call, "args", {})
        )
        if isinstance(raw_arguments, str):
            raw_arguments = json.loads(raw_arguments)
        if not isinstance(raw_arguments, dict):
            raise TypeError("Tool arguments must be an object")
        explicit_number = extract_reimbursement_number(question)
        if explicit_number is None:
            fallback = cls._fallback_for_missing_number(question)
            if fallback.capability == "policy_rag":
                # 纯制度问题误触发工具时，服务端收敛回 RAG，不能让模型制造查询目标。
                return fallback
        if name == "get_my_reimbursement_status":
            arguments = _validated_arguments(
                ReimbursementStatusToolArguments,
                raw_arguments,
                explicit_number=explicit_number,
            )
            return ToolCallDecision(capability="reimbursement_status", arguments=arguments)
        if name == "get_my_reimbursement_detail":
            arguments = _validated_arguments(
                ReimbursementDetailToolArguments,
                raw_arguments,
                explicit_number=explicit_number,
            )
            return ToolCallDecision(capability="compliance_precheck", arguments=arguments)
        raise ValueError("Unknown tool")

    @classmethod
    def _fallback_for_missing_number(cls, question: str) -> ToolCallDecision:
        """模型没发工具调用时，仍为缺少单号的本人业务问题保留追问语义。"""

        reimbursement_number = extract_reimbursement_number(question)
        if any(marker in question for marker in cls._compliance_markers):
            arguments = (
                ReimbursementDetailToolArguments(reimbursement_number=reimbursement_number)
                if reimbursement_number
                else None
            )
            return ToolCallDecision(capability="compliance_precheck", arguments=arguments)
        if any(marker in question for marker in cls._status_markers):
            arguments = (
                ReimbursementStatusToolArguments(reimbursement_number=reimbursement_number)
                if reimbursement_number
                else None
            )
            return ToolCallDecision(capability="reimbursement_status", arguments=arguments)
        return ToolCallDecision(capability="policy_rag")

    def _get_model(self) -> ToolRoutingModel:
        if self._model is None:
            self._model = self._build_model()
        return self._model

    def _build_model(self) -> ToolRoutingModel:
        if self._api_key is None or not self._api_key.get_secret_value().strip():
            raise ToolRoutingProviderError("Tool routing model is not configured")
        model = ChatOpenAI(
            model=self.model_name,
            api_key=self._api_key,
            base_url=self.base_url,
            temperature=self.temperature,
            timeout=self.timeout_seconds,
            max_retries=self.max_retries,
        )
        return model.bind_tools(
            [REIMBURSEMENT_STATUS_TOOL_SCHEMA, REIMBURSEMENT_DETAIL_TOOL_SCHEMA],
            tool_choice="auto",
        )


def _validated_arguments(
    argument_model: type[ReimbursementStatusToolArguments],
    raw_arguments: dict[str, object],
    *,
    explicit_number: str | None,
) -> ReimbursementStatusToolArguments | ReimbursementDetailToolArguments | None:
    """校验模型参数，并以用户原文中的单号作为唯一可信来源。"""

    if "reimbursement_number" not in raw_arguments:
        if raw_arguments:
            # 缺少单号也不能借机放入 user_id、SQL 等未声明参数。
            argument_model.model_validate(raw_arguments)
        if explicit_number is None:
            return None
        return argument_model(reimbursement_number=explicit_number)

    arguments = argument_model.model_validate(raw_arguments)
    if explicit_number is None:
        raise ToolRoutingProviderError(
            "Model supplied a reimbursement number that was not present in the question"
        )
    if arguments.reimbursement_number != explicit_number:
        raise ToolRoutingProviderError(
            "Model reimbursement number does not match the question"
        )
    return arguments
