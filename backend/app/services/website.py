"""网页抓取规则业务规则：状态流转、字段校验与筛选口径都收在这里。

提交校验不再只看必填项：选择器语法、请求间隔与执行窗口的关系都会逐字段给出结论。
校验结论同时落在规则行上，采集任务页据此生成阻断条目。所有写入都在锁内完成，重复
提交和并发提交都只会产生一份结果。
"""
from __future__ import annotations

import copy
import re
import threading
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "website"
REQUIRED_FIELDS = ["规则编号", "目标站点", "列表选择器"]
SELECTOR_FIELDS = ["列表选择器", "详情选择器"]
INTERVAL_FIELD = "请求间隔"
WINDOW_FIELD = "执行窗口"
EDITABLE_FIELDS = [
    "规则编号",
    "目标站点",
    "列表选择器",
    "详情选择器",
    "翻页参数",
    "请求间隔",
    "字符编码",
    "重试上限",
    "关联任务",
    "执行窗口",
]
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "启用配置": "已启用", "停用配置": "已停用"}
NEGATIVE_ACTIONS = ["停用配置"]

_SECONDS_PATTERN = re.compile(
    r"^\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>毫秒|ms|秒|s|sec|seconds?)?\s*$",
    re.IGNORECASE,
)
_ILLEGAL_SELECTOR_CHARS = ("\x00", "{", "}")

_CREATE_LOCK = threading.Lock()
_ENTRY_LOCKS: dict[int, threading.Lock] = {}
_ENTRY_LOCKS_GUARD = threading.Lock()


def _lock_for(entry_id: int) -> threading.Lock:
    with _ENTRY_LOCKS_GUARD:
        return _ENTRY_LOCKS.setdefault(entry_id, threading.Lock())


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _format_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _parse_seconds(raw: Any) -> tuple[float | None, str | None]:
    """把「5」「5 秒」「500ms」这类写法统一成秒；返回 (秒数, 失败原因)。"""
    if raw is None:
        return None, "未配置"
    if isinstance(raw, bool):
        return None, "格式错误"
    if isinstance(raw, (int, float)):
        return float(raw), None
    text = str(raw).strip()
    if not text:
        return None, "未配置"
    match = _SECONDS_PATTERN.match(text)
    if match is None:
        return None, "格式错误"
    value = float(match.group("value"))
    unit = (match.group("unit") or "秒").lower()
    if unit in {"毫秒", "ms"}:
        value /= 1000.0
    return value, None


def _balanced(text: str, opening: str, closing: str) -> bool:
    depth = 0
    quote: str | None = None
    escaped = False
    for char in text:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
            continue
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and quote is None


def _selector_reason(selector: str) -> str | None:
    """返回选择器不合法的人话原因；合法时返回 None。

    这里不追求完整的 CSS/XPath 语法树，只拦截执行器会直接报错的形态：
    括号/引号不闭合、组合符位置错误、属性选择器缺值、类名或 ID 残缺等。
    """
    text = selector.strip()
    if not text:
        return "不能为空"
    if len(text) > 500:
        return "长度超过 500 个字符"
    for char in _ILLEGAL_SELECTOR_CHARS:
        if char in text:
            return f"包含非法字符“{char}”"
    if not _balanced(text, "[", "]"):
        return "属性方括号未闭合"
    if not _balanced(text, "(", ")"):
        return "圆括号未闭合"
    if text.count('"') % 2 or text.count("'") % 2:
        return "引号未成对"
    for match in re.finditer(r"\[([^\]]*)\]", text):
        inner = match.group(1).strip()
        if not inner:
            return "属性选择器为空"
        if re.search(r"(?:[~^$*|]?=)\s*$", inner):
            return "属性选择器缺少取值"
        if "=" in inner and not re.search(r"(?:[~^$*|]?=)\s*[^=\s]+", inner):
            return "属性选择器缺少取值"
    outside_attributes = re.sub(r"\[[^\]]*\]", "", text)
    if "=" in outside_attributes:
        return "属性赋值必须写在方括号内"
    if text[0] in ">+~,":
        return "不能以组合符开头"
    if text[-1] in ">+~,":
        return "不能以组合符结尾"
    if re.search(r"(?:[>+~])\s*(?:[>+~])", outside_attributes):
        return "组合符位置不合法"
    if re.search(r",\s*,", text):
        return "存在空的并列选择器"
    if re.search(r"[.#](?:\s|$)", text):
        return "类名或 ID 不完整"
    if ".." in text or "##" in text:
        return "类名或 ID 不完整"
    return None


