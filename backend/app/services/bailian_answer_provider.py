"""位于 Provider 无关回答协议之后的阿里云百炼适配器。

只有本模块知道 SDK 和模型构造细节。回答编排和测试依赖 ``AnswerProvider``，
因此模型网络调用可替换。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import SecretStr, ValidationError

from app.core.config import get_settings
from app.services.answer_prompts import AnswerPrompt
from app.services.answering import AnswerDraft, AnswerProvider, GeneratedAnswer, GenerationUsage


class AnswerProviderError(RuntimeError):
    """The configured answer model is unavailable or returned an invalid payload."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        usage: GenerationUsage | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.usage = usage or GenerationUsage()


class BailianAnswerProvider:
    provider_name = "bailian"

    def __init__(
        self,
        *,
        model_name: str,
        base_url: str,
        api_key: SecretStr | None,
        temperature: float,
        enable_thinking: bool,
        timeout_seconds: float,
        max_retries: int,
        model: Any | None = None,
    ) -> None:
        self.model_name = model_name
        self.base_url = base_url
        self._api_key = api_key
        self.temperature = temperature
        self.enable_thinking = enable_thinking
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._model = model

    async def generate_answer(self, prompt: AnswerPrompt) -> GeneratedAnswer:
        model = self._get_model()
        try:
            response = await model.ainvoke(
                [SystemMessage(content=prompt.system), HumanMessage(content=prompt.user)]
            )
            content = response.content
            if not isinstance(content, str):
                raise AnswerProviderError(
                    "Answer model returned unsupported content",
                    code="unsupported_content",
                    usage=_read_usage(response),
                )
            payload = json.loads(content)
            draft = AnswerDraft.model_validate(payload)
        except AnswerProviderError:
            raise
        except json.JSONDecodeError as exc:
            raise AnswerProviderError(
                "Answer model returned invalid JSON",
                code="invalid_json",
                usage=_read_usage(response),
            ) from exc
        except ValidationError as exc:
            raise AnswerProviderError(
                "Answer model returned JSON that violates the answer schema",
                code="schema_validation",
                usage=_read_usage(response),
            ) from exc
        except Exception as exc:
            raise AnswerProviderError(
                "Answer model request failed",
                code="request_failed",
            ) from exc
        return GeneratedAnswer(draft=draft, usage=_read_usage(response))

    def _get_model(self):
        if self._model is None:
            # The bound client is safe to reuse across concurrent evaluation requests.
            self._model = self._build_model()
        return self._model

    def _build_model(self):
        if self._api_key is None or not self._api_key.get_secret_value().strip():
            raise AnswerProviderError(
                "Answer model is not configured",
                code="configuration_error",
            )
        model = ChatOpenAI(
            model=self.model_name,
            api_key=self._api_key,
            base_url=self.base_url,
            temperature=self.temperature,
            timeout=self.timeout_seconds,
            max_retries=self.max_retries,
            extra_body={"enable_thinking": self.enable_thinking},
        )
        return model.bind(response_format={"type": "json_object"})


def _read_usage(response: Any) -> GenerationUsage:
    usage = getattr(response, "usage_metadata", None) or {}
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if input_tokens is None or output_tokens is None:
        token_usage = getattr(response, "response_metadata", {}).get("token_usage", {})
        input_tokens = (
            input_tokens if input_tokens is not None else token_usage.get("prompt_tokens")
        )
        output_tokens = (
            output_tokens if output_tokens is not None else token_usage.get("completion_tokens")
        )
    return GenerationUsage(input_tokens=input_tokens, output_tokens=output_tokens)


def get_answer_provider() -> AnswerProvider:
    settings = get_settings()
    return BailianAnswerProvider(
        model_name=settings.answer_model_name,
        base_url=settings.answer_base_url,
        api_key=settings.dashscope_api_key,
        temperature=settings.answer_temperature,
        enable_thinking=settings.answer_enable_thinking,
        timeout_seconds=settings.answer_timeout_seconds,
        max_retries=settings.answer_max_retries,
    )
