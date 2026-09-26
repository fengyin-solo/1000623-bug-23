"""检测执行业务规则：状态流转、字段校验与筛选口径都收在这里。

口径约定：记录只在登记时落账一次，执行列表、记录详情、统计三处都读同一份数据；
执行状态由内部 status 派生，已作废记录不参与任何统计，也不再接受任何动作。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.store import store

MODULE = "execute"
REQUIRED_FIELDS = ["记录编号", "关联任务", "前处理方式"]
# 登记时完整落账的业务字段：检测条件、前处理方式等只保留这一份，三处展示自然一致。
BUSINESS_FIELDS = ["记录编号", "关联任务", "前处理方式", "检测条件", "原始记录号", "执行人员"]
STATUS_ORDER = ["待执行", "执行中", "已提交", "已作废"]
ACTION_RULES = {"开始执行": "执行中", "提交记录": "已提交", "作废记录": "已作废"}
NEGATIVE_ACTIONS = ["作废记录"]
VOID_STATUS = "已作废"
# 只有进行中的状态才算「待处理」。
ACTIVE_STATUSES = ["待执行", "执行中"]
# 每个动作允许的前置状态；已作废是终态，任何动作都进不去。
ALLOWED_SOURCES = {
    "开始执行": ["待执行"],
    "提交记录": ["执行中"],
    "作废记录": ["待执行", "执行中"],
}


def _synced(row: dict[str, Any]) -> dict[str, Any]:
    """对外展示用的记录：执行状态以 status 为准，保证列表、详情、统计口径一致。"""
    view = dict(row)
    view["执行状态"] = row.get("status") or STATUS_ORDER[0]
    return view


class ExecuteService:
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
            rows = [row for row in rows if keyword in str(row.get("记录编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [_synced(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return _synced(entry) if entry is not None else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str], bool]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing, False
        rows = store.rows(MODULE)
        duplicate = self._find_active_duplicate(rows, values)
        if duplicate is not None:
            # 幂等：重复提交或网络重试都返回已存在的记录，不再新增。
            return _synced(duplicate), [], False
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in BUSINESS_FIELDS:
            value = values.get(field)
            entry[field] = value.strip() if isinstance(value, str) else value
        # 执行时间由「开始执行」落戳，登记时一律清空，避免残留上一条记录的时间。
        entry["执行时间"] = None
        entry["status"] = STATUS_ORDER[0]
        entry["执行状态"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, [], True

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"执行记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于检测执行可执行范围"
        target = ACTION_RULES[action]
        current = str(entry.get("status") or STATUS_ORDER[0])
        if current == target:
            # 重复提交/网络重试：已在目标状态，直接确认，不再改动记录。
            return _synced(entry), f"执行记录已处于「{target}」，重复操作已忽略"
        if current == VOID_STATUS:
            return None, "执行记录已作废，不能再变更状态"
        if current not in ALLOWED_SOURCES[action]:
            return None, f"当前状态「{current}」不允许执行「{action}」"
        if action == "提交记录":
            missing = [field for field in REQUIRED_FIELDS if not str(entry.get(field) or "").strip()]
            if missing:
                return None, f"提交前请补全必填字段：{'、'.join(missing)}"
        if action == "开始执行":
            # 重新执行一律重新落戳，不残留上一条记录的执行时间。
            entry["执行时间"] = date.today().isoformat()
        entry["status"] = target
        entry["执行状态"] = target
        entry["pending"] = target in ACTIVE_STATUSES
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"执行记录已{action}"

    def stats(self) -> dict[str, int]:
        """执行统计：与列表、详情读同一份数据，已作废记录不计入任何数字。"""
        rows = [row for row in store.rows(MODULE) if row.get("status") != VOID_STATUS]
        today = date.today().isoformat()
        return {
            "待执行记录": sum(1 for row in rows if row.get("status") == "待执行"),
            "执行中记录": sum(1 for row in rows if row.get("status") == "执行中"),
            "今日提交数": sum(
                1
                for row in rows
                if row.get("status") == "已提交" and str(row.get("执行时间") or "")[:10] == today
            ),
        }

    @staticmethod
    def _find_active_duplicate(rows: list[dict[str, Any]], values: dict[str, Any]) -> dict[str, Any] | None:
        """按记录编号或原始记录号查重；已作废记录不占号，允许作废后重新执行。"""
        code = str(values.get("记录编号") or "").strip()
        origin = str(values.get("原始记录号") or "").strip()
        for row in rows:
            if row.get("status") == VOID_STATUS:
                continue
            if code and str(row.get("记录编号") or "").strip() == code:
                return row
            if origin and str(row.get("原始记录号") or "").strip() == origin:
                return row
        return None
