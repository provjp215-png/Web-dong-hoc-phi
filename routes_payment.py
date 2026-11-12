"""Luồng thanh toán: chọn kênh → VNPay / VietQR / cổng mô phỏng → đối soát → kết quả."""
from flask import (Blueprint, Response, abort, current_app, flash, g, jsonify, redirect,
                   render_template, request, url_for)

import json

import audit
import db
import payments
import services
from auth import login_required

bp = Blueprint("payment", __name__)
CHANNEL_FLAG = {"vnpay": "ENABLE_VNPAY", "vietqr": "ENABLE_VIETQR", "simulator": "ENABLE_SIMULATOR"}


def _wlog(source, http_status, payload, result, ok):
    """Lưu mọi lần ngân hàng / cổng thanh toán gọi vào để admin tra soát."""
    conn = db.get_db()
    conn.execute("INSERT INTO webhook_logs(source, ip, http_status, payload, result, ok, created_at)"
                 " VALUES (?,?,?,?,?,?,?)",
                 (source, audit.client_ip(), http_status, json.dumps(payload, ensure_ascii=False),
                  json.dumps(result, ensure_ascii=False), 1 if ok else 0, services.now_str()))
    conn.commit()


def _channel_off(method, txn_ref):
    if not current_app.config[CHANNEL_FLAG[method]]:
        flash("Kênh thanh toán này đang tạm ngừng. Vui lòng chọn kênh khác.", "warning")
        return redirect(url_for(".start", txn_ref=txn_ref))
    return None


def _txn(txn_ref, check_owner=True):
    t = db.query("SELECT * FROM transactions WHERE txn_ref=?", (txn_ref,), one=True)
    if t is None:
        abort(404)
    if check_owner and g.user["role"] != "accountant" and t["student_id"] != g.user["id"]:
        abort(403)
    return t


def _set_method(txn, method):
    conn = db.get_db()
    conn.execute("UPDATE transactions SET method=? WHERE id=?", (method, txn["id"]))
    conn.commit()


@bp.route("/thanh-toan/<txn_ref>")
@login_required()
def start(txn_ref):
    t = _txn(txn_ref)
    if t["status"] != "pending":
        return redirect(url_for(".result", txn_ref=txn_ref))
    inv = services.invoice_overview(db.get_db(), t["invoice_id"])
    return render_template("payment/choose.html", t=t, inv=inv, cfg=current_app.config,
                           vnpay_ready=bool(current_app.config["VNPAY_TMN_CODE"]))


# --------------------------------------------------------------------- VNPay
@bp.route("/thanh-toan/<txn_ref>/vnpay")
@login_required()
def vnpay(txn_ref):
    t = _txn(txn_ref)
    cfg = current_app.config
    if (off := _channel_off("vnpay", txn_ref)):
        return off
    if not cfg["VNPAY_TMN_CODE"]:
        flash("Chưa cấu hình VNPAY_TMN_CODE / VNPAY_HASH_SECRET trong file .env", "warning")
        return redirect(url_for(".start", txn_ref=txn_ref))
    _set_method(t, "vnpay")
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    return redirect(payments.vnpay_build_url(cfg, t, ip, url_for(".vnpay_return", _external=True)))


@bp.route("/vnpay/return")
def vnpay_return():
    ok, p = payments.vnpay_verify(current_app.config, request.args)
    ref = p.get("vnp_TxnRef", "")
    if not ok:
        _wlog("vnpay_return", 400, dict(request.args), {"error": "Sai chữ ký"}, False)
        flash("Chữ ký dữ liệu VNPay không hợp lệ!", "danger")
        return redirect(url_for("index"))
    conn = db.get_db()
    if p.get("vnp_ResponseCode") == "00" and p.get("vnp_TransactionStatus", "00") == "00":
        # Trên máy cá nhân VNPay không gọi được IPN, nên đối soát luôn tại đây (có kiểm tra chữ ký)
        st, msg = services.apply_payment(conn, ref, int(p.get("vnp_Amount", 0)) // 100,
                                         p.get("vnp_TransactionNo"), p.get("vnp_BankCode"), "VNPay return")
        if st == "success":
            audit.log(conn, "payment_success", ref, "VNPay")
    else:
        st = "failed"
        msg = payments.VNPAY_RESPONSE.get(p.get("vnp_ResponseCode"), "Lỗi")
        services.mark_failed(conn, ref, msg)
    _wlog("vnpay_return", 200, dict(request.args), {"txn_ref": ref, "status": st, "message": msg}, st != "failed")
    return redirect(url_for(".result", txn_ref=ref))


@bp.route("/vnpay/ipn")
def vnpay_ipn():
    """VNPay gọi server-to-server để báo kết quả (cần địa chỉ public, VD qua ngrok)."""
    rsp = _vnpay_ipn()
    _wlog("vnpay_ipn", 200, dict(request.args), rsp, rsp["RspCode"] in ("00", "02"))
    return jsonify(rsp)


def _vnpay_ipn():
    ok, p = payments.vnpay_verify(current_app.config, request.args)
    if not ok:
        return dict(RspCode="97", Message="Invalid Checksum")
    t = db.query("SELECT * FROM transactions WHERE txn_ref=?", (p.get("vnp_TxnRef"),), one=True)
    if t is None:
        return dict(RspCode="01", Message="Order not Found")
    if int(p.get("vnp_Amount", 0)) // 100 != t["amount"]:
        return dict(RspCode="04", Message="Invalid amount")
    if t["status"] == "success":
        return dict(RspCode="02", Message="Order already confirmed")
    conn = db.get_db()
    if p.get("vnp_ResponseCode") == "00" and p.get("vnp_TransactionStatus") == "00":
        services.apply_payment(conn, t["txn_ref"], t["amount"], p.get("vnp_TransactionNo"),
                               p.get("vnp_BankCode"), "VNPay IPN")
    else:
        services.mark_failed(conn, t["txn_ref"], payments.VNPAY_RESPONSE.get(p.get("vnp_ResponseCode"), "Lỗi"))
    return dict(RspCode="00", Message="Confirm Success")


