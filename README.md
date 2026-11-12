# Hệ thống quản lý và thanh toán học phí trực tuyến – Trường Đại học Việt Nhật (VJU)

> Đồ án môn Công nghệ tài chính. Giao diện theo màu nhận diện đỏ – trắng và logo của Trường Đại học Việt Nhật – ĐHQGHN. Đây là bản demo, không phải cổng thanh toán chính thức của trường. Nhãn **DEMO** được hiện trên giao diện và biên lai; tắt bằng `DEMO=0` trong `.env`.

Ứng dụng web viết bằng **Python (Flask + SQLite)** cho phép nhà trường quản lý các khoản học phí. Sinh viên có thể thanh toán trực tuyến qua **VietQR** hoặc **VNPay**. Hệ thống **tự động đối soát** để gạch nợ, rồi phát hành **biên lai điện tử** có mã QR xác thực.

## 1. Chạy chương trình

**Cách nhanh trên Windows:** nháy đúp vào `run.bat`. Lần đầu chạy, file này tự tạo môi trường ảo và cài thư viện.

**Cách thủ công:**

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Sau đó mở trình duyệt tại <http://127.0.0.1:5000>. Ở lần chạy đầu, hệ thống tự tạo CSDL `instance/hocphi.db` kèm dữ liệu mẫu: 40 sinh viên thuộc 6 chương trình đào tạo của VJU, 2 học kỳ (học phí trọn gói 29 triệu đồng/kỳ) và khoảng 80 giao dịch.

| Vai trò | Tài khoản | Mật khẩu |
|---|---|---|
| Sinh viên | `sv` (hoặc mã SV bất kỳ trong danh sách) | `123456` |
| Kế toán | `ketoan` | `123456` |
| Quản trị viên | `admin` | `123456` |

Nếu bạn đã chạy bản cũ, chương trình **tự nâng cấp CSDL** khi khởi động (giữ nguyên dữ liệu) và tự tạo tài khoản `admin` / `123456`. Quên mật khẩu admin thì chạy `flask --app app create-admin admin <mật_khẩu_mới>`.

Để tạo lại dữ liệu mẫu từ đầu, chạy `flask --app app reset-db`.

> Giao diện tải Bootstrap, icon và Chart.js từ CDN, nên máy cần có Internet để hiển thị đẹp. Phần lõi (thanh toán, mã QR, biên lai PDF) vẫn chạy được khi không có mạng.

## 2. Chức năng

### Sinh viên
- **Quên mật khẩu:** nhập mã SV và email đã đăng ký để nhận liên kết đặt lại mật khẩu (hiệu lực 30 phút, chỉ dùng một lần). Khi chưa cấu hình email, ở chế độ demo liên kết hiện ngay trên màn hình. Trang đăng nhập có thêm hướng dẫn liên hệ phòng ban; thông tin liên hệ chỉnh bằng các biến `SUPPORT_*` trong `.env`.
- **Tổng quan kiểu ví điện tử:** tổng tiền cần thanh toán, số đã nộp và còn nợ, lịch nộp sắp tới, cảnh báo quá hạn.
- **Chi tiết học phí:** các khoản thu (học phí tín chỉ, bảo hiểm y tế, dịch vụ, ký túc xá), học bổng/miễn giảm, tổng tiền ghi bằng chữ.
- **Trả góp:** tự chọn nộp 1 lần hoặc chia 2–3 đợt, mỗi đợt cách nhau 30 ngày.
- **Thanh toán:**
  - **VietQR:** mã QR được sinh ngay trên máy chủ theo chuẩn EMVCo/NAPAS, quét được bằng mọi app ngân hàng. Trang tự cập nhật khi tiền về.
  - **VNPay sandbox:** ký dữ liệu bằng HMAC-SHA512, nhận kết quả qua Return URL và IPN.
  - **Cổng mô phỏng:** dùng để trình diễn khi không có mạng.
- **Phí trễ hạn:** tính tự động theo số ngày quá hạn.
- **Lịch sử giao dịch, biên lai PDF, thông báo.**

### Kế toán (phòng Kế hoạch – Tài chính)
- **Dashboard:** các chỉ số chính, tiến độ thu, biểu đồ trạng thái, biểu đồ theo khoa, theo ngày và theo kênh thanh toán.
- **Công nợ:** lọc theo học kỳ, khoa, trạng thái hoặc tìm theo tên.
- **Chi tiết hoá đơn:** áp dụng học bổng/miễn giảm (theo % học phí hoặc số tiền cố định), đổi số đợt, **thu tiền mặt tại quầy**.
- **Đối soát:** giao dịch khớp tiền được gạch nợ tự động. Giao dịch lệch tiền chuyển sang **Cần kiểm tra**, kế toán chọn *Duyệt* hoặc *Từ chối*.
- **Tạo yêu cầu thu cho sinh viên chỉ định:** chọn một hoặc nhiều mã SV, chọn khoản trong danh mục và/hoặc nhập khoản phát sinh (phí thi lại, học lại…), hạn nộp riêng. Chưa có hoá đơn → tạo hoá đơn mới; đã có nhưng chưa nộp → cộng vào và chia lại đợt; đã nộp → thêm **đợt bổ sung**. Sinh viên nhận thông báo ngay.
- **Hỗ trợ tài khoản:** đặt lại mật khẩu sinh viên về mặc định khi sinh viên đến liên hệ trực tiếp.
- **Danh mục:** quản lý học kỳ, khoản thu (theo tín chỉ hoặc cố định, bắt buộc hoặc tự chọn), **lập hoá đơn hàng loạt**.
- **Nhắc hạn:** liệt kê các đợt quá hạn hoặc sắp đến hạn, gửi thông báo trong hệ thống và email.
- **Báo cáo học kỳ:** tổng hợp theo khoa và theo kênh, danh sách sinh viên còn nợ. **Xuất được Excel** (2 sheet, có biểu đồ) hoặc CSV.

