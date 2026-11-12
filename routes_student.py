"""Chức năng dành cho sinh viên."""
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

import db
import services
from auth import login_required

bp = Blueprint("student", __name__, url_prefix="/sv")


def _own_invoice(invoice_id):
    inv = db.query("SELECT * FROM invoices WHERE id=?", (invoice_id,), one=True)
    if inv is None:
        abort(404)
    if inv["student_id"] != g.user["id"]:
        abort(403)
    return inv


@bp.route("/")
@login_required("student")
def dashboard():
    conn = db.get_db()
    rows = conn.execute(
        "SELECT i.id FROM invoices i JOIN semesters s ON s.id=i.semester_id WHERE i.student_id=?"
        " ORDER BY s.code DESC", (g.user["id"],)).fetchall()
    invoices = [services.invoice_overview(conn, r["id"]) for r in rows]
    totals = dict(total=sum(i["total"] for i in invoices), paid=sum(i["paid"] for i in invoices),
                  outstanding=sum(i["outstanding"] for i in invoices),
                  late_fee=sum(i["late_fee_due"] for i in invoices))
    upcoming = sorted([(inv, i) for inv in invoices for i in inv["installments"] if i["outstanding"] > 0],
                      key=lambda x: x[1]["due_date"])
    txns = conn.execute("SELECT t.*, r.receipt_no FROM transactions t LEFT JOIN receipts r"
                        " ON r.transaction_id=t.id WHERE t.student_id=? ORDER BY COALESCE(t.paid_at, t.created_at) DESC LIMIT 5",
                        (g.user["id"],)).fetchall()
    notes = conn.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 4",
                         (g.user["id"],)).fetchall()
    return render_template("student/dashboard.html", invoices=invoices, totals=totals,
                           upcoming=upcoming, txns=txns, notes=notes,
                           remind_days=current_app.config["REMIND_BEFORE_DAYS"])


@bp.route("/hoc-phi/<int:invoice_id>")
@login_required("student")
def invoice(invoice_id):
    _own_invoice(invoice_id)
    ov = services.invoice_overview(db.get_db(), invoice_id)
    return render_template("student/invoice.html", inv=ov, cfg=current_app.config)


@bp.route("/hoc-phi/<int:invoice_id>/tra-gop", methods=["POST"])
@login_required("student")
def installment_plan(invoice_id):
    _own_invoice(invoice_id)
    conn = db.get_db()
    try:
        services.set_installment_plan(conn, invoice_id, request.form.get("n", 1))
        conn.commit()
        flash("Đã cập nhật kế hoạch nộp học phí.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for(".invoice", invoice_id=invoice_id))


@bp.route("/thanh-toan", methods=["POST"])
@login_required("student")
def checkout():
    invoice_id = int(request.form["invoice_id"])
    _own_invoice(invoice_id)
    conn = db.get_db()
    try:
        txn = services.create_transaction(conn, invoice_id, request.form.getlist("installment_ids"),
                                          request.form.get("method", "vietqr"),
                                          current_app.config["SCHOOL_SHORT"])
    except ValueError as e:
        flash(str(e), "danger")
        return redirect(url_for(".invoice", invoice_id=invoice_id))
    return redirect(url_for("payment.start", txn_ref=txn["txn_ref"]))


@bp.route("/lich-su")
@login_required("student")
def history():
    txns = db.query("SELECT t.*, r.receipt_no, s.name AS semester_name FROM transactions t"
                    " JOIN invoices i ON i.id=t.invoice_id JOIN semesters s ON s.id=i.semester_id"
                    " LEFT JOIN receipts r ON r.transaction_id=t.id WHERE t.student_id=?"
                    " ORDER BY COALESCE(t.paid_at, t.created_at) DESC",
                    (g.user["id"],))
    return render_template("student/history.html", txns=txns)


@bp.route("/thong-bao")
@login_required()
def notifications():
    conn = db.get_db()
    notes = conn.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC", (g.user["id"],)).fetchall()
    conn.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (g.user["id"],))
    conn.commit()
    return render_template("student/notifications.html", notes=notes)
