"""Natural-language request → validated edit plan, with bounded self-repair.

The model never writes raw form brackets. It receives a compact JSON
description of the form (elements, pages, style passports) and returns a JSON
edit plan (see :mod:`edit_plan`). Every candidate plan is applied to a
throw-away copy of the form; on failure the exact error goes back to the model,
at most ``MAX_REPAIRS`` times.

Providers:
* ``openai``     - existing stdlib transport (:class:`ai.OpenAIResponsesAdapter`);
* ``anthropic``  - official ``anthropic`` SDK (optional extra ``[claude]``);
* ``recorded``   - offline adapter replaying responses from a file (tests, demos).
"""

from __future__ import annotations

import copy
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .ai import AIConfig, AIError, AIRequest, OpenAIResponsesAdapter, RecordedResponseAdapter
from .edit_plan import PlanError, apply_plan, describe_form
from .ordinary_form import OrdinaryForm

MAX_REPAIRS = 3
DEFAULT_MODELS = {"anthropic": "claude-opus-5-5"}
PROVIDER_ALIASES = {"claude": "anthropic"}
KNOWN_PROVIDERS = ("anthropic", "openai")

SYSTEM_PROMPT = """Ты помощник разработчика 1С. Ты правишь ОБЫЧНУЮ форму 1С (толстый клиент, 8.3),
описанную JSON-объектом FORM. Твоя задача - вернуть ТОЛЬКО JSON-план правок, без пояснений и без markdown.

Формат плана:
{"operations": [ ... ]}

Операции:
- {"op":"add","type":"Label|Field|Button|CheckBox|Group","name":"ИмяЭлемента", ...необязательно:
   "caption":"текст" (Label/Button/CheckBox/Group), "label":"Подпись:" (для Field/Button: создаёт надпись
   "Надпись<Имя>" и элемент в одной новой строке), "tooltip":"подсказка",
   "value_type":"Строка(50)|Число(15,2)|Дата|ДатаВремя|Булево|like:<ИмяПоля>" (только Field),
   "data":"<ИмяРеквизита>" (Field/CheckBox: привязать к реквизиту объекта из FORM.object_attributes;
   тип поля берётся из реквизита, value_type тогда не указывай; CheckBox - только к Булево),
   "handler":"ИмяПроцедуры" (основное событие: Нажатие для Button/Label, ПриИзменении для Field/CheckBox),
   "events":{"<Событие>":"<ИмяПроцедуры>"} (события из FORM.events; заглушки процедур добавятся в модуль),
   "container":"<ИмяПанели>","page":"<ИмяСтраницы>" (по умолчанию - сама форма),
   "beside":"<ИмяЭлемента>" (поставить справа от элемента в той же строке),
   "left","top","width","height" (целые, пиксели; обычно НЕ указывай - размещение и размеры
   выбираются автоматически в стиле формы)}
- {"op":"modify","name":"<ИмяЭлемента>", "caption"?, "tooltip"?, "value_type"?, "handler"?, "data"?, "events"?,
   "left"?, "top"?, "width"?, "height"?}
- {"op":"remove","name":"<ИмяЭлемента>"} (если модуль ссылается на элемент, операция будет отклонена;
   "force":true ставь только по прямой просьбе пользователя)
- {"op":"set_title","title":"Заголовок формы"}

Правила:
- Имена - идентификаторы 1С (буквы, цифры, _; не с цифры), уникальные в форме.
- Не трогай элементы, о которых не просили. Не удаляй элементы без прямой просьбы.
- Нельзя добавлять таблицы и панели. Если пользователь просит поле для реквизита объекта, используй "data".
- Если просьбу нельзя выполнить этими операциями, верни {"operations": [], "reason": "почему"}.
"""


@dataclass
class PlanAttempt:
    response: str
    error: str | None = None


@dataclass
class PlanResult:
    operations: list[dict[str, Any]]
    results: list[dict[str, Any]]
    attempts: list[PlanAttempt] = field(default_factory=list)


