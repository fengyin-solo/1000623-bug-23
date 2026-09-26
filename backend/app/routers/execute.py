"""检测执行接口：维护执行记录，覆盖开始执行、提交记录、作废记录等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.execute import ExecuteService

router = APIRouter(prefix="/api/execute", tags=["检测执行"])

service = ExecuteService()

LIST_FIELDS = ["记录编号", "关联任务", "前处理方式", "检测条件", "原始记录号", "执行人员", "执行时间", "执行状态"]
STATUSES = ["待执行", "执行中", "已提交", "已作废"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按记录编号检索"),
    status: str | None = Query(default=None, description="待执行、执行中、已提交、已作废"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按记录编号与状态过滤检测执行列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出检测执行清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "execute", "total": total, "items": items}


@router.get("/stats")
def execute_stats() -> dict[str, int]:
    """执行统计：待执行、执行中与今日提交数；已作废记录不计入任何数字。"""
    return service.stats()


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条执行记录明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"执行记录 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条执行记录，缺字段时说明原因而不是静默丢弃；重复提交返回已有记录。"""
    entry, missing, created = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    if not created:
        return ActionResult(ok=True, message="相同记录编号或原始记录号的记录已存在，未重复登记", entry=entry)
    return ActionResult(ok=True, message="执行记录已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条执行记录执行开始执行、提交记录、作废记录；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
