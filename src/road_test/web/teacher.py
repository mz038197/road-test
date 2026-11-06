"""老師密碼：開關正式路考（#9）、管理頁（#10）都用同一個檢查。

密碼放在環境變數 ROAD_TEST_TEACHER_PASSWORD（Fly secrets），前端放在 HTTP 標頭 X-Teacher-Password。
伺服器沒設定密碼時，老師的 API 一律拒絕（不會變成「沒密碼也能進」）。

用法（路由）：
    teacher = teacher_check(password)
    @app.put("/api/teacher/...", dependencies=[Depends(teacher)])
需要「是不是老師」但不想拒絕時（例如 #10 讓老師看任一組的結果），用 is_teacher(password, 標頭的值)。
"""
import hmac
import os

from fastapi import Header, HTTPException, Request

ENV = "ROAD_TEST_TEACHER_PASSWORD"
HEADER = "X-Teacher-Password"


def password_from_env() -> str | None:
    return os.environ.get(ENV) or None


def is_teacher(password: str | None, given: str | None) -> bool:
    if not password or not given:
        return False
    return hmac.compare_digest(password.encode(), given.encode())


TARGET = "teacher"  # 猜錯次數限制（limiter.FailureLimit）裡老師密碼的目標名稱


def teacher_check(password: str | None, limit=None):
    """FastAPI 依賴：老師密碼不對就 403；limit（limiter.FailureLimit）：猜錯太多次就 429。"""

    def check(request: Request, x_teacher_password: str | None = Header(None)) -> None:
        if not password:
            raise HTTPException(403, "伺服器沒有設定老師密碼，老師的功能暫時不能用。請在 Fly secrets 設定 "
                                     f"{ENV}。")
        if limit:
            limit.check(request, TARGET)
        if not is_teacher(password, x_teacher_password):
            if limit:
                limit.failed(request, TARGET)
            raise HTTPException(403, "老師密碼不對。")

    return check
