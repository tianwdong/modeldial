from __future__ import annotations

from dataclasses import dataclass
import re


REASONING_EFFORTS = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
)
DEFAULT_SCAN_PROFILES = frozenset({"default"})


@dataclass(frozen=True)
class ModelDisplayIdentity:
    model: str
    effort: str
    family_id: str | None
    variant_id: str | None


def infer_reasoning_suffix_aliases(
    model_ids: list[str],
) -> dict[str, tuple[str, str]]:
    proposed: dict[str, tuple[str, str]] = {}
    grouped_model_ids: dict[str, set[str]] = {}
    for model_id in model_ids:
        family_id, separator, suffix = model_id.rpartition("-")
        normalized_suffix = suffix.lower()
        if not separator or not family_id or normalized_suffix not in REASONING_EFFORTS:
            continue
        proposed[model_id] = (family_id, normalized_suffix)
        grouped_model_ids.setdefault(family_id, set()).add(model_id)
    return {
        model_id: identity
        for model_id, identity in proposed.items()
        if len(grouped_model_ids[identity[0]]) > 1
    }


def resolve_model_display_identity(
    *,
    model_id: str,
    scan_profile: str,
    family_id: str | None = None,
    variant_id: str | None = None,
    inferred_alias: tuple[str, str] | None = None,
) -> ModelDisplayIdentity:
    normalized_profile = scan_profile.strip().lower()
    if normalized_profile not in DEFAULT_SCAN_PROFILES:
        return ModelDisplayIdentity(
            model=model_id,
            effort=normalized_profile,
            family_id=family_id or model_id,
            variant_id=variant_id or normalized_profile,
        )

    explicit_variant = (variant_id or "").strip().lower()
    if (
        family_id
        and family_id != model_id
        and explicit_variant in REASONING_EFFORTS
    ):
        return ModelDisplayIdentity(
            model=family_id,
            effort=explicit_variant,
            family_id=family_id,
            variant_id=explicit_variant,
        )

    if inferred_alias is not None:
        inferred_family, inferred_effort = inferred_alias
        return ModelDisplayIdentity(
            model=inferred_family,
            effort=inferred_effort,
            family_id=inferred_family,
            variant_id=inferred_effort,
        )

    return ModelDisplayIdentity(
        model=model_id,
        effort=normalized_profile,
        family_id=family_id,
        variant_id=variant_id,
    )


def model_display_label(
    *,
    raw_model_id: str,
    identity: ModelDisplayIdentity,
) -> str:
    if identity.effort not in DEFAULT_SCAN_PROFILES:
        return f"{identity.model} / {identity.effort}"
    if (
        identity.family_id
        and identity.variant_id
        and raw_model_id == f"{identity.family_id}-{identity.variant_id}"
    ):
        return f"{identity.family_id} / {identity.variant_id}"
    versioned_family = re.fullmatch(
        r"(.+-v\d+(?:\.\d+)?)-(.+)",
        raw_model_id,
        flags=re.IGNORECASE,
    )
    if versioned_family:
        return f"{versioned_family.group(1)} / {versioned_family.group(2)}"
    return raw_model_id
