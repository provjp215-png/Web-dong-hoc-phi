"""Kiểm tra đăng nhập và phân quyền (sinh viên / kế toán)."""
from functools import wraps

from flask import abort, g, redirect, request, url_for


def login_required(role=None):
    def deco(view):
        @wraps(view)
        def wrapped(*a, **kw):
            if g.user is None:
                return redirect(url_for("login", next=request.path))
            if role and g.user["role"] != role:
                abort(403)
            return view(*a, **kw)
        return wrapped
    return deco
