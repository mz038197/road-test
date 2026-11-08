"""上傳的模型檔有大小上限：太大的用中文退件，不先整個收下來才檢查。"""
from conftest import as_group, model, records, seat, submit

LIMIT = 4000


def test_a_model_over_the_limit_is_refused_in_chinese(open_site, data_dir) -> None:
    client = open_site(max_upload_bytes=LIMIT)

    response = submit(client, content=b"x" * (LIMIT + 1))

    assert response.status_code == 413, "超過上限要退件"
    assert "太大" in response.json()["detail"] and "MB" in response.json()["detail"], response.json()
    assert records(client, "01") == [], "退件不算一次路考"
    assert list((data_dir / "submissions").glob("*")) == [], "退件不能留下檔案"


def test_a_model_under_the_limit_is_accepted(open_site) -> None:
    client = open_site(max_upload_bytes=LIMIT)

    assert submit(client, content=model()).status_code == 201


def test_a_streamed_upload_without_a_length_is_cut_off_at_the_limit(open_site) -> None:
    client = open_site(max_upload_bytes=LIMIT)
    seat(client)
    boundary = "b0undary"
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"kind\"\r\n\r\nmock\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"; filename=\"big.onnx\"\r\n\r\n").encode()

    def body():
        yield head
        for _ in range(100):  # 100 × 1000 bytes，遠超過上限
            yield b"x" * 1000
        yield f"\r\n--{boundary}--\r\n".encode()

    response = client.post("/api/submissions", content=body(),
                           headers={"Content-Type": f"multipart/form-data; boundary={boundary}", **as_group("01")})


    assert response.status_code == 413, "沒有 Content-Length 的上傳，收到超過上限就要退件"
    assert "太大" in response.json()["detail"]
