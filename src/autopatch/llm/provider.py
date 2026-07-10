"""Swappable LLMProvider interface with Claude (default) and OpenAI backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from autopatch.config import Settings
from autopatch.tracing.logger import StructuredLogger, TokenUsage, estimate_cost_usd


@dataclass(frozen=True)
class LLMMessage:
    """A single chat message."""

    role: str  # system | user | assistant
    content: str


@dataclass(frozen=True)
class LLMResponse:
    """Normalized completion response across providers."""

    content: str
    model: str
    input_tokens: int
    output_tokens: int
    raw: Any = None

    @property
    def usage(self) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            model=self.model,
            cost_usd=estimate_cost_usd(self.model, self.input_tokens, self.output_tokens),
        )


class LLMProvider(ABC):
    """Provider-agnostic LLM interface used by the agent loop."""

    def __init__(self, model: str, logger: StructuredLogger | None = None) -> None:
        self.model = model
        self.logger = logger

    @abstractmethod
    def complete(
        self,
        messages: list[LLMMessage],
        *,
        purpose: str = "completion",
        max_tokens: int = 8192,
        temperature: float = 0.0,
        system: str | None = None,
    ) -> LLMResponse:
        """Run a single chat completion and return normalized text + usage."""

    def _record_usage(self, response: LLMResponse, purpose: str) -> None:
        if self.logger is not None:
            self.logger.log_model_call(
                model=response.model,
                purpose=purpose,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
            )


class ClaudeProvider(LLMProvider):
    """Anthropic Claude via the official SDK. Product default is Sonnet."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-6",
        logger: StructuredLogger | None = None,
    ) -> None:
        super().__init__(model=model, logger=logger)
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        purpose: str = "completion",
        max_tokens: int = 8192,
        temperature: float = 0.0,
        system: str | None = None,
    ) -> LLMResponse:
        system_text = system or ""
        api_messages: list[dict[str, str]] = []
        for msg in messages:
            if msg.role == "system":
                system_text = f"{system_text}\n{msg.content}".strip() if system_text else msg.content
                continue
            if msg.role not in {"user", "assistant"}:
                raise ValueError(f"Unsupported role for Claude: {msg.role}")
            api_messages.append({"role": msg.role, "content": msg.content})

        if not api_messages:
            raise ValueError("Claude complete() requires at least one user/assistant message")

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": api_messages,
        }
        # Sonnet 4.6 supports temperature; omit when 0 for deterministic-ish defaults
        if temperature > 0:
            kwargs["temperature"] = temperature
        if system_text:
            kwargs["system"] = system_text

        raw = self._client.messages.create(**kwargs)
        text_parts: list[str] = []
        for block in raw.content:
            if getattr(block, "type", None) == "text":
                text_parts.append(block.text)
        usage = raw.usage
        response = LLMResponse(
            content="".join(text_parts),
            model=self.model,
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            raw=raw,
        )
        self._record_usage(response, purpose)
        return response


class OpenAIProvider(LLMProvider):
    """OpenAI GPT-class models via the official SDK (swap path)."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4.1",
        logger: StructuredLogger | None = None,
    ) -> None:
        super().__init__(model=model, logger=logger)
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key)

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        purpose: str = "completion",
        max_tokens: int = 8192,
        temperature: float = 0.0,
        system: str | None = None,
    ) -> LLMResponse:
        api_messages: list[dict[str, str]] = []
        if system:
            api_messages.append({"role": "system", "content": system})
        for msg in messages:
            api_messages.append({"role": msg.role, "content": msg.content})

        raw = self._client.chat.completions.create(
            model=self.model,
            messages=api_messages,  # type: ignore[arg-type]
            max_tokens=max_tokens,
            temperature=temperature,
        )
        choice = raw.choices[0].message
        content = choice.content or ""
        usage = raw.usage
        response = LLMResponse(
            content=content,
            model=self.model,
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            raw=raw,
        )
        self._record_usage(response, purpose)
        return response


def create_provider(settings: Settings, logger: StructuredLogger | None = None) -> LLMProvider:
    """Factory: build the configured LLMProvider."""
    if settings.llm_provider == "claude":
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for llm_provider=claude")
        return ClaudeProvider(
            api_key=settings.anthropic_api_key,
            model=settings.llm_model,
            logger=logger,
        )
    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for llm_provider=openai")
        return OpenAIProvider(
            api_key=settings.openai_api_key,
            model=settings.llm_model,
            logger=logger,
        )
    raise ValueError(f"Unknown llm_provider: {settings.llm_provider}")
