"""采集任务业务规则：状态流转、字段校验与筛选口径都收在这里。

采集任务页会同步网页抓取规则的校验结论：规则被阻断时，在任务行上生成阻断条目，
并把任务标记为「不可调度」。阻断条目只影响调度结论，不改写任务自身的执行状态。
"""
from __future__ import annotations

import copy
from typing import Any

from app.store import store
from app.services.website import WebsiteService

MODULE = "task"
REQUIRED_FIELDS = ["任务编号", "任务名称", "关联源端"]
STATUS_ORDER = ["草稿", "待执行", "执行中", "已完成"]
ACTION_RULES = {"提交执行": "待执行", "暂停任务": "执行中", "恢复任务": "已完成"}
NEGATIVE_ACTIONS = []


class TaskService:
    def __init__(self) -> None:
        self.website = WebsiteService()

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        task_name: str | None = None,
        source: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("任务编号", ""))]
        if task_name:
            rows = [row for row in rows if task_name in str(row.get("任务名称", ""))]
        if source:
            rows = [row for row in rows if source in str(row.get("关联源端", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._public(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self._public(entry) if entry is not None else None

    def export_rows(
        self,
        *,
        keyword: str | None = None,
        task_name: str | None = None,
        source: str | None = None,
        status: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """导出沿用列表口径，返回副本，导出过程不回写任务或规则。"""
        items, total = self.list_entries(
            keyword=keyword, task_name=task_name, source=source, status=status, page=1, size=10**6
        )
        return copy.deepcopy(items), total

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str, list[dict[str, Any]]]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"采集任务 {entry_id} 不存在或已归档", []
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于采集任务可执行范围", []
        if action == "提交执行":
            blockers = self.blockers_for(entry)
            if blockers:
                messages = "；".join(str(item.get("阻断原因") or item.get("message") or "") for item in blockers)
                return None, f"采集任务提交执行被阻断：{messages}（任务状态未变更）", blockers
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里", []
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return self._public(entry), f"采集任务已{action}", []

    # ------------------------------------------------------------ 规则同步
    def linked_rules(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        task_no = str(task.get("任务编号") or "").strip()
        rules: list[dict[str, Any]] = []
        for rule in store.rows("website"):
            if task_no and str(rule.get("关联任务") or "").strip() == task_no:
                rules.append(rule)
        return rules

    def blockers_for(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        """把关联规则的阻断问题整理成任务侧的阻断条目。每条都指向具体字段。"""
        blockers: list[dict[str, Any]] = []
        for rule in self.linked_rules(task):
            usable, issues, conclusion = self.website.usable(rule)
            if usable:
                continue
            blocks = issues or [{
                "field": "校验结论",
                "fields": ["校验结论"],
                "message": str(rule.get("阻断原因") or "尚未通过提交校验"),
            }]
            for issue in blocks:
                blockers.append({
                    "规则编号": rule.get("规则编号"),
                    "关联任务": task.get("任务编号"),
                    "阻断状态": conclusion,
                    "字段": issue.get("field"),
                    "涉及字段": issue.get("fields") or [issue.get("field")],
                    "阻断原因": issue.get("message"),
                })
        return blockers

    def _public(self, entry: dict[str, Any] | None) -> dict[str, Any]:
        if entry is None:
            return {}
        view = copy.deepcopy(entry)
        blockers = self.blockers_for(entry)
        view["阻断条目"] = blockers
        view["阻断原因"] = "；".join(str(item["阻断原因"]) for item in blockers)
        rules = self.linked_rules(entry)
        view["可调度"] = None if not rules else not blockers
        return view
