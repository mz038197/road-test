"""road-test 自己的 Key Check 用戶端（router ADR 0082：沒有共用套件，規則照 spec #89）。

呼叫一次 `KeyCheck.check(金鑰)`，回傳 Identity（router 認得這把金鑰）或 Refusal（被拒，或 router 連不上）。

  VCR_URL                 router 的網址，預設 https://ai.vanscoding.com（一律走公開網址，router ADR 0077）
  VCR_SERVICE_CREDENTIAL  road-test 的 Service Credential（Fly secret；它的 SHA-256 加進 router 的 VCR_SERVICE_TOKENS）
"""
import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from collections.abc import Mapping
from typing import Callable

import httpx

DEFAULT_URL = "https://ai.vanscoding.com"
KEY_PREFIX = "vcr_sk_"
KEEP_SECONDS = 60  # 通過的結果留多久；router 故障時不延長
TIMEOUT_SECONDS = 3.0  # 最多等 router 幾秒，逾時當作 unavailable
PREFIX_SHOWN = 11  # 記錄裡只寫金鑰的前幾個字（vcr_sk_ 加 4 個字）

log = logging.getLogger(__name__)  # road_test 底下的 ERROR 會送到 vans-signals（signals.py）


@dataclass(frozen=True)
class Identity:
    kind: str                            # "classroom"（課堂金鑰）或 "personal"（個人金鑰）
    student_id: int | None = None        # 課堂金鑰才有
    class_id: int | None = None
    class_session_id: int | None = None
    user_id: int | None = None           # 個人金鑰才有
    is_admin: bool = False
    classes: tuple[tuple[int, str], ...] = ()  # 個人金鑰能管理的班級 (id, 名稱)；admin 是所有班級（router ADR 0083）

    @property
    def is_teacher(self) -> bool:
        """個人金鑰、而且是 admin 或有班級，才有老師身分（ADR 0083）。"""
        return self.kind == "personal" and (self.is_admin or bool(self.classes))

    def manages(self, class_id: int) -> bool:
        return self.kind == "personal" and any(cid == class_id for cid, _ in self.classes)


@dataclass(frozen=True)
class Refusal:
    reason: str    # router 的原因代碼，或 router 連不上時的 "unavailable"
    message: str   # 直接給使用者看的中文訊息（router 給的）


INVALID = Refusal("invalid", "無效的 API 金鑰")  # 不是 vcr_sk_ 開頭，不用問 router（訊息跟 router 的一樣）
# router 連不上、逾時、5xx、429、401：唯一要自己帶的訊息，其他都用 router 給的
UNAVAILABLE = Refusal("unavailable", "驗證服務暫時無法使用，請稍後再試")


class KeyCheck:
    """router 網址、Service Credential、HTTP transport、時間來源都可以換（測試用 httpx.MockTransport 和假時鐘）。"""

    def __init__(self, url: str, credential: str, *, transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self._url = url.rstrip("/") + "/internal/key-check"
        self._credential = credential
        self._http = httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS)
        self._clock = clock
        self._kept: dict[str, tuple[float, Identity]] = {}  # 金鑰的 SHA-256 → (到期時間, 身分)，不存金鑰原文
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls, env: Mapping[str, str], **options) -> "KeyCheck":
        """VCR_URL（預設 https://ai.vanscoding.com）、VCR_SERVICE_CREDENTIAL。"""
        url = (env.get("VCR_URL") or "").strip() or DEFAULT_URL
        return cls(url, (env.get("VCR_SERVICE_CREDENTIAL") or "").strip(), **options)

    def check(self, api_key: str) -> Identity | Refusal:
        api_key = (api_key or "").strip()
        if not api_key.startswith(KEY_PREFIX):
            return INVALID
        digest = hashlib.sha256(api_key.encode()).hexdigest()
        now = self._clock()
        with self._lock:
            kept = self._kept.get(digest)
        if kept and kept[0] > now:
            return kept[1]
        answer = self._ask(api_key)
        if isinstance(answer, Identity):
            with self._lock:
                self._kept[digest] = (now + KEEP_SECONDS, answer)
        return answer

    def _ask(self, api_key: str) -> Identity | Refusal:
        prefix = api_key[:PREFIX_SHOWN]
        try:
            response = self._http.post(self._url, json={"api_key": api_key},
                                       headers={"Authorization": f"Bearer {self._credential}"})
        except httpx.HTTPError as e:  # 連不上、逾時
            log.error("Key Check 失敗（%s）：%s，金鑰 %s…", self._url, type(e).__name__, prefix)
            return UNAVAILABLE
        if response.status_code != 200:  # 5xx、429；401 是 Service Credential 設錯
            log.error("Key Check 失敗（%s）：HTTP %s，金鑰 %s…", self._url, response.status_code, prefix)
            return UNAVAILABLE
        try:
            return self._read(response.json())
        except (ValueError, KeyError, TypeError) as e:  # 回應不符合合約
            log.error("Key Check 的回應不符合合約：%s，金鑰 %s…", type(e).__name__, prefix)
            return UNAVAILABLE

    @staticmethod
    def _read(body: dict) -> Identity | Refusal:
        if not body["accepted"]:
            return Refusal(body["reason"], body["message"])
        if body["kind"] == "personal":
            return Identity(kind="personal", user_id=body["user_id"], is_admin=bool(body["is_admin"]),
                            classes=tuple((c["id"], c["name"]) for c in body["classes"]))
        return Identity(kind=body["kind"], student_id=body["student_id"], class_id=body["class_id"],
                        class_session_id=body["class_session_id"])
