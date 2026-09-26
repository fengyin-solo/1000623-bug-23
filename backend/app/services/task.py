"""检测任务业务规则：状态流转、字段校验与筛选口径都收在这里。

任务详情/列表里附带的执行信息（检测条件、前处理方式、执行状态）直接投影自
检测执行模块的同一份记录，不另存副本，保证与执行列表、统计三处口径一致。
"""
from __future__ import annotations

from typing import Any

from app.services.execute import ExecuteService
from app.store import store

MODULE = "task"
REQUIRED_FIELDS = ["任务编号", "关联样品", "检测项目"]
STATUS_ORDER = ["待派发", "检测中", "待复核", "已完成"]
ACTION_RULES = {"派发任务": "检测中", "提交复核": "待复核", "确认完成": "已完成"}
NEGATIVE_ACTIONS = []

execute_service = ExecuteService()

EXECUTE_SUMMARY_FIELDS = ["检测条件", "前处理方式", "执行状态"]


class TaskService:
    def _public(self, row: dict[str, Any]) -> dict[str, Any]:
        """对外投影：附上该任务当前有效的执行摘要；不改动存储里的任务记录。"""
        public = dict(row)
        summary = execute_service.task_summary(str(row.get("任务编号") or ""))
        if summary is not None:
            public["检测执行"] = {field: summary.get(field) for field in EXECUTE_SUMMARY_FIELDS}
        return public

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("任务编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._public(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self._public(entry) if entry is not None else None

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

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"检测任务 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于检测任务可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"检测任务已{action}"
