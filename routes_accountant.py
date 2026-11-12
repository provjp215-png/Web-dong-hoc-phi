"""Chức năng dành cho phòng Kế hoạch – Tài chính (kế toán)."""
import csv
import io
from datetime import datetime

from flask import (Blueprint, Response, current_app, flash, g, redirect, render_template, request,
                   send_file, url_for)
from werkzeug.security import generate_password_hash

import approvals
import audit
import db
import notifier
import services
from auth import login_required

bp = Blueprint("accountant", __name__, url_prefix="/kt")

FACULTIES = services.FACULTIES
CATEGORIES = {"tuition": "Học phí", "insurance": "Bảo hiểm", "dorm": "Ký túc xá",
              "service": "Dịch vụ", "other": "Khác"}


def _semesters():
    return db.query("SELECT * FROM semesters ORDER BY code DESC")


def _current_semester_id():
    sid = request.args.get("hk", type=int)
    if sid:
        return sid
    s = _semesters()
    return s[0]["id"] if s else None


# ------------------------------------------------------------------ tổng quan
@bp.route("/")
@login_required("accountant")
def dashboard():
    sid = _current_semester_id()
    rep = services.semester_report(db.get_db(), sid) if sid else None
    recent = db.query("SELECT t.*, u.full_name, u.student_code FROM transactions t JOIN users u"
                      " ON u.id=t.student_id ORDER BY COALESCE(t.paid_at, t.created_at) DESC LIMIT 8")
    review_count = db.query("SELECT COUNT(*) FROM transactions WHERE status='review'", one=True)[0]
    return render_template("accountant/dashboard.html", rep=rep, semesters=_semesters(), sid=sid,
                           recent=recent, review_count=review_count)


# ------------------------------------------------------------------ hoá đơn
@bp.route("/hoa-don")
@login_required("accountant")
def invoices():
    sid = _current_semester_id()
    rep = services.semester_report(db.get_db(), sid) if sid else dict(invoices=[])
    rows = rep["invoices"]
    q = request.args.get("q", "").strip().lower()
    fac = request.args.get("khoa", "")
    st = request.args.get("tt", "")
    if q:
        rows = [r for r in rows if q in r["full_name"].lower() or q in r["student_code"].lower()]
    if fac:
        rows = [r for r in rows if r["faculty"] == fac]
    if st:
        rows = [r for r in rows if r["display_status"] == st]
    return render_template("accountant/invoices.html", rows=rows, semesters=_semesters(), sid=sid,
                           faculties=FACULTIES, q=q, fac=fac, st=st)


