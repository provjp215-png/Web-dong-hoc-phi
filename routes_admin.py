"""Trang quản trị hệ thống (Admin).

Nguyên tắc phân tách nhiệm vụ: admin quản lý tài khoản, cấu hình, bảo mật, dữ liệu và phê duyệt;
admin KHÔNG trực tiếp thu tiền hay lập hoá đơn (việc của kế toán).
"""
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
from datetime import datetime, timedelta

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file,
                   session, url_for)
from werkzeug.security import generate_password_hash

import approvals
import audit
import db
import notifier
import services
import sysconfig
from auth import login_required

bp = Blueprint("admin", __name__, url_prefix="/admin")
PAGE_SIZE = 50


def _conn():
    return db.get_db()


def _user(uid):
    u = _conn().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if u is None:
        abort(404)
    return u


# =================================================================== 1. tổng quan
@bp.route("/")
@login_required("admin")
def dashboard():
    conn = _conn()
    day_ago = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    today = datetime.now().strftime("%Y-%m-%d")
    stats = dict(
        by_role={r["role"]: r["n"] for r in conn.execute(
            "SELECT role, COUNT(*) n FROM users WHERE status='active' GROUP BY role")},
        pending_reg=conn.execute("SELECT COUNT(*) FROM users WHERE status='pending'").fetchone()[0],
        pending_appr=conn.execute("SELECT COUNT(*) FROM approvals WHERE status='pending'").fetchone()[0],
        locked=conn.execute("SELECT COUNT(*) FROM users WHERE status='locked' OR locked_until > ?",
                            (services.now_str(),)).fetchone()[0],
        failed_24h=conn.execute("SELECT COUNT(*) FROM audit_logs WHERE action IN ('login_failed','login_blocked',"
                                "'account_locked_auto') AND created_at >= ?", (day_ago,)).fetchone()[0],
        login_24h=conn.execute("SELECT COUNT(*) FROM audit_logs WHERE action='login_success' AND created_at >= ?",
                               (day_ago,)).fetchone()[0],
        txn_today=conn.execute("SELECT COUNT(*), COALESCE(SUM(amount),0) FROM transactions WHERE status='success'"
                               " AND substr(paid_at,1,10)=?", (today,)).fetchone(),
        webhook_24h=conn.execute("SELECT COUNT(*), COALESCE(SUM(ok=0),0) FROM webhook_logs WHERE created_at >= ?",
                                 (day_ago,)).fetchone(),
        review=conn.execute("SELECT COUNT(*) FROM transactions WHERE status='review'").fetchone()[0],
    )
    # cảnh báo bất thường: IP / tài khoản sai mật khẩu nhiều lần trong 24h
    suspicious = conn.execute(
        "SELECT target, ip, COUNT(*) n, MAX(created_at) last FROM audit_logs WHERE action IN"
        " ('login_failed','account_locked_auto','login_blocked') AND created_at >= ? GROUP BY target, ip"
        " HAVING n >= 3 ORDER BY n DESC LIMIT 5", (day_ago,)).fetchall()
    recent = conn.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 10").fetchall()
    days = [(datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
    activity = {d: [0, 0] for d in days}
    for r in conn.execute("SELECT substr(created_at,1,10) d, action, COUNT(*) n FROM audit_logs WHERE"
                          " created_at >= ? AND action IN ('login_success','login_failed') GROUP BY d, action",
                          (days[0],)):
        if r["d"] in activity:
            activity[r["d"]][0 if r["action"] == "login_success" else 1] = r["n"]
    return render_template("admin/dashboard.html", s=stats, suspicious=suspicious, recent=recent,
                           activity=activity, labels=audit.ACTION_LABELS, danger=audit.DANGER)


# =================================================================== 2. tài khoản & phân quyền
@bp.route("/tai-khoan", methods=["GET", "POST"])
@login_required("admin")
def users():
    conn = _conn()
    if request.method == "POST":
        action = request.form["action"]
        if action == "create":
            return _create_staff(conn)
        u = _user(int(request.form["id"]))
        if u["id"] == g.user["id"] and action in ("lock", "role"):
            flash("Bạn không thể tự khoá hoặc tự đổi vai trò của chính mình.", "danger")
            return redirect(request.referrer or url_for(".users"))
        if action == "lock":
            if u["role"] == "admin" and _active_admins(conn) <= 1:
                flash("Phải còn ít nhất một quản trị viên đang hoạt động.", "danger")
            else:
                conn.execute("UPDATE users SET status='locked' WHERE id=?", (u["id"],))
                audit.log(conn, "user_lock", u["username"], request.form.get("reason", ""))
                flash(f"Đã khoá tài khoản {u['username']}.", "success")
        elif action == "unlock":
            conn.execute("UPDATE users SET status='active', failed_attempts=0, locked_until=NULL WHERE id=?",
                         (u["id"],))
            audit.log(conn, "user_unlock", u["username"])
            flash(f"Đã mở khoá tài khoản {u['username']}.", "success")
        elif action == "role":
            role = request.form["role"]
            if role not in services.ROLE_LABEL:
                abort(400)
            if u["role"] == "admin" and role != "admin" and _active_admins(conn) <= 1:
                flash("Phải còn ít nhất một quản trị viên.", "danger")
            elif role == "student" and not u["student_code"]:
                flash("Tài khoản cán bộ không có mã sinh viên nên không thể chuyển thành sinh viên.", "danger")
            else:
                conn.execute("UPDATE users SET role=? WHERE id=?", (role, u["id"]))
                audit.log(conn, "user_role", u["username"],
                          f"{services.ROLE_LABEL[u['role']]} → {services.ROLE_LABEL[role]}")
                flash("Đã đổi vai trò.", "success")
        elif action == "reset_pw":
            conn.execute("UPDATE users SET password_hash=?, must_change_password=1, failed_attempts=0,"
                         " locked_until=NULL WHERE id=?",
                         (generate_password_hash(current_app.config["DEFAULT_PASSWORD"]), u["id"]))
            services.notify(conn, u["id"], "Mật khẩu đã được đặt lại",
                            "Quản trị viên đã đặt lại mật khẩu của bạn. Hãy đăng nhập và đổi mật khẩu mới.")
            audit.log(conn, "user_reset_pw", u["username"])
            flash(f"Đã đặt lại mật khẩu {u['username']} về {current_app.config['DEFAULT_PASSWORD']} "
                  "(bắt đổi ở lần đăng nhập tới).", "success")
        conn.commit()
        return redirect(request.referrer or url_for(".users"))

    role, status, q = request.args.get("role", ""), request.args.get("status", ""), request.args.get("q", "").strip()
    sql, args = "SELECT * FROM users WHERE status != 'pending'", []
    if role:
        sql += " AND role=?"
        args.append(role)
    if status == "temp_locked":
        sql += " AND locked_until > ?"
        args.append(services.now_str())
    elif status:
        sql += " AND status=?"
        args.append(status)
    if q:
        sql += " AND (username LIKE ? OR full_name LIKE ? OR email LIKE ?)"
        args += [f"%{q}%"] * 3
    rows = conn.execute(sql + " ORDER BY CASE role WHEN 'admin' THEN 0 WHEN 'accountant' THEN 1 ELSE 2 END,"
                              " username LIMIT 500", args).fetchall()
    return render_template("admin/users.html", rows=rows, role=role, status=status, q=q, now=services.now_str())


def _active_admins(conn):
    return conn.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND status='active'").fetchone()[0]


def _create_staff(conn):
    f = {k: request.form.get(k, "").strip() for k in ("username", "full_name", "email", "role")}
    if not re.fullmatch(r"[A-Za-z0-9_.]{3,30}", f["username"]):
        flash("Tên đăng nhập 3–30 ký tự, chỉ gồm chữ, số, dấu chấm, gạch dưới.", "danger")
    elif f["role"] not in ("accountant", "admin"):
        flash("Vai trò không hợp lệ.", "danger")
    elif conn.execute("SELECT 1 FROM users WHERE username=?", (f["username"],)).fetchone():
        flash("Tên đăng nhập đã tồn tại.", "danger")
    else:
        pw = current_app.config["DEFAULT_PASSWORD"]
        conn.execute("INSERT INTO users(username, password_hash, role, full_name, email, status,"
                     " must_change_password, created_at) VALUES (?,?,?,?,?,?,1,?)",
                     (f["username"], generate_password_hash(pw), f["role"], f["full_name"] or f["username"],
                      f["email"], "active", services.now_str()))
        audit.log(conn, "user_create", f["username"], services.ROLE_LABEL[f["role"]])
        flash(f"Đã tạo tài khoản {f['username']} – mật khẩu tạm {pw} (bắt đổi khi đăng nhập lần đầu).", "success")
    return redirect(url_for(".users"))


# =================================================================== 3. duyệt đăng ký sinh viên
@bp.route("/duyet-dang-ky", methods=["GET", "POST"])
@login_required("admin")
def registrations():
    conn = _conn()
    if request.method == "POST":
        ids = [int(x) for x in request.form.getlist("ids")]
        approve = request.form["action"] == "approve"
        reason = request.form.get("reason", "").strip() or "Thông tin không khớp hồ sơ sinh viên"
        n = 0
        for uid in ids:
            u = _user(uid)
            if u["status"] != "pending":
                continue
            n += 1
            if approve:
                conn.execute("UPDATE users SET status='active', note=NULL WHERE id=?", (uid,))
                services.notify(conn, uid, "Tài khoản đã được duyệt",
                                "Chào mừng bạn đến với Cổng thanh toán học phí. Học phí sẽ hiển thị sau khi "
                                "phòng Kế hoạch – Tài chính lập hoá đơn học kỳ.")
                body = (f"Chào {u['full_name']},\n\nTài khoản {u['student_code']} đã được duyệt. "
                        f"Đăng nhập tại {current_app.config['BASE_URL']}")
                audit.log(conn, "registration_approve", u["username"], u["full_name"], commit=False)
            else:
                conn.execute("UPDATE users SET status='rejected', note=? WHERE id=?", (reason, uid))
                body = f"Chào {u['full_name']},\n\nĐăng ký tài khoản {u['student_code']} bị từ chối: {reason}."
                audit.log(conn, "registration_reject", u["username"], reason, commit=False)
            try:
                notifier.send_email(current_app.config, u["email"],
                                    f"[{current_app.config['SCHOOL_SHORT']}] Kết quả đăng ký tài khoản", body)
            except Exception:
                pass
        conn.commit()
        flash(f"Đã {'duyệt' if approve else 'từ chối'} {n} đăng ký.", "success" if approve else "info")
        return redirect(url_for(".registrations"))
    pending = conn.execute("SELECT * FROM users WHERE status='pending' ORDER BY created_at").fetchall()
    # đối chiếu nhanh: email / họ tên trùng với tài khoản đã có
    warn = {}
    for p in pending:
        dup = conn.execute("SELECT username FROM users WHERE id!=? AND status!='pending' AND"
                           " (lower(full_name)=lower(?) OR phone=?)", (p["id"], p["full_name"], p["phone"] or "-")
                           ).fetchall()
        if dup:
            warn[p["id"]] = "Trùng họ tên/SĐT với: " + ", ".join(d[0] for d in dup)
    decided = conn.execute("SELECT a.*, u.full_name FROM audit_logs a LEFT JOIN users u ON u.username=a.target"
                           " WHERE a.action IN ('registration_approve','registration_reject')"
                           " ORDER BY a.id DESC LIMIT 20").fetchall()
    return render_template("admin/registrations.html", pending=pending, warn=warn, decided=decided)


# =================================================================== 4. phê duyệt hai cấp
@bp.route("/phe-duyet", methods=["GET", "POST"])
@login_required("admin")
def approval_list():
    conn = _conn()
    if request.method == "POST":
        st, msg = approvals.decide(conn, int(request.form["id"]), request.form["action"] == "approve", g.user,
                                   request.form.get("note", "").strip())
        flash(msg, "success" if st == "approved" else "info" if st == "rejected" else "warning")
        return redirect(url_for(".approval_list"))
    rows = conn.execute(
        "SELECT a.*, r.full_name AS requester, d.full_name AS decider FROM approvals a"
        " JOIN users r ON r.id=a.requested_by LEFT JOIN users d ON d.id=a.decided_by"
        " ORDER BY a.status='pending' DESC, a.id DESC LIMIT 100").fetchall()
    details = {}
    for a in rows:
        p = json.loads(a["payload"] or "{}")
        if a["kind"] == "review_txn":
            t = conn.execute("SELECT t.*, u.full_name, u.student_code FROM transactions t JOIN users u"
                             " ON u.id=t.student_id WHERE t.id=?", (a["ref_id"],)).fetchone()
            details[a["id"]] = dict(p, txn=t)
        else:
            inv = conn.execute("SELECT i.*, u.full_name, u.student_code FROM invoices i JOIN users u"
                               " ON u.id=i.student_id WHERE i.id=?", (a["ref_id"],)).fetchone()
            details[a["id"]] = dict(p, inv=inv, tuition=services.tuition_amount(conn, a["ref_id"]))
    return render_template("admin/approvals.html", rows=rows, details=details, KIND=approvals.KIND_LABEL,
                           ST=approvals.STATUS)


# =================================================================== 5. nhật ký hệ thống
def _audit_query():
    f = {k: request.args.get(k, "").strip() for k in ("user", "action", "group", "from", "to", "ip")}
    sql, args = "SELECT * FROM audit_logs WHERE 1=1", []
    if f["user"]:
        sql += " AND (username LIKE ? OR target LIKE ?)"
        args += [f"%{f['user']}%"] * 2
    if f["action"]:
        sql += " AND action=?"
        args.append(f["action"])
    if f["group"] == "login":
        sql += " AND (action LIKE 'login%' OR action IN ('logout', 'account_locked_auto'))"
    elif f["group"] == "danger":
        sql += f" AND action IN ({','.join('?' * len(audit.DANGER))})"
        args += sorted(audit.DANGER)
    elif f["group"] == "money":
        sql += f" AND action IN ({','.join('?' * len(audit.MONEY))})"
        args += sorted(audit.MONEY)
    if f["ip"]:
        sql += " AND ip=?"
        args.append(f["ip"])
    if f["from"]:
        sql += " AND created_at >= ?"
        args.append(f["from"])
    if f["to"]:
        sql += " AND created_at <= ?"
        args.append(f["to"] + " 23:59:59")
    return f, sql, args


@bp.route("/nhat-ky")
@login_required("admin")
def audit_log():
    f, sql, args = _audit_query()
    page = max(1, request.args.get("page", 1, type=int))
    total = _conn().execute(sql.replace("SELECT *", "SELECT COUNT(*)", 1), args).fetchone()[0]
    rows = _conn().execute(sql + " ORDER BY id DESC LIMIT ? OFFSET ?", args + [PAGE_SIZE, (page - 1) * PAGE_SIZE]
                           ).fetchall()
    return render_template("admin/audit.html", rows=rows, f=f, page=page, total=total,
                           pages=max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE), labels=audit.ACTION_LABELS,
                           danger=audit.DANGER, money=audit.MONEY)


@bp.route("/nhat-ky/xuat.xlsx")
@login_required("admin")
def audit_export():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    f, sql, args = _audit_query()
    rows = _conn().execute(sql + " ORDER BY id DESC LIMIT 20000", args).fetchall()
    wb = Workbook()
    ws = wb.active
    ws.title = "Nhật ký hệ thống"
    ws.append(["Thời gian", "Tài khoản", "Vai trò", "Hành động", "Đối tượng", "Chi tiết", "Địa chỉ IP"])
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="B8021E")
    for r in rows:
        ws.append([r["created_at"], r["username"], services.ROLE_LABEL.get(r["role"], r["role"]),
                   audit.ACTION_LABELS.get(r["action"], r["action"]), r["target"], r["detail"], r["ip"]])
    for col, w in zip("ABCDEFG", [20, 16, 14, 32, 22, 60, 15]):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    audit.log(_conn(), "report_export", "Nhật ký hệ thống", f"{len(rows)} dòng")
    return send_file(buf, as_attachment=True, download_name=f"nhat_ky_he_thong_{datetime.now():%Y%m%d_%H%M}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# =================================================================== 6. nhật ký webhook
@bp.route("/webhook")
@login_required("admin")
def webhooks():
    ok = request.args.get("ok", "")
    sql = "SELECT * FROM webhook_logs" + (" WHERE ok=?" if ok != "" else "") + " ORDER BY id DESC LIMIT 200"
    rows = _conn().execute(sql, (int(ok),) if ok != "" else ()).fetchall()
    pretty = {}
    for r in rows:
        for k in ("payload", "result"):
            try:
                pretty[(r["id"], k)] = json.dumps(json.loads(r[k]), ensure_ascii=False, indent=2)
            except Exception:
                pretty[(r["id"], k)] = r[k]
    cfg = current_app.config
    return render_template("admin/webhooks.html", rows=rows, ok=ok, pretty=pretty,
                           endpoint=f"{cfg['BASE_URL']}{url_for('payment.bank_webhook')}",
                           token=cfg["BANK_WEBHOOK_TOKEN"])


# =================================================================== 7. cấu hình hệ thống
@bp.route("/cau-hinh", methods=["GET", "POST"])
@login_required("admin")
def settings():
    app, conn = current_app._get_current_object(), _conn()
    if request.method == "POST":
        if request.form.get("action") == "regen_token":
            new = secrets.token_urlsafe(24)
            sysconfig.save(app, conn, "BANK_WEBHOOK_TOKEN", new, g.user["username"])
            audit.log(conn, "webhook_token_regen", "BANK_WEBHOOK_TOKEN", "Khoá cũ hết hiệu lực ngay")
            flash("Đã tạo khoá webhook mới. Nhớ cập nhật khoá này trong dịch vụ báo có ngân hàng (SePay/Casso).",
                  "warning")
            return redirect(url_for(".settings"))
        changes, errors = [], []
        for key, label, typ, _, _ in sysconfig.SCHEMA:
            if typ == "secret":
                continue
            try:
                val = sysconfig.parse_form(key, request.form)
            except ValueError as e:
                errors.append(f"{label}: {e}")
                continue
            if val != app.config.get(key):
                old = app.config.get(key)
                sysconfig.save(app, conn, key, val, g.user["username"])
                fmt = (lambda v: f"{v * 100:g}%") if typ == "percent" else (lambda v: "Bật" if v is True else
                                                                             "Tắt" if v is False else v)
                changes.append(f"{label}: {fmt(old)} → {fmt(val)}")
                audit.log(conn, "setting_change", key, f"{fmt(old)} → {fmt(val)}", commit=False)
        conn.commit()
        for e in errors:
            flash(e, "danger")
        flash(f"Đã lưu {len(changes)} thay đổi." if changes else "Không có thay đổi nào.",
              "success" if changes else "info")
        return redirect(url_for(".settings"))
    groups = {}
    for key, label, typ, group, note in sysconfig.SCHEMA:
        val = app.config.get(key)
        groups.setdefault(group, []).append(dict(key=key, label=label, type=typ, note=note,
                                                 value=round(val * 100, 4) if typ == "percent" else val))
    meta = {r["key"]: r for r in conn.execute("SELECT * FROM settings")}
    return render_template("admin/settings.html", groups=groups, meta=meta,
                           vnpay_ready=bool(app.config["VNPAY_TMN_CODE"]), smtp=bool(app.config["SMTP_HOST"]))


# =================================================================== 8. sao lưu & khôi phục
def _backup_dir():
    d = os.path.join(os.path.dirname(current_app.config["DATABASE"]), "backups")
    os.makedirs(d, exist_ok=True)
    return d


def make_backup(label="thu-cong"):
    name = f"hocphi_{datetime.now():%Y%m%d_%H%M%S}_{label}.db"
    dst = sqlite3.connect(os.path.join(_backup_dir(), name))
    _conn().commit()
    _conn().backup(dst)
    dst.close()
    return name


def _safe_backup_path(name):
    name = os.path.basename(name)
    path = os.path.join(_backup_dir(), name)
    if not (name.endswith(".db") and os.path.isfile(path)):
        abort(404)
    return path


def _validate_db(path):
    try:
        c = sqlite3.connect(path)
        ok = c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        c.close()
        return ok and {"users", "invoices", "transactions"} <= tables
    except sqlite3.DatabaseError:
        return False


def _after_data_swap(action, detail):
    """Sau khi khôi phục / tạo lại dữ liệu: nâng cấp lược đồ, nạp lại cấu hình, đăng xuất."""
    app, conn = current_app._get_current_object(), _conn()
    db.init_db(conn)
    db.ensure_admin(conn, app.config["DEFAULT_PASSWORD"])
    sysconfig.load_into(app, conn)
    shutil.rmtree(current_app.config["RECEIPT_DIR"], ignore_errors=True)  # biên lai PDF cũ không còn khớp
    audit.log(conn, action, "CSDL", detail, user=False)
    session.clear()


@bp.route("/sao-luu", methods=["GET", "POST"])
@login_required("admin")
def backup():
    conn = _conn()
    if request.method == "POST":
        action = request.form["action"]
        if action == "create":
            name = make_backup()
            audit.log(conn, "backup_create", name)
            flash(f"Đã tạo bản sao lưu {name}.", "success")
        elif action == "delete":
            path = _safe_backup_path(request.form["name"])
            os.remove(path)
            audit.log(conn, "backup_delete", os.path.basename(path))
            flash("Đã xoá bản sao lưu.", "info")
        elif action in ("restore", "upload"):
            if action == "upload":
                f = request.files.get("file")
                if not f or not f.filename.lower().endswith(".db"):
                    flash("Hãy chọn file .db hợp lệ.", "danger")
                    return redirect(url_for(".backup"))
                name = f"hocphi_{datetime.now():%Y%m%d_%H%M%S}_tai-len.db"
                path = os.path.join(_backup_dir(), name)
                f.save(path)
            else:
                path = _safe_backup_path(request.form["name"])
            if not _validate_db(path):
                flash("File không phải CSDL hợp lệ của hệ thống, đã huỷ khôi phục.", "danger")
                return redirect(url_for(".backup"))
            auto = make_backup("truoc-khoi-phuc")
            who = g.user["username"]
            src = sqlite3.connect(path)
            src.backup(conn)
            src.close()
            _after_data_swap("backup_restore", f"Từ {os.path.basename(path)} (bởi {who}); "
                                               f"bản trước khi khôi phục: {auto}")
            flash(f"Đã khôi phục dữ liệu từ {os.path.basename(path)}. Bản dữ liệu trước đó được lưu tự động "
                  f"thành {auto}. Vui lòng đăng nhập lại.", "success")
            return redirect(url_for("login"))
        elif action == "demo_reset":
            if request.form.get("confirm", "").strip().upper() != "TAO LAI":
                flash('Gõ đúng "TAO LAI" để xác nhận.', "danger")
                return redirect(url_for(".backup"))
            import seed
            auto = make_backup("truoc-tao-lai")
            who = g.user["username"]
            conn.commit()
            conn.execute("PRAGMA foreign_keys = OFF")
            for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"
                                     " AND name NOT LIKE 'sqlite_%'").fetchall():
                conn.execute(f'DROP TABLE IF EXISTS "{t}"')
            conn.commit()
            conn.execute("PRAGMA foreign_keys = ON")
            db.init_db(conn)
            seed.seed(conn)
            # cấu hình bị xoá cùng bảng settings → trả về giá trị mặc định
            from config import Config
            for key, *_ in sysconfig.SCHEMA:
                current_app.config[key] = getattr(Config, key)
            _after_data_swap("demo_reset", f"Bởi {who}; bản trước khi tạo lại: {auto}")
            flash(f"Đã tạo lại dữ liệu demo (bản cũ lưu tại {auto}). Vui lòng đăng nhập lại.", "success")
            return redirect(url_for("login"))
        return redirect(url_for(".backup"))
    files = []
    for name in sorted(os.listdir(_backup_dir()), reverse=True):
        if name.endswith(".db"):
            st = os.stat(os.path.join(_backup_dir(), name))
            files.append(dict(name=name, size=st.st_size, time=datetime.fromtimestamp(st.st_mtime)))
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("users", "invoices", "transactions", "receipts", "audit_logs")}
    size = os.path.getsize(current_app.config["DATABASE"])
    return render_template("admin/backup.html", files=files, counts=counts, size=size)


