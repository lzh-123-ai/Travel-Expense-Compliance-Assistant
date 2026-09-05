"""Function Calling 的稳定契约。

模型可以选择工具并填写业务参数，但 schema 故意不提供 ``user_id``、SQL 或任意表名。
真正的身份和资源归属由调用工具的服务端上下文决定。
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_REIMBURSEMENT_NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]{2,12}[-_][A-Za-z0-9_-]{2,48}"
)


def extract_reimbursement_number(question: str) -> str | None:
    """只从用户原始问题提取报销单号，防止模型凭空生成查询目标。"""

    match = _REIMBURSEMENT_NUMBER_PATTERN.search(question)
    return match.group(0) if match else None


class ReimbursementStatusToolArguments(BaseModel):
    """只允许模型传入报销单号，禁止客户端或模型指定查询用户。"""

    model_config = ConfigDict(extra="forbid")

    reimbursement_number: str = Field(min_length=3, max_length=64)

    @field_validator("reimbursement_number")
    @classmethod
    def validate_number(cls, value: str) -> str:
        normalized = value.strip()
        allowed_characters = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
        if not normalized or any(
            character not in allowed_characters for character in normalized
        ):
            raise ValueError("Invalid reimbursement number")
        return normalized


class ReimbursementDetailToolArguments(ReimbursementStatusToolArguments):
    """明细工具复用同一报销单号校验，不扩张到用户身份或任意查询条件。"""


class ToolCallDecision(BaseModel):
    """能力分类器的结构化结果；模型适配器和规则分类器共用它。"""

    model_config = ConfigDict(extra="forbid")

    capability: Literal["policy_rag", "reimbursement_status", "compliance_precheck"]
    arguments: (
        ReimbursementStatusToolArguments
        | ReimbursementDetailToolArguments
        | None
    ) = None


REIMBURSEMENT_STATUS_TOOL_SCHEMA: dict[str, object] = {
    "type": "function",
    "function": {
        "name": "get_my_reimbursement_status",
        "description": "查询当前登录员工本人报销单的只读状态",
        "parameters": {
            "type": "object",
            "properties": {
                "reimbursement_number": {
                    "type": "string",
                    "description": "用户明确提供的报销单号",
                }
            },
            "required": ["reimbursement_number"],
            "additionalProperties": False,
        },
        "strict": True,
    },
}

REIMBURSEMENT_DETAIL_TOOL_SCHEMA: dict[str, object] = {
    "type": "function",
    "function": {
        "name": "get_my_reimbursement_detail",
        "description": "读取当前登录员工本人报销单的只读事实，用于合规预检查",
        "parameters": {
            "type": "object",
            "properties": {
                "reimbursement_number": {
                    "type": "string",
                    "description": "用户明确提供的报销单号",
                }
            },
            "required": ["reimbursement_number"],
            "additionalProperties": False,
        },
        "strict": True,
    },
}