@bp.route("/hoa-don/<int:invoice_id>", methods=["GET", "POST"])
@login_required("accountant")
def invoice_detail(invoice_id):
    conn = db.get_db()
    if request.method == "POST":
        action = request.form.get("action")
        try:
            inv_row = conn.execute("SELECT i.code, u.full_name, u.student_code FROM invoices i JOIN users u"
                                   " ON u.id=i.student_id WHERE i.id=?", (invoice_id,)).fetchone()
            who = f"{inv_row['code']} · {inv_row['full_name']}"
            if action == "discount":
                pct = float(request.form.get("percent") or 0)
                amt = int(float(request.form.get("amount") or 0))
                if pct <= 0 and amt <= 0:
                    raise ValueError("Nhập % học phí hoặc số tiền miễn giảm.")
                tuition = services.tuition_amount(conn, invoice_id) or 1
                eff = pct if pct else amt * 100 / tuition
                desc = request.form["description"].strip()
                label = f"{desc} ({f'{pct:g}% học phí' if pct else f'{amt:,}đ'.replace(',', '.')})"
                cfg = current_app.config
                if cfg["MAKER_CHECKER"] and eff > cfg["APPROVAL_DISCOUNT_PERCENT"]:
                    if conn.execute("SELECT paid FROM invoices WHERE id=?", (invoice_id,)).fetchone()[0] > 0:
                        raise ValueError("Hoá đơn đã có thanh toán, không thể thêm miễn giảm.")
                    approvals.request(conn, "discount", invoice_id,
                                      dict(kind=request.form["kind"], description=desc, percent=pct, amount=amt),
                                      f"{who} – {label}", g.user)
                    audit.log(conn, "discount_request", who, label)
                    flash(f"Mức miễn giảm vượt {cfg['APPROVAL_DISCOUNT_PERCENT']}% học phí nên cần quản trị viên "
                          "phê duyệt. Đề nghị đã được gửi.", "warning")
                else:
                    services.add_discount(conn, invoice_id, request.form["kind"], desc, pct, amt)
                    audit.log(conn, "discount_apply", who, label, commit=False)
                    flash("Đã áp dụng miễn giảm / học bổng.", "success")
            elif action == "plan":
                services.set_installment_plan(conn, invoice_id, request.form["n"])
                audit.log(conn, "plan_change", who, f"{request.form['n']} đợt", commit=False)
                flash("Đã cập nhật số đợt nộp.", "success")
            elif action == "cash":
                if not current_app.config["ENABLE_CASH"]:
                    raise ValueError("Thu tiền mặt tại quầy đang bị tắt trong cấu hình hệ thống.")
                ov = services.invoice_overview(conn, invoice_id)
                ids = [i["id"] for i in ov["installments"] if i["outstanding"] > 0][:int(request.form["count"])]
                txn = services.create_transaction(conn, invoice_id, ids, "cash",
                                                  current_app.config["SCHOOL_SHORT"])
                services.apply_payment(conn, txn["txn_ref"], txn["amount"], "QUAY", "CASH", "Thu tại quầy")
                audit.log(conn, "cash_collect", who, f"{txn['txn_ref']} · {txn['amount']:,}đ".replace(",", "."))
                flash(f"Đã thu tiền mặt {txn['amount']:,}đ và phát hành biên lai.".replace(",", "."), "success")
            conn.commit()
        except ValueError as e:
            flash(str(e), "danger")
        return redirect(url_for(".invoice_detail", invoice_id=invoice_id))
    ov = services.invoice_overview(conn, invoice_id)
    txns = conn.execute("SELECT t.*, r.receipt_no FROM transactions t LEFT JOIN receipts r"
                        " ON r.transaction_id=t.id WHERE t.invoice_id=? ORDER BY t.id DESC",
                        (invoice_id,)).fetchall()
    pending = conn.execute("SELECT * FROM approvals WHERE kind='discount' AND ref_id=? AND status='pending'",
                           (invoice_id,)).fetchall()
    return render_template("accountant/invoice_detail.html", inv=ov, txns=txns, pending=pending,
                           max_n=current_app.config["MAX_INSTALLMENTS"], cfg=current_app.config)


@bp.route("/lap-hoa-don", methods=["POST"])
@login_required("accountant")
def generate_invoices():
    """Lập hoá đơn hàng loạt cho mọi sinh viên chưa có hoá đơn trong học kỳ."""
    conn = db.get_db()
    sid = int(request.form["semester_id"])
    credits = int(request.form.get("credits", 15))
    students = conn.execute(
        "SELECT id FROM users WHERE role='student' AND status='active' AND id NOT IN"
        " (SELECT student_id FROM invoices WHERE semester_id=?)", (sid,)).fetchall()
    for s in students:
        services.create_invoice(conn, s["id"], sid, credits)
    audit.log(conn, "invoice_generate", f"Học kỳ #{sid}", f"{len(students)} hoá đơn, {credits} tín chỉ")
    flash(f"Đã lập {len(students)} hoá đơn mới.", "success")
    return redirect(url_for(".fees", hk=sid))


# ------------------------------------------------------------------ yêu cầu thu cho sinh viên chỉ định
def _parse_codes(text):
    import re as _re
    return [c for c in dict.fromkeys(_re.split(r"[\s,;]+", text or "")) if c]


