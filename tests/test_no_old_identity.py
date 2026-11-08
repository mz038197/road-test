"""組別代碼、老師密碼、X-Group／X-Group-Code／X-Teacher-Password 標頭都拿掉了，身分只看金鑰（road-test ADR 0001）。"""
import pytest

from conftest import model

OLD_HEADERS = {"X-Group": "01", "X-Group-Code": "apple", "X-Teacher-Password": "teacher-secret"}


def test_a_hand_in_without_a_key_is_refused_even_with_a_group_code(client) -> None:
    response = client.post("/api/submissions", data={"group": "01", "code": "apple", "kind": "mock"},
                           files={"model": ("best.onnx", model(), "application/octet-stream")}, headers=OLD_HEADERS)

    assert response.status_code == 401
    assert "API Key" in response.json()["detail"]


@pytest.mark.parametrize("method, path", [
    ("get", "/api/groups"), ("get", "/api/groups/01/records"), ("get", "/api/road-tests"),
    ("get", "/api/teacher/final"), ("put", "/api/teacher/final"), ("get", "/api/teacher/records"),
    ("get", "/api/teacher/groups"), ("put", "/api/teacher/groups"), ("post", "/api/teacher/codes"),
    ("post", "/api/teacher/groups/01/code"), ("post", "/api/teacher/groups/01/final-returns"),
    ("post", "/api/teacher/clear"),
])
def test_the_group_code_and_teacher_password_apis_are_gone(client, method, path) -> None:
    response = client.request(method, path, headers=OLD_HEADERS, json={})

    assert response.status_code in (404, 405), f"{method.upper()} {path} 拿掉了"


def test_old_headers_do_not_open_a_result(client) -> None:
    assert client.get("/api/submissions/1", headers=OLD_HEADERS).status_code == 401


@pytest.mark.parametrize("page", ["teacher-password.html", "teacher-password.js"])
def test_the_password_admin_page_is_gone(client, page) -> None:
    assert client.get(f"/{page}").status_code == 404
