"""Tạo dữ liệu mẫu để demo: 1 tài khoản kế toán, 40 sinh viên, 2 học kỳ, lịch sử thanh toán."""
import random
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

import services

HO = ["Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Huỳnh", "Phan", "Vũ", "Võ", "Đặng", "Bùi", "Đỗ"]
DEM = ["Văn", "Thị", "Minh", "Ngọc", "Thanh", "Quốc", "Gia", "Hoài", "Đức", "Thu", "Bảo", "Khánh"]
TEN = ["An", "Bình", "Châu", "Dũng", "Giang", "Hà", "Hiền", "Hùng", "Khoa", "Linh", "Long", "Mai",
       "Nam", "Nhung", "Phúc", "Quân", "Sơn", "Tâm", "Thảo", "Trang", "Tuấn", "Vy", "Yến", "Huy"]
# Các chương trình đào tạo đại học của Trường Đại học Việt Nhật (VJU)
FACULTIES = [("Nhật Bản học", "BJS"), ("Khoa học và Kỹ thuật máy tính", "CSE"),
             ("Cơ điện tử thông minh", "EMJM"), ("Công nghệ thực phẩm và sức khoẻ", "FTH"),
             ("Nông nghiệp thông minh và bền vững", "SAS"), ("Kỹ thuật xây dựng", "ECE")]