@bp.route("/yeu-cau-thu", methods=["GET", "POST"])
@login_required("accountant")
def charge_request():
    """Tạo yêu cầu đóng tiền cho một hoặc vài sinh viên cụ thể (hoá đơn mới hoặc khoản phát sinh)."""
    conn = db.get_db()
    cfg = current_app.config
    results = None
    form = request.form if request.method == "POST" else request.args
    if request.method == "POST":
        codes = _parse_codes(form.get("codes"))
        sid = int(form["semester_id"])
        item_ids = [int(x) for x in form.getlist("fee_item_ids")]
        credits = int(form.get("credits") or 15)
        n_inst = int(form.get("n_installments") or 1)
        due = form.get("due_date") or None
        note = form.get("note", "").strip()
        extra, errors = [], []
        for d, a in zip(form.getlist("extra_desc"), form.getlist("extra_amount")):
            d, a = d.strip(), a.strip().replace(".", "").replace(",", "")
            if not d and not a:
                continue
            if not d or not a.isdigit() or int(a) <= 0:
                errors.append(f"Khoản phát sinh '{d or '(chưa có tên)'}' cần có tên và số tiền lớn hơn 0.")
            else:
                extra.append((d, int(a)))
        if not codes:
            errors.append("Nhập ít nhất một mã sinh viên.")
        if not item_ids and not extra:
            errors.append("Chọn ít nhất một khoản thu trong danh mục hoặc nhập khoản phát sinh.")
        if due and due < services.today().isoformat():
            errors.append("Hạn nộp không được ở quá khứ.")
        for e in errors:
            flash(f"⚠️ Chưa tạo được yêu cầu thu: {e}", "danger")
        if not errors:
            results = []
            sem = conn.execute("SELECT * FROM semesters WHERE id=?", (sid,)).fetchone()
            st_label = {"pending": "đang chờ duyệt đăng ký", "locked": "đang bị khoá",
                        "rejected": "đã bị từ chối đăng ký"}
            for code in codes:
                u = conn.execute("SELECT * FROM users WHERE student_code=? OR username=?", (code, code)).fetchone()
                if u is None:
                    results.append(dict(code=code, ok=False, msg="Không tìm thấy sinh viên có mã này.",
                                        hint="Kiểm tra lại mã sinh viên (có thể gõ nhầm)."))
                    continue
                if u["role"] != "student":
                    results.append(dict(code=code, name=u["full_name"], ok=False,
                                        msg="Tài khoản này không phải sinh viên."))
                    continue
                if u["status"] != "active":
                    results.append(dict(code=code, name=u["full_name"], ok=False,
                                        msg=f"Tài khoản sinh viên {st_label.get(u['status'], u['status'])}.",
                                        hint="Nhờ quản trị viên duyệt / mở khoá tài khoản trước khi tạo khoản thu."))
                    continue
                try:
                    inv = conn.execute("SELECT * FROM invoices WHERE student_id=? AND semester_id=?",
                                       (u["id"], sid)).fetchone()
                    skipped = []
                    if inv is None:
                        iid = services.create_invoice(conn, u["id"], sid, credits, item_ids, n_inst, exact=True,
                                                      extra_lines=extra, due_date=due)
                        total = conn.execute("SELECT total FROM invoices WHERE id=?", (iid,)).fetchone()[0]
                        how = f"Tạo hoá đơn mới, {min(n_inst, cfg['MAX_INSTALLMENTS'])} đợt"
                    else:
                        iid = inv["id"]
                        total, how, skipped = services.add_charges(conn, iid, item_ids, extra, due, credits)
                    if total <= 0:
                        if skipped:
                            msg = (f"Khoản {', '.join(repr(x) for x in skipped)} đã có trong hoá đơn "
                                   f"{sem['name']} của sinh viên này nên không thu trùng.")
                            hint = ("Nếu thật sự cần thu thêm một lần nữa, hãy nhập ở mục "
                                    "\"4. Khoản phát sinh ngoài danh mục\" với tên và số tiền.")
                        else:
                            msg, hint = "Không có khoản nào để thu.", "Chọn khoản trong danh mục hoặc nhập khoản phát sinh."
                        results.append(dict(code=code, name=u["full_name"], ok=False, msg=msg, hint=hint,
                                            invoice_id=iid))
                        continue
                    ov = services.invoice_overview(conn, iid)
                    nxt = ov["next_due"]["due_date"] if ov["next_due"] else ""
                    money = f"{total:,}đ".replace(",", ".")
                    services.notify(conn, u["id"], f"Yêu cầu đóng học phí mới – {sem['name']}",
                                    f"Phòng Kế hoạch – Tài chính vừa tạo khoản cần nộp {money}"
                                    f"{' (hạn ' + nxt + ')' if nxt else ''}."
                                    f"{' Ghi chú: ' + note if note else ''} Vào mục Học phí & thanh toán để nộp.")
                    audit.log(conn, "charge_request", f"{u['student_code']} · {sem['code']}",
                              f"{money} – {how}{' – ' + note if note else ''}", commit=False)
                    if skipped:
                        how += f". Bỏ qua khoản đã có: {', '.join(skipped)}"
                    results.append(dict(code=code, name=u["full_name"], ok=True, amount=total, msg=how,
                                        invoice_id=iid, due=nxt))
                except ValueError as e:
                    results.append(dict(code=code, name=u["full_name"], ok=False, msg=str(e)))
            conn.commit()
            okr = [r for r in results if r["ok"]]
            bad = [r for r in results if not r["ok"]]
            if okr:
                money = f"{sum(r['amount'] for r in okr):,}".replace(",", ".")
                flash(f"✅ Tạo yêu cầu thu THÀNH CÔNG cho {len(okr)} sinh viên, tổng {money}đ. "
                      "Sinh viên đã nhận được thông báo.", "success")
            if bad:
                flash(f"⚠️ KHÔNG tạo được cho {len(bad)} sinh viên: " +
                      "; ".join(f"{r['code']} – {r['msg'].rstrip('.')}" for r in bad) + ".", "danger")
    semesters = _semesters()
    sid = int(form.get("semester_id") or form.get("hk") or (semesters[0]["id"] if semesters else 0))
    items = conn.execute("SELECT * FROM fee_items ORDER BY mandatory DESC, id").fetchall()
    students = conn.execute("SELECT student_code, full_name, faculty FROM users WHERE role='student'"
                            " AND status='active' ORDER BY student_code").fetchall()
    return render_template("accountant/charge.html", semesters=semesters, sid=sid, items=items,
                           students=students, results=results, form=form, cfg=cfg,
                           categories=CATEGORIES)


