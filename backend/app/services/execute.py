"""检测执行业务规则：状态流转、字段校验与筛选口径都收在这里。

执行记录以「一条记录一份快照」为唯一事实源：检测条件、前处理方式、执行人员等
字段在登记时写入本条记录，之后只在状态流转时按规则更新；执行列表、任务详情与
统计都从同一份记录投影生成，保证三处口径一致。作废记录保留在列表里备查，但
不再计入任何统计。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.store import store

MODULE = "execute"
REQUIRED_FIELDS = ["记录编号", "关联任务", "前处理方式"]
DISPLAY_FIELDS = ["记录编号", "关联任务", "前处理方式", "检测条件", "原始记录号", "执行人员", "执行时间"]
STATUS_ORDER = ["待执行", "执行中", "已提交", "已作废"]
ACTION_RULES = {"开始执行": "执行中", "提交记录": "已提交", "作废记录": "已作废"}
NEGATIVE_ACTIONS = ["作废记录"]
VOID_STATUS = "已作废"
TERMINAL_STATUSES = {"已提交", "已作废"}
# 状态机：只允许沿流程前进或作废；已作废是终态，不能再改回执行中，
# 需要重新检测时登记新记录，新记录不携带上一条的任何执行信息。
ALLOWED_TRANSITIONS = {
    "待执行": {"开始执行", "作废记录"},
    "执行中": {"提交记录", "作废记录"},
    "已提交": {"作废记录"},
    "已作废": set(),
}
# 幂等键：双击、网络失败重试等重复提交携带相同标识时，返回已存在的记录而不再新增。
IDEMPOTENT_FIELDS = ["请求号", "记录编号"]


class ExecuteService:
    def _public(self, entry: dict[str, Any]) -> dict[str, Any]:
        """对外投影：执行状态由机器状态派生，三处视图读到同一个值；不改动存储记录。"""
        row = dict(entry)
        row["执行状态"] = str(entry.get("status") or "")
        return row

    def _find_duplicate(self, rows: list[dict[str, Any]], values: dict[str, Any]) -> dict[str, Any] | None:
        for field in IDEMPOTENT_FIELDS:
            key = str(values.get(field) or "").strip()
            if not key:
                continue
            for row in rows:
                if str(row.get(field) or "").strip() == key:
                    return row
        # 原始记录号在未作废记录里唯一：重复登记不会让列表多出一条；
        # 已作废的不算冲突，允许作废后重新执行。
        serial = str(values.get("原始记录号") or "").strip()
        if serial:
            for row in rows:
                if row.get("status") != VOID_STATUS and str(row.get("原始记录号") or "").strip() == serial:
                    return row
        return None

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
        return [self._public(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self._public(entry) if entry is not None else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str], bool]:
        """登记执行记录；返回 (记录, 缺失字段, 是否新建)。重复提交命中已存在记录时不新增。"""
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing, False
        rows = store.rows(MODULE)
        duplicate = self._find_duplicate(rows, values)
        if duplicate is not None:
            return self._public(duplicate), [], False
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        # 展示字段全量留在本条记录上，每条记录自带检测条件等快照，互不覆盖。
        for field in DISPLAY_FIELDS:
            entry[field] = values.get(field)
        request_id = str(values.get("请求号") or "").strip()
        if request_id:
            entry["请求号"] = request_id
        # 新记录的执行时间一律从空开始，不残留任何上一条记录的时间。
        entry["执行时间"] = str(values.get("执行时间") or "").strip()
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return self._public(entry), [], True

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"执行记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于检测执行可执行范围"
        current = str(entry.get("status") or "")
        if current == VOID_STATUS:
            return None, "执行记录已作废，不能再执行其他动作；如需重新检测请登记新的执行记录"
        if action not in ALLOWED_TRANSITIONS.get(current, set()):
            return None, f"当前状态「{current}」不允许执行「{action}」"
        if action == "提交记录":
            # 提交前复核必填字段，前处理方式为空（含纯空白）不能提交。
            missing = [field for field in REQUIRED_FIELDS if not str(entry.get(field) or "").strip()]
            if missing:
                return None, f"提交前请补全必填字段：{'、'.join(missing)}"
        target = ACTION_RULES[action]
        entry["status"] = target
        entry["pending"] = target not in TERMINAL_STATUSES
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        today = date.today().isoformat()
        if action == "开始执行":
            entry["执行时间"] = today
        if action == "提交记录":
            entry["提交时间"] = today
        return self._public(entry), f"执行记录已{action}"

    def task_summary(self, task_code: str) -> dict[str, Any] | None:
        """任务详情用的执行摘要：取该任务最新一条未作废记录，与执行列表完全同源。"""
        code = str(task_code or "").strip()
        if not code:
            return None
        candidates = [
            row for row in store.rows(MODULE)
            if str(row.get("关联任务") or "").strip() == code and row.get("status") != VOID_STATUS
        ]
        if not candidates:
            return None
        latest = max(candidates, key=lambda row: int(row.get("id", 0)))
        return self._public(latest)

    def stats(self) -> dict[str, Any]:
        """执行统计：作废记录不计入任何口径；执行人员按记录上的快照统计，改名不会造成对不上。"""
        rows = store.rows(MODULE)
        active = [row for row in rows if row.get("status") != VOID_STATUS]
        today = date.today().isoformat()
        submitted_today = [
            row for row in active
            if row.get("status") == "已提交" and str(row.get("提交时间") or row.get("执行时间") or "")[:10] == today
        ]
        by_executor: dict[str, int] = {}
        for row in active:
            if row.get("status") != "已提交":
                continue
            name = str(row.get("执行人员") or "").strip() or "未登记"
            by_executor[name] = by_executor.get(name, 0) + 1
        return {
            "待执行": sum(1 for row in active if row.get("status") == "待执行"),
            "执行中": sum(1 for row in active if row.get("status") == "执行中"),
            "今日提交": len(submitted_today),
            "按执行人员": [{"执行人员": name, "完成数": count} for name, count in sorted(by_executor.items())],
            "已作废": sum(1 for row in rows if row.get("status") == VOID_STATUS),
        }