# --------------------------------------------------------------------- VietQR
@bp.route("/thanh-toan/<txn_ref>/vietqr")
@login_required()
def vietqr(txn_ref):
    t = _txn(txn_ref)
    if t["status"] != "pending":
        return redirect(url_for(".result", txn_ref=txn_ref))
    if (off := _channel_off("vietqr", txn_ref)):
        return off
    _set_method(t, "vietqr")
    cfg = current_app.config
    return render_template("payment/vietqr.html", t=t, cfg=cfg,
                           qr_url=url_for(".vietqr_image", txn_ref=txn_ref),
                           qr_online=payments.vietqr_image_url(cfg, t["amount"], t["txn_ref"]))


@bp.route("/thanh-toan/<txn_ref>/vietqr.svg")
@login_required()
def vietqr_image(txn_ref):
    """Mã VietQR tự sinh theo chuẩn NAPAS – mọi ứng dụng ngân hàng đều quét được."""
    t = _txn(txn_ref)
    cfg = current_app.config
    payload = payments.vietqr_payload(cfg["BANK_ID"], cfg["BANK_ACCOUNT_NO"], t["amount"], t["txn_ref"])
    return Response(payments.qr_svg(payload), mimetype="image/svg+xml")


@bp.route("/webhook/bank", methods=["POST"])
def bank_webhook():
    """Nhận báo có từ dịch vụ theo dõi tài khoản ngân hàng (SePay / Casso / tự xây dựng)."""
    token = current_app.config["BANK_WEBHOOK_TOKEN"]
    auth = request.headers.get("Authorization", "")
    payload = request.get_json(silent=True)
    if payload is None:
        payload = {"raw": request.get_data(as_text=True)[:2000]}
    if not token or not (auth.split(" ")[-1] == token or request.headers.get("Secure-Token") == token):
        _wlog("bank", 401, payload, {"error": "Sai khoá xác thực"}, False)
        return jsonify(success=False, message="Unauthorized"), 401
    results = []
    conn = db.get_db()
    for item in payments.parse_bank_webhook(request.get_json(silent=True) or {}):
        ref = payments.extract_txn_ref(item["content"], current_app.config["SCHOOL_SHORT"])
        if not ref:
            results.append(dict(content=item["content"], status="no_ref"))
            continue
        status, msg = services.apply_payment(conn, ref, item["amount"], item["reference"], "BANK",
                                             "Webhook ngân hàng")
        results.append(dict(txn_ref=ref, status=status, message=msg))
        if status == "success":
            audit.log(conn, "payment_success", ref, f"Webhook ngân hàng · {item['amount']}", user=False)
    _wlog("bank", 200, payload, results, bool(results) and all(r["status"] in ("success", "duplicate")
                                                               for r in results))
    return jsonify(success=True, results=results)


# ------------------------------------------------------------- cổng mô phỏng
@bp.route("/thanh-toan/<txn_ref>/mo-phong", methods=["GET", "POST"])
@login_required()
def simulator(txn_ref):
    """Cổng thanh toán giả lập để demo khi chưa có tài khoản sandbox / không có mạng."""
    t = _txn(txn_ref)
    if t["status"] != "pending":
        return redirect(url_for(".result", txn_ref=txn_ref))
    if (off := _channel_off("simulator", txn_ref)):
        return off
    if request.method == "POST":
        _set_method(t, "simulator")
        conn = db.get_db()
        if request.form.get("action") == "success":
            services.apply_payment(conn, txn_ref, t["amount"], "SIM" + txn_ref[-6:],
                                   request.form.get("bank", "NCB"), "Cổng mô phỏng")
            audit.log(conn, "payment_success", txn_ref, "Cổng mô phỏng")
        else:
            services.mark_failed(conn, txn_ref, "Khách hàng huỷ giao dịch")
        return redirect(url_for(".result", txn_ref=txn_ref))
    return render_template("payment/simulator.html", t=t)


# --------------------------------------------------------------------- chung
@bp.route("/api/giao-dich/<txn_ref>/trang-thai")
@login_required()
def status_api(txn_ref):
    t = _txn(txn_ref)
    return jsonify(status=t["status"], redirect=url_for(".result", txn_ref=txn_ref))


@bp.route("/thanh-toan/<txn_ref>/huy", methods=["POST"])
@login_required()
def cancel(txn_ref):
    t = _txn(txn_ref)
    services.mark_failed(db.get_db(), txn_ref, "Người dùng huỷ")
    audit.log(db.get_db(), "payment_cancel", txn_ref)
    return redirect(url_for("student.invoice", invoice_id=t["invoice_id"])
                    if g.user["role"] == "student" else url_for("accountant.transactions"))


@bp.route("/thanh-toan/<txn_ref>/ket-qua")
@login_required()
def result(txn_ref):
    t = _txn(txn_ref)
    r = db.query("SELECT * FROM receipts WHERE transaction_id=?", (t["id"],), one=True)
    return render_template("payment/result.html", t=t, receipt=r)
