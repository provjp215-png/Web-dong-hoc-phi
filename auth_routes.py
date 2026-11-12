"""Đăng nhập (có khoá khi sai nhiều lần), đăng ký sinh viên, quên / đổi mật khẩu."""
import re
import secrets
from datetime import datetime, timedelta

from flask import flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import audit
import db
import notifier
import services
from auth import login_required

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _home_for(user):
    return {"admin": "admin.dashboard", "accountant": "accountant.dashboard"}.get(user["role"], "student.dashboard")


def init_app(app):
    @app.before_request
    def load_user():
        uid = session.get("user_id")
        g.user = db.query("SELECT * FROM users WHERE id=?", (uid,), one=True) if uid else None
        if g.user is not None and g.user["status"] != "active":
            session.clear()  # tài khoản vừa bị khoá trong lúc đang đăng nhập
            g.user = None
        if (g.user is not None and g.user["must_change_password"]
                and request.endpoint not in ("change_password", "logout", "static")):
            return redirect(url_for("change_password"))

    @app.route("/")
    def index():
        if g.user is None:
            return redirect(url_for("login"))
        return redirect(url_for(_home_for(g.user)))

    # ------------------------------------------------------------------ đăng nhập
    @app.route("/dang-nhap", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            conn = db.get_db()
            ident = request.form["username"].strip()
            u = conn.execute("SELECT * FROM users WHERE username=? OR student_code=?", (ident, ident)).fetchone()
            now = services.now_str()
            if u is None:
                audit.log(conn, "login_failed", ident, "Tài khoản không tồn tại", user=False)
                flash("Sai tên đăng nhập hoặc mật khẩu.", "danger")
            elif u["locked_until"] and u["locked_until"] > now:
                audit.log(conn, "login_blocked", ident, f"Đang khoá tạm đến {u['locked_until']}", user=u)
                flash(f"Tài khoản tạm khoá do nhập sai mật khẩu nhiều lần. Thử lại sau {u['locked_until'][11:16]}.",
                      "danger")
            elif not check_password_hash(u["password_hash"], request.form["password"]):
                fails = u["failed_attempts"] + 1
                limit = app.config["LOCKOUT_ATTEMPTS"]
                if fails >= limit:
                    until = (datetime.now() + timedelta(minutes=app.config["LOCKOUT_MINUTES"])).strftime(
                        "%Y-%m-%d %H:%M:%S")
                    conn.execute("UPDATE users SET failed_attempts=0, locked_until=? WHERE id=?", (until, u["id"]))
                    audit.log(conn, "account_locked_auto", ident, f"Sai {fails} lần, khoá đến {until}", user=u)
                    flash(f"Bạn đã nhập sai {fails} lần. Tài khoản bị khoá tạm "
                          f"{app.config['LOCKOUT_MINUTES']} phút.", "danger")
                else:
                    conn.execute("UPDATE users SET failed_attempts=? WHERE id=?", (fails, u["id"]))
                    audit.log(conn, "login_failed", ident, f"Sai mật khẩu lần {fails}/{limit}", user=u)
                    flash(f"Sai tên đăng nhập hoặc mật khẩu (còn {limit - fails} lần thử).", "danger")
            elif u["status"] == "pending":
                flash("Tài khoản đang chờ nhà trường duyệt. Bạn sẽ nhận email khi được duyệt.", "warning")
            elif u["status"] == "rejected":
                flash(f"Đăng ký tài khoản bị từ chối: {u['note'] or 'thông tin không khớp hồ sơ'}. "
                      "Vui lòng liên hệ phòng ban hỗ trợ.", "danger")
            elif u["status"] == "locked":
                audit.log(conn, "login_blocked", ident, "Tài khoản bị quản trị viên khoá", user=u)
                flash("Tài khoản đã bị khoá. Vui lòng liên hệ phòng ban hỗ trợ.", "danger")
            else:
                conn.execute("UPDATE users SET failed_attempts=0, locked_until=NULL, last_login_at=? WHERE id=?",
                             (now, u["id"]))
                audit.log(conn, "login_success", ident, request.headers.get("User-Agent", "")[:120], user=u)
                session.clear()
                session["user_id"] = u["id"]
                if u["must_change_password"]:
                    flash("Đây là lần đăng nhập đầu tiên (hoặc mật khẩu vừa được đặt lại). Vui lòng đổi mật khẩu.",
                          "warning")
                    return redirect(url_for("change_password"))
                nxt = request.args.get("next")
                return redirect(nxt if nxt and nxt.startswith("/") else url_for(_home_for(u)))
        return render_template("login.html")

    @app.route("/dang-xuat")
    def logout():
        if g.user:
            audit.log(db.get_db(), "logout", g.user["username"])
        session.clear()
        return redirect(url_for("login"))

    # ------------------------------------------------------------------ đăng ký
    @app.route("/dang-ky", methods=["GET", "POST"])
    def register():
        form, errors, done = {}, {}, False
        if request.method == "POST" and app.config["ALLOW_REGISTRATION"]:
            form = {k: request.form.get(k, "").strip() for k in
                    ("student_code", "full_name", "faculty", "class_name", "email", "phone")}
            pw, pw2 = request.form.get("password", ""), request.form.get("password2", "")
            conn = db.get_db()
            if not re.fullmatch(r"[A-Za-z0-9]{6,15}", form["student_code"]):
                errors["student_code"] = "Mã sinh viên gồm 6–15 chữ hoặc số."
            elif conn.execute("SELECT 1 FROM users WHERE student_code=? OR username=?",
                              (form["student_code"], form["student_code"])).fetchone():
                errors["student_code"] = "Mã sinh viên đã có tài khoản. Hãy dùng chức năng Quên mật khẩu."
            if len(form["full_name"].split()) < 2:
                errors["full_name"] = "Vui lòng nhập đầy đủ họ và tên."
            if form["faculty"] not in services.FACULTIES:
                errors["faculty"] = "Vui lòng chọn ngành học."
            if not EMAIL_RE.match(form["email"]):
                errors["email"] = "Email không hợp lệ."
            elif conn.execute("SELECT 1 FROM users WHERE lower(email)=?", (form["email"].lower(),)).fetchone():
                errors["email"] = "Email đã được dùng cho tài khoản khác."
            if form["phone"] and not re.fullmatch(r"0\d{9,10}", form["phone"]):
                errors["phone"] = "Số điện thoại gồm 10–11 chữ số, bắt đầu bằng 0."
            if len(pw) < 6:
                errors["password"] = "Mật khẩu phải có ít nhất 6 ký tự."
            elif pw != pw2:
                errors["password2"] = "Hai lần nhập mật khẩu không khớp."
            if not request.form.get("agree"):
                errors["agree"] = "Bạn cần xác nhận thông tin là chính xác."
            if not errors:
                conn.execute(
                    "INSERT INTO users(username, password_hash, role, full_name, email, phone, student_code, faculty,"
                    " class_name, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (form["student_code"], generate_password_hash(pw), "student", form["full_name"],
                     form["email"].lower(), form["phone"], form["student_code"], form["faculty"],
                     form["class_name"], "pending", services.now_str()))
                for a in conn.execute("SELECT id FROM users WHERE role='admin' AND status='active'"):
                    services.notify(conn, a["id"], "Có đăng ký tài khoản mới cần duyệt",
                                    f"{form['full_name']} – {form['student_code']} – {form['faculty']}")
                audit.log(conn, "register", form["student_code"], f"{form['full_name']} · {form['email']}",
                          user=False)
                done = True
        return render_template("register.html", form=form, errors=errors, done=done,
                               faculties=services.FACULTIES, open=app.config["ALLOW_REGISTRATION"])

    # ------------------------------------------------------------------ đổi mật khẩu
    @app.route("/doi-mat-khau", methods=["GET", "POST"])
    @login_required()
    def change_password():
        if request.method == "POST":
            cur, pw, pw2 = (request.form.get(k, "") for k in ("current", "password", "password2"))
            if not check_password_hash(g.user["password_hash"], cur):
                flash("Mật khẩu hiện tại không đúng.", "danger")
            elif len(pw) < 6:
                flash("Mật khẩu mới phải có ít nhất 6 ký tự.", "danger")
            elif pw == cur:
                flash("Mật khẩu mới phải khác mật khẩu hiện tại.", "danger")
            elif pw != pw2:
                flash("Hai lần nhập mật khẩu mới không khớp.", "danger")
            else:
                conn = db.get_db()
                conn.execute("UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?",
                             (generate_password_hash(pw), g.user["id"]))
                audit.log(conn, "password_change", g.user["username"])
                flash("Đổi mật khẩu thành công.", "success")
                return redirect(url_for("index"))
        return render_template("change_password.html")

    # ------------------------------------------------------------------ quên mật khẩu
    @app.route("/quen-mat-khau", methods=["GET", "POST"])
    def forgot_password():
        """Nhập mã SV + email đã đăng ký để nhận liên kết đặt lại mật khẩu."""
        demo_link = None
        if request.method == "POST":
            ident = request.form["username"].strip()
            email = request.form["email"].strip().lower()
            conn = db.get_db()
            u = conn.execute("SELECT * FROM users WHERE (username=? OR student_code=?) AND lower(email)=?"
                             " AND status='active'", (ident, ident, email)).fetchone()
            audit.log(conn, "password_forgot", ident, "Khớp hồ sơ" if u else "Không khớp hồ sơ", user=u or False)
            if u:
                token = secrets.token_urlsafe(24)
                expires = datetime.now() + timedelta(minutes=app.config["RESET_TOKEN_MINUTES"])
                conn.execute("INSERT INTO password_resets(token, user_id, expires_at) VALUES (?,?,?)",
                             (token, u["id"], expires.strftime("%Y-%m-%d %H:%M:%S")))
                conn.commit()
                link = f"{app.config['BASE_URL']}{url_for('reset_password', token=token)}"
                sent = False
                try:
                    sent = notifier.send_email(
                        app.config, u["email"], f"[{app.config['SCHOOL_SHORT']}] Đặt lại mật khẩu",
                        f"Chào {u['full_name']},\n\nNhấn vào liên kết sau để đặt lại mật khẩu "
                        f"(hiệu lực {app.config['RESET_TOKEN_MINUTES']} phút):\n{link}\n\n"
                        "Nếu bạn không yêu cầu, hãy bỏ qua email này.")
                except Exception:
                    sent = False
                if not sent and app.config["DEMO"]:
                    demo_link = link  # chưa cấu hình email: hiện liên kết ngay để demo
            # Luôn trả lời giống nhau để không lộ thông tin tài khoản nào tồn tại
            flash("Nếu mã sinh viên và email khớp với hồ sơ, liên kết đặt lại mật khẩu đã được gửi tới email "
                  f"của bạn (hiệu lực {app.config['RESET_TOKEN_MINUTES']} phút).", "success")
        return render_template("forgot_password.html", demo_link=demo_link)

    @app.route("/dat-lai-mat-khau/<token>", methods=["GET", "POST"])
    def reset_password(token):
        conn = db.get_db()
        r = conn.execute("SELECT * FROM password_resets WHERE token=?", (token,)).fetchone()
        valid = r is not None and not r["used"] and r["expires_at"] >= services.now_str()
        if valid and request.method == "POST":
            pw, pw2 = request.form["password"], request.form["password2"]
            if len(pw) < 6:
                flash("Mật khẩu phải có ít nhất 6 ký tự.", "danger")
            elif pw != pw2:
                flash("Hai lần nhập mật khẩu không khớp.", "danger")
            else:
                conn.execute("UPDATE users SET password_hash=?, failed_attempts=0, locked_until=NULL,"
                             " must_change_password=0 WHERE id=?", (generate_password_hash(pw), r["user_id"]))
                conn.execute("UPDATE password_resets SET used=1 WHERE user_id=?", (r["user_id"],))
                services.notify(conn, r["user_id"], "Mật khẩu đã được thay đổi",
                                "Mật khẩu tài khoản của bạn vừa được đặt lại. Nếu không phải bạn, "
                                "hãy liên hệ nhà trường ngay.")
                u = conn.execute("SELECT * FROM users WHERE id=?", (r["user_id"],)).fetchone()
                audit.log(conn, "password_reset", u["username"], "Qua liên kết email", user=u)
                flash("Đặt lại mật khẩu thành công. Hãy đăng nhập bằng mật khẩu mới.", "success")
                return redirect(url_for("login"))
        return render_template("reset_password.html", valid=valid)
