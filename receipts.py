"""Phát hành biên lai điện tử dạng PDF (có mã QR để tra cứu tính xác thực)."""
import os

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A5, landscape
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
_FONT_CANDIDATES = [
    (os.path.join(BASE_DIR, "fonts", "DejaVuSans.ttf"), os.path.join(BASE_DIR, "fonts", "DejaVuSans-Bold.ttf")),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
]
_font_ready = False


def _register_fonts():
    global _font_ready
    if _font_ready:
        return
    for regular, bold in _FONT_CANDIDATES:
        if os.path.exists(regular) and os.path.exists(bold):
            pdfmetrics.registerFont(TTFont("VN", regular))
            pdfmetrics.registerFont(TTFont("VN-Bold", bold))
            _font_ready = True
            return
    raise RuntimeError("Không tìm thấy font tiếng Việt (đặt DejaVuSans.ttf vào thư mục fonts/)")


# --------------------------------------------------------------- đọc số thành chữ
_DIGITS = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]


def _read_triple(n, full):
    h, t, u = n // 100, (n // 10) % 10, n % 10
    words = []
    if full or h:
        words += [_DIGITS[h], "trăm"]
    if t == 0:
        if u and (full or h):
            words.append("lẻ")
    elif t == 1:
        words.append("mười")
    else:
        words += [_DIGITS[t], "mươi"]
    if u:
        if u == 1 and t > 1:
            words.append("mốt")
        elif u == 5 and t > 0:
            words.append("lăm")
        elif u == 4 and t > 1:
            words.append("tư")
        else:
            words.append(_DIGITS[u])
    return words


def money_to_words(n):
    n = int(n)
    if n == 0:
        return "Không đồng"
    units = ["", "nghìn", "triệu", "tỷ", "nghìn tỷ", "triệu tỷ"]
    groups = []
    while n:
        groups.append(n % 1000)
        n //= 1000
    words = []
    for idx in range(len(groups) - 1, -1, -1):
        g = groups[idx]
        if g == 0:
            continue
        words += _read_triple(g, full=idx != len(groups) - 1)
        if units[idx]:
            words.append(units[idx])
    s = " ".join(words) + " đồng"
    return s[0].upper() + s[1:]


def vnd(n):
    return f"{int(n):,}".replace(",", ".") + " đ"


