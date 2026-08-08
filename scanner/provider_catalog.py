from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class CatalogVariant:
    variant_id: str | None
    display_name: str
    model_ids: tuple[str, ...]
    reasoning_efforts: tuple[str, ...] = ()
    default_reasoning_effort: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "variant_id": self.variant_id,
            "display_name": self.display_name,
            "model_ids": list(self.model_ids),
            "reasoning_efforts": list(self.reasoning_efforts),
            "default_reasoning_effort": self.default_reasoning_effort,
        }


@dataclass(frozen=True)
class CatalogFamily:
    family_id: str
    display_name: str
    variants: tuple[CatalogVariant, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "family_id": self.family_id,
            "display_name": self.display_name,
            "variants": [variant.to_dict() for variant in self.variants],
        }


@dataclass(frozen=True)
class CatalogProvider:
    provider_id: str
    display_name: str
    provider_preset: str
    default_base_url: str | None = None
    base_url_hosts: tuple[str, ...] = ()
    families: tuple[CatalogFamily, ...] = ()
    default_api_format: str = "openai_chat_completions"
    default_model_ids: tuple[str, ...] = ()
    website_url: str | None = None
    api_key_url: str | None = None
    connection_supported: bool = True
    availability_note: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "provider_preset": self.provider_preset,
            "default_base_url": self.default_base_url,
            "base_url_hosts": list(self.base_url_hosts),
            "featured": self.provider_id in FEATURED_PROVIDER_IDS,
            "default_api_format": self.default_api_format,
            "default_model_ids": list(self.default_model_ids),
            "website_url": self.website_url,
            "api_key_url": self.api_key_url,
            "connection_supported": self.connection_supported,
            "availability_note": self.availability_note,
            "families": [family.to_dict() for family in self.families],
        }


@dataclass(frozen=True)
class ResolvedConnectionCatalog:
    provider_id: str
    provider_display_name: str
    auth_mode: str
    catalog_source: str


@dataclass(frozen=True)
class ResolvedCandidateCatalogIdentity:
    family_id: str | None = None
    variant_id: str | None = None


