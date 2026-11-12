"""Hệ thống quản lý và thanh toán học phí trực tuyến – điểm khởi chạy ứng dụng.

Chạy:  python app.py          (lần đầu sẽ tự tạo CSDL và dữ liệu mẫu)
"""
import os

import click
from flask import Flask, abort, g, render_template, send_file
from werkzeug.security import generate_password_hash

import auth_routes
import db
import notifier
import services
import sysconfig
from auth import login_required
from config import Config
from receipts import build_receipt_pdf, money_to_words
from services import METHOD_LABEL, STATUS_LABEL


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    app.teardown_appcontext(db.close_db)

    # ---- tạo CSDL lần đầu
    with app.app_context():
        conn = db.get_db()
        db.init_db(conn)
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0 and not app.config.get("NO_SEED"):
            import seed
            seed.seed(conn)
        db.ensure_admin(conn, app.config["DEFAULT_PASSWORD"])
        sysconfig.load_into(app, conn)   # cấu hình admin đã lưu trên giao diện

    # ---- bộ lọc hiển thị
    @app.template_filter("vnd")
    def vnd(n):
        return f"{int(n or 0):,}".replace(",", ".") + " ₫"

    @app.template_filter("words")
    def words(n):
        return money_to_words(n or 0)

    @app.context_processor
    def inject():
        unread = 0
        if g.get("user"):
            unread = db.query("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0",
                              (g.user["id"],), one=True)[0]
        badges = {}
        if g.get("user") and g.user["role"] == "admin":
            badges = dict(
                reg=db.query("SELECT COUNT(*) FROM users WHERE status='pending'", one=True)[0],
                appr=db.query("SELECT COUNT(*) FROM approvals WHERE status='pending'", one=True)[0])
        return dict(STATUS_LABEL=STATUS_LABEL, METHOD_LABEL=METHOD_LABEL, unread=unread, badges=badges,
                    ROLE_LABEL=services.ROLE_LABEL, USER_STATUS_LABEL=services.USER_STATUS_LABEL, cfg_live=app.config,
                    SCHOOL=app.config["SCHOOL_NAME"], SCHOOL_SHORT=app.config["SCHOOL_SHORT"],
                    SCHOOL_EN=app.config["SCHOOL_EN"], DEMO=app.config["DEMO"],
                    SUPPORT={k[8:].lower(): app.config[k] for k in app.config if k.startswith("SUPPORT_")})

    auth_routes.init_app(app)

    # ---- biên lai (dùng chung cho sinh viên và kế toán)
    @app.route("/bien-lai/<receipt_no>.pdf")
    @login_required()
    def receipt_pdf(receipt_no):
        conn = db.get_db()
        r = conn.execute("SELECT * FROM receipts WHERE receipt_no=?", (receipt_no,)).fetchone()
        if r is None:
            abort(404)
        txn = conn.execute("SELECT * FROM transactions WHERE id=?", (r["transaction_id"],)).fetchone()
        if g.user["role"] != "accountant" and txn["student_id"] != g.user["id"]:
            abort(403)
        path = os.path.join(app.config["RECEIPT_DIR"], f"{receipt_no}.pdf")
        if not os.path.exists(path):
            inv = services.invoice_overview(conn, txn["invoice_id"])
            ids = [int(x) for x in (txn["installment_ids"] or "").split(",") if x]
            insts = []
            for i in conn.execute(f"SELECT * FROM installments WHERE id IN ({','.join('?' * len(ids))})"
                                  " ORDER BY seq", ids).fetchall() if ids else []:
                insts.append(dict(seq=i["seq"], amount=i["amount"], late_fee=i["late_fee_paid"]))
            if not insts:  # nộp tại quầy không gắn đợt
                insts = [dict(seq="-", amount=txn["principal"], late_fee=txn["late_fee"])]
            build_receipt_pdf(path, app.config["SCHOOL_NAME"], r, txn, inv, insts,
                              f"{app.config['BASE_URL']}/xac-thuc/{receipt_no}", demo=app.config["DEMO"])
            conn.execute("UPDATE receipts SET file_path=? WHERE id=?", (path, r["id"]))
            conn.commit()
        return send_file(path, mimetype="application/pdf", download_name=f"{receipt_no}.pdf")

    @app.route("/xac-thuc/<receipt_no>")
    def verify_receipt(receipt_no):
        row = db.query(
            "SELECT r.receipt_no, r.issued_at, t.amount, t.txn_ref, t.method, u.full_name, u.student_code"
            " FROM receipts r JOIN transactions t ON t.id=r.transaction_id JOIN users u ON u.id=t.student_id"
            " WHERE r.receipt_no=?", (receipt_no,), one=True)
        return render_template("verify.html", r=row, receipt_no=receipt_no)

    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("error.html", code=403, msg="Bạn không có quyền truy cập trang này."), 403

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("error.html", code=404, msg="Không tìm thấy trang yêu cầu."), 404

    # ---- lệnh dòng lệnh
    @app.cli.command("send-reminders")
    def send_reminders_cmd():
        """Gửi nhắc hạn (có thể đặt lịch chạy hằng ngày bằng Task Scheduler/cron)."""
        stats = notifier.send_reminders(db.get_db(), app.config)
        click.echo(f"Đã tạo {stats['total']} nhắc nhở, gửi {stats['email']} email.")

    @app.cli.command("reset-db")
    def reset_db_cmd():
        """Xoá và tạo lại dữ liệu mẫu."""
        import seed
        db.close_db()
        if os.path.exists(app.config["DATABASE"]):
            os.remove(app.config["DATABASE"])
        conn = db.connect(app.config["DATABASE"])
        db.init_db(conn)
        seed.seed(conn)
        click.echo("Đã tạo lại CSDL mẫu.")

    @app.cli.command("create-admin")
    @click.argument("username")
    @click.argument("password")
    def create_admin_cmd(username, password):
        """Tạo (hoặc đặt lại) tài khoản quản trị: flask --app app create-admin <tên> <mật khẩu>"""
        conn = db.get_db()
        if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            conn.execute("UPDATE users SET password_hash=?, role='admin', status='active', failed_attempts=0,"
                         " locked_until=NULL WHERE username=?", (generate_password_hash(password), username))
        else:
            conn.execute("INSERT INTO users(username, password_hash, role, full_name, status) VALUES (?,?,?,?,?)",
                         (username, generate_password_hash(password), "admin", "Quản trị hệ thống", "active"))
        conn.commit()
        click.echo(f"Đã tạo/cập nhật tài khoản quản trị {username}.")

    from routes_student import bp as student_bp
    from routes_accountant import bp as accountant_bp
    from routes_payment import bp as payment_bp
    from routes_admin import bp as admin_bp
    app.register_blueprint(admin_bp)
    app.register_blueprint(student_bp)
    app.register_blueprint(accountant_bp)
    app.register_blueprint(payment_bp)
    return app


if __name__ == "__main__":
    create_app().run(debug=True, host="127.0.0.1", port=5000)
