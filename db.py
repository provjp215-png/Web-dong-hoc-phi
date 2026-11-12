"""Kết nối SQLite và lược đồ cơ sở dữ liệu."""
import os
import sqlite3

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('student', 'accountant', 'admin')),
    full_name     TEXT NOT NULL,
    email         TEXT,
    phone         TEXT,
    student_code  TEXT UNIQUE,
    faculty       TEXT,
    class_name    TEXT,
    status        TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active', 'pending', 'locked', 'rejected')),
    failed_attempts      INTEGER NOT NULL DEFAULT 0,   -- số lần đăng nhập sai liên tiếp
    locked_until         TEXT,                         -- khoá tạm thời đến thời điểm này
    must_change_password INTEGER NOT NULL DEFAULT 0,   -- bắt đổi mật khẩu ở lần đăng nhập tới
    last_login_at        TEXT,
    note                 TEXT,                         -- lý do từ chối / ghi chú quản trị
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS semesters (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT UNIQUE NOT NULL,      -- VD: 2026-1
    name       TEXT NOT NULL,             -- VD: Học kỳ 1 năm học 2026-2027
    start_date TEXT,
    end_date   TEXT,
    due_date   TEXT NOT NULL              -- hạn nộp mặc định
);

-- Danh mục khoản thu theo học kỳ
CREATE TABLE IF NOT EXISTS fee_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    semester_id INTEGER NOT NULL REFERENCES semesters(id),
    name        TEXT NOT NULL,
    category    TEXT NOT NULL,            -- tuition / insurance / dorm / service / other
    unit_price  INTEGER NOT NULL,         -- đơn giá (đồng)
    per_credit  INTEGER NOT NULL DEFAULT 0, -- 1 = tính theo số tín chỉ
    mandatory   INTEGER NOT NULL DEFAULT 1
);

-- Hoá đơn học phí của 1 sinh viên trong 1 học kỳ
CREATE TABLE IF NOT EXISTS invoices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT UNIQUE NOT NULL,
    student_id  INTEGER NOT NULL REFERENCES users(id),
    semester_id INTEGER NOT NULL REFERENCES semesters(id),
    credits     INTEGER NOT NULL DEFAULT 0,
    due_date    TEXT,                        -- hạn nộp riêng (trống = theo học kỳ)
    subtotal    INTEGER NOT NULL DEFAULT 0,
    discount    INTEGER NOT NULL DEFAULT 0,
    total       INTEGER NOT NULL DEFAULT 0,  -- phải nộp sau miễn giảm (chưa gồm phí trễ hạn)
    paid        INTEGER NOT NULL DEFAULT 0,  -- đã nộp vào gốc
    late_fee_paid INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'unpaid', -- unpaid / partial / paid
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (student_id, semester_id)
);

CREATE TABLE IF NOT EXISTS invoice_lines (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id  INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    fee_item_id INTEGER REFERENCES fee_items(id),
    description TEXT NOT NULL,
    quantity    INTEGER NOT NULL DEFAULT 1,
    unit_price  INTEGER NOT NULL,
    amount      INTEGER NOT NULL
);

-- Học bổng / miễn giảm
CREATE TABLE IF NOT EXISTS discounts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id  INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,           -- scholarship / exemption
    description TEXT NOT NULL,
    percent     REAL NOT NULL DEFAULT 0, -- % trên tiền học phí (tuition)
    amount      INTEGER NOT NULL DEFAULT 0, -- hoặc số tiền cố định
    computed    INTEGER NOT NULL DEFAULT 0
);

-- Các đợt nộp (trả góp)
CREATE TABLE IF NOT EXISTS installments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id  INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    amount      INTEGER NOT NULL,
    due_date    TEXT NOT NULL,
    paid_amount INTEGER NOT NULL DEFAULT 0,
    late_fee_paid INTEGER NOT NULL DEFAULT 0,
    paid_at     TEXT,
    status      TEXT NOT NULL DEFAULT 'unpaid'  -- unpaid / partial / paid
);

-- Giao dịch thanh toán
CREATE TABLE IF NOT EXISTS transactions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    txn_ref        TEXT UNIQUE NOT NULL,     -- mã tham chiếu, cũng là nội dung chuyển khoản
    invoice_id     INTEGER NOT NULL REFERENCES invoices(id),
    student_id     INTEGER NOT NULL REFERENCES users(id),
    installment_ids TEXT,                    -- danh sách id đợt, cách nhau dấu phẩy
    principal      INTEGER NOT NULL,
    late_fee       INTEGER NOT NULL DEFAULT 0,
    amount         INTEGER NOT NULL,         -- = principal + late_fee
    method         TEXT NOT NULL,            -- vnpay / vietqr / simulator
    status         TEXT NOT NULL DEFAULT 'pending', -- pending / success / failed / review
    gateway_txn_no TEXT,
    bank_code      TEXT,
    received_amount INTEGER,
    note           TEXT,
    created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
    paid_at        TEXT
);