_PROVIDER_CATALOG: tuple[CatalogProvider, ...] = (
    CatalogProvider(
        provider_id="deepseek",
        display_name="DeepSeek",
        provider_preset="generic",
        default_base_url="https://api.deepseek.com",
        base_url_hosts=("api.deepseek.com",),
        default_model_ids=("deepseek-v4-flash", "deepseek-v4-pro"),
        website_url="https://platform.deepseek.com/",
        api_key_url="https://platform.deepseek.com/api_keys",
        families=(
            CatalogFamily(
                family_id="deepseek-v4",
                display_name="DeepSeek V4",
                variants=(
                    CatalogVariant(
                        variant_id="flash",
                        display_name="Flash",
                        model_ids=("deepseek-v4-flash",),
                        reasoning_efforts=("low", "high", "max"),
                        default_reasoning_effort="high",
                    ),
                    CatalogVariant(
                        variant_id="pro",
                        display_name="Pro",
                        model_ids=("deepseek-v4-pro",),
                        reasoning_efforts=("high", "max"),
                        default_reasoning_effort="high",
                    ),
                ),
            ),
        ),
    ),
    CatalogProvider(
        provider_id="openai",
        display_name="OpenAI",
        provider_preset="generic",
        default_base_url="https://api.openai.com/v1",
        base_url_hosts=("api.openai.com",),
        default_api_format="openai_responses",
        default_model_ids=("gpt-5.4",),
        website_url="https://platform.openai.com/",
        api_key_url="https://platform.openai.com/api-keys",
        families=(
            CatalogFamily(
                family_id="gpt-5.4",
                display_name="GPT-5.4",
                variants=(CatalogVariant(None, "GPT-5.4", ("gpt-5.4",)),),
            ),
            CatalogFamily(
                family_id="gpt-5.5",
                display_name="GPT-5.5",
                variants=(CatalogVariant(None, "GPT-5.5", ("gpt-5.5",)),),
            ),
            CatalogFamily(
                family_id="gpt-5.6-sol",
                display_name="GPT-5.6 Sol",
                variants=(CatalogVariant(None, "GPT-5.6 Sol", ("gpt-5.6-sol",)),),
            ),
            CatalogFamily(
                family_id="gpt-5.6-luna",
                display_name="GPT-5.6 Luna",
                variants=(CatalogVariant(None, "GPT-5.6 Luna", ("gpt-5.6-luna",)),),
            ),
            CatalogFamily(
                family_id="gpt-5.6-terra",
                display_name="GPT-5.6 Terra",
                variants=(CatalogVariant(None, "GPT-5.6 Terra", ("gpt-5.6-terra",)),),
            ),
        ),
    ),
    CatalogProvider(
        provider_id="xai",
        display_name="Grok API",
        provider_preset="generic",
        default_base_url="https://api.x.ai/v1",
        base_url_hosts=("api.x.ai",),
        default_api_format="openai_responses",
        default_model_ids=("grok-4.5",),
        website_url="https://x.ai/",
        api_key_url="https://console.x.ai/team/default/api-keys",
        families=(
            CatalogFamily(
                family_id="grok-4.5",
                display_name="Grok 4.5",
                variants=(CatalogVariant(None, "Grok 4.5", ("grok-4.5",)),),
            ),
        ),
    ),
    CatalogProvider(
        provider_id="openrouter",
        display_name="OpenRouter",
        provider_preset="openrouter",
        default_base_url="https://openrouter.ai/api/v1",
        base_url_hosts=("openrouter.ai",),
        website_url="https://openrouter.ai/",
        api_key_url="https://openrouter.ai/keys",
    ),
    CatalogProvider(
        provider_id="anthropic",
        display_name="Anthropic",
        provider_preset="generic",
        default_base_url="https://api.anthropic.com",
        base_url_hosts=("api.anthropic.com",),
        website_url="https://console.anthropic.com/",
        connection_supported=False,
        availability_note="原生协议适配器待接入",
        families=(
            CatalogFamily(
                family_id="claude-sonnet-4",
                display_name="Claude Sonnet 4",
                variants=(CatalogVariant(None, "Claude Sonnet 4", ("claude-sonnet-4",)),),
            ),
        ),
    ),
    CatalogProvider(
        provider_id="gemini",
        display_name="Gemini",
        provider_preset="generic",
        default_base_url="https://generativelanguage.googleapis.com",
        base_url_hosts=("generativelanguage.googleapis.com",),
        website_url="https://aistudio.google.com/",
        connection_supported=False,
        availability_note="原生协议适配器待接入",
        families=(
            CatalogFamily(
                family_id="gemini-2.5-pro",
                display_name="Gemini 2.5 Pro",
                variants=(CatalogVariant(None, "Gemini 2.5 Pro", ("gemini-2.5-pro",)),),
            ),
            CatalogFamily(
                family_id="gemini-2.5-flash",
                display_name="Gemini 2.5 Flash",
                variants=(CatalogVariant(None, "Gemini 2.5 Flash", ("gemini-2.5-flash",)),),
            ),
        ),
    ),
    CatalogProvider(
        provider_id="moonshot",
        display_name="Moonshot",
        provider_preset="generic",
        default_base_url="https://api.moonshot.cn/v1",
        base_url_hosts=("api.moonshot.cn", "api.moonshot.ai", "api.kimi.com"),
        default_model_ids=("kimi-k2.7-code",),
        website_url="https://platform.moonshot.cn/",
        families=(
            CatalogFamily(
                family_id="kimi-for-coding",
                display_name="Kimi For Coding",
                variants=(CatalogVariant(None, "Kimi For Coding", ("kimi-for-coding",)),),
            ),
            CatalogFamily(
                family_id="kimi-k2.7-code",
                display_name="Kimi K2.7 Code",
                variants=(CatalogVariant(None, "Kimi K2.7 Code", ("kimi-k2.7-code",)),),
            ),
        ),
    ),
    CatalogProvider(
        provider_id="zhipu",
        display_name="Zhipu",
        provider_preset="generic",
        default_base_url="https://open.bigmodel.cn/api/coding/paas/v4",
        base_url_hosts=("open.bigmodel.cn",),
        default_model_ids=("glm-5.2",),
        website_url="https://open.bigmodel.cn/",
        families=(
            CatalogFamily(
                family_id="glm-5.2",
                display_name="GLM-5.2",
                variants=(CatalogVariant(None, "GLM-5.2", ("glm-5.2",)),),
            ),
            CatalogFamily(
                family_id="glm-5.1",
                display_name="GLM-5.1",
                variants=(CatalogVariant(None, "GLM-5.1", ("glm-5.1",)),),
            ),
        ),
    ),
    CatalogProvider(
        provider_id="z-ai",
        display_name="Z.ai",
        provider_preset="generic",
        default_base_url="https://api.z.ai/api/coding/paas/v4",
        base_url_hosts=("api.z.ai",),
        default_model_ids=("glm-5.1",),
        website_url="https://z.ai/",
        families=(
            CatalogFamily(
                family_id="glm-5.1",
                display_name="GLM-5.1",
                variants=(CatalogVariant(None, "GLM-5.1", ("glm-5.1",)),),
            ),
        ),
    ),
    CatalogProvider(
        provider_id="minimax",
        display_name="MiniMax",
        provider_preset="generic",
        default_base_url="https://api.minimaxi.com/anthropic",
        base_url_hosts=("api.minimaxi.com",),
        website_url="https://platform.minimaxi.com/",
        connection_supported=False,
        availability_note="原生协议适配器待接入",
        families=(
            CatalogFamily(
                family_id="minimax-m2.5",
                display_name="MiniMax M2.5",
                variants=(CatalogVariant(None, "MiniMax M2.5", ("MiniMax-M2.5",)),),
            ),
            CatalogFamily(
                family_id="minimax-m3",
                display_name="MiniMax M3",
                variants=(CatalogVariant(None, "MiniMax M3", ("MiniMax-M3",)),),
            ),
        ),
    ),
    CatalogProvider(
        provider_id="vercel-ai-gateway",
        display_name="Vercel AI Gateway",
        provider_preset="generic",
        default_base_url="https://ai-gateway.vercel.sh/v1",
        base_url_hosts=("ai-gateway.vercel.sh",),
    ),
)
FEATURED_PROVIDER_IDS = {
    "deepseek",
    "openai",
    "xai",
    "openrouter",
    "anthropic",
    "gemini",
}