# ------------------------------------------------------------------ danh mục khoản thu
@bp.route("/khoan-thu", methods=["GET", "POST"])
@login_required("accountant")
def fees():
    conn = db.get_db()
    if request.method == "POST":
        if request.form.get("action") == "semester":
            conn.execute("INSERT INTO semesters(code, name, start_date, end_date, due_date) VALUES (?,?,?,?,?)",
                         (request.form["code"], request.form["name"], request.form["start_date"],
                          request.form["end_date"], request.form["due_date"]))
            audit.log(conn, "semester_create", request.form["code"], request.form["name"], commit=False)
            flash("Đã thêm học kỳ.", "success")
        elif request.form.get("action") == "delete":
            used = conn.execute("SELECT COUNT(*) FROM invoice_lines WHERE fee_item_id=?",
                                (request.form["id"],)).fetchone()[0]
            if used:
                flash("Khoản thu đã được dùng trong hoá đơn, không thể xoá.", "danger")
            else:
                conn.execute("DELETE FROM fee_items WHERE id=?", (request.form["id"],))
                audit.log(conn, "fee_delete", f"Khoản thu #{request.form['id']}", commit=False)
        else:
            conn.execute("INSERT INTO fee_items(semester_id, name, category, unit_price, per_credit, mandatory)"
                         " VALUES (?,?,?,?,?,?)",
                         (request.form["semester_id"], request.form["name"], request.form["category"],
                          int(request.form["unit_price"]), 1 if request.form.get("per_credit") else 0,
                          1 if request.form.get("mandatory") else 0))
            audit.log(conn, "fee_create", request.form["name"], f"{int(request.form['unit_price']):,}đ", commit=False)
            flash("Đã thêm khoản thu.", "success")
        conn.commit()
        return redirect(url_for(".fees", hk=request.form.get("semester_id") or None))
    sid = _current_semester_id()
    items = conn.execute("SELECT * FROM fee_items WHERE semester_id=?", (sid,)).fetchall()
    n_inv = conn.execute("SELECT COUNT(*) FROM invoices WHERE semester_id=?", (sid,)).fetchone()[0]
    n_stu = conn.execute("SELECT COUNT(*) FROM users WHERE role='student' AND status='active'").fetchone()[0]
    return render_template("accountant/fees.html", items=items, semesters=_semesters(), sid=sid,
                           categories=CATEGORIES, n_inv=n_inv, n_stu=n_stu)


