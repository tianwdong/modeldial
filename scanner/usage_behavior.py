from __future__ import annotations

from collections.abc import Callable
import json
import re


BEHAVIOR_SCHEMA_VERSION = 1
_MAX_MESSAGE_CHARS = 2000
_PATCH_PATH = re.compile(
    r"\*\*\* (?:Update|Add|Delete) File: (.*?)(?:\\n|\r?\n|$)"
)
_TOOL_REFERENCE = re.compile(r"\btools\.([A-Za-z0-9_]+)")

_MESSAGE_PATTERNS = (
    (
        "refactoring",
        re.compile(
            r"\b(refactor|rename|reorganize|simplify|extract|restructure|migrate|split)\b"
            r"|重构|重命名|整理|简化|抽取|重组|迁移|拆分",
            re.IGNORECASE,
        ),
    ),
    (
        "feature",
        re.compile(
            r"\b(add|create|implement|new|build|feature|introduce|scaffold|generate)\b"
            r"|添加|新增|实现|创建|开发|加入|接入|生成功能",
            re.IGNORECASE,
        ),
    ),
    (
        "debugging",
        re.compile(
            r"\b(fix|bug|error|broken|failing|crash|issue|debug|traceback|exception)\b"
            r"|修复|错误|失败|崩溃|问题|排查|调试|异常|不工作",
            re.IGNORECASE,
        ),
    ),
    (
        "testing",
        re.compile(
            r"\b(test|pytest|unittest|vitest|jest|mocha|spec|coverage)\b"
            r"|测试|验证|回归|覆盖率",
            re.IGNORECASE,
        ),
    ),
    (
        "planning",
        re.compile(
            r"\b(plan|design|approach|strategy|architecture|how should)\b"
            r"|计划|规划|方案|设计|架构|怎么做|如何推进",
            re.IGNORECASE,
        ),
    ),
    (
        "exploration",
        re.compile(
            r"\b(research|investigate|review|understand|explain|compare|analyze|search)\b"
            r"|调研|调查|研究|审查|分析|了解|解释|对比|搜索|看看",
            re.IGNORECASE,
        ),
    ),
)
_TEST_COMMAND = re.compile(
    r"\b(pytest|unittest|vitest|jest|mocha|npm\s+(?:run\s+)?test|cargo\s+test|go\s+test)\b",
    re.IGNORECASE,
)
_GIT_COMMAND = re.compile(
    r"\bgit\s+(?:push|pull|commit|merge|rebase|checkout|branch|stash|log|diff|status|add|reset|cherry-pick|tag)\b",
    re.IGNORECASE,
)
_BUILD_COMMAND = re.compile(
    r"\b(npm\s+(?:run\s+)?build|npm\s+publish|pip\s+install|docker|deploy|make\s+build|cargo\s+build|brew\s+install)\b",
    re.IGNORECASE,
)

_EDIT_TOOLS = {
    "apply_patch",
    "apply_diff",
    "edit",
    "fileedittool",
    "filewritetool",
    "notebookedit",
    "write",
    "write_file",
}
_SHELL_TOOLS = {
    "bash",
    "bashtool",
    "exec_command",
    "powershelltool",
    "shell_command",
}
_READ_TOOLS = {
    "grep",
    "glob",
    "read_dir",
    "read_file",
    "view_image",
}
_SEARCH_TOOLS = {"web__run", "web_fetch", "web_search"}
_PLANNING_TOOLS = {"enterplanmode", "exitplanmode", "todowrite", "update_plan"}
_DELEGATION_TOOLS = {"spawn_agent", "create_thread"}


def message_category_from_response_item(payload: dict[str, object]) -> str | None:
    if payload.get("type") != "message" or payload.get("role") != "user":
        return None
    content = payload.get("content")
    texts: list[str] = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") not in {"input_text", "text"}:
                continue
            text = item.get("text")
            if isinstance(text, str):
                texts.append(text)
    return _message_category(" ".join(texts)[:_MAX_MESSAGE_CHARS])


def tool_step_from_response_item(
    payload: dict[str, object],
    file_fingerprint: Callable[[str], str],
) -> dict[str, object] | None:
    payload_type = payload.get("type")
    if payload_type == "function_call":
        name = _text(payload.get("name"))
        arguments, raw_arguments = _arguments(payload.get("arguments"))
        names = [name] if name else []
        source = raw_arguments
    elif payload_type == "custom_tool_call":
        name = _text(payload.get("name"))
        source = _text(payload.get("input")) or ""
        names = _TOOL_REFERENCE.findall(source) if name == "exec" else [name]
        arguments = {}
    else:
        return None

    kinds = sorted({_tool_kind(name) for name in names} - {"unknown"})
    if not kinds:
        return None
    file_keys = _file_keys(arguments, source, file_fingerprint)
    step: dict[str, object] = {"kinds": kinds, "file_keys": file_keys}
    if "shell" in kinds:
        command_text = " ".join(
            value
            for value in (
                _text(arguments.get("cmd")),
                _text(arguments.get("command")),
                source,
            )
            if value
        )
        shell_hint = _shell_category(command_text)
        if shell_hint:
            step["shell_category_hint"] = shell_hint
    return step


