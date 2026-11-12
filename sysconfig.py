"""Cấu hình hệ thống chỉnh trên giao diện admin.

Giá trị mặc định lấy từ config.py / .env; khi admin lưu, giá trị được ghi vào bảng `settings`
và ghi đè lên app.config ngay lập tức (không cần khởi động lại).
"""
from services import now_str

# (khoá, nhãn, kiểu, nhóm, ghi chú)   kiểu: percent | int | bool | str | secret
SCHEMA = [
    ("LATE_FEE_RATE_PER_DAY", "Lãi phí trễ hạn mỗi ngày", "percent", "Nghiệp vụ học phí", "% trên số tiền quá hạn"),
    ("LATE_FEE_MAX_RATE", "Phí trễ hạn tối đa", "percent", "Nghiệp vụ học phí", "% trên số tiền quá hạn"),
    ("MAX_INSTALLMENTS", "Số đợt trả góp tối đa", "int", "Nghiệp vụ học phí", "1 – 6"),
    ("REMIND_BEFORE_DAYS", "Nhắc hạn trước", "int", "Nghiệp vụ học phí", "số ngày"),

    ("ENABLE_VIETQR", "Cho phép chuyển khoản VietQR", "bool", "Kênh thanh toán", ""),
    ("ENABLE_VNPAY", "Cho phép cổng VNPay", "bool", "Kênh thanh toán", "cần mã sandbox trong .env"),
    ("ENABLE_SIMULATOR", "Cho phép cổng mô phỏng (demo)", "bool", "Kênh thanh toán", ""),
    ("ENABLE_CASH", "Cho phép thu tiền mặt tại quầy", "bool", "Kênh thanh toán", ""),

    ("BANK_ID", "Mã BIN ngân hàng nhận tiền", "str", "Tài khoản nhận tiền (VietQR)", "VD 970436 = Vietcombank"),
    ("BANK_NAME", "Tên ngân hàng", "str", "Tài khoản nhận tiền (VietQR)", ""),
    ("BANK_ACCOUNT_NO", "Số tài khoản", "str", "Tài khoản nhận tiền (VietQR)", ""),
    ("BANK_ACCOUNT_NAME", "Tên chủ tài khoản", "str", "Tài khoản nhận tiền (VietQR)", "viết hoa không dấu"),
    ("BANK_WEBHOOK_TOKEN", "Khoá bí mật webhook ngân hàng", "secret", "Tài khoản nhận tiền (VietQR)", ""),

    ("ALLOW_REGISTRATION", "Cho phép sinh viên tự đăng ký", "bool", "Tài khoản & bảo mật", "tài khoản mới phải chờ admin duyệt"),
    ("LOCKOUT_ATTEMPTS", "Khoá tài khoản sau số lần sai mật khẩu", "int", "Tài khoản & bảo mật", ""),
    ("LOCKOUT_MINUTES", "Thời gian khoá tạm", "int", "Tài khoản & bảo mật", "phút"),

    ("MAKER_CHECKER", "Bật phê duyệt hai cấp", "bool", "Phê duyệt hai cấp", "kế toán đề nghị – admin phê duyệt"),
    ("APPROVAL_DISCOUNT_PERCENT", "Miễn giảm vượt mức này cần admin duyệt", "int", "Phê duyệt hai cấp", "% học phí"),

    ("SUPPORT_OFFICE", "Đơn vị hỗ trợ", "str", "Thông tin liên hệ hỗ trợ", ""),
    ("SUPPORT_PHONE", "Điện thoại", "str", "Thông tin liên hệ hỗ trợ", ""),
    ("SUPPORT_EMAIL", "Email", "str", "Thông tin liên hệ hỗ trợ", ""),
    ("SUPPORT_HOURS", "Giờ làm việc", "str", "Thông tin liên hệ hỗ trợ", ""),
    ("SUPPORT_LOCATION", "Địa điểm", "str", "Thông tin liên hệ hỗ trợ", ""),
]
TYPES = {k: t for k, _, t, _, _ in SCHEMA}
LIMITS = {"MAX_INSTALLMENTS": (1, 6), "REMIND_BEFORE_DAYS": (0, 60), "LOCKOUT_ATTEMPTS": (3, 20),
          "LOCKOUT_MINUTES": (1, 1440), "APPROVAL_DISCOUNT_PERCENT": (0, 100)}


def to_python(key, raw):
    t = TYPES[key]
    if t == "percent":
        return float(raw)
    if t == "int":
        return int(float(raw))
    if t == "bool":
        return str(raw) in ("1", "true", "True", "on")
    return str(raw)


def load_into(app, conn):
    for row in conn.execute("SELECT key, value FROM settings"):
        if row["key"] in TYPES:
            app.config[row["key"]] = to_python(row["key"], row["value"])


def parse_form(key, form):
    """Đọc giá trị từ form (percent hiển thị dạng %, lưu dạng tỉ lệ). Trả về giá trị Python."""
    t = TYPES[key]
    if t == "bool":
        return key in form
    raw = form.get(key, "").strip()
    if t == "percent":
        return round(float(raw.replace(",", ".")) / 100, 6)
    if t == "int":
        v = int(raw)
        lo, hi = LIMITS.get(key, (0, 10 ** 9))
        if not lo <= v <= hi:
            raise ValueError(f"Giá trị phải trong khoảng {lo} – {hi}")
        return v
    if not raw and t != "secret":
        raise ValueError("Không được để trống")
    return raw


def save(app, conn, key, value, username):
    stored = "1" if value is True else "0" if value is False else str(value)
    conn.execute("INSERT INTO settings(key, value, updated_at, updated_by) VALUES (?,?,?,?)"
                 " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at,"
                 " updated_by=excluded.updated_by", (key, stored, now_str(), username))
    app.config[key] = value
