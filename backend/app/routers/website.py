"""网页抓取规则接口：维护抓取规则，覆盖提交校验、启用配置、停用配置等动作。

导出路由必须声明在 /{entry_id} 之前，否则 "export" 会被当成路径参数匹配走。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.website import WebsiteService

router = APIRouter(prefix="/api/website", tags=["网页抓取规则"])

service = WebsiteService()

LIST_FIELDS = ["规则编号", "目标站点", "列表选择器", "详情选择器", "翻页参数", "请求间隔", "字符编码", "重试上限", "关联任务", "执行窗口"]
STATUSES = ["草稿", "待校验", "已启用", "已停用"]
FILTER_FIELDS = ["目标站点", "列表选择器"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按规则编号检索（兼容参数）"),
    规则编号: str | None = Query(default=None, alias="规则编号", description="按规则编号检索"),
    status: str | None = Query(default=None, description="草稿、待校验、已启用、已停用"),
    目标站点: str | None = Query(default=None, alias="目标站点", description="按目标站点检索"),
    列表选择器: str | None = Query(default=None, alias="列表选择器", description="按列表选择器检索"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按规则编号、状态、目标站点与列表选择器过滤；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=规则编号 or keyword,
        status=status,
        target=目标站点,
        selector=列表选择器,
        page=page,
        size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries(
    keyword: str | None = Query(default=None, description="按规则编号检索（兼容参数）"),
    规则编号: str | None = Query(default=None, alias="规则编号"),
    status: str | None = Query(default=None, description="草稿、待校验、已启用、已停用"),
    目标站点: str | None = Query(default=None, alias="目标站点"),
    列表选择器: str | None = Query(default=None, alias="列表选择器"),
) -> dict[str, Any]:
    """导出网页抓取规则清单：沿用列表的筛选条件，只读快照，不回写详情字段。"""
    items, total = service.export_rows(
        keyword=规则编号 or keyword, status=status, target=目标站点, selector=列表选择器
    )
    return {"module": "website", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条抓取规则明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"抓取规则 {entry_id} 不存在或已归档")
    return entry


@router.put("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """保存抓取规则字段；保存后原校验结论作废，等待重新提交校验。"""
    entry, message, issues = service.update_entry(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message, issues=issues)
    return ActionResult(ok=True, message=message, entry=entry, issues=issues)


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条抓取规则，缺字段时说明原因而不是静默丢弃；重复编号只登记一次。"""
    entry, missing, duplicated = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    if duplicated:
        return ActionResult(ok=True, message="抓取规则编号已存在，未重复登记", entry=entry)
    return ActionResult(ok=True, message="抓取规则已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条抓取规则执行提交校验、启用配置、停用配置；校验问题逐字段返回。"""
    action = str(payload.values.get("action") or "").strip()
    values = {key: value for key, value in payload.values.items() if key != "action"}
    entry, message, issues = service.run_action(entry_id, action, values)
    if entry is None:
        return ActionResult(ok=False, message=message, issues=issues)
    return ActionResult(ok=True, message=message, entry=entry, issues=issues)
