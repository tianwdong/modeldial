from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GraderSpec:
    kind: str
    payload: dict[str, object]


@dataclass
class QuestionSpec:
    id: str
    title: str
    enabled: bool
    prompt: str
    grader: GraderSpec
    tags: list[str]
    capability_id: str = ""
    capability_label: str = ""
    detail_label: str = ""


@dataclass(frozen=True)
class EvaluationProfileSpec:
    id: str
    label: str
    summary: str
    question_ids: list[str]
    result_level: str
    score_presentation: str
    score_max: int
    upgrade_to: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "summary": self.summary,
            "question_ids": list(self.question_ids),
            "question_count": len(self.question_ids),
            "result_level": self.result_level,
            "score_presentation": self.score_presentation,
            "score_max": self.score_max,
            "upgrade_to": self.upgrade_to,
        }


@dataclass
class QuestionCollection:
    questions: list[QuestionSpec]
    metadata: "QuestionPackMetadata"
    evaluation_profiles: list[EvaluationProfileSpec]
    default_evaluation_profile_id: str

    @property
    def enabled_questions(self) -> list[QuestionSpec]:
        return [question for question in self.questions if question.enabled]

    @property
    def enabled_question_ids(self) -> list[str]:
        return [question.id for question in self.enabled_questions]

    @property
    def question_count(self) -> int:
        return len(self.enabled_questions)

    def evaluation_profile(self, profile_id: str | None = None) -> EvaluationProfileSpec:
        requested_id = profile_id or self.default_evaluation_profile_id
        for profile in self.evaluation_profiles:
            if profile.id == requested_id:
                return profile
        raise ValueError(f"unknown evaluation_profile_id: {requested_id}")

    @property
    def complete_evaluation_profile(self) -> EvaluationProfileSpec:
        full_profile = next(
            (
                profile
                for profile in self.evaluation_profiles
                if profile.id == "full" and profile.result_level == "complete"
            ),
            None,
        )
        if full_profile is not None:
            return full_profile
        return next(
            profile
            for profile in self.evaluation_profiles
            if profile.result_level == "complete"
        )

    def questions_for_profile(
        self,
        profile_id: str | None = None,
    ) -> list[QuestionSpec]:
        profile = self.evaluation_profile(profile_id)
        questions_by_id = {question.id: question for question in self.enabled_questions}
        return [questions_by_id[question_id] for question_id in profile.question_ids]

    def questions_for_ids(self, question_ids: list[str]) -> list[QuestionSpec]:
        questions_by_id = {question.id: question for question in self.enabled_questions}
        missing = [question_id for question_id in question_ids if question_id not in questions_by_id]
        if missing:
            raise ValueError(f"unknown enabled question_ids: {', '.join(missing)}")
        return [questions_by_id[question_id] for question_id in question_ids]


@dataclass(frozen=True)
class QuestionPackMetadata:
    question_pack_id: str
    question_pack_version: str
    question_count: int