_PROVIDER_BY_ID = {provider.provider_id: provider for provider in _PROVIDER_CATALOG}
_HOST_TO_PROVIDER_ID = {
    host.casefold(): provider.provider_id
    for provider in _PROVIDER_CATALOG
    for host in provider.base_url_hosts
}
_NAME_TO_PROVIDER_ID = {
    provider.display_name.casefold(): provider.provider_id
    for provider in _PROVIDER_CATALOG
}
_NAME_TO_PROVIDER_ID.update(
    {
        "open ai": "openai",
        "openrouter": "openrouter",
        "xai": "xai",
        "x.ai": "xai",
        "grok": "xai",
        "deepseek": "deepseek",
        "anthropic": "anthropic",
        "gemini": "gemini",
        "moonshot": "moonshot",
        "kimi": "moonshot",
        "zhipu": "zhipu",
        "z.ai": "z-ai",
        "minimax": "minimax",
        "vercel ai gateway": "vercel-ai-gateway",
    }
)

_MODEL_TO_IDENTITIES: dict[str, list[ResolvedCandidateCatalogIdentity]] = {}
_MODEL_TO_PROVIDER_IDENTITIES: dict[
    tuple[str, str], ResolvedCandidateCatalogIdentity
] = {}
for provider in _PROVIDER_CATALOG:
    for family in provider.families:
        for variant in family.variants:
            identity = ResolvedCandidateCatalogIdentity(
                family_id=family.family_id,
                variant_id=variant.variant_id,
            )
            for model_id in variant.model_ids:
                normalized_model_id = model_id.casefold()
                _MODEL_TO_PROVIDER_IDENTITIES[(provider.provider_id, normalized_model_id)] = (
                    identity
                )
                _MODEL_TO_IDENTITIES.setdefault(normalized_model_id, []).append(identity)


def list_provider_catalog() -> tuple[CatalogProvider, ...]:
    return _PROVIDER_CATALOG


def provider_catalog_payload() -> list[dict[str, object]]:
    return [provider.to_dict() for provider in _PROVIDER_CATALOG]


def resolve_model_reasoning_efforts(
    *,
    model_id: str,
    provider_id: str | None = None,
    base_url: str | None = None,
) -> tuple[str, ...]:
    variant = _resolve_catalog_variant(
        model_id=model_id,
        provider_id=provider_id,
        base_url=base_url,
    )
    return variant.reasoning_efforts if variant is not None else ()


def resolve_model_default_reasoning_effort(
    *,
    model_id: str,
    provider_id: str | None = None,
    base_url: str | None = None,
) -> str | None:
    variant = _resolve_catalog_variant(
        model_id=model_id,
        provider_id=provider_id,
        base_url=base_url,
    )
    return variant.default_reasoning_effort if variant is not None else None


def _resolve_catalog_variant(
    *,
    model_id: str,
    provider_id: str | None,
    base_url: str | None,
) -> CatalogVariant | None:
    resolved_provider_id = (
        _normalize_provider_id(provider_id)
        or _provider_id_from_base_url(base_url)
    )
    provider = _PROVIDER_BY_ID.get(resolved_provider_id or "")
    if provider is None:
        return None
    normalized_model_id = model_id.strip().casefold()
    for family in provider.families:
        for variant in family.variants:
            if any(item.casefold() == normalized_model_id for item in variant.model_ids):
                return variant
    return None


