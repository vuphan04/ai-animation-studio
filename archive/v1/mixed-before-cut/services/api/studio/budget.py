"""Monthly cost ledger and the shared budget gate used before every paid request.

The ledger only covers requests sent by this app; it is not provider billing evidence.
A reservation with an unknown outcome stays counted until the operator resolves it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .domain import DomainError
from .service import now, require, uid


CURRENCIES = ("USD", "CNY")
CATEGORIES = ("PLANNING", "SERIES_EXTRACT", "VOICE", "VIDEO_DRAFT", "VIDEO_FINAL", "OTHER")


def current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _used(conn, project_id: str, month: str, currency: str) -> tuple[int, int]:
    row = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN state='SETTLED' THEN actual_micro ELSE 0 END),0),"
        "COALESCE(SUM(CASE WHEN state='RESERVED' THEN reserved_micro ELSE 0 END),0) "
        "FROM cost_ledger WHERE project_id=? AND month=? AND currency=?", (project_id, month, currency)).fetchone()
    return row[0], row[1]


def _cap(policy, currency: str) -> int:
    return policy["monthly_cap_micro_usd"] if currency == "USD" else policy["monthly_cap_micro_cny"]


def check(conn, project_id: str, currency: str, amount_micro: int) -> dict:
    """Read-only gate decision; `reserve` applies the same rule inside the caller's write transaction."""
    project = require(conn.execute("SELECT workflow_version FROM projects WHERE id=?", (project_id,)).fetchone(), "project")
    policy = conn.execute("SELECT * FROM budget_policies WHERE project_id=?", (project_id,)).fetchone()
    if policy is None and project["workflow_version"] == "V1":
        # Legacy projects keep their grant-only behaviour until an operator sets a cap.
        return {"allowed": True, "enforced": False, "reason": None}
    if currency not in CURRENCIES or type(amount_micro) is not int or amount_micro < 0:
        return {"allowed": False, "enforced": True, "reason": "Đơn vị tiền hoặc số tiền dự trù không hợp lệ."}
    if policy is None or _cap(policy, currency) <= 0:
        return {"allowed": False, "enforced": True,
                "reason": f"Chưa đặt trần chi phí tháng ({currency}) ở bước S1 của Thư viện series; không gửi yêu cầu có phí."}
    if policy["paid_paused"]:
        return {"allowed": False, "enforced": True, "reason": "Yêu cầu có phí đang tạm dừng trong S1; bật lại trước khi gửi."}
    settled, reserved = _used(conn, project_id, current_month(), currency)
    cap = _cap(policy, currency)
    if settled + reserved + amount_micro > cap:
        return {"allowed": False, "enforced": True,
                "reason": f"Vượt trần chi phí tháng: đã dùng {settled / 1e6:.2f} + đang giữ {reserved / 1e6:.2f} + yêu cầu {amount_micro / 1e6:.2f} > trần {cap / 1e6:.2f} {currency}."}
    # `near_cap` lets a gate warn before the month's total actually reaches the cap.
    return {"allowed": True, "enforced": True, "reason": None,
            "near_cap": (settled + reserved + amount_micro) * 100 >= cap * policy["warn_percent"]}


def reserve(conn, project_id: str, operation_id: str, *, gate: int, category: str, provider_kind: str,
            currency: str, amount_micro: int) -> None:
    existing = conn.execute("SELECT * FROM cost_ledger WHERE operation_id=?", (operation_id,)).fetchone()
    if existing:
        if existing["state"] != "RESERVED" or existing["reserved_micro"] != amount_micro or existing["project_id"] != project_id:
            raise DomainError("Operation này đã có bản ghi chi phí khác; không giữ ngân sách hai lần.", 409)
        return
    decision = check(conn, project_id, currency, amount_micro)
    if not decision["allowed"]:
        raise DomainError(decision["reason"], 409)
    if category not in CATEGORIES or gate not in (0, 1, 2, 3):
        raise DomainError("Loại chi phí hoặc cổng ngân sách không hợp lệ.")
    stamp = now()
    conn.execute("INSERT INTO cost_ledger(id,project_id,month,gate,category,provider_kind,currency,operation_id,state,reserved_micro,actual_micro,enforced,note,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,'RESERVED',?,NULL,?,NULL,?,?)",
                 (uid(), project_id, current_month(), gate, category, provider_kind, currency, operation_id,
                  amount_micro, 1 if decision["enforced"] else 0, stamp, stamp))


def settle(conn, operation_id: str, actual_micro: int) -> None:
    # Jobs queued before this ledger existed have no entry; their grants still settle as before.
    conn.execute("UPDATE cost_ledger SET state='SETTLED',actual_micro=?,updated_at=? WHERE operation_id=? AND state='RESERVED'",
                 (actual_micro, now(), operation_id))


def release(conn, operation_id: str) -> None:
    """Only for requests known not to have been sent."""
    conn.execute("UPDATE cost_ledger SET state='RELEASED',actual_micro=0,updated_at=? WHERE operation_id=? AND state='RESERVED'",
                 (now(), operation_id))


