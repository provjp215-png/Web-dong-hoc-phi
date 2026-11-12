"""Tích hợp kênh thanh toán: VNPay (sandbox), VietQR chuyển khoản, webhook báo có ngân hàng."""
import hashlib
import hmac
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, quote_plus, urlencode

# ======================================================================= VNPay
VNPAY_RESPONSE = {
    "00": "Giao dịch thành công",
    "07": "Trừ tiền thành công, giao dịch bị nghi ngờ",
    "09": "Thẻ/Tài khoản chưa đăng ký Internet Banking",
    "10": "Xác thực thông tin thẻ/tài khoản không đúng quá 3 lần",
    "11": "Hết hạn chờ thanh toán",
    "12": "Thẻ/Tài khoản bị khoá",
    "13": "Nhập sai mật khẩu xác thực (OTP)",
    "24": "Khách hàng huỷ giao dịch",
    "51": "Tài khoản không đủ số dư",
    "65": "Vượt quá hạn mức giao dịch trong ngày",
    "75": "Ngân hàng thanh toán đang bảo trì",
    "79": "Nhập sai mật khẩu thanh toán quá số lần quy định",
    "99": "Lỗi khác",
}


def _vnp_hash(params, secret):
    data = "&".join(f"{k}={quote_plus(str(params[k]))}" for k in sorted(params))
    return hmac.new(secret.encode(), data.encode(), hashlib.sha512).hexdigest()


def vnpay_build_url(cfg, txn, client_ip, return_url):
    """Tạo URL chuyển hướng sang cổng VNPay theo đặc tả phiên bản 2.1.0."""
    now = datetime.now(timezone.utc) + timedelta(hours=7)  # giờ Việt Nam
    params = {
        "vnp_Version": "2.1.0",
        "vnp_Command": "pay",
        "vnp_TmnCode": cfg["VNPAY_TMN_CODE"],
        "vnp_Amount": int(txn["amount"]) * 100,
        "vnp_CurrCode": "VND",
        "vnp_TxnRef": txn["txn_ref"],
        "vnp_OrderInfo": f"Thanh toan hoc phi {txn['txn_ref']}",
        "vnp_OrderType": "250000",  # nhóm hàng: học phí / giáo dục
        "vnp_Locale": "vn",
        "vnp_ReturnUrl": return_url,
        "vnp_IpAddr": client_ip or "127.0.0.1",
        "vnp_CreateDate": now.strftime("%Y%m%d%H%M%S"),
        "vnp_ExpireDate": (now + timedelta(minutes=15)).strftime("%Y%m%d%H%M%S"),
    }
    secure = _vnp_hash(params, cfg["VNPAY_HASH_SECRET"])
    return f"{cfg['VNPAY_URL']}?{urlencode(sorted(params.items()))}&vnp_SecureHash={secure}"


def vnpay_verify(cfg, args):
    """Kiểm tra chữ ký dữ liệu VNPay trả về. Trả về (hợp_lệ, dict_tham_số)."""
    params = {k: v for k, v in args.items()
              if k.startswith("vnp_") and k not in ("vnp_SecureHash", "vnp_SecureHashType")}
    received = args.get("vnp_SecureHash", "")
    expected = _vnp_hash(params, cfg["VNPAY_HASH_SECRET"])
    return hmac.compare_digest(received.lower(), expected.lower()), params


# ======================================================================= VietQR
def _tlv(tag, value):
    return f"{tag}{len(value):02d}{value}"


def _crc16_ccitt(data):
    crc = 0xFFFF
    for b in data.encode():
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return f"{crc:04X}"


def vietqr_payload(bank_bin, account_no, amount, content):
    """Tạo chuỗi VietQR theo chuẩn EMVCo/NAPAS (mã QR động có số tiền và nội dung)."""
    beneficiary = _tlv("00", bank_bin) + _tlv("01", account_no)
    merchant = _tlv("00", "A000000727") + _tlv("01", beneficiary) + _tlv("02", "QRIBFTTA")
    data = (_tlv("00", "01") + _tlv("01", "12") + _tlv("38", merchant) + _tlv("53", "704")
            + _tlv("54", str(int(amount))) + _tlv("58", "VN") + _tlv("62", _tlv("08", content)))
    data += "6304"
    return data + _crc16_ccitt(data)


def qr_svg(text, size=280):
    """Vẽ mã QR thành ảnh SVG ngay trên máy chủ (không cần Internet)."""
    from reportlab.graphics import renderSVG
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing
    w = QrCodeWidget(text, barLevel="M")
    x0, y0, x1, y1 = w.getBounds()
    d = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
    d.add(w)
    return renderSVG.drawToString(d).replace("<svg ", '<svg shape-rendering="crispEdges" ', 1)


def vietqr_image_url(cfg, amount, content):
    """Ảnh mã QR chuẩn VietQR (NAPAS) – mọi app ngân hàng đều quét được."""
    return (f"https://img.vietqr.io/image/{cfg['BANK_ID']}-{cfg['BANK_ACCOUNT_NO']}-compact2.png"
            f"?amount={int(amount)}&addInfo={quote(content)}&accountName={quote(cfg['BANK_ACCOUNT_NAME'])}")


def extract_txn_ref(text, prefix):
    """Tìm mã giao dịch trong nội dung chuyển khoản (ngân hàng có thể thêm chữ khác vào)."""
    m = re.search(rf"{re.escape(prefix)}\d{{6}}[A-Z0-9]{{6}}", (text or "").upper().replace(" ", ""))
    return m.group(0) if m else None


def parse_bank_webhook(payload):
    """Chuẩn hoá dữ liệu báo có từ các dịch vụ phổ biến (SePay, Casso) hoặc định dạng chung.

    Trả về danh sách dict {content, amount, reference}.
    """
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("data"), list):  # dạng Casso: {"data": [{description, amount, tid}]}
        return [{"content": d.get("description", ""), "amount": d.get("amount", 0),
                 "reference": str(d.get("tid") or d.get("id") or "")} for d in payload["data"]]
    if "transferAmount" in payload:             # dạng SePay
        if payload.get("transferType", "in") != "in":
            return []
        return [{"content": payload.get("content", ""), "amount": payload.get("transferAmount", 0),
                 "reference": str(payload.get("referenceCode") or payload.get("id") or "")}]
    return [{"content": payload.get("content", ""), "amount": payload.get("amount", 0),
             "reference": str(payload.get("reference", ""))}]