# ------------------------------------------------------------------ sinh viên
@bp.route("/sinh-vien", methods=["GET", "POST"])
@login_required("accountant")
def students():
    conn = db.get_db()
    if request.method == "POST" and request.form.get("action") == "reset_pw":
        uid = int(request.form["id"])
        pw = current_app.config["DEFAULT_PASSWORD"]
        conn.execute("UPDATE users SET password_hash=?, must_change_password=1, failed_attempts=0, locked_until=NULL"
                     " WHERE id=? AND role='student'", (generate_password_hash(pw), uid))
        services.notify(conn, uid, "Mật khẩu đã được đặt lại",
                        "Phòng Kế hoạch – Tài chính đã đặt lại mật khẩu của bạn về mặc định. Hãy đăng nhập và đổi mật khẩu.")
        u = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
        audit.log(conn, "user_reset_pw", u["username"] if u else uid, "Kế toán đặt lại khi sinh viên liên hệ")
        flash(f"Đã đặt lại mật khẩu sinh viên về {pw} (sinh viên phải đổi ở lần đăng nhập tới).", "success")
        return redirect(url_for(".students"))
    if request.method == "POST":
        code = request.form["student_code"].strip()
        try:
            conn.execute(
                "INSERT INTO users(username, password_hash, role, full_name, email, phone, student_code,"
                " faculty, class_name, must_change_password, created_at) VALUES (?,?,?,?,?,?,?,?,?,1,?)",
                (code, generate_password_hash(request.form.get("password") or current_app.config["DEFAULT_PASSWORD"]),
                 "student", request.form["full_name"], request.form.get("email"), request.form.get("phone"), code,
                 request.form["faculty"], request.form.get("class_name"), services.now_str()))
            audit.log(conn, "user_create", code, f"Sinh viên · {request.form['full_name']}")
            flash("Đã thêm sinh viên (mật khẩu mặc định 123456 nếu để trống).", "success")
        except Exception:
            flash("Mã sinh viên đã tồn tại.", "danger")
        return redirect(url_for(".students"))
    rows = conn.execute(
        "SELECT u.*, COALESCE(SUM(i.total-i.paid),0) AS debt FROM users u LEFT JOIN invoices i"
        " ON i.student_id=u.id WHERE u.role='student' AND u.status IN ('active','locked')"
        " GROUP BY u.id ORDER BY u.student_code").fetchall()
    return render_template("accountant/students.html", rows=rows, faculties=FACULTIES)