class BudgetService:
    def __init__(self, studio):
        self.studio = studio
        self.db = studio.db

    def policy(self, project_id: str) -> dict:
        self.studio.project(project_id)
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM budget_policies WHERE project_id=?", (project_id,)).fetchone()
        if row:
            return {**dict(row), "paid_paused": bool(row["paid_paused"])}
        return {"project_id": project_id, "revision": 0, "monthly_cap_micro_usd": 0, "monthly_cap_micro_cny": 0,
                "warn_percent": 80, "paid_paused": False, "updated_at": None}

    def save_policy(self, project_id: str, expected_revision: int, monthly_cap_micro_usd: int,
                    monthly_cap_micro_cny: int, warn_percent: int, paid_paused: bool) -> dict:
        self.studio.project(project_id)
        caps = (monthly_cap_micro_usd, monthly_cap_micro_cny)
        if any(type(value) is not int or not 0 <= value <= 10**13 for value in caps) or type(warn_percent) is not int or not 1 <= warn_percent <= 100 or type(paid_paused) is not bool:
            raise DomainError("Trần chi phí phải là số không âm, ngưỡng cảnh báo từ 1 đến 100%.")
        with self.db.transaction() as conn:
            row = conn.execute("SELECT revision FROM budget_policies WHERE project_id=?", (project_id,)).fetchone()
            if (row["revision"] if row else 0) != expected_revision:
                raise DomainError("Trần chi phí đã đổi ở phiên khác; tải lại trước khi lưu.", 409)
            conn.execute("INSERT OR REPLACE INTO budget_policies(project_id,revision,monthly_cap_micro_usd,monthly_cap_micro_cny,warn_percent,paid_paused,updated_at) VALUES(?,?,?,?,?,?,?)",
                         (project_id, expected_revision + 1, monthly_cap_micro_usd, monthly_cap_micro_cny, warn_percent, int(paid_paused), now()))
            self.studio.audit(conn, project_id, "budget.policy_saved", project_id,
                              {"revision": expected_revision + 1, "cap_usd": monthly_cap_micro_usd, "cap_cny": monthly_cap_micro_cny, "paused": paid_paused})
        return self.summary(project_id)

    def summary(self, project_id: str) -> dict:
        policy = self.policy(project_id)
        month = current_month()
        with self.db.connect() as conn:
            totals = []
            for currency in CURRENCIES:
                settled, reserved = _used(conn, project_id, month, currency)
                cap = _cap(policy, currency)
                used = settled + reserved
                totals.append({"currency": currency, "cap_micro": cap, "settled_micro": settled, "reserved_micro": reserved,
                               "remaining_micro": max(cap - used, 0),
                               "warning": bool(cap and used * 100 >= cap * policy["warn_percent"]),
                               "exceeded": bool(cap and used >= cap)})
            entries = [dict(row) for row in conn.execute(
                "SELECT * FROM cost_ledger WHERE project_id=? ORDER BY created_at DESC LIMIT 200", (project_id,))]
        return {"policy": policy, "month": month, "totals": totals, "entries": entries,
                "scope_note": "Sổ chỉ tính yêu cầu do app này gửi; không thay hóa đơn của nhà cung cấp."}

    def resolve(self, entry_id: str, action: str, confirmed: bool) -> dict:
        """Operator closes a reservation whose outcome the app could not determine."""
        if action not in ("SETTLE_AT_RESERVED", "RELEASE") or not confirmed:
            raise DomainError("Chọn cách đóng khoản giữ và xác nhận đã kiểm tra với nhà cung cấp.")
        with self.db.transaction() as conn:
            entry = require(conn.execute("SELECT * FROM cost_ledger WHERE id=?", (entry_id,)).fetchone(), "ledger entry")
            if entry["state"] != "RESERVED":
                raise DomainError("Khoản này đã được đóng.", 409)
            for table in ("planning_jobs", "video_jobs", "series_extraction_jobs", "breakdown_jobs", "dialogue_jobs", "ai_video_jobs", "final_jobs"):
                job = conn.execute(f"SELECT state FROM {table} WHERE operation_id=?", (entry["operation_id"],)).fetchone()
                if job and job["state"] in ("QUEUED", "RUNNING", "PENDING"):
                    raise DomainError("Công việc của khoản giữ này còn đang chạy; chờ kết quả trước khi đóng tay.", 409)
            actual = entry["reserved_micro"] if action == "SETTLE_AT_RESERVED" else 0
            conn.execute("UPDATE cost_ledger SET state=?,actual_micro=?,note=?,updated_at=? WHERE id=?",
                         ("SETTLED" if action == "SETTLE_AT_RESERVED" else "RELEASED", actual,
                          "OPERATOR_RESOLVED_UNVERIFIED", now(), entry_id))
            # The operator has now decided the outcome, so the request no longer blocks a new one.
            for table in ("series_extraction_jobs", "breakdown_jobs", "dialogue_jobs", "ai_video_jobs", "final_jobs"):
                conn.execute(f"UPDATE {table} SET state='BLOCKED',error=COALESCE(error,'') || ' Đã đóng tay trong Sổ chi phí.',updated_at=? WHERE operation_id=? AND state='RECONCILING'",
                             (now(), entry["operation_id"]))
            self.studio.audit(conn, entry["project_id"], "budget.entry_resolved", entry_id, {"action": action})
        return self.summary(entry["project_id"])