@bp.route("/sao-luu/tai/<name>")
@login_required("admin")
def backup_download(name):
    path = _safe_backup_path(name)
    return send_file(path, as_attachment=True, download_name=os.path.basename(path))


# =================================================================== 9. nhập sinh viên từ Excel
IMPORT_COLS = ["Mã sinh viên", "Họ và tên", "Ngành", "Lớp", "Email", "Số điện thoại"]


@bp.route("/nhap-sinh-vien/mau.xlsx")
@login_required("admin")
def import_template():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = Workbook()
    ws = wb.active
    ws.title = "Danh sách sinh viên"
    ws.append(IMPORT_COLS)
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="B8021E")
    ws.append(["26CSE0101", "Trần Văn Minh", services.FACULTIES[1], "CSE2601", "minh.tv@sv.demo.local", "0912345678"])
    ws.append(["26BJS0102", "Lê Thị Hoa", services.FACULTIES[0], "BJS2601", "hoa.lt@sv.demo.local", "0987654321"])
    lists = wb.create_sheet("Danh mục ngành")
    for f in services.FACULTIES:
        lists.append([f])
    dv = DataValidation(type="list", formula1=f"='Danh mục ngành'!$A$1:$A${len(services.FACULTIES)}",
                        allow_blank=False, showErrorMessage=True, error="Chọn ngành trong danh sách")
    ws.add_data_validation(dv)
    dv.add("C2:C2000")
    for col, w in zip("ABCDEF", [16, 26, 36, 12, 30, 16]):
        ws.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="mau_nhap_sinh_vien.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.route("/nhap-sinh-vien", methods=["GET", "POST"])