def resolve_connection_catalog_metadata(
    *,
    source_id: str,
    name: str,
    base_url: str | None,
    provider_preset: str,
    explicit_provider_id: str | None = None,
    explicit_provider_display_name: str | None = None,
    explicit_auth_mode: str | None = None,
    explicit_catalog_source: str | None = None,
) -> ResolvedConnectionCatalog:
    if source_id == "codex_local":
        return ResolvedConnectionCatalog(
            provider_id=explicit_provider_id or "codex",
            provider_display_name=explicit_provider_display_name or "Codex",
            auth_mode=explicit_auth_mode or "local_import",
            catalog_source=explicit_catalog_source or "local_builtin",
        )
    if source_id == "claude_local":
        return ResolvedConnectionCatalog(
            provider_id=explicit_provider_id or "claude-code",
            provider_display_name=explicit_provider_display_name or "Claude Code",
            auth_mode=explicit_auth_mode or "local_import",
            catalog_source=explicit_catalog_source or "local_builtin",
        )
    if source_id == "grok_local":
        return ResolvedConnectionCatalog(
            provider_id=explicit_provider_id or "grok-build",
            provider_display_name=explicit_provider_display_name or "Grok Build",
            auth_mode=explicit_auth_mode or "local_import",
            catalog_source=explicit_catalog_source or "local_builtin",
        )

    resolved_provider_id = (
        _normalize_provider_id(explicit_provider_id)
        or _provider_id_from_base_url(base_url)
        or _provider_id_from_provider_preset(provider_preset)
        or _provider_id_from_name(name)
    )
    if resolved_provider_id is None or resolved_provider_id == "custom":
        return ResolvedConnectionCatalog(
            provider_id=explicit_provider_id or "custom",
            provider_display_name=(
                explicit_provider_display_name
                or _clean_optional_text(name)
                or "自定义 endpoint"
            ),
            auth_mode=explicit_auth_mode or "api_key",
            catalog_source=explicit_catalog_source or "manual",
        )

    provider = _PROVIDER_BY_ID[resolved_provider_id]
    return ResolvedConnectionCatalog(
        provider_id=resolved_provider_id,
        provider_display_name=explicit_provider_display_name or provider.display_name,
        auth_mode=explicit_auth_mode or "api_key",
        catalog_source=explicit_catalog_source or "catalog_inferred",
    )


def resolve_candidate_catalog_identity(
    *,
    model_id: str,
    provider_id: str | None = None,
) -> ResolvedCandidateCatalogIdentity:
    normalized_model_id = model_id.casefold()
    normalized_provider_id = _normalize_provider_id(provider_id)
    if normalized_provider_id is not None:
        direct_match = _MODEL_TO_PROVIDER_IDENTITIES.get(
            (normalized_provider_id, normalized_model_id)
        )
        if direct_match is not None:
            return direct_match

    identities = _MODEL_TO_IDENTITIES.get(normalized_model_id, [])
    if len(identities) == 1:
        return identities[0]

    if normalized_model_id.startswith("deepseek-v4-"):
        return ResolvedCandidateCatalogIdentity(
            family_id="deepseek-v4",
            variant_id=model_id.removeprefix("deepseek-v4-"),
        )
    if normalized_model_id.startswith("gpt-"):
        return ResolvedCandidateCatalogIdentity(family_id=model_id)
    if normalized_model_id.startswith("claude-"):
        return ResolvedCandidateCatalogIdentity(family_id=model_id)
    if normalized_model_id.startswith("gemini-"):
        return ResolvedCandidateCatalogIdentity(family_id=model_id)
    return ResolvedCandidateCatalogIdentity()


def _normalize_provider_id(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized.casefold() if normalized else None


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _provider_id_from_base_url(base_url: str | None) -> str | None:
    if base_url is None:
        return None
    host = urlparse(base_url).netloc.casefold()
    for known_host, provider_id in _HOST_TO_PROVIDER_ID.items():
        if host == known_host or host.endswith(f".{known_host}"):
            return provider_id
    return None


def _provider_id_from_provider_preset(provider_preset: str) -> str | None:
    return "openrouter" if provider_preset == "openrouter" else None


def _provider_id_from_name(name: str) -> str | None:
    return _NAME_TO_PROVIDER_ID.get(name.strip().casefold())
