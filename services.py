"""Nghiệp vụ học phí: lập hoá đơn, miễn giảm, trả góp, phí trễ hạn, đối soát thanh toán."""
import secrets
import string
from datetime import date, datetime, timedelta

ROUND_UNIT = 1000  # làm tròn tiền đến nghìn đồng

# Các chương trình đào tạo đại học của Trường Đại học Việt Nhật (VJU)
FACULTIES = ["Nhật Bản học", "Khoa học và Kỹ thuật máy tính", "Cơ điện tử thông minh",
             "Công nghệ thực phẩm và sức khoẻ", "Nông nghiệp thông minh và bền vững", "Kỹ thuật xây dựng"]
ROLE_LABEL = {"student": "Sinh viên", "accountant": "Kế toán", "admin": "Quản trị viên"}
USER_STATUS_LABEL = {"active": ("Hoạt động", "success"), "pending": ("Chờ duyệt", "warning"),
                     "locked": ("Đã khoá", "danger"), "rejected": ("Bị từ chối", "secondary")}

STATUS_LABEL = {
    "paid": ("Đã hoàn thành", "success"),
    "partial": ("Đã nộp một phần", "warning"),
    "unpaid": ("Chưa nộp", "secondary"),
    "overdue": ("Quá hạn", "danger"),
    "pending": ("Chờ thanh toán", "secondary"),
    "success": ("Thành công", "success"),
    "failed": ("Thất bại", "danger"),
    "review": ("Cần kiểm tra", "warning"),
}
METHOD_LABEL = {"vnpay": "VNPay", "vietqr": "Chuyển khoản VietQR",
                "simulator": "Cổng mô phỏng", "cash": "Tiền mặt tại quầy"}


# ---------------------------------------------------------------- tiện ích
def today():
    return date.today()


def parse_date(s):
    if isinstance(s, date):
        return s
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def round_money(x):
    return int(round(x / ROUND_UNIT) * ROUND_UNIT)


def _fmt(n):
    return f"{int(n):,}".replace(",", ".") + "đ"


def _rand(n):
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


# ---------------------------------------------------------------- hoá đơn
def create_invoice(conn, student_id, semester_id, credits, fee_item_ids=None,
                   n_installments=1, exact=False, extra_lines=None, due_date=None):
    """Lập hoá đơn học kỳ cho sinh viên từ danh mục khoản thu.

    exact=True: chỉ dùng đúng các khoản được chọn (không tự thêm khoản bắt buộc).
    extra_lines: danh sách (mô tả, số tiền) – khoản phát sinh ngoài danh mục.
    due_date: hạn nộp riêng cho hoá đơn này (mặc định theo học kỳ).
    """
    sem = conn.execute("SELECT * FROM semesters WHERE id=?", (semester_id,)).fetchone()
    items = conn.execute("SELECT * FROM fee_items WHERE semester_id=?", (semester_id,)).fetchall()
    chosen = set(int(i) for i in fee_item_ids or [])
    if exact:
        items = [it for it in items if it["id"] in chosen]
    else:
        items = [it for it in items if it["mandatory"] or it["id"] in chosen]

    code = f"HD{sem['code'].replace('-', '')}{student_id:05d}"
    cur = conn.execute(
        "INSERT INTO invoices(code, student_id, semester_id, credits, due_date) VALUES (?,?,?,?,?)",
        (code, student_id, semester_id, credits, due_date or None),
    )
    invoice_id = cur.lastrowid
    for desc, amount in extra_lines or []:
        conn.execute("INSERT INTO invoice_lines(invoice_id, description, quantity, unit_price, amount)"
                     " VALUES (?,?,1,?,?)", (invoice_id, desc, int(amount), int(amount)))
    for it in items:
        qty = credits if it["per_credit"] else 1
        desc = it["name"] + (f" ({qty} tín chỉ)" if it["per_credit"] else "")
        conn.execute(
            "INSERT INTO invoice_lines(invoice_id, fee_item_id, description, quantity, unit_price, amount)"
            " VALUES (?,?,?,?,?,?)",
            (invoice_id, it["id"], desc, qty, it["unit_price"], qty * it["unit_price"]),
        )
    recompute_invoice(conn, invoice_id)
    set_installment_plan(conn, invoice_id, n_installments)
    return invoice_id