# --------------------------------------------------------------- vẽ biên lai
def build_receipt_pdf(path, school, receipt, txn, invoice, installments, verify_url, demo=False):
    _register_fonts()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    W, H = landscape(A5)
    c = canvas.Canvas(path, pagesize=(W, H))
    c.setTitle(f"Biên lai {receipt['receipt_no']}")
    red = colors.HexColor("#b8021e")
    green = colors.HexColor("#1f701f")

    # dải tiêu đề: nền trắng, logo trường, viền đỏ phía dưới
    logo = os.path.join(BASE_DIR, "static", "img", "vju-logo.png")
    text_x = 12 * mm
    if os.path.exists(logo):
        c.drawImage(logo, 10 * mm, H - 21 * mm, 18 * mm, 18 * mm, mask="auto")
        text_x = 31 * mm
    c.setFillColor(red)
    c.setFont("VN-Bold", 13)
    c.drawString(text_x, H - 10 * mm, school.upper())
    c.setFillColor(green)
    c.setFont("VN", 9)
    c.drawString(text_x, H - 16 * mm, "Phòng Kế hoạch – Tài chính · Cổng thanh toán học phí trực tuyến")
    c.setFillColor(red)
    c.rect(0, H - 24 * mm, W, 1.2 * mm, fill=1, stroke=0)

    c.setFillColor(red)
    c.setFont("VN-Bold", 15)
    c.drawCentredString(W / 2, H - 32 * mm, "BIÊN LAI THU TIỀN ĐIỆN TỬ")
    c.setFillColor(colors.black)
    c.setFont("VN", 9)
    c.drawCentredString(W / 2, H - 37 * mm,
                        f"Số: {receipt['receipt_no']}    Ngày: {txn['paid_at']}")

    y = H - 47 * mm
    rows = [
        ("Họ và tên sinh viên", invoice["full_name"]),
        ("Mã sinh viên / Lớp", f"{invoice['student_code']} / {invoice['class_name']}"),
        ("Khoa", invoice["faculty"]),
        ("Học kỳ", invoice["semester_name"]),
        ("Hình thức thanh toán", {"vnpay": "Cổng VNPay", "vietqr": "Chuyển khoản VietQR",
                                  "simulator": "Cổng thanh toán mô phỏng", "cash": "Tiền mặt tại quầy"}.get(txn["method"], txn["method"])),
        ("Mã giao dịch", f"{txn['txn_ref']}   (Mã ngân hàng/cổng: {txn['gateway_txn_no'] or '—'})"),
    ]
    for label, value in rows:
        c.setFont("VN", 9.5)
        c.drawString(12 * mm, y, f"{label}:")
        c.setFont("VN-Bold", 9.5)
        c.drawString(52 * mm, y, str(value or ""))
        y -= 5.5 * mm

    # bảng chi tiết
    y -= 2 * mm
    c.setFillColor(colors.HexColor("#fdecee"))
    c.rect(12 * mm, y - 1.5 * mm, W - 60 * mm, 6 * mm, fill=1, stroke=0)
    c.setFillColor(colors.black)
    c.setFont("VN-Bold", 9)
    cols = [14 * mm, 70 * mm, 105 * mm, 135 * mm]
    for x, t in zip(cols, ["Nội dung", "Tiền gốc", "Phí trễ hạn", "Thành tiền"]):
        c.drawString(x, y, t)
    c.setFont("VN", 9)
    for inst in installments:
        y -= 5.5 * mm
        c.drawString(cols[0], y, f"Học phí đợt {inst['seq']}")
        c.drawString(cols[1], y, vnd(inst["amount"]))
        c.drawString(cols[2], y, vnd(inst["late_fee"]))
        c.drawString(cols[3], y, vnd(inst["amount"] + inst["late_fee"]))
    y -= 3 * mm
    c.line(12 * mm, y, W - 48 * mm, y)
    y -= 5 * mm
    c.setFont("VN-Bold", 10.5)
    c.setFillColor(red)
    c.drawString(cols[0], y, "TỔNG CỘNG")
    c.drawString(cols[3], y, vnd(txn["amount"]))
    c.setFillColor(colors.black)
    y -= 5.5 * mm
    c.setFont("VN", 9)
    c.drawString(cols[0], y, f"Bằng chữ: {money_to_words(txn['amount'])}.")

    # mã QR tra cứu
    qr = QrCodeWidget(verify_url)
    b = qr.getBounds()
    size = 32 * mm
    d = Drawing(size, size, transform=[size / (b[2] - b[0]), 0, 0, size / (b[3] - b[1]), 0, 0])
    d.add(qr)
    renderPDF.draw(d, c, W - 44 * mm, H - 88 * mm)
    c.setFont("VN", 7.5)
    c.drawCentredString(W - 28 * mm, H - 91 * mm, "Quét để xác thực biên lai")

    c.setFont("VN", 7.5)
    c.setFillColor(colors.grey)
    c.drawString(12 * mm, 8 * mm,
                 "Biên lai được phát hành tự động sau khi hệ thống đối soát thành công với ngân hàng/cổng thanh toán.")
    if demo:
        c.setFillColor(red)
        c.setFont("VN-Bold", 7.5)
        c.drawString(12 * mm, 12 * mm, "BẢN DEMO ĐỒ ÁN MÔN HỌC – KHÔNG CÓ GIÁ TRỊ THANH TOÁN")
    c.showPage()
    c.save()
    return path