@login_required("admin")
def import_students():
    result = None
    if request.method == "POST":
        from openpyxl import load_workbook
        f = request.files.get("file")
        if not f or not f.filename.lower().endswith(".xlsx"):
            flash("Hãy chọn file Excel (.xlsx) theo mẫu.", "danger")
            return redirect(url_for(".import_students"))
        try:
            ws = load_workbook(f, read_only=True, data_only=True).worksheets[0]
            rows = list(ws.iter_rows(values_only=True))
        except Exception:
            flash("Không đọc được file Excel.", "danger")
            return redirect(url_for(".import_students"))
        header = [str(c or "").strip() for c in rows[0]] if rows else []
        missing = [c for c in IMPORT_COLS[:3] if c not in header]
        if missing:
            flash("File thiếu cột: " + ", ".join(missing) + ". Hãy dùng file mẫu.", "danger")
            return redirect(url_for(".import_students"))
        idx = {c: header.index(c) for c in IMPORT_COLS if c in header}
        conn = _conn()
        pw_hash = generate_password_hash(current_app.config["DEFAULT_PASSWORD"])
        result = dict(added=[], skipped=[])
        seen = set()
        for n, r in enumerate(rows[1:], start=2):
            val = {c: str(r[i]).strip() if i < len(r) and r[i] is not None else "" for c, i in idx.items()}
            code, name, fac = val.get("Mã sinh viên", ""), val.get("Họ và tên", ""), val.get("Ngành", "")
            email = val.get("Email", "").lower()
            if not any(val.values()):
                continue
            reason = None
            if not re.fullmatch(r"[A-Za-z0-9]{6,15}", code):
                reason = "Mã sinh viên không hợp lệ"
            elif code in seen or conn.execute("SELECT 1 FROM users WHERE student_code=? OR username=?",
                                              (code, code)).fetchone():
                reason = "Mã sinh viên đã tồn tại"
            elif len(name.split()) < 2:
                reason = "Thiếu họ tên"
            elif fac not in services.FACULTIES:
                reason = f"Ngành '{fac}' không có trong danh mục"
            elif email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                reason = "Email không hợp lệ"
            if reason:
                result["skipped"].append(dict(row=n, code=code, name=name, reason=reason))
                continue
            seen.add(code)
            conn.execute("INSERT INTO users(username, password_hash, role, full_name, email, phone, student_code,"
                         " faculty, class_name, status, must_change_password, created_at)"
                         " VALUES (?,?,?,?,?,?,?,?,?,'active',1,?)",
                         (code, pw_hash, "student", name, email, val.get("Số điện thoại", ""), code, fac,
                          val.get("Lớp", ""), services.now_str()))
            result["added"].append(dict(row=n, code=code, name=name, faculty=fac))
        audit.log(conn, "student_import", f.filename,
                  f"Thêm {len(result['added'])}, bỏ qua {len(result['skipped'])}", commit=False)
        conn.commit()
    return render_template("admin/import.html", result=result, cols=IMPORT_COLS,
                           default_pw=current_app.config["DEFAULT_PASSWORD"])
