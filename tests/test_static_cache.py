"""前端檔案每次都要跟伺服器確認有沒有新版（Cache-Control: no-cache），部署後學生才不會跑到舊的 JS。"""
import pytest


@pytest.mark.parametrize("path", ["/", "/index.html", "/result.js", "/style.css"])
def test_front_end_files_are_revalidated_after_deploys(client, path: str) -> None:
    r = client.get(path)

    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-cache", f"{path} 要加 Cache-Control: no-cache，部署後瀏覽器才會拿新版"


def test_unchanged_front_end_file_is_not_downloaded_again(client) -> None:
    etag = client.get("/result.js").headers["etag"]

    assert client.get("/result.js", headers={"If-None-Match": etag}).status_code == 304, "沒改過的檔案回 304，不用重下"