CREATE TABLE IF NOT EXISTS receipts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_no     TEXT UNIQUE NOT NULL,
    transaction_id INTEGER UNIQUE NOT NULL REFERENCES transactions(id),
    issued_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    file_path      TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    title      TEXT NOT NULL,
    body       TEXT NOT NULL,
    channel    TEXT NOT NULL DEFAULT 'app',   -- app / email
    is_read    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Yêu cầu đặt lại mật khẩu (quên mật khẩu)
CREATE TABLE IF NOT EXISTS password_resets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    token      TEXT UNIQUE NOT NULL,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    expires_at TEXT NOT NULL,
    used       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Cấu hình hệ thống do admin chỉnh trên giao diện (ghi đè giá trị trong .env)
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT,
    updated_by TEXT
);

-- Nhật ký hệ thống: ai làm gì, lúc nào, từ đâu
CREATE TABLE IF NOT EXISTS audit_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER,
    username   TEXT,
    role       TEXT,
    action     TEXT NOT NULL,
    target     TEXT,
    detail     TEXT,
    ip         TEXT,
    created_at TEXT NOT NULL
);

-- Nhật ký các lần ngân hàng / cổng thanh toán gọi vào hệ thống
CREATE TABLE IF NOT EXISTS webhook_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,        -- bank / vnpay_ipn / vnpay_return
    ip          TEXT,
    http_status INTEGER,
    payload     TEXT,
    result      TEXT,
    ok          INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

-- Phê duyệt hai cấp (maker – checker)
CREATE TABLE IF NOT EXISTS approvals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,       -- review_txn / discount
    ref_id       INTEGER NOT NULL,    -- id giao dịch hoặc id hoá đơn
    payload      TEXT,                -- JSON chi tiết đề nghị
    summary      TEXT,
    requested_by INTEGER NOT NULL REFERENCES users(id),
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending / approved / rejected / failed
    decided_by   INTEGER REFERENCES users(id),
    decided_at   TEXT,
    note         TEXT,
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_inv_student ON invoices(student_id);
CREATE INDEX IF NOT EXISTS idx_inst_invoice ON installments(invoice_id);
CREATE INDEX IF NOT EXISTS idx_txn_invoice ON transactions(invoice_id);
"""


def connect(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


USER_COLUMNS_OLD = ("id, username, password_hash, role, full_name, email, phone, student_code, faculty,"
                    " class_name, created_at")


def migrate(conn):
    """Nâng cấp CSDL tạo từ phiên bản cũ (bảng users chưa có trạng thái / vai trò admin)."""
    inv_cols = [r[1] for r in conn.execute("PRAGMA table_info(invoices)")]
    if inv_cols and "due_date" not in inv_cols:
        conn.execute("ALTER TABLE invoices ADD COLUMN due_date TEXT")
        conn.commit()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
    if not cols or "status" in cols:
        return False
    users_sql = SCHEMA[SCHEMA.index("CREATE TABLE IF NOT EXISTS users ("):]
    users_sql = users_sql[:users_sql.index(");") + 2].replace("IF NOT EXISTS users (", "IF NOT EXISTS users_new (")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        "BEGIN;" + users_sql +
        f"INSERT INTO users_new ({USER_COLUMNS_OLD}) SELECT {USER_COLUMNS_OLD} FROM users;"
        "DROP TABLE users; ALTER TABLE users_new RENAME TO users; COMMIT;")
    conn.execute("PRAGMA foreign_keys = ON")
    return True


def init_db(conn):
    conn.executescript(SCHEMA)
    migrate(conn)
    conn.executescript(SCHEMA)  # tạo các bảng mới sau khi nâng cấp
    conn.commit()


def ensure_admin(conn, default_password):
    """CSDL cũ (trước khi có trang admin) chưa có tài khoản quản trị → tạo admin với mật khẩu mặc định."""
    from werkzeug.security import generate_password_hash
    if not conn.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
        conn.execute("INSERT OR IGNORE INTO users(username, password_hash, role, full_name, email, status)"
                     " VALUES (?,?,?,?,?,?)", ("admin", generate_password_hash(default_password), "admin",
                                               "Quản trị hệ thống", "admin@demo.local", "active"))
        conn.commit()


def query(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    return (rows[0] if rows else None) if one else rows
