"""委託頁：學生完全從路考網站開始。首頁就是委託（說明、注意事項），從這裡下載訓練影片、訓練程式，再去交件頁交模型。"""
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import pytest

STUDENT = Path(__file__).parents[1] / "student"
VIDEO = bytes(range(256)) * 40  # 假的訓練影片，10240 bytes


@pytest.fixture
def training_video(data_dir):
    (data_dir / "videos").mkdir()
    (data_dir / "videos" / "0016E5.mp4").write_bytes(VIDEO)
    return VIDEO


def test_anyone_can_download_the_training_video_without_a_group_code(client, training_video) -> None:
    response = client.get("/downloads/0016E5.mp4")

    assert response.status_code == 200, f"訓練影片不需要組別代碼就能下載：{response.text}"
    assert response.content == training_video
    assert response.headers["content-type"] == "video/mp4"
    assert "attachment" in response.headers.get("content-disposition", ""), "按連結要直接存檔，不是在分頁裡播"


def test_the_training_video_supports_range_requests_so_downloads_can_resume(client, training_video) -> None:
    response = client.get("/downloads/0016E5.mp4", headers={"Range": "bytes=100-199"})

    assert response.status_code == 206, "下載中斷後瀏覽器會用 Range 接著下載，要回 206"
    assert response.content == training_video[100:200]


def test_missing_training_video_says_so_in_chinese(client) -> None:
    response = client.get("/downloads/0016E5.mp4")

    assert response.status_code == 404
    assert "訓練影片" in response.json()["detail"], "找不到時要說是訓練影片不見了，請學生通知老師"


def test_anyone_can_download_the_local_training_program(client) -> None:
    response = client.get("/downloads/train_yolo.py")

    assert response.status_code == 200, f"本機訓練程式不需要組別代碼就能下載：{response.text}"
    assert response.content == (STUDENT / "train_yolo.py").read_bytes(), "下載的要是 repo 裡 student/ 的那一支"
    assert "attachment" in response.headers.get("content-disposition", ""), "按連結要直接存成 train_yolo.py"


COLAB = "https://colab.research.google.com/github/mz038197/road-test/blob/main/student/train_yolo_colab.ipynb"


def test_the_home_page_is_the_brief(client) -> None:
    page = client.get("/").text

    assert COLAB in page, "「在 Colab 開啟」要直接打開 GitHub 上 road-test repo main 的訓練筆記本"
    assert (STUDENT / "train_yolo_colab.ipynb").is_file(), "Colab 連結指的筆記本要在 repo 的 student/ 裡"
    for link in ["/downloads/0016E5.mp4", "/downloads/train_yolo.py", "submit.html"]:
        assert f'href="{link}"' in page, f"委託頁要有連到 {link} 的連結"
    assert "Stretch" in page, "注意事項要提醒 Roboflow 匯出時不要選 Stretch"
    for name in ["car", "pedestrian", "bicyclist", "sign"]:
        assert f"<code>{name}</code>" in page, f"委託頁要寫出類別名稱 {name}"


def test_the_submission_form_moved_to_its_own_page(client) -> None:
    page = client.get("/submit.html")

    assert page.status_code == 200
    assert 'id="submit"' in page.text and "submit.js" in page.text, "交件表單在 submit.html"


@pytest.mark.parametrize("page", ["/", "/submit.html", "/result.html", "/teacher.html"])
def test_every_link_on_the_site_pages_works(client, training_video, page: str) -> None:
    html = client.get(page).text
    links = re.findall(r'(?:href|src)="([^"#?]+)', html)

    assert links, f"{page} 應該有連結"
    for link in links:
        if link.startswith("http"):
            continue
        response = client.get(urlsplit(urljoin(f"http://testserver{page}", link)).path)
        assert response.status_code == 200, f"{page} 裡的 {link} 打不開（{response.status_code}）"


@pytest.mark.parametrize("page", ["/", "/submit.html"])
def test_step_navigation_jumps_to_each_step(client, page: str) -> None:
    html = client.get(page).text
    anchors = re.findall(r'href="#([^"]+)"', html)
    ids = set(re.findall(r'\bid="([^"]+)"', html))

    assert anchors, f"{page} 要有步驟導覽（連到頁內每一步）"
    missing = [a for a in anchors if a not in ids]
    assert not missing, f"{page} 的步驟導覽連到不存在的地方：{missing}"


@pytest.mark.parametrize("page", ["/", "/submit.html", "/result.html", "/teacher.html"])
def test_pages_load_nothing_from_other_sites(client, page: str) -> None:
    html = client.get(page).text
    external = re.findall(r'<(?:link|script)[^>]+(?:href|src)="(https?://[^"]+)"', html)

    assert not external, f"{page} 不要從外站載字型或程式，學校網路擋外站時會卡住：{external}"


def test_submission_fields_are_named_after_what_they_ask_for(client) -> None:
    html = client.get("/submit.html").text
    labels = dict(re.findall(r'<label for="([^"]+)">([^<]+)</label>', html))

    assert "模型檔" in labels.get("model", ""), f"上傳欄位的名稱要寫「模型檔」：{labels.get('model')}"
    assert "CONF" in labels.get("conf", ""), f"CONF 欄位的名稱要寫「CONF」：{labels.get('conf')}"