def _remove_accents(s):
    import unicodedata
    s = s.replace("Đ", "D").replace("đ", "d")
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def seed(conn, n_students=40, rnd_seed=42):
    rnd = random.Random(rnd_seed)
    pw = generate_password_hash("123456")
    today = datetime.now().replace(microsecond=0)

    conn.execute("INSERT INTO users(username, password_hash, role, full_name, email) VALUES (?,?,?,?,?)",
                 ("ketoan", pw, "accountant", "Phòng Kế hoạch – Tài chính", "ketoan@demo.local"))
    conn.execute("INSERT INTO users(username, password_hash, role, full_name, email) VALUES (?,?,?,?,?)",
                 ("admin", pw, "admin", "Quản trị hệ thống", "admin@demo.local"))

    # ---- học kỳ: kỳ trước (đã kết thúc) và kỳ hiện tại (đang thu)
    y = today.year if today.month >= 8 else today.year - 1
    due_cur = today - timedelta(days=23)   # đợt 1 kỳ hiện tại đã quá hạn
    sems = [
        (f"{y - 1}-2", f"Học kỳ 2 năm học {y - 1}-{y}", f"{y}-01-15", f"{y}-06-15",
         (today - timedelta(days=260)).date().isoformat()),
        (f"{y}-1", f"Học kỳ 1 năm học {y}-{y + 1}", f"{y}-09-01", f"{y + 1}-01-15",
         due_cur.date().isoformat()),
    ]
    sem_ids, dorm = [], {}
    for code, name, sd, ed, due in sems:
        sid = conn.execute("INSERT INTO semesters(code, name, start_date, end_date, due_date) VALUES (?,?,?,?,?)",
                           (code, name, sd, ed, due)).lastrowid
        sem_ids.append(sid)
        # VJU thu học phí trọn gói theo học kỳ (~29 triệu đồng/kỳ với sinh viên Việt Nam)
        for item in [("Học phí học kỳ (trọn gói)", "tuition", 29_000_000, 0, 1),
                     ("Bảo hiểm y tế sinh viên (6 tháng)", "insurance", 631_800, 0, 1),
                     ("Phí thư viện số & hệ thống học trực tuyến", "service", 250_000, 0, 1),
                     ("Phí ký túc xá Hoà Lạc (tự chọn)", "dorm", 1_800_000, 0, 0)]:
            fid = conn.execute("INSERT INTO fee_items(semester_id, name, category, unit_price, per_credit,"
                               " mandatory) VALUES (?,?,?,?,?,?)", (sid,) + item).lastrowid
            if item[1] == "dorm":
                dorm[sid] = fid

    # ---- sinh viên
    students = []
    for i in range(1, n_students + 1):
        fac, short = FACULTIES[(i - 1) % len(FACULTIES)]
        name = "Nguyễn Minh Anh" if i == 1 else f"{rnd.choice(HO)} {rnd.choice(DEM)} {rnd.choice(TEN)}"
        code = f"{str(y)[2:]}5{(i - 1) % 5 + 1:02d}{i:04d}"
        email = _remove_accents(name).lower().replace(" ", ".") + f"{i}@sv.demo.local"
        uid = conn.execute(
            "INSERT INTO users(username, password_hash, role, full_name, email, phone, student_code, faculty,"
            " class_name) VALUES (?,?,?,?,?,?,?,?,?)",
            ("sv" if i == 1 else code, pw, "student", name, email, f"09{rnd.randint(10000000, 99999999)}",
             code, fac, f"{short}{str(y)[2:]}0{rnd.randint(1, 3)}")).lastrowid
        students.append(uid)

    prev_sid, cur_sid = sem_ids
    methods = ["vnpay", "vietqr", "vietqr", "simulator", "cash"]

    def pay(inv_id, k, when):
        ov = services.invoice_overview(conn, inv_id, when.date())
        ids = [x["id"] for x in ov["installments"] if x["outstanding"] > 0][:k]
        if not ids:
            return
        method = rnd.choice(methods)
        t = services.create_transaction(conn, inv_id, ids, method, "VJU", at=when)
        gw, bank = (("QUAY", "CASH") if method == "cash" else
                    (f"{rnd.randint(10**7, 10**8 - 1)}", rnd.choice(["NCB", "VCB", "TCB", "MB", "BIDV", "ACB"])))
        services.apply_payment(conn, t["txn_ref"], t["amount"], gw, bank, "Dữ liệu mẫu",
                               at=when + timedelta(minutes=2))

    # ---- kỳ trước: gần như tất cả đã nộp
    prev_due = datetime.fromisoformat(sems[0][4])
    for idx, uid in enumerate(students):
        inv = services.create_invoice(conn, uid, prev_sid, rnd.choice([15, 17, 18, 20]),
                                      fee_item_ids=None if rnd.random() < .7 else [dorm[prev_sid]])
        if idx % 9 == 3:
            services.add_discount(conn, inv, "scholarship", "Học bổng khuyến khích học tập loại Giỏi (50%)", percent=50)
        if idx != 7:  # 1 sinh viên còn nợ kỳ trước
            pay(inv, 3, prev_due - timedelta(days=rnd.randint(0, 20), hours=rnd.randint(0, 10)))

    # ---- kỳ hiện tại: đủ các trạng thái để demo
    for idx, uid in enumerate(students):
        n_inst = 3 if idx == 0 or rnd.random() < .35 else (2 if rnd.random() < .3 else 1)
        inv = services.create_invoice(conn, uid, cur_sid, rnd.choice([15, 17, 18, 20, 21]),
                                      fee_item_ids=[dorm[cur_sid]] if idx % 4 == 1 else None,
                                      n_installments=n_inst)
        if idx % 8 == 5:
            services.add_discount(conn, inv, "scholarship", "Học bổng toàn phần của doanh nghiệp đối tác Nhật Bản",
                                  percent=100)
        elif idx % 11 == 2:
            services.add_discount(conn, inv, "exemption", "Miễn giảm 70% – sinh viên thuộc hộ cận nghèo",
                                  percent=70)
        if idx == 0:
            continue  # tài khoản demo "sv": chưa nộp, đợt 1 quá hạn → thấy phí trễ hạn
        r = rnd.random()
        if r < .45:
            pay(inv, 3, due_cur - timedelta(days=rnd.randint(1, 25), hours=rnd.randint(0, 12)))
        elif r < .75:
            pay(inv, 1, due_cur - timedelta(days=rnd.randint(1, 10)))
        elif r < .85:
            pay(inv, 3, due_cur + timedelta(days=rnd.randint(3, 15)))  # nộp trễ → có phí trễ hạn

    # giao dịch chuyển khoản đang chờ / lệch tiền để demo màn hình đối soát
    pending_inv = conn.execute("SELECT id FROM invoices WHERE semester_id=? AND status!='paid' AND student_id!=?"
                               " LIMIT 2", (cur_sid, students[0])).fetchall()
    if len(pending_inv) == 2:
        ov = services.invoice_overview(conn, pending_inv[0]["id"])
        unpaid = [x["id"] for x in ov["installments"] if x["outstanding"] > 0][:1]
        t = services.create_transaction(conn, pending_inv[0]["id"], unpaid, "vietqr", "VJU")
        services.apply_payment(conn, t["txn_ref"], t["amount"] - 50_000, "FT26" + t["txn_ref"][-6:], "VCB",
                               "Webhook ngân hàng")
        ov = services.invoice_overview(conn, pending_inv[1]["id"])
        unpaid = [x["id"] for x in ov["installments"] if x["outstanding"] > 0][:1]
        services.create_transaction(conn, pending_inv[1]["id"], unpaid, "vietqr", "VJU")

    # 2 sinh viên mới tự đăng ký, đang chờ admin duyệt
    for code, name, fac, cls, mail in [
            (f"{str(y)[2:]}CSE0141", "Phạm Đức Thịnh", "Khoa học và Kỹ thuật máy tính", "CSE2601",
             "thinh.pd@sv.demo.local"),
            (f"{str(y)[2:]}BJS0142", "Vũ Ngọc Mai", "Nhật Bản học", "BJS2601", "mai.vn@sv.demo.local")]:
        conn.execute("INSERT INTO users(username, password_hash, role, full_name, email, phone, student_code, faculty,"
                     " class_name, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,'pending',?)",
                     (code, pw, "student", name, mail, f"09{rnd.randint(10000000, 99999999)}", code, fac, cls,
                      (today - timedelta(hours=rnd.randint(1, 30))).strftime("%Y-%m-%d %H:%M:%S")))

    conn.execute("DELETE FROM notifications")
    services.notify(conn, students[0], "Chào mừng đến Cổng thanh toán học phí VJU",
                    "Bạn có thể xem hoá đơn, chọn trả góp và thanh toán bằng VNPay hoặc VietQR.")
    conn.commit()