def tool_step_from_patch_event(
    payload: dict[str, object],
    file_fingerprint: Callable[[str], str],
) -> dict[str, object]:
    changes = payload.get("changes")
    paths = list(changes) if isinstance(changes, dict) else []
    return {
        "kinds": ["edit"],
        "file_keys": _fingerprints(paths, file_fingerprint),
    }


def mcp_tool_step() -> dict[str, object]:
    return {"kinds": ["external"], "file_keys": []}


def summarize_turn_behavior(turn: dict[str, object]) -> dict[str, object]:
    if turn.get("behavior_observed") is not True:
        return {
            "behavior_schema_version": None,
            "task_category": None,
            "has_edits": None,
            "retry_count": None,
            "one_shot": None,
        }
    steps = [step for step in turn.get("tool_steps", []) if isinstance(step, dict)]
    kinds = {
        str(kind)
        for step in steps
        for kind in step.get("kinds", [])
        if isinstance(kind, str)
    }
    has_edits = "edit" in kinds
    retry_count = _retry_count(steps) if has_edits else None
    return {
        "behavior_schema_version": BEHAVIOR_SCHEMA_VERSION,
        "task_category": _task_category(turn, steps, kinds, has_edits),
        "has_edits": has_edits,
        "retry_count": retry_count,
        "one_shot": retry_count == 0 if retry_count is not None else None,
    }


def _message_category(text: str) -> str:
    best: tuple[int, int, str] | None = None
    for order, (category, pattern) in enumerate(_MESSAGE_PATTERNS):
        match = pattern.search(text)
        if match is None:
            continue
        candidate = (match.start(), order, category)
        if best is None or candidate < best:
            best = candidate
    return best[2] if best else "conversation"


def _task_category(
    turn: dict[str, object],
    steps: list[dict[str, object]],
    kinds: set[str],
    has_edits: bool,
) -> str:
    message_hint = _text(turn.get("message_category_hint"))
    if has_edits:
        return (
            message_hint
            if message_hint in {"feature", "debugging", "refactoring", "testing"}
            else "coding"
        )
    shell_hint = next(
        (
            hint
            for step in steps
            if (hint := _text(step.get("shell_category_hint"))) is not None
        ),
        None,
    )
    if shell_hint:
        return shell_hint
    if "delegation" in kinds:
        return "delegation"
    if "planning" in kinds:
        return "planning"
    if kinds & {"read", "search", "shell"}:
        return "exploration"
    if message_hint:
        return message_hint
    return "general" if kinds else "conversation"


def _retry_count(steps: list[dict[str, object]]) -> int | None:
    edit_steps = [
        step
        for step in steps
        if "edit" in {str(kind) for kind in step.get("kinds", [])}
    ]
    if any(
        not any(isinstance(file_key, str) for file_key in step.get("file_keys", []))
        for step in edit_steps
    ):
        return None
    last_edit_step: dict[str, int] = {}
    last_shell_step = -1
    retries = 0
    for index, step in enumerate(steps):
        kinds = {str(kind) for kind in step.get("kinds", [])}
        if "shell" in kinds:
            last_shell_step = index
        if "edit" not in kinds:
            continue
        for file_key in step.get("file_keys", []):
            if not isinstance(file_key, str):
                continue
            previous = last_edit_step.get(file_key)
            if previous is not None and previous < last_shell_step < index:
                retries += 1
            last_edit_step[file_key] = index
    return retries


def _tool_kind(name: str) -> str:
    normalized = name.strip().casefold()
    if normalized in _EDIT_TOOLS:
        return "edit"
    if normalized in _SHELL_TOOLS:
        return "shell"
    if normalized in _READ_TOOLS or normalized.startswith("read_mcp_"):
        return "read"
    if normalized in _SEARCH_TOOLS:
        return "search"
    if normalized.startswith("mcp__"):
        return "external"
    if normalized in _PLANNING_TOOLS or normalized.startswith("task"):
        return "planning"
    if normalized in _DELEGATION_TOOLS:
        return "delegation"
    return "unknown"


def _arguments(value: object) -> tuple[dict[str, object], str]:
    if isinstance(value, dict):
        return value, ""
    if not isinstance(value, str):
        return {}, ""
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}, value
    return (parsed, value) if isinstance(parsed, dict) else ({}, value)


def _file_keys(
    arguments: dict[str, object],
    source: str,
    file_fingerprint: Callable[[str], str],
) -> list[str]:
    paths = [
        value
        for key in ("file_path", "path")
        if (value := _text(arguments.get(key))) is not None
    ]
    patch_texts = [source]
    patch_texts.extend(
        value
        for key in ("patch", "input")
        if (value := _text(arguments.get(key))) is not None
    )
    for text in patch_texts:
        paths.extend(match.strip().strip('"\'') for match in _PATCH_PATH.findall(text))
    return _fingerprints(paths, file_fingerprint)


def _fingerprints(
    paths: list[str],
    file_fingerprint: Callable[[str], str],
) -> list[str]:
    result: list[str] = []
    for path in paths:
        normalized = path.strip()
        if not normalized:
            continue
        fingerprint = file_fingerprint(normalized)
        if fingerprint not in result:
            result.append(fingerprint)
    return result


def _shell_category(text: str) -> str | None:
    if _TEST_COMMAND.search(text):
        return "testing"
    if _GIT_COMMAND.search(text):
        return "git"
    if _BUILD_COMMAND.search(text):
        return "build_deploy"
    return None


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None