### Quản trị viên (admin)
Admin quản trị hệ thống, **không** trực tiếp thu tiền hay lập hoá đơn (nguyên tắc *phân tách nhiệm vụ* trong kiểm soát nội bộ).
1. **Tổng quan hệ thống:** số tài khoản theo vai trò, đăng nhập 24 giờ, tài khoản bị khoá, webhook lỗi, biểu đồ đăng nhập 7 ngày, **cảnh báo bất thường** (tài khoản / IP sai mật khẩu nhiều lần).
2. **Tài khoản & phân quyền:** tạo tài khoản cán bộ, khoá / mở khoá (người bị khoá bị đăng xuất ngay), đổi vai trò, đặt lại mật khẩu. Không cho tự khoá mình và luôn giữ ít nhất 1 admin.
3. **Duyệt đăng ký:** sinh viên tự đăng ký ở trang `/dang-ky`; tài khoản ở trạng thái *chờ duyệt* cho tới khi admin đối chiếu hồ sơ và duyệt (hệ thống tự cảnh báo khi trùng họ tên / SĐT với tài khoản khác). Có thể duyệt / từ chối hàng loạt kèm lý do.
4. **Phê duyệt hai cấp (maker – checker):** kế toán *đề nghị* duyệt giao dịch lệch tiền hoặc miễn giảm trên 50% học phí → admin phê duyệt thì mới thực hiện.
5. **Nhật ký hệ thống:** ghi mọi sự kiện quan trọng (đăng nhập, khoá, thu tiền, miễn giảm, đổi cấu hình, sao lưu…) kèm IP; lọc theo nhóm, tài khoản, hành động, ngày; xuất Excel.
6. **Nhật ký webhook:** lưu nguyên văn mọi lần ngân hàng / VNPay gọi vào và kết quả xử lý.
7. **Cấu hình hệ thống trên giao diện:** lãi phí trễ hạn, số đợt trả góp, bật / tắt từng kênh thanh toán, tài khoản nhận tiền VietQR, tạo lại khoá webhook, chính sách khoá tài khoản, ngưỡng phê duyệt, thông tin hỗ trợ. Có hiệu lực ngay.
8. **Sao lưu & khôi phục:** sao lưu 1 chạm, tải về, khôi phục (tự sao lưu bản hiện tại trước), tải file `.db` lên để khôi phục, nút **tạo lại dữ liệu demo**.
9. **Nhập sinh viên từ Excel:** tải file mẫu (có danh sách chọn ngành), tải lên → báo dòng thêm được / bị bỏ qua và lý do.

### Bảo mật tài khoản
- Sai mật khẩu **5 lần liên tiếp → khoá tạm 15 phút** (chỉnh được).
- Tài khoản do admin / kế toán tạo hoặc đặt lại mật khẩu **bắt buộc đổi mật khẩu** ở lần đăng nhập đầu.
- Mọi người dùng tự đổi mật khẩu ở menu tên tài khoản → *Đổi mật khẩu*.

## 3. Quy tắc nghiệp vụ

| Nội dung | Công thức / quy tắc |
|---|---|
| Tiền học phí | số tín chỉ × đơn giá/tín chỉ + các khoản cố định |
| Miễn giảm | % × tiền học phí (chỉ áp dụng cho khoản loại *Học phí*), hoặc một số tiền cố định |
| Chia đợt | tổng ÷ n, làm tròn đến nghìn đồng, đợt cuối nhận phần dư |
| Phí trễ hạn | `min(nợ × 0,05% × số ngày quá hạn, nợ × 10%)`, tham số chỉnh trong `.env` |
| Thứ tự nộp | phải nộp đợt sớm hơn trước (kiểm tra ở cả giao diện và máy chủ) |
| Đối soát | tiền về đúng số tiền thì gạch nợ và phát hành biên lai; lệch tiền thì chuyển trạng thái *review*. Hàm đối soát idempotent: cùng một giao dịch không bao giờ bị cộng tiền hai lần |

## 4. Kịch bản demo gợi ý