# ------------------------------------------------------------------ giao dịch & đối soát
@bp.route("/giao-dich", methods=["GET", "POST"])
@login_required("accountant")
def transactions():
    conn = db.get_db()
    if request.method == "POST":
        tid = int(request.form["id"])
        action = request.form["action"]
        t = conn.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()
        who = f"{t['txn_ref']}"
        if action == "approve":
            if current_app.config["MAKER_CHECKER"]:
                summary = (f"{t['txn_ref']} – nhận {t['received_amount'] or 0:,}đ / cần {t['amount']:,}đ"
                           ).replace(",", ".")
                if approvals.request(conn, "review_txn", tid, dict(reason=request.form.get("reason", "")),
                                     summary, g.user):
                    audit.log(conn, "review_request", who, summary)
                    flash("Đã gửi đề nghị duyệt giao dịch lệch tiền tới quản trị viên (phê duyệt hai cấp).", "warning")
                else:
                    flash("Giao dịch này đã có đề nghị đang chờ duyệt.", "info")
            else:
                st_, msg = services.approve_review(conn, tid)
                if st_ == "success":
                    audit.log(conn, "review_approve", who, msg)
                flash(msg, "info")
        elif action == "reject":
            conn.execute("UPDATE transactions SET status='failed', note=COALESCE(note,'')||' – Kế toán từ chối,"
                         " hoàn tiền' WHERE id=?", (tid,))
            audit.log(conn, "review_reject", who, "Kế toán từ chối, hoàn tiền")
            flash("Đã từ chối giao dịch.", "info")
        elif action == "simulate_bank":
            # Giả lập ngân hàng báo có đúng số tiền (dùng khi demo VietQR không có webhook thật)
            amt = int(request.form.get("amount") or t["amount"])
            status, msg = services.apply_payment(conn, t["txn_ref"], amt, f"FT{datetime.now():%y%m%d%H%M%S}",
                                                 "VCB", "Giả lập báo có")
            audit.log(conn, "bank_simulate", t["txn_ref"], f"{amt:,}đ → {msg}".replace(",", "."))
            flash(f"{t['txn_ref']}: {msg}", "success" if status == "success" else "warning")
        return redirect(url_for(".transactions", tt=request.args.get("tt", "")))
    st = request.args.get("tt", "")
    sql = ("SELECT t.*, u.full_name, u.student_code, r.receipt_no FROM transactions t JOIN users u"
           " ON u.id=t.student_id LEFT JOIN receipts r ON r.transaction_id=t.id")
    rows = conn.execute(sql + (" WHERE t.status=?" if st else "") + " ORDER BY t.created_at DESC",
                        (st,) if st else ()).fetchall()
    waiting = approvals.pending_refs(conn, "review_txn")
    return render_template("accountant/transactions.html", rows=rows, st=st, waiting=waiting)


# ------------------------------------------------------------------ nhắc hạn
@bp.route("/nhac-han", methods=["GET", "POST"])
@login_required("accountant")
def reminders():
    conn = db.get_db()
    cfg = current_app.config
    if request.method == "POST":
        stats = notifier.send_reminders(conn, cfg)
        audit.log(conn, "reminder_send", f"{stats['total']} khoản", f"{stats['email']} email")
        msg = f"Đã gửi {stats['total']} nhắc nhở trong hệ thống"
        msg += f", {stats['email']} email." if cfg["SMTP_HOST"] else " (chưa cấu hình SMTP nên không gửi email)."
        flash(msg, "success")
        return redirect(url_for(".reminders"))
    items = services.find_reminders(conn, cfg["REMIND_BEFORE_DAYS"])
    return render_template("accountant/reminders.html", items=items, days=cfg["REMIND_BEFORE_DAYS"],
                           smtp=bool(cfg["SMTP_HOST"]))


# ------------------------------------------------------------------ báo cáo
@bp.route("/bao-cao")
@login_required("accountant")
def report():
    sid = _current_semester_id()
    rep = services.semester_report(db.get_db(), sid)
    sem = db.query("SELECT * FROM semesters WHERE id=?", (sid,), one=True)
    return render_template("accountant/report.html", rep=rep, sem=sem, semesters=_semesters(), sid=sid)


def _report_rows(rep):
    header = ["Mã SV", "Họ tên", "Khoa", "Lớp", "Tín chỉ", "Tổng khoản thu", "Miễn giảm",
              "Phải nộp", "Đã nộp", "Còn nợ", "Phí trễ hạn đã thu", "Phí trễ hạn tạm tính", "Trạng thái"]
    from services import STATUS_LABEL
    rows = [[i["student_code"], i["full_name"], i["faculty"], i["class_name"], i["credits"], i["subtotal"],
             i["discount"], i["total"], i["paid"], i["outstanding"], i["late_fee_paid"], i["late_fee_due"],
             STATUS_LABEL[i["display_status"]][0]] for i in rep["invoices"]]
    return header, rows


