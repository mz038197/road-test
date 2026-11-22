"""router 介面：路考網站跟 router 打交道的事都經過這裡，由 main 傳進 create_app。

正式環境接真的 router（RealRouter），測試和本機開發接假的 router（FakeRouter）。

  ROAD_TEST_ROUTER  real（預設）或 fake。fake 只能在本機用：在 Fly 上（有 FLY_APP_NAME）會拒絕啟動
  VCR_URL、VCR_SERVICE_CREDENTIAL  真的 router，見 key_check.py
"""
from collections.abc import Collection, Mapping
from typing import Protocol

from road_test.web.key_check import INVALID, UNAVAILABLE, Identity, KeyCheck, Refusal, read_answer

# 假的 router 在本機開發時認得的兩把金鑰（不是真的金鑰，真的 router 不認得）
DEV_STUDENT_KEY = "vcr_sk_dev_student"
DEV_TEACHER_KEY = "vcr_sk_dev_teacher"
_DEV_ANSWERS = {
    DEV_STUDENT_KEY: {"accepted": True, "kind": "classroom", "student_id": 1234, "class_id": 56,
                      "class_session_id": 789, "display_name": "本機學生"},
    DEV_TEACHER_KEY: {"accepted": True, "kind": "personal", "user_id": 42, "display_name": "本機老師",
                      "is_admin": False, "classes": [{"id": 56, "name": "本機班級"}]},
}


class Router(Protocol):
    def check_key(self, api_key: str) -> Identity | Refusal:
        """Key Check：這把金鑰是誰，或為什麼被拒（router 連不上時是 UNAVAILABLE）。"""
        ...


class RealRouter:
    """真的 router：Key Check 沿用 KeyCheck 的 60 秒快取和 3 秒逾時。"""

    def __init__(self, key_check: KeyCheck):
        self._key_check = key_check

    def check_key(self, api_key: str) -> Identity | Refusal:
        return self._key_check.check(api_key)


class FakeRouter:
    """假的 router：answers 是 {金鑰: router 的 Key Check 回應}；down 裡的金鑰當成 router 連不上。"""

    def __init__(self, answers: Mapping[str, dict], *, down: Collection[str] = ()):
        self._answers = answers
        self._down = down

    def check_key(self, api_key: str) -> Identity | Refusal:
        api_key = (api_key or "").strip()
        if api_key in self._down:
            return UNAVAILABLE
        answer = self._answers.get(api_key)
        return read_answer(answer) if answer else INVALID


def router_from_env(env: Mapping[str, str], **options) -> Router:
    """依 ROAD_TEST_ROUTER 選 router；options 傳給 KeyCheck（測試換 transport）。"""
    choice = (env.get("ROAD_TEST_ROUTER") or "real").strip().lower()
    if choice == "real":
        return RealRouter(KeyCheck.from_env(env, **options))
    if choice != "fake":
        raise SystemExit(f"環境變數 ROAD_TEST_ROUTER 要是 real 或 fake，現在是「{choice}」。")
    if env.get("FLY_APP_NAME"):
        raise SystemExit("環境變數 ROAD_TEST_ROUTER=fake 不能用在正式環境，網站拒絕啟動。")
    return FakeRouter(_DEV_ANSWERS)