def add_charges(conn, invoice_id, fee_item_ids=(), extra_lines=(), due_date=None, credits=None):
    """Thêm khoản phát sinh vào hoá đơn đã có.

    - Hoá đơn chưa nộp đồng nào: cộng vào hoá đơn, chia lại các đợt như cũ.
    - Hoá đơn đã nộp (một phần/hết): tạo thêm một "đợt bổ sung" đúng bằng số tiền phát sinh.
    Trả về (số tiền thêm, mô tả cách xử lý, danh sách tên khoản bị bỏ qua vì đã có).
    """
    inv = conn.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    have = {r[0] for r in conn.execute("SELECT fee_item_id FROM invoice_lines WHERE invoice_id=? AND"
                                       " fee_item_id IS NOT NULL", (invoice_id,))}
    credits = credits or inv["credits"]
    old_total = inv["total"]
    wanted = {int(x) for x in fee_item_ids}
    skipped = []
    for it in conn.execute("SELECT * FROM fee_items WHERE semester_id=?", (inv["semester_id"],)).fetchall():
        if it["id"] not in wanted:
            continue
        if it["id"] in have:
            skipped.append(it["name"])
            continue
        qty = credits if it["per_credit"] else 1
        desc = it["name"] + (f" ({qty} tín chỉ)" if it["per_credit"] else "")
        conn.execute("INSERT INTO invoice_lines(invoice_id, fee_item_id, description, quantity, unit_price, amount)"
                     " VALUES (?,?,?,?,?,?)", (invoice_id, it["id"], desc, qty, it["unit_price"],
                                               qty * it["unit_price"]))
    for desc, amount in extra_lines:
        conn.execute("INSERT INTO invoice_lines(invoice_id, description, quantity, unit_price, amount)"
                     " VALUES (?,?,1,?,?)", (invoice_id, desc, int(amount), int(amount)))
    recompute_invoice(conn, invoice_id)
    added = conn.execute("SELECT total FROM invoices WHERE id=?", (invoice_id,)).fetchone()[0] - old_total
    if added <= 0:
        return 0, "", skipped
    if inv["paid"] == 0:
        n = conn.execute("SELECT COUNT(*) FROM installments WHERE invoice_id=?", (invoice_id,)).fetchone()[0]
        if due_date:
            conn.execute("UPDATE invoices SET due_date=? WHERE id=?", (due_date, invoice_id))
        set_installment_plan(conn, invoice_id, max(n, 1))
        return added, f"Cộng vào hoá đơn đang có, chia lại {max(n, 1)} đợt", skipped
    seq = conn.execute("SELECT COALESCE(MAX(seq),0)+1 FROM installments WHERE invoice_id=?",
                       (invoice_id,)).fetchone()[0]
    due = due_date or (today() + timedelta(days=14)).isoformat()
    conn.execute("INSERT INTO installments(invoice_id, seq, amount, due_date) VALUES (?,?,?,?)",
                 (invoice_id, seq, added, due))
    _refresh_invoice_status(conn, invoice_id)
    return added, f"Tạo đợt bổ sung số {seq}, hạn nộp {due}", skipped


def tuition_amount(conn, invoice_id):
    row = conn.execute(
        "SELECT COALESCE(SUM(l.amount),0) FROM invoice_lines l JOIN fee_items f ON f.id=l.fee_item_id"
        " WHERE l.invoice_id=? AND f.category='tuition'", (invoice_id,)).fetchone()
    return row[0]


def recompute_invoice(conn, invoice_id):
    """Tính lại tổng tiền, miễn giảm (theo % học phí hoặc số tiền cố định)."""
    subtotal = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM invoice_lines WHERE invoice_id=?", (invoice_id,)
    ).fetchone()[0]
    tuition = tuition_amount(conn, invoice_id)
    total_discount = 0
    for d in conn.execute("SELECT * FROM discounts WHERE invoice_id=?", (invoice_id,)).fetchall():
        value = round_money(tuition * d["percent"] / 100) if d["percent"] else d["amount"]
        value = min(value, max(subtotal - total_discount, 0))
        conn.execute("UPDATE discounts SET computed=? WHERE id=?", (value, d["id"]))
        total_discount += value
    total = subtotal - total_discount
    conn.execute("UPDATE invoices SET subtotal=?, discount=?, total=? WHERE id=?",
                 (subtotal, total_discount, total, invoice_id))
    _refresh_invoice_status(conn, invoice_id)