@bp.route("/bao-cao/xuat.<fmt>")
@login_required("accountant")
def export(fmt):
    sid = _current_semester_id()
    rep = services.semester_report(db.get_db(), sid)
    sem = db.query("SELECT * FROM semesters WHERE id=?", (sid,), one=True)
    header, rows = _report_rows(rep)
    fname = f"bao_cao_hoc_phi_{sem['code']}"
    audit.log(db.get_db(), "report_export", f"Báo cáo {sem['code']}", fmt.upper())
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        w.writerows(rows)
        return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename={fname}.csv"})
    return send_file(_build_xlsx(sem, rep, header, rows), as_attachment=True,
                     download_name=f"{fname}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _build_xlsx(sem, rep, header, rows):
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Tổng hợp"
    blue = PatternFill("solid", fgColor="B8021E")
    thin = Side(style="thin", color="BFC9D9")
    money = '#,##0'
    ws["A1"] = f"BÁO CÁO THU HỌC PHÍ – {sem['name'].upper()}"
    ws["A1"].font = Font(bold=True, size=14, color="B8021E")
    ws["A2"] = f"Ngày lập: {datetime.now():%d/%m/%Y %H:%M}"
    t = rep["totals"]
    kpis = [("Số hoá đơn", t["count"], None), ("Tổng khoản thu", t["subtotal"], money),
            ("Miễn giảm, học bổng", t["discount"], money), ("Phải thu", t["total"], money),
            ("Đã thu", t["paid"], money), ("Còn phải thu", t["outstanding"], money),
            ("Phí trễ hạn đã thu", t["late_fee_paid"], money), ("Tỷ lệ hoàn thành (%)", t["rate"], None)]
    for i, (k, v, fmt) in enumerate(kpis, start=4):
        ws.cell(i, 1, k).font = Font(bold=True)
        c = ws.cell(i, 2, v)
        if fmt:
            c.number_format = fmt
    r0 = 4 + len(kpis) + 1
    for j, h in enumerate(["Khoa", "Số SV", "Đã hoàn thành", "Phải thu", "Đã thu", "Tỷ lệ (%)"], 1):
        c = ws.cell(r0, j, h)
        c.font, c.fill = Font(bold=True, color="FFFFFF"), blue
    for i, (fac, f) in enumerate(rep["by_faculty"].items(), start=r0 + 1):
        vals = [fac, f["students"], f["done"], f["total"], f["paid"],
                round(100 * f["paid"] / f["total"], 1) if f["total"] else 0]
        for j, v in enumerate(vals, 1):
            c = ws.cell(i, j, v)
            if j in (4, 5):
                c.number_format = money
    last = r0 + len(rep["by_faculty"])
    if rep["by_faculty"]:
        ch = BarChart()
        ch.title = "Phải thu và đã thu theo khoa"
        ch.add_data(Reference(ws, min_col=4, max_col=5, min_row=r0, max_row=last), titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=1, min_row=r0 + 1, max_row=last))
        ch.height, ch.width = 8, 18
        ws.add_chart(ch, f"H{r0}")
    for col, w in zip("ABCDEF", [30, 18, 16, 18, 18, 12]):
        ws.column_dimensions[col].width = w

    ws2 = wb.create_sheet("Chi tiết sinh viên")
    ws2.append(header)
    for c in ws2[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), blue
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for r in rows:
        ws2.append(r)
    for row in ws2.iter_rows(min_row=2):
        for c in row:
            c.border = Border(top=thin, bottom=thin, left=thin, right=thin)
            if 6 <= c.column <= 12:
                c.number_format = money
    widths = [12, 24, 22, 10, 8, 15, 13, 15, 15, 15, 14, 14, 16]
    for i, w in enumerate(widths, 1):
        ws2.column_dimensions[get_column_letter(i)].width = w
    ws2.freeze_panes = "C2"
    ws2.auto_filter.ref = ws2.dimensions

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