def anthropic_missing_message() -> str:
    """Why the Claude provider is unavailable; a frozen build never bundles the Anthropic SDK."""
    if getattr(sys, "frozen", False):
        return ("this build does not bundle the Anthropic SDK; use ONE_C_FORM_AI_PROVIDER=openai "
                "or install from source: pip install \".[claude]\"")
    return "provider 'anthropic' needs the SDK: pip install anthropic"


class AnthropicMessagesAdapter:
    """Claude via the official SDK. Install with ``pip install anthropic``."""

    name = "anthropic-messages"

    def __init__(self, client_factory: Callable[..., Any] | None = None) -> None:
        self._client_factory = client_factory

    def complete(self, request: AIRequest, *, api_key: str | None) -> str:
        try:
            import anthropic
        except ImportError:
            raise AIError("E_AI_PROVIDER_UNSUPPORTED", anthropic_missing_message()) from None
        factory = self._client_factory or anthropic.Anthropic
        client = factory(api_key=api_key, timeout=request.timeout_seconds) if api_key else \
            factory(timeout=request.timeout_seconds)
        try:
            response = client.beta.messages.create(
                model=request.model,
                max_tokens=request.max_output_tokens,
                system=request.instructions,
                messages=[{"role": "user", "content": request.user_content}],
                output_config={"effort": "medium"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError:
            raise AIError("E_AI_AUTH", "Anthropic rejected the configured credential") from None
        except anthropic.RateLimitError:
            raise AIError("E_AI_RATE_LIMIT", "Anthropic rate limit response") from None
        except anthropic.APIStatusError as error:
            raise AIError("E_AI_HTTP", f"Anthropic returned HTTP status {error.status_code}") from None
        except anthropic.APIConnectionError:
            raise AIError("E_AI_TRANSPORT", "Anthropic transport failed") from None
        if response.stop_reason == "refusal":
            raise AIError("E_AI_REFUSAL", "Claude declined the request")
        if response.stop_reason == "max_tokens":
            raise AIError("E_AI_RESPONSE_INCOMPLETE", "Claude response hit max_tokens")
        text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
        if len(text.encode("utf-8")) > request.max_output_bytes:
            raise AIError("E_AI_OUTPUT_LIMIT", "Claude text exceeded the configured output limit")
        return text


class ScriptedAdapter:
    """Offline adapter returning prepared responses in order (tests, demos)."""

    name = "recorded-offline"

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.requests: list[AIRequest] = []

    def complete(self, request: AIRequest, *, api_key: str | None) -> str:
        self.requests.append(request)
        if not self.responses:
            raise AIError("E_AI_RESPONSE_INVALID", "recorded responses exhausted")
        return self.responses.pop(0)


def normalize_provider(provider: str) -> str:
    """Lower-case provider name with aliases resolved (``claude`` -> ``anthropic``)."""
    key = provider.strip().casefold()
    return PROVIDER_ALIASES.get(key, key)


def config_from_env() -> AIConfig:
    raw = os.environ.get("ONE_C_FORM_AI_PROVIDER", "").strip()
    if not raw:
        raise AIError("E_AI_CONFIG_INVALID", "set ONE_C_FORM_AI_PROVIDER (anthropic | openai)")
    provider = normalize_provider(raw)
    if provider not in KNOWN_PROVIDERS:
        raise AIError("E_AI_CONFIG_INVALID", f"unknown ONE_C_FORM_AI_PROVIDER {raw!r}; use anthropic or openai")
    model = os.environ.get("ONE_C_FORM_AI_MODEL", "").strip() or DEFAULT_MODELS.get(provider, "")
    if not model:
        raise AIError("E_AI_CONFIG_INVALID", f"set ONE_C_FORM_AI_MODEL for provider {provider}")
    # AIConfig.from_env validates the remaining settings; give it the resolved provider and model
    # for the duration of the call without leaving them in the environment.
    saved = {name: os.environ.get(name) for name in ("ONE_C_FORM_AI_PROVIDER", "ONE_C_FORM_AI_MODEL")}
    os.environ["ONE_C_FORM_AI_PROVIDER"] = provider
    os.environ["ONE_C_FORM_AI_MODEL"] = model
    try:
        config = AIConfig.from_env()
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    if config.max_output_tokens < 8_000 and "ONE_C_FORM_AI_MAX_OUTPUT_TOKENS" not in os.environ:
        config = AIConfig(**{**{f: getattr(config, f) for f in config.__dataclass_fields__},
                             "max_output_tokens": 16_000, "timeout_seconds": max(config.timeout_seconds, 120.0)})
    return config


def adapter_for(config: AIConfig):
    provider = normalize_provider(config.provider)
    if provider == "openai":
        return OpenAIResponsesAdapter()
    if provider in {"anthropic", "claude"}:
        return AnthropicMessagesAdapter()
    raise AIError("E_AI_PROVIDER_UNSUPPORTED", f"unknown provider {config.provider!r}; use anthropic or openai")


def _api_key(config: AIConfig, adapter) -> str | None:
    if isinstance(adapter, (ScriptedAdapter, RecordedResponseAdapter)):
        return None
    env = config.api_key_env or ("ANTHROPIC_API_KEY" if isinstance(adapter, AnthropicMessagesAdapter)
                                 else "OPENAI_API_KEY")
    key = os.environ.get(env)
    if not key and isinstance(adapter, AnthropicMessagesAdapter):
        return None          # SDK resolves its own credentials (env / ant auth profile)
    if not key:
        raise AIError("E_AI_CREDENTIAL_MISSING", f"API key variable {env} is empty")
    return key


def extract_json(text: str) -> str:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        return fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start != -1 and end > start else text


def generate_plan(form: OrdinaryForm, user_request: str, *, config: AIConfig, adapter,
                  max_repairs: int = MAX_REPAIRS) -> PlanResult:
    if not user_request.strip():
        raise AIError("E_AI_REQUEST_INVALID", "request text is empty")
    context = json.dumps(describe_form(form), ensure_ascii=False, separators=(",", ":"))
    if len(context.encode("utf-8")) > config.max_input_bytes:
        raise AIError("E_AI_INPUT_LIMIT", "form description exceeds ONE_C_FORM_AI_MAX_INPUT_BYTES")
    api_key = _api_key(config, adapter)
    attempts: list[PlanAttempt] = []
    conversation = f"FORM:\n{context}\n\nЗАПРОС:\n{user_request.strip()}"
    for _attempt in range(max_repairs + 1):
        request = AIRequest(provider=config.provider, model=config.model, mode="plan", context_xml="",
                            timeout_seconds=config.timeout_seconds, max_output_bytes=config.max_output_bytes,
                            instructions=SYSTEM_PROMPT, user_content=conversation,
                            max_input_bytes=config.max_input_bytes, max_output_tokens=config.max_output_tokens)
        text = adapter.complete(request, api_key=api_key)
        attempt = PlanAttempt(text)
        attempts.append(attempt)
        try:
            payload = json.loads(extract_json(text))
            if isinstance(payload, dict) and payload.get("operations") == []:
                raise AIError("E_AI_DECLINED", "model could not express the request: "
                              + str(payload.get("reason", "no reason given"))[:300])
            trial = copy.deepcopy(form)
            results = apply_plan(trial, payload)
            operations = payload["operations"] if isinstance(payload, dict) else payload
            return PlanResult(operations, results, attempts)
        except (ValueError, PlanError) as error:
            if isinstance(error, AIError):
                raise
            attempt.error = str(error)
            conversation += (f"\n\nТВОЙ ПРЕДЫДУЩИЙ ОТВЕТ:\n{text.strip()[:4000]}\n\n"
                             f"ОШИБКА ПРИ ПРИМЕНЕНИИ: {error}\nИсправь план и верни полный JSON-план заново.")
    raise AIError("E_AI_REPAIR_EXHAUSTED",
                  f"no valid plan after {max_repairs} repairs; last error: {attempts[-1].error}")


def load_recorded(path: Path) -> ScriptedAdapter:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    responses = data if isinstance(data, list) else [data]
    return ScriptedAdapter([item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
                            for item in responses])