def add_discount(conn, invoice_id, kind, description, percent=0, amount=0):
    inv = conn.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    if inv["paid"] > 0:
        raise ValueError("Hoá đơn đã có thanh toán, không thể thêm miễn giảm.")
    conn.execute(
        "INSERT INTO discounts(invoice_id, kind, description, percent, amount) VALUES (?,?,?,?,?)",
        (invoice_id, kind, description, float(percent or 0), int(amount or 0)))
    recompute_invoice(conn, invoice_id)
    n = conn.execute("SELECT COUNT(*) FROM installments WHERE invoice_id=?", (invoice_id,)).fetchone()[0]
    set_installment_plan(conn, invoice_id, max(n, 1))


def set_installment_plan(conn, invoice_id, n):
    """Chia hoá đơn thành n đợt, mỗi đợt cách nhau 30 ngày kể từ hạn nộp học kỳ."""
    from flask import current_app
    try:
        max_n = current_app.config["MAX_INSTALLMENTS"]
    except RuntimeError:
        max_n = 3
    n = max(1, min(int(n), max_n))
    inv = conn.execute(
        "SELECT i.*, COALESCE(i.due_date, s.due_date) AS eff_due FROM invoices i"
        " JOIN semesters s ON s.id=i.semester_id WHERE i.id=?", (invoice_id,)).fetchone()
    if inv["paid"] > 0:
        raise ValueError("Hoá đơn đã có thanh toán, không thể đổi kế hoạch trả góp.")
    conn.execute("DELETE FROM installments WHERE invoice_id=?", (invoice_id,))
    base = round_money(inv["total"] / n)
    due0 = parse_date(inv["eff_due"])
    remaining = inv["total"]
    for i in range(1, n + 1):
        amt = base if i < n else remaining
        remaining -= amt
        conn.execute("INSERT INTO installments(invoice_id, seq, amount, due_date) VALUES (?,?,?,?)",
                     (invoice_id, i, amt, (due0 + timedelta(days=30 * (i - 1))).isoformat()))
    _refresh_invoice_status(conn, invoice_id)


def late_fee_for(outstanding, due_date, on_date=None, rate=0.0005, max_rate=0.10):
    """Phí trễ hạn = số nợ × lãi suất/ngày × số ngày quá hạn, tối đa max_rate × số nợ."""
    on_date = on_date or today()
    days = (on_date - parse_date(due_date)).days
    if outstanding <= 0 or days <= 0:
        return 0, 0
    fee = min(outstanding * rate * days, outstanding * max_rate)
    return round_money(fee), days


def _fee_cfg():
    from flask import current_app
    try:
        c = current_app.config
        return c["LATE_FEE_RATE_PER_DAY"], c["LATE_FEE_MAX_RATE"]
    except RuntimeError:
        return 0.0005, 0.10


def installment_view(conn, invoice_id, on_date=None):
    """Danh sách đợt nộp kèm số còn nợ, phí trễ hạn tính đến ngày hiện tại."""
    rate, max_rate = _fee_cfg()
    on_date = on_date or today()
    out = []
    for r in conn.execute("SELECT * FROM installments WHERE invoice_id=? ORDER BY seq", (invoice_id,)):
        outstanding = r["amount"] - r["paid_amount"]
        fee, days = late_fee_for(outstanding, r["due_date"], on_date, rate, max_rate)
        d = dict(r)
        d.update(outstanding=outstanding, late_fee=fee, overdue_days=days,
                 overdue=outstanding > 0 and days > 0,
                 days_left=(parse_date(r["due_date"]) - on_date).days)
        out.append(d)
    return out


def invoice_overview(conn, invoice_id, on_date=None):
    inv = conn.execute(
        "SELECT i.*, s.name AS semester_name, s.code AS semester_code, u.full_name, u.student_code,"
        " u.faculty, u.class_name, u.email FROM invoices i"
        " JOIN semesters s ON s.id=i.semester_id JOIN users u ON u.id=i.student_id WHERE i.id=?",
        (invoice_id,)).fetchone()
    if inv is None:
        return None
    insts = installment_view(conn, invoice_id, on_date)
    d = dict(inv)
    d["installments"] = insts
    d["lines"] = conn.execute("SELECT * FROM invoice_lines WHERE invoice_id=?", (invoice_id,)).fetchall()
    d["discounts"] = conn.execute("SELECT * FROM discounts WHERE invoice_id=?", (invoice_id,)).fetchall()
    d["outstanding"] = inv["total"] - inv["paid"]
    d["late_fee_due"] = sum(i["late_fee"] for i in insts)
    d["overdue"] = any(i["overdue"] for i in insts)
    unpaid = [i for i in insts if i["outstanding"] > 0]
    d["next_due"] = unpaid[0] if unpaid else None
    d["display_status"] = "overdue" if d["overdue"] else inv["status"]
    return d