def _issue(field: str, message: str, related: list[str] | None = None) -> dict[str, Any]:
    fields = [field]
    if related:
        fields.extend(item for item in related if item not in fields)
    return {"field": field, "fields": fields, "message": message}


def _fields_of(issues: list[dict[str, Any]]) -> list[str]:
    fields: list[str] = []
    for item in issues:
        for field in item.get("fields") or [item.get("field")]:
            if field and field not in fields:
                fields.append(field)
    return fields


def _message_of(issues: list[dict[str, Any]]) -> str:
    return "；".join(str(item.get("message") or "").strip("。； ") for item in issues)


def _apply_values(entry: dict[str, Any], values: dict[str, Any]) -> bool:
    """写入字段值；只有真正变化的字段才让原校验结论失效。"""
    changed = False
    for field in EDITABLE_FIELDS:
        if field not in values:
            continue
        incoming = values.get(field)
        if str(incoming if incoming is not None else "") == str(entry.get(field) or ""):
            continue
        entry[field] = incoming
        changed = True
    return changed


class WebsiteService:
    # ---------------------------------------------------------------- 查询
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        target: str | None = None,
        selector: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("规则编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if target:
            rows = [row for row in rows if target in str(row.get("目标站点", ""))]
        if selector:
            rows = [row for row in rows if selector in str(row.get("列表选择器", ""))]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._public(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self._public(entry) if entry is not None else None

    def raw_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def export_rows(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        target: str | None = None,
        selector: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """导出与列表使用同一套筛选口径，且只返回副本，绝不回写详情字段。"""
        items, total = self.list_entries(
            keyword=keyword, status=status, target=target, selector=selector, page=1, size=10**6
        )
        return copy.deepcopy(items), total

    # ---------------------------------------------------------------- 创建
    def create_entry(
        self, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, list[str], bool]:
        """登记规则；同一规则编号并发重复提交时只保留第一条。"""
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing, False
        rule_no = str(values.get("规则编号") or "").strip()
        with _CREATE_LOCK:
            for row in store.rows(MODULE):
                if str(row.get("规则编号") or "").strip() == rule_no:
                    return self._public(row), [], True
            rows = store.rows(MODULE)
            entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
            for field in EDITABLE_FIELDS:
                if field in values:
                    entry[field] = values.get(field)
            for field in REQUIRED_FIELDS:
                entry[field] = values.get(field)
            entry["status"] = STATUS_ORDER[0]
            entry["pending"] = True
            entry["abnormal"] = False
            entry["校验结论"] = "未校验"
            entry["校验问题"] = []
            entry["阻断原因"] = "尚未通过提交校验"
            entry["可调度"] = False
            entry["校验时间"] = ""
            rows.append(entry)
            return self._public(entry), [], False

    # ---------------------------------------------------------------- 编辑
    def update_entry(
        self, entry_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str, list[dict[str, Any]]]:
        """保存字段但不改状态；字段变动后原校验结论作废，等待重新提交校验。"""
        lock = _lock_for(entry_id)
        with lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"抓取规则 {entry_id} 不存在或已归档", []
            if _apply_values(entry, values):
                entry["校验结论"] = "未校验"
                entry["校验问题"] = []
                entry["阻断原因"] = "字段已修改，请重新提交校验"
                entry["可调度"] = False
                entry["校验时间"] = _now()
            return self._public(entry), "抓取规则已保存，等待重新提交校验", []

    # ---------------------------------------------------------------- 校验
    def _resolve_window(self, entry: dict[str, Any]) -> tuple[dict[str, Any] | None, Any]:
        task: dict[str, Any] | None = None
        linked_task = str(entry.get("关联任务") or "").strip()
        for row in store.rows("task"):
            if linked_task and str(row.get("任务编号") or "").strip() == linked_task:
                task = row
                break
        if task is None:
            for row in store.rows("task"):
                if str(row.get("关联规则") or "").strip() == str(entry.get("规则编号") or "").strip():
                    task = row
                    break
        raw = task.get(WINDOW_FIELD) if task is not None else entry.get(WINDOW_FIELD)
        return task, raw

    def validate_entry(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        """按字段给出校验问题；请求间隔与执行窗口的问题排在最前（冲突时窗口优先）。"""
        issues: list[dict[str, Any]] = []

        for field in REQUIRED_FIELDS:
            if not str(entry.get(field) or "").strip():
                issues.append(_issue(field, f"{field}不能为空"))

        for field in SELECTOR_FIELDS:
            value = entry.get(field)
            if value is None or not str(value).strip():
                continue
            reason = _selector_reason(str(value))
            if reason:
                issues.append(_issue(field, f"{field}格式错误：{reason}"))

        task, window_raw = self._resolve_window(entry)
        window_seconds, window_error = _parse_seconds(window_raw)
        window_text = "执行窗口未配置"
        if window_raw is not None and str(window_raw).strip():
            window_text = (
                f"执行窗口为 {_format_number(window_seconds)} 秒"
                if window_seconds is not None
                else f"执行窗口“{window_raw}”格式错误"
            )
        if window_error == "格式错误":
            issues.append(_issue(WINDOW_FIELD, f"执行窗口“{window_raw}”格式错误，需填写秒数"))
        elif window_seconds is not None and window_seconds <= 0:
            issues.append(
                _issue(WINDOW_FIELD, f"执行窗口 {_format_number(window_seconds)} 秒无效，必须大于 0")
            )

        interval_raw = entry.get(INTERVAL_FIELD)
        interval_seconds, interval_error = _parse_seconds(interval_raw)
        if interval_error == "未配置":
            issues.append(_issue(
                INTERVAL_FIELD,
                f"请求间隔不能为空，必须大于 0；{window_text}",
                related=[WINDOW_FIELD],
            ))
        elif interval_error == "格式错误":
            issues.append(_issue(
                INTERVAL_FIELD,
                f"请求间隔“{interval_raw}”格式错误，需填写秒数；{window_text}",
                related=[WINDOW_FIELD],
            ))
        elif interval_seconds is not None and interval_seconds <= 0:
            issues.append(_issue(
                INTERVAL_FIELD,
                f"请求间隔 {_format_number(interval_seconds)} 秒无效，必须大于 0；{window_text}",
                related=[WINDOW_FIELD],
            ))
        elif (
            interval_seconds is not None
            and window_seconds is not None
            and window_seconds > 0
            and interval_seconds > window_seconds
        ):
            issues.append(_issue(
                INTERVAL_FIELD,
                f"请求间隔 {_format_number(interval_seconds)} 秒超过执行窗口 "
                f"{_format_number(window_seconds)} 秒，请缩短请求间隔或放宽执行窗口",
                related=[WINDOW_FIELD],
            ))

        # 执行窗口 / 请求间隔的冲突优先于选择器等其它字段问题。
        def priority(item: dict[str, Any]) -> int:
            fields = item.get("fields") or [item.get("field")]
            if WINDOW_FIELD in fields or INTERVAL_FIELD in fields:
                return 0
            if any(field in SELECTOR_FIELDS for field in fields):
                return 1
            return 2

        return sorted(issues, key=priority)

    def usable(self, entry: dict[str, Any]) -> tuple[bool, list[dict[str, Any]], str]:
        """规则当前是否可被任务工作台调度，以及结论/阻断原因。"""
        issues = self.validate_entry(entry)
        if issues:
            return False, issues, "阻断"
        conclusion = str(entry.get("校验结论") or "未校验")
        if conclusion != "通过":
            reason = [{
                "field": "校验结论",
                "fields": ["校验结论"],
                "message": str(entry.get("阻断原因") or "尚未通过提交校验"),
            }]
            return False, reason, conclusion if conclusion in {"未校验", "已停用"} else "未校验"
        if str(entry.get("status") or "") == "已停用":
            reason = [{
                "field": "当前状态",
                "fields": ["当前状态"],
                "message": "抓取规则已停用，不能参与调度",
            }]
            return False, reason, "已停用"
        return True, [], "通过"

    def _record(self, entry: dict[str, Any], issues: list[dict[str, Any]]) -> None:
        entry["校验问题"] = issues
        entry["校验结论"] = "阻断" if issues else "通过"
        entry["阻断原因"] = _message_of(issues)
        entry["可调度"] = not issues
        entry["校验时间"] = _now()

    # ---------------------------------------------------------------- 动作
    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str, list[dict[str, Any]]]:
        """执行动作；失败时返回结构化问题，且不改写已有状态。"""
        lock = _lock_for(entry_id)
        with lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"抓取规则 {entry_id} 不存在或已归档", []
            if action not in ACTION_RULES:
                return None, f"动作「{action}」不属于网页抓取规则可执行范围", []
            if values and _apply_values(entry, values):
                entry["校验结论"] = "未校验"
                entry["校验问题"] = []
                entry["阻断原因"] = "字段已修改，请重新提交校验"
                entry["可调度"] = False

            if action == "提交校验":
                issues = self.validate_entry(entry)
                self._record(entry, issues)
                if issues:
                    entry["abnormal"] = True
                    return (
                        None,
                        f"提交校验未通过：{_message_of(issues)}（当前状态保持为{entry.get('status')}）",
                        issues,
                    )
                entry["abnormal"] = False
                # 历史已启用/已停用的规则不因提交校验改写状态；草稿进入待校验。
                if entry.get("status") == "草稿":
                    entry["status"] = "待校验"
                    entry["pending"] = True
                return self._public(entry), "提交校验通过，抓取规则进入待校验", []

            if action == "启用配置":
                # 启用时按当前字段现场复校验：修正字段后可直接启用，且只改本条规则。
                issues = self.validate_entry(entry)
                self._record(entry, issues)
                if issues:
                    entry["abnormal"] = True
                    return (
                        None,
                        f"启用配置被拦截：{_message_of(issues)}（仅本条规则生效，其他规则状态未变）",
                        issues,
                    )
                entry["status"] = "已启用"
                entry["pending"] = False
                entry["abnormal"] = False
                return self._public(entry), "抓取规则已启用配置（仅本条规则生效）", []

            # 停用配置
            entry["status"] = "已停用"
            entry["pending"] = False
            entry["abnormal"] = True
            entry["校验结论"] = "已停用"
            entry["可调度"] = False
            entry["阻断原因"] = "抓取规则已停用，不能参与调度"
            entry["校验问题"] = []
            return self._public(entry), "抓取规则已停用", []

    # ---------------------------------------------------------------- 序列化
    def _public(self, entry: dict[str, Any] | None) -> dict[str, Any]:
        """返回只读视图：动态补齐校验问题/执行窗口，调用方改不到仓库里的原行。"""
        if entry is None:
            return {}
        view = copy.deepcopy(entry)
        issues = self.validate_entry(entry)
        view["校验问题"] = issues
        if issues:
            view["校验结论"] = "阻断"
            view["阻断原因"] = _message_of(issues)
            view["可调度"] = False
        else:
            stored = str(entry.get("校验结论") or "")
            if stored in {"通过", "未校验", "已停用"}:
                view["校验结论"] = stored
                view["可调度"] = stored == "通过" and str(entry.get("status") or "") != "已停用"
            else:
                # 旧数据或改字段后失效的「阻断」结论，不冒充已校验通过。
                view["校验结论"] = "未校验"
                view["阻断原因"] = "尚未通过提交校验"
                view["可调度"] = False
        task, window_raw = self._resolve_window(entry)
        if task is not None:
            view.setdefault("关联任务", task.get("任务编号"))
        view[WINDOW_FIELD] = window_raw if window_raw not in (None, "") else "未配置"
        view["校验字段"] = _fields_of(issues)
        return view
