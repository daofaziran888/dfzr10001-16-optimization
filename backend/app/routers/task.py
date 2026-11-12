"""采集任务接口：维护采集任务，覆盖提交执行、暂停任务、恢复任务等动作。

任务页会同步关联网页抓取规则的校验结论；导出路由必须声明在 /{entry_id} 之前。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.task import TaskService

router = APIRouter(prefix="/api/task", tags=["采集任务"])

service = TaskService()

LIST_FIELDS = ["任务编号", "任务名称", "关联源端", "采集频率", "增量字段", "起始水位", "批次大小", "执行窗口"]
STATUSES = ["草稿", "待执行", "执行中", "已完成"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按任务编号检索（兼容参数）"),
    任务编号: str | None = Query(default=None, alias="任务编号", description="按任务编号检索"),
    任务名称: str | None = Query(default=None, alias="任务名称", description="按任务名称检索"),
    关联源端: str | None = Query(default=None, alias="关联源端", description="按关联源端检索"),
    status: str | None = Query(default=None, description="草稿、待执行、执行中、已完成"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按任务编号、任务名称、关联源端与状态过滤采集任务列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=任务编号 or keyword,
        task_name=任务名称,
        source=关联源端,
        status=status,
        page=page,
        size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries(
    keyword: str | None = Query(default=None, description="按任务编号检索（兼容参数）"),
    任务编号: str | None = Query(default=None, alias="任务编号"),
    任务名称: str | None = Query(default=None, alias="任务名称"),
    关联源端: str | None = Query(default=None, alias="关联源端"),
    status: str | None = Query(default=None, description="草稿、待执行、执行中、已完成"),
) -> dict[str, Any]:
    """导出采集任务清单：沿用列表的筛选条件，包含规则同步出的阻断条目。"""
    items, total = service.export_rows(
        keyword=任务编号 or keyword, task_name=任务名称, source=关联源端, status=status
    )
    return {"module": "task", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条采集任务明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"采集任务 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条采集任务，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="采集任务已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条采集任务执行提交执行、暂停任务、恢复任务；校验阻断时说明具体字段。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message, issues = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message, issues=issues)
    return ActionResult(ok=True, message=message, entry=entry, issues=issues)
