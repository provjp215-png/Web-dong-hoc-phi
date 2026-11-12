"""Nhật ký hệ thống (audit log) – phục vụ truy vết và kiểm soát nội bộ."""
from flask import g, has_request_context, request

from services import now_str

ACTION_LABELS = {
    "login_success": "Đăng nhập thành công",
    "login_failed": "Đăng nhập sai mật khẩu",
    "login_blocked": "Đăng nhập bị chặn",
    "account_locked_auto": "Tự động khoá tạm do sai mật khẩu",
    "logout": "Đăng xuất",
    "register": "Sinh viên đăng ký tài khoản",
    "password_forgot": "Yêu cầu quên mật khẩu",
    "password_reset": "Đặt lại mật khẩu qua liên kết",
    "password_change": "Đổi mật khẩu",
    "user_create": "Tạo tài khoản",
    "user_lock": "Khoá tài khoản",
    "user_unlock": "Mở khoá tài khoản",
    "user_role": "Đổi vai trò",
    "user_reset_pw": "Đặt lại mật khẩu (quản trị)",
    "registration_approve": "Duyệt đăng ký",
    "registration_reject": "Từ chối đăng ký",
    "student_import": "Nhập sinh viên từ Excel",
    "setting_change": "Thay đổi cấu hình",
    "webhook_token_regen": "Tạo lại khoá webhook",
    "backup_create": "Tạo bản sao lưu",
    "backup_restore": "Khôi phục dữ liệu",
    "backup_delete": "Xoá bản sao lưu",
    "demo_reset": "Tạo lại dữ liệu demo",
    "discount_apply": "Áp dụng miễn giảm / học bổng",
    "discount_request": "Đề nghị miễn giảm (chờ duyệt)",
    "plan_change": "Đổi số đợt nộp",
    "cash_collect": "Thu tiền mặt tại quầy",
    "review_approve": "Duyệt giao dịch lệch tiền",
    "review_request": "Đề nghị duyệt giao dịch lệch tiền",
    "review_reject": "Từ chối giao dịch lệch tiền",
    "bank_simulate": "Giả lập ngân hàng báo có",
    "approval_approve": "Admin phê duyệt đề nghị",
    "approval_reject": "Admin từ chối đề nghị",
    "invoice_generate": "Lập hoá đơn hàng loạt",
    "charge_request": "Tạo yêu cầu thu cho sinh viên",
    "fee_create": "Thêm khoản thu",
    "fee_delete": "Xoá khoản thu",
    "semester_create": "Thêm học kỳ",
    "reminder_send": "Gửi nhắc hạn nộp",
    "report_export": "Xuất báo cáo",
    "payment_success": "Thanh toán thành công",
    "payment_cancel": "Huỷ giao dịch",
}

# nhóm hành động để tô màu trên giao diện
DANGER = {"login_failed", "login_blocked", "account_locked_auto", "user_lock", "backup_restore", "demo_reset",
          "review_reject", "approval_reject", "registration_reject"}
MONEY = {"charge_request", "cash_collect", "payment_success", "review_approve", "bank_simulate", "discount_apply", "approval_approve"}


def client_ip():
    if not has_request_context():
        return None
    return (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()


def log(conn, action, target="", detail="", user=None, commit=True):
    """Ghi một dòng nhật ký. `user` mặc định là người đang đăng nhập (g.user)."""
    if user is None and has_request_context():
        user = g.get("user")
    if user:
        uid, uname, role = user["id"], user["username"], user["role"]
    elif user is False or has_request_context():
        uid, uname, role = None, "(khách)", "guest"      # người chưa đăng nhập
    else:
        uid, uname, role = None, "hệ thống", "system"    # tác vụ tự động / dòng lệnh
    conn.execute(
        "INSERT INTO audit_logs(user_id, username, role, action, target, detail, ip, created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (uid, uname, role, action, str(target or ""), str(detail or ""), client_ip(), now_str()))
    if commit:
        conn.commit()
