"""Cấu hình hệ thống. Có thể ghi đè bằng biến môi trường hoặc file .env."""
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def _load_dotenv():
    """Đọc file .env đơn giản (KEY=VALUE) mà không cần thư viện ngoài."""
    path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "doi-chuoi-nay-khi-trien-khai-that")
    DATABASE = os.getenv("DATABASE", os.path.join(BASE_DIR, "instance", "hocphi.db"))
    RECEIPT_DIR = os.path.join(BASE_DIR, "instance", "receipts")
    BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:5000")

    # Thông tin đơn vị thu (hiển thị trên giao diện, biên lai)
    SCHOOL_NAME = os.getenv("SCHOOL_NAME", "Trường Đại học Việt Nhật – ĐHQGHN")
    SCHOOL_EN = os.getenv("SCHOOL_EN", "VNU Vietnam Japan University")
    SCHOOL_SHORT = os.getenv("SCHOOL_SHORT", "VJU")  # cũng là tiền tố mã giao dịch
    # Hiện nhãn "DEMO" để không bị nhầm với cổng thanh toán chính thức của trường
    DEMO = os.getenv("DEMO", "1") == "1"

    # Thông tin hỗ trợ khi sinh viên không đăng nhập được (sửa trong .env cho đúng thực tế)
    SUPPORT_OFFICE = os.getenv("SUPPORT_OFFICE", "Phòng Đào tạo / Phòng Kế hoạch – Tài chính")
    SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "hotro.hocphi@demo.local")
    SUPPORT_PHONE = os.getenv("SUPPORT_PHONE", "024 0000 0000")
    SUPPORT_HOURS = os.getenv("SUPPORT_HOURS", "Thứ 2 – Thứ 6, 8:00 – 17:00")
    SUPPORT_LOCATION = os.getenv("SUPPORT_LOCATION", "Cơ sở Mỹ Đình, đường Lưu Hữu Phước, Nam Từ Liêm, Hà Nội")
    RESET_TOKEN_MINUTES = 30

    # ---- Nghiệp vụ tài chính ----
    LATE_FEE_RATE_PER_DAY = float(os.getenv("LATE_FEE_RATE_PER_DAY", "0.0005"))  # 0,05%/ngày
    LATE_FEE_MAX_RATE = float(os.getenv("LATE_FEE_MAX_RATE", "0.10"))            # tối đa 10% số nợ
    REMIND_BEFORE_DAYS = int(os.getenv("REMIND_BEFORE_DAYS", "7"))
    MAX_INSTALLMENTS = int(os.getenv("MAX_INSTALLMENTS", "3"))

    # ---- Kênh thanh toán được bật (admin chỉnh trên giao diện) ----
    ENABLE_VIETQR = True
    ENABLE_VNPAY = True
    ENABLE_SIMULATOR = True
    ENABLE_CASH = True

    # ---- Tài khoản & bảo mật ----
    ALLOW_REGISTRATION = True        # sinh viên tự đăng ký, chờ admin duyệt
    LOCKOUT_ATTEMPTS = 5             # sai mật khẩu 5 lần liên tiếp ...
    LOCKOUT_MINUTES = 15             # ... thì khoá tạm 15 phút
    DEFAULT_PASSWORD = "123456"      # mật khẩu khi tạo / đặt lại (bắt đổi ở lần đăng nhập đầu)

    # ---- Phê duyệt hai cấp (maker – checker) ----
    MAKER_CHECKER = True
    APPROVAL_DISCOUNT_PERCENT = 50   # miễn giảm trên 50% học phí phải được admin duyệt

    # ---- VNPay sandbox (đăng ký miễn phí tại sandbox.vnpayment.vn) ----
    VNPAY_TMN_CODE = os.getenv("VNPAY_TMN_CODE", "")
    VNPAY_HASH_SECRET = os.getenv("VNPAY_HASH_SECRET", "")
    VNPAY_URL = os.getenv("VNPAY_URL", "https://sandbox.vnpayment.vn/paymentv2/vpcpay.html")

    # ---- VietQR: tài khoản nhận tiền (mã BIN/tên viết tắt ngân hàng theo VietQR) ----
    BANK_ID = os.getenv("BANK_ID", "970436")          # 970436 = Vietcombank
    BANK_NAME = os.getenv("BANK_NAME", "Vietcombank")
    BANK_ACCOUNT_NO = os.getenv("BANK_ACCOUNT_NO", "0011001234567")
    BANK_ACCOUNT_NAME = os.getenv("BANK_ACCOUNT_NAME", "TAI KHOAN DEMO DO AN")
    # Khoá bí mật để dịch vụ báo có (Casso/SePay...) gọi webhook đối soát
    BANK_WEBHOOK_TOKEN = os.getenv("BANK_WEBHOOK_TOKEN", "demo-webhook-token")

    # ---- Email nhắc hạn (để trống thì chỉ lưu thông báo trong hệ thống) ----
    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_SENDER = os.getenv("SMTP_SENDER", "")