class QuestionBank:
    def __init__(self, root: Path) -> None:
        self.root = root

    def load(self) -> QuestionCollection:
        payload = self._catalog_payload()
        catalog = self._catalog_items(payload)
        questions: list[QuestionSpec] = []
        for item in catalog:
            prompt_path = self.root / str(item["prompt_path"])
            answer_path = self.root / str(item["answer_path"])
            prompt = prompt_path.read_text(encoding="utf-8").strip()
            answer = json.loads(answer_path.read_text(encoding="utf-8"))
            grader_payload = dict(answer["grader"])
            questions.append(
                QuestionSpec(
                    id=str(item["id"]),
                    title=str(item["title"]),
                    capability_id=str(item.get("capability_id") or item["id"]),
                    capability_label=str(item.get("capability_label") or item["title"]),
                    detail_label=str(item.get("detail_label") or item["title"]),
                    enabled=bool(item.get("enabled", True)),
                    prompt=prompt,
                    grader=GraderSpec(
                        kind=str(grader_payload["kind"]),
                        payload=grader_payload,
                    ),
                    tags=[str(tag) for tag in item.get("tags", [])],
                )
            )
        metadata = self._metadata(payload, catalog)
        evaluation_profiles, default_profile_id = self._evaluation_profiles(
            payload,
            questions,
        )
        return QuestionCollection(
            questions=questions,
            metadata=QuestionPackMetadata(
                question_pack_id=metadata.question_pack_id,
                question_pack_version=metadata.question_pack_version,
                question_count=sum(1 for question in questions if question.enabled),
            ),
            evaluation_profiles=evaluation_profiles,
            default_evaluation_profile_id=default_profile_id,
        )

    def metadata(self) -> QuestionPackMetadata:
        payload = self._catalog_payload()
        items = self._catalog_items(payload)
        return self._metadata(payload, items)

    @staticmethod
    def _metadata(
        payload: object,
        items: list[dict[str, object]],
    ) -> QuestionPackMetadata:
        if isinstance(payload, dict):
            pack_id = str(payload.get("id") or payload.get("question_pack_id") or "coding-fast")
            version = str(payload.get("version") or payload.get("question_pack_version") or "coding-fast-v1")
        else:
            pack_id = "coding-fast"
            version = "coding-fast-v1"
        question_count = sum(1 for item in items if bool(item.get("enabled", True)))
        return QuestionPackMetadata(
            question_pack_id=pack_id,
            question_pack_version=version,
            question_count=question_count,
        )

    @staticmethod
    def _evaluation_profiles(
        payload: object,
        questions: list[QuestionSpec],
    ) -> tuple[list[EvaluationProfileSpec], str]:
        enabled_questions = [question for question in questions if question.enabled]
        enabled_question_ids = [question.id for question in enabled_questions]
        enabled_question_id_set = set(enabled_question_ids)
        score_max_by_question_id = {
            question.id: max(1, int(question.grader.payload.get("max_score") or 1))
            for question in enabled_questions
        }
        raw_profiles = payload.get("evaluation_profiles") if isinstance(payload, dict) else None
        if raw_profiles is None:
            return (
                [
                    EvaluationProfileSpec(
                        id="full",
                        label="完整评测",
                        summary="运行当前题包全部启用题目",
                        question_ids=list(enabled_question_ids),
                        result_level="complete",
                        score_presentation="percent",
                        score_max=sum(score_max_by_question_id.values()),
                    )
                ],
                "full",
            )
        if not isinstance(raw_profiles, list) or not raw_profiles:
            raise ValueError("evaluation_profiles must be a non-empty list")

        profiles: list[EvaluationProfileSpec] = []
        seen_profile_ids: set[str] = set()
        for raw_profile in raw_profiles:
            if not isinstance(raw_profile, dict):
                raise ValueError("evaluation profile must be an object")
            profile_id = str(raw_profile.get("id") or "").strip()
            if not profile_id:
                raise ValueError("evaluation profile id is required")
            if profile_id in seen_profile_ids:
                raise ValueError(f"duplicate evaluation profile id: {profile_id}")
            seen_profile_ids.add(profile_id)
            selector = raw_profile.get("question_selector")
            if not isinstance(selector, dict):
                raise ValueError(f"evaluation profile {profile_id} requires question_selector")
            selector_kind = str(selector.get("kind") or "").strip()
            if selector_kind == "all_enabled":
                question_ids = list(enabled_question_ids)
            elif selector_kind == "explicit":
                raw_question_ids = selector.get("question_ids")
                if not isinstance(raw_question_ids, list) or not raw_question_ids:
                    raise ValueError(
                        f"evaluation profile {profile_id} explicit question_ids must be non-empty"
                    )
                question_ids = [str(question_id) for question_id in raw_question_ids]
                if len(question_ids) != len(set(question_ids)):
                    raise ValueError(
                        f"evaluation profile {profile_id} contains duplicate question_ids"
                    )
                unknown_question_ids = [
                    question_id
                    for question_id in question_ids
                    if question_id not in enabled_question_id_set
                ]
                if unknown_question_ids:
                    raise ValueError(
                        f"evaluation profile {profile_id} references unknown enabled questions: "
                        + ", ".join(unknown_question_ids)
                    )
            else:
                raise ValueError(
                    f"unsupported question selector for {profile_id}: {selector_kind}"
                )
            result_level = str(raw_profile.get("result_level") or "").strip()
            if result_level not in {"provisional", "complete"}:
                raise ValueError(
                    f"unsupported result_level for {profile_id}: {result_level}"
                )
            score_presentation = str(
                raw_profile.get("score_presentation")
                or ("percent" if result_level == "complete" else "raw")
            )
            if score_presentation not in {"raw", "percent"}:
                raise ValueError(
                    f"unsupported score_presentation for {profile_id}: {score_presentation}"
                )
            upgrade_to_raw = raw_profile.get("upgrade_to")
            upgrade_to = (
                str(upgrade_to_raw).strip()
                if upgrade_to_raw is not None and str(upgrade_to_raw).strip()
                else None
            )
            profiles.append(
                EvaluationProfileSpec(
                    id=profile_id,
                    label=str(raw_profile.get("label") or profile_id),
                    summary=str(raw_profile.get("summary") or ""),
                    question_ids=question_ids,
                    result_level=result_level,
                    score_presentation=score_presentation,
                    score_max=sum(
                        score_max_by_question_id[question_id]
                        for question_id in question_ids
                    ),
                    upgrade_to=upgrade_to,
                )
            )

        profiles_by_id = {profile.id: profile for profile in profiles}
        complete_profiles = [
            profile for profile in profiles if profile.result_level == "complete"
        ]
        if not complete_profiles:
            raise ValueError("at least one complete evaluation profile is required")
        if any(
            set(profile.question_ids) != enabled_question_id_set
            for profile in complete_profiles
        ):
            raise ValueError("complete evaluation profiles must cover all enabled questions")
        for profile in profiles:
            if profile.upgrade_to is None:
                continue
            target = profiles_by_id.get(profile.upgrade_to)
            if target is None:
                raise ValueError(
                    f"evaluation profile {profile.id} references unknown upgrade target: "
                    f"{profile.upgrade_to}"
                )
            if not set(profile.question_ids) < set(target.question_ids):
                raise ValueError(
                    f"evaluation profile {profile.id} upgrade target must be a strict superset"
                )

        for profile in profiles:
            visited: set[str] = set()
            current = profile
            while current.upgrade_to is not None:
                if current.id in visited:
                    raise ValueError("evaluation profile upgrade graph contains a cycle")
                visited.add(current.id)
                current = profiles_by_id[current.upgrade_to]

        default_profile_id = str(
            payload.get("default_evaluation_profile_id")
            if isinstance(payload, dict)
            else ""
        ).strip()
        if not default_profile_id:
            default_profile_id = complete_profiles[0].id
        if default_profile_id not in profiles_by_id:
            raise ValueError(
                f"unknown default_evaluation_profile_id: {default_profile_id}"
            )
        return profiles, default_profile_id

    def _catalog_payload(self) -> object:
        return json.loads((self.root / "catalog.json").read_text(encoding="utf-8"))

    def _catalog_items(
        self,
        payload: object | None = None,
    ) -> list[dict[str, object]]:
        if payload is None:
            payload = self._catalog_payload()
        if isinstance(payload, dict):
            raw_items = payload.get("questions", [])
        else:
            raw_items = payload
        return [dict(item) for item in raw_items]  # type: ignore[arg-type]
