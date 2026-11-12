"""Gửi nhắc hạn nộp học phí qua email (SMTP) và lưu thông báo trong hệ thống."""
import smtplib
from email.mime.text import MIMEText

import services


def send_email(cfg, to, subject, body):
    if not (cfg.get("SMTP_HOST") and to):
        return False
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = cfg.get("SMTP_SENDER") or cfg.get("SMTP_USER")
    msg["To"] = to
    with smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=15) as s:
        s.starttls()
        if cfg.get("SMTP_USER"):
            s.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
        s.send_message(msg)
    return True


def _vnd(n):
    return f"{int(n):,}".replace(",", ".") + "đ"


def send_reminders(conn, cfg, on_date=None):
    """Tạo thông báo + gửi email cho mọi đợt nộp sắp đến hạn / quá hạn. Trả về thống kê."""
    stats = dict(total=0, email=0, email_error=0)
    for inv, inst in services.find_reminders(conn, cfg["REMIND_BEFORE_DAYS"], on_date):
        if inst["overdue"]:
            title = f"Học phí đợt {inst['seq']} đã quá hạn {inst['overdue_days']} ngày"
            body = (f"Chào {inv['full_name']},\n\nHọc phí đợt {inst['seq']} – {inv['semester_name']} "
                    f"({_vnd(inst['outstanding'])}) đã quá hạn từ {inst['due_date']}. "
                    f"Phí trễ hạn tạm tính: {_vnd(inst['late_fee'])}. "
                    f"Vui lòng thanh toán sớm tại {cfg['BASE_URL']} để tránh bị khoá đăng ký học phần.")
        else:
            title = f"Nhắc hạn nộp học phí đợt {inst['seq']} (còn {inst['days_left']} ngày)"
            body = (f"Chào {inv['full_name']},\n\nHọc phí đợt {inst['seq']} – {inv['semester_name']} "
                    f"số tiền {_vnd(inst['outstanding'])} đến hạn ngày {inst['due_date']}. "
                    f"Bạn có thể thanh toán bằng VNPay hoặc quét mã VietQR tại {cfg['BASE_URL']}.")
        services.notify(conn, inv["student_id"], title, body, "app")
        stats["total"] += 1
        try:
            if send_email(cfg, inv["email"], f"[{cfg['SCHOOL_SHORT']}] {title}", body):
                stats["email"] += 1
        except Exception:  # lỗi mạng/SMTP không làm hỏng việc nhắc trong hệ thống
            stats["email_error"] += 1
    conn.commit()
    return stats
