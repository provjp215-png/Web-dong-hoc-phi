"""Phê duyệt hai cấp (maker – checker).

Kế toán (người lập) gửi đề nghị → Quản trị viên (người duyệt) phê duyệt thì nghiệp vụ mới được thực hiện.
Áp dụng cho: duyệt giao dịch lệch tiền, miễn giảm / học bổng vượt ngưỡng.
"""
import json

import audit
import services
from services import now_str

KIND_LABEL = {"review_txn": "Duyệt giao dịch lệch tiền", "discount": "Miễn giảm / học bổng vượt ngưỡng"}
STATUS = {"pending": ("Chờ duyệt", "warning"), "approved": ("Đã duyệt", "success"),
          "rejected": ("Từ chối", "danger"), "failed": ("Không thực hiện được", "secondary")}


def pending_refs(conn, kind):
    return {r[0] for r in conn.execute("SELECT ref_id FROM approvals WHERE kind=? AND status='pending'", (kind,))}


def request(conn, kind, ref_id, payload, summary, user):
    if ref_id in pending_refs(conn, kind) and kind == "review_txn":
        return False
    conn.execute("INSERT INTO approvals(kind, ref_id, payload, summary, requested_by, created_at)"
                 " VALUES (?,?,?,?,?,?)",
                 (kind, ref_id, json.dumps(payload, ensure_ascii=False), summary, user["id"], now_str()))
    for a in conn.execute("SELECT id FROM users WHERE role='admin' AND status='active'").fetchall():
        services.notify(conn, a["id"], "Có đề nghị cần phê duyệt", f"{KIND_LABEL[kind]}: {summary}")
    conn.commit()
    return True


def decide(conn, approval_id, approve, admin, note=""):
    a = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
    if a is None or a["status"] != "pending":
        return "skip", "Đề nghị không còn ở trạng thái chờ duyệt"
    payload = json.loads(a["payload"] or "{}")
    status, msg = "rejected", "Đã từ chối đề nghị"
    if approve:
        try:
            if a["kind"] == "discount":
                services.add_discount(conn, a["ref_id"], payload["kind"], payload["description"],
                                      payload.get("percent") or 0, payload.get("amount") or 0)
                status, msg = "approved", "Đã phê duyệt và áp dụng miễn giảm"
            elif a["kind"] == "review_txn":
                st, m = services.approve_review(conn, a["ref_id"])
                status, msg = ("approved", "Đã phê duyệt, giao dịch được gạch nợ") if st == "success" \
                    else ("failed", m)
        except ValueError as e:
            status, msg = "failed", str(e)
    elif a["kind"] == "review_txn":
        conn.execute("UPDATE transactions SET status='failed', note=COALESCE(note,'')||' – Admin từ chối, hoàn tiền'"
                     " WHERE id=? AND status='review'", (a["ref_id"],))
    conn.execute("UPDATE approvals SET status=?, decided_by=?, decided_at=?, note=? WHERE id=?",
                 (status, admin["id"], now_str(), note or msg, approval_id))
    services.notify(conn, a["requested_by"], f"Đề nghị #{approval_id}: {STATUS[status][0]}",
                    f"{KIND_LABEL[a['kind']]} – {a['summary']}. {note or msg}")
    audit.log(conn, "approval_approve" if approve else "approval_reject", f"Đề nghị #{approval_id}",
              f"{a['summary']} → {msg}", user=admin)
    return status, msg