def _refresh_invoice_status(conn, invoice_id):
    inv = conn.execute("SELECT total, paid FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    if inv["paid"] >= inv["total"]:
        st = "paid"
    elif inv["paid"] > 0:
        st = "partial"
    else:
        st = "unpaid"
    conn.execute("UPDATE invoices SET status=? WHERE id=?", (st, invoice_id))


# ---------------------------------------------------------------- giao dịch & đối soát
def create_transaction(conn, invoice_id, installment_ids, method, prefix="VJU", at=None):
    """Tạo giao dịch chờ thanh toán cho các đợt được chọn (gồm cả phí trễ hạn).

    `at` (datetime) chỉ dùng khi nạp dữ liệu lịch sử; mặc định là thời điểm hiện tại.
    """
    at = at or datetime.now()
    view = {i["id"]: i for i in installment_view(conn, invoice_id, at.date())}
    ids = [int(x) for x in installment_ids if int(x) in view and view[int(x)]["outstanding"] > 0]
    if not ids:
        raise ValueError("Không có khoản nào cần thanh toán.")
    # bắt buộc trả theo thứ tự: không được bỏ qua đợt trước
    unpaid_seq = sorted(i["seq"] for i in view.values() if i["outstanding"] > 0)
    chosen_seq = sorted(view[i]["seq"] for i in ids)
    if chosen_seq != unpaid_seq[:len(chosen_seq)]:
        raise ValueError("Vui lòng thanh toán các đợt theo thứ tự (đợt sớm hơn trước).")
    principal = sum(view[i]["outstanding"] for i in ids)
    late_fee = sum(view[i]["late_fee"] for i in ids)
    inv = conn.execute("SELECT student_id FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    txn_ref = f"{prefix}{at:%y%m%d}{_rand(6)}"
    conn.execute(
        "INSERT INTO transactions(txn_ref, invoice_id, student_id, installment_ids, principal, late_fee,"
        " amount, method, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (txn_ref, invoice_id, inv["student_id"], ",".join(map(str, ids)), principal, late_fee,
         principal + late_fee, method, at.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    return conn.execute("SELECT * FROM transactions WHERE txn_ref=?", (txn_ref,)).fetchone()


def apply_payment(conn, txn_ref, received_amount, gateway_txn_no=None, bank_code=None, source="",
                  at=None):
    """Đối soát tự động: ghi nhận tiền về, cập nhật đợt nộp, hoá đơn, phát hành biên lai.

    Hàm có tính idempotent – gọi lại nhiều lần với cùng giao dịch không bị cộng tiền 2 lần.
    Trả về (trạng_thái, thông_điệp).
    """
    txn = conn.execute("SELECT * FROM transactions WHERE txn_ref=?", (txn_ref,)).fetchone()
    if txn is None:
        return "not_found", "Không tìm thấy giao dịch"
    if txn["status"] == "success":
        return "duplicate", "Giao dịch đã được ghi nhận trước đó"
    received_amount = int(received_amount)
    if received_amount != txn["amount"]:
        conn.execute(
            "UPDATE transactions SET status='review', received_amount=?, gateway_txn_no=?, bank_code=?,"
            " note=? WHERE id=?",
            (received_amount, gateway_txn_no, bank_code,
             f"Lệch số tiền: nhận {_fmt(received_amount)}, cần {_fmt(txn['amount'])} ({source})", txn["id"]))
        conn.commit()
        return "review", "Số tiền không khớp, chuyển kế toán kiểm tra"

    paid_at = (at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    ids = [int(x) for x in (txn["installment_ids"] or "").split(",") if x]
    rate, max_rate = _fee_cfg()
    for iid in ids:
        r = conn.execute("SELECT * FROM installments WHERE id=?", (iid,)).fetchone()
        outstanding = r["amount"] - r["paid_amount"]
        fee, _ = late_fee_for(outstanding, r["due_date"], parse_date(txn["created_at"]), rate, max_rate)
        conn.execute(
            "UPDATE installments SET paid_amount=amount, late_fee_paid=late_fee_paid+?, paid_at=?,"
            " status='paid' WHERE id=?", (fee, paid_at, iid))
    conn.execute(
        "UPDATE invoices SET paid=paid+?, late_fee_paid=late_fee_paid+? WHERE id=?",
        (txn["principal"], txn["late_fee"], txn["invoice_id"]))
    _refresh_invoice_status(conn, txn["invoice_id"])
    conn.execute(
        "UPDATE transactions SET status='success', received_amount=?, gateway_txn_no=?, bank_code=?,"
        " paid_at=?, note=? WHERE id=?",
        (received_amount, gateway_txn_no, bank_code, paid_at, f"Đối soát tự động ({source})", txn["id"]))
    receipt_no = f"BL{paid_at[:10].replace('-', '')}{txn['id']:06d}"
    conn.execute("INSERT OR IGNORE INTO receipts(receipt_no, transaction_id) VALUES (?,?)",
                 (receipt_no, txn["id"]))
    notify(conn, txn["student_id"], "Thanh toán thành công",
           f"Đã nhận {_fmt(received_amount)} cho giao dịch {txn_ref}. Biên lai số {receipt_no}.")
    conn.commit()
    return "success", "Thanh toán thành công"


def mark_failed(conn, txn_ref, note=""):
    conn.execute("UPDATE transactions SET status='failed', note=? WHERE txn_ref=? AND status='pending'",
                 (note, txn_ref))
    conn.commit()


def approve_review(conn, txn_id):
    """Kế toán chấp nhận giao dịch lệch tiền: ghi nhận theo đúng số tiền của giao dịch."""
    txn = conn.execute("SELECT * FROM transactions WHERE id=?", (txn_id,)).fetchone()
    if txn and txn["status"] == "review":
        conn.execute("UPDATE transactions SET status='pending' WHERE id=?", (txn_id,))
        return apply_payment(conn, txn["txn_ref"], txn["amount"], txn["gateway_txn_no"],
                             txn["bank_code"], "kế toán duyệt thủ công")
    return "skip", "Không ở trạng thái chờ kiểm tra"


# ---------------------------------------------------------------- thông báo
def notify(conn, user_id, title, body, channel="app"):
    conn.execute("INSERT INTO notifications(user_id, title, body, channel, created_at) VALUES (?,?,?,?,?)",
                 (user_id, title, body, channel, now_str()))


def find_reminders(conn, before_days=7, on_date=None):
    """Các đợt nộp sắp đến hạn (trong before_days ngày) hoặc đã quá hạn."""
    on_date = on_date or today()
    result = []
    for inv in conn.execute("SELECT id FROM invoices WHERE status!='paid'").fetchall():
        ov = invoice_overview(conn, inv["id"], on_date)
        for i in ov["installments"]:
            if i["outstanding"] > 0 and i["days_left"] <= before_days:
                result.append((ov, i))
    return result


# ---------------------------------------------------------------- báo cáo
def semester_report(conn, semester_id, on_date=None):
    rows = conn.execute(
        "SELECT i.id FROM invoices i JOIN users u ON u.id=i.student_id WHERE i.semester_id=?"
        " ORDER BY u.faculty, u.student_code", (semester_id,)).fetchall()
    invoices = [invoice_overview(conn, r["id"], on_date) for r in rows]
    tot = dict(subtotal=0, discount=0, total=0, paid=0, outstanding=0, late_fee_paid=0, late_fee_due=0)
    by_faculty, by_status = {}, {"paid": 0, "partial": 0, "unpaid": 0, "overdue": 0}
    for inv in invoices:
        for k in tot:
            tot[k] += inv[k]
        f = by_faculty.setdefault(inv["faculty"] or "Khác", dict(total=0, paid=0, students=0, done=0))
        f["total"] += inv["total"]
        f["paid"] += inv["paid"]
        f["students"] += 1
        f["done"] += inv["status"] == "paid"
        by_status[inv["display_status"]] += 1
    by_method = conn.execute(
        "SELECT t.method, COUNT(*) AS n, SUM(t.amount) AS amount FROM transactions t"
        " JOIN invoices i ON i.id=t.invoice_id WHERE i.semester_id=? AND t.status='success'"
        " GROUP BY t.method", (semester_id,)).fetchall()
    by_day = conn.execute(
        "SELECT substr(t.paid_at,1,10) AS d, SUM(t.amount) AS amount FROM transactions t"
        " JOIN invoices i ON i.id=t.invoice_id WHERE i.semester_id=? AND t.status='success'"
        " GROUP BY d ORDER BY d", (semester_id,)).fetchall()
    tot["rate"] = round(100 * tot["paid"] / tot["total"], 1) if tot["total"] else 0
    tot["count"] = len(invoices)
    return dict(invoices=invoices, totals=tot, by_faculty=by_faculty, by_status=by_status,
                by_method=by_method, by_day=by_day)