1. Đăng nhập `sv`. Đợt 1 đang quá hạn, nên bạn sẽ thấy cảnh báo và phí trễ hạn.
2. Vào **Thanh toán**, chọn đợt 1 (thử chọn đợt 2 khi chưa chọn đợt 1, hệ thống sẽ không cho), rồi **Tiếp tục thanh toán**.
3. Chọn **VietQR**. Màn hình hiện mã QR và thông tin chuyển khoản.
4. Mở cửa sổ ẩn danh, đăng nhập `ketoan`, vào **Giao dịch & đối soát** và bấm **Giả lập báo có**. Trong khoảng 3 giây, trang của sinh viên tự chuyển sang *Thanh toán thành công*.
5. Tải **biên lai PDF**, rồi quét mã QR trên biên lai để mở trang xác thực.
6. Quay lại tài khoản kế toán: xem dashboard, xử lý giao dịch *Cần kiểm tra*, gửi nhắc hạn, xuất báo cáo Excel.

Bạn cũng có thể giả lập webhook ngân hàng bằng lệnh sau:

```bash
curl -X POST http://127.0.0.1:5000/webhook/bank -H "Authorization: Apikey demo-webhook-token" ^
     -H "Content-Type: application/json" -d "{\"content\":\"<MÃ GIAO DỊCH>\",\"amount\":<SỐ TIỀN>}"
```

## 5. Kết nối dịch vụ thật (tuỳ chọn)

- **VNPay sandbox:** đăng ký tại <https://sandbox.vnpayment.vn/devreg/> để nhận `vnp_TmnCode` và `vnp_HashSecret`. Điền hai giá trị này vào `.env` (sao chép từ `.env.example`). Thẻ test: ngân hàng NCB, số thẻ `9704198526191432198`, tên `NGUYEN VAN A`, ngày phát hành `07/15`, OTP `123456`.
- **Báo có ngân hàng tự động:** dùng dịch vụ như SePay hoặc Casso và trỏ webhook về `https://<địa chỉ public>/webhook/bank`, với header `Authorization: Apikey <BANK_WEBHOOK_TOKEN>`. Khi chạy trên máy cá nhân, dùng **ngrok** để có địa chỉ public. Hệ thống tự nhận diện định dạng dữ liệu của SePay và Casso.
- **Email:** điền `SMTP_*` (Gmail cần *Mật khẩu ứng dụng*). Để nhắc hạn tự động mỗi ngày, đặt lịch chạy `flask --app app send-reminders` bằng Task Scheduler.

## 6. Cấu trúc mã nguồn

```
app.py               Khởi tạo ứng dụng, đăng nhập, biên lai, lệnh CLI
config.py            Cấu hình (đọc từ .env)
db.py                Lược đồ CSDL SQLite (9 bảng)
services.py          Nghiệp vụ: lập hoá đơn, miễn giảm, trả góp, phí trễ hạn, đối soát, báo cáo
payments.py          VNPay (ký/kiểm tra HMAC-SHA512), VietQR (EMVCo + CRC16), webhook ngân hàng
receipts.py          Biên lai PDF (ReportLab), đọc số tiền bằng chữ
notifier.py          Nhắc hạn qua email và thông báo trong hệ thống
routes_student.py    Màn hình sinh viên
routes_payment.py    Luồng thanh toán và các endpoint VNPay/webhook
routes_accountant.py Màn hình kế toán, xuất Excel/CSV
routes_admin.py      Trang quản trị (9 chức năng)
auth_routes.py       Đăng nhập (khoá khi sai nhiều lần), đăng ký, quên / đổi mật khẩu
audit.py             Nhật ký hệ thống
approvals.py         Phê duyệt hai cấp (maker – checker)
sysconfig.py         Cấu hình hệ thống chỉnh trên giao diện
seed.py              Dữ liệu mẫu
templates/, static/  Giao diện (Bootstrap 5, Chart.js), logo tại static/img/
fonts/               Font DejaVu cho biên lai tiếng Việt
```

**Các bảng dữ liệu (15):** `users`, `semesters`, `fee_items`, `invoices`, `invoice_lines`, `discounts`, `installments`, `transactions`, `receipts`, `notifications`, `password_resets`, `settings`, `audit_logs`, `webhook_logs`, `approvals`.

## 7. Luồng thanh toán và đối soát

```
Sinh viên chọn đợt ──► tạo giao dịch (mã VJUyymmddXXXXXX, số tiền = gốc + phí trễ)
        │
        ├─ VietQR ──► app ngân hàng chuyển khoản ──► ngân hàng/SePay gọi /webhook/bank ─┐
        ├─ VNPay  ──► cổng VNPay ──► Return URL / IPN (kiểm tra chữ ký HMAC) ────────────┤
        └─ Mô phỏng / Tiền mặt tại quầy ─────────────────────────────────────────────────┤
                                                                                        ▼
                         apply_payment(): so khớp mã + số tiền ──► khớp: gạch nợ các đợt,
                         cập nhật hoá đơn, phát hành biên lai, gửi thông báo
                                                          └─► lệch: chuyển kế toán kiểm tra
```
