"""結果頁播放要的東西：路考影片的網址（<video> 不能帶標頭，所以網址自己帶權限）、支援拖時間軸的 Range 請求。"""
import pytest

from conftest import PED, as_group, model, run_queue, submit

VIDEO = bytes(range(256)) * 40  # 假的路考影片，10240 bytes


@pytest.fixture
def video(data_dir):
    (data_dir / "videos").mkdir()
    (data_dir / "videos" / "Mini.mp4").write_bytes(VIDEO)
    return VIDEO


def video_url(client, sid: int, group: str = "01") -> str:
    return client.get(f"/api/submissions/{sid}", headers=as_group(group)).json()["video_url"]


def test_the_result_carries_a_video_url_that_plays_without_group_headers(client, video) -> None:
    sid = submit(client).json()["id"]

    url = video_url(client, sid)
    response = client.get(url)  # 不帶 X-Group：<video> 沒辦法帶標頭

    assert response.status_code == 200, f"結果裡的影片網址不帶標頭也要能播：{response.text}"
    assert response.content == video, "影片網址給的是這次路考的影片"
    assert response.headers["content-type"] == "video/mp4"


def test_the_video_supports_range_requests_so_dragging_the_timeline_is_smooth(client, video) -> None:
    sid = submit(client).json()["id"]

    response = client.get(video_url(client, sid), headers={"Range": "bytes=1000-1099"})

    assert response.status_code == 206, "拖時間軸時瀏覽器只要影片的一段，要回 206"
    assert response.content == video[1000:1100], "回的要是要求的那一段"
    assert response.headers["content-range"] == f"bytes 1000-1099/{len(video)}"


def test_a_video_url_without_a_valid_signature_is_refused(client, video) -> None:
    sid = submit(client).json()["id"]
    url = video_url(client, sid)
    path = url.split("?")[0]

    assert client.get(path).status_code == 403, "沒有簽章的影片網址不能看"
    assert client.get(f"{path}?t=0123abcd").status_code == 403, "簽章不對不能看"
    other = submit(client, group="02").json()["id"]
    forged = url.replace(f"/submissions/{sid}/", f"/submissions/{other}/")
    assert client.get(forged).status_code == 403, "拿自己這次的簽章不能看別次的影片"


def test_the_detections_and_the_result_line_up_on_video_frames(client) -> None:
    # 給播放用：計分幀的判定、考官紀錄都用影片幀號；每一幀的框照影片幀號排
    sid = submit(client, content=model({3: [("pedestrian", PED, 0.6)], 5: [("pedestrian", PED, 0.9)]})).json()["id"]
    run_queue(client)

    result = client.get(f"/api/submissions/{sid}", headers=as_group("01")).json()["result"]
    detections = client.get(f"/api/submissions/{sid}/detections", headers=as_group("01")).json()

    assert [f["video_frame"] for f in result["frames"]] == detections["scoring_frames"] == [2, 5, 8]
    assert detections["frames"][3] == [[100, 100, 140, 200, 0.6, 1]], "第 3 幀（非計分幀）的框也要有"
    assert detections["frames"][4] == [], "沒框到的幀是空陣列"
    assert result["log"][0]["video_frame"] == 5, "考官紀錄記在結算的影片幀"


class Clock:
    """假時鐘：測試自己往前撥。"""

    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_a_video_url_expires_after_six_hours_and_a_fresh_one_works(open_site, video) -> None:
    clock = Clock()
    client = open_site(clock=clock)
    sid = submit(client).json()["id"]
    url = video_url(client, sid)

    clock.now += 6 * 3600 - 1
    assert client.get(url).status_code == 200, "6 小時內影片網址都能用"
    clock.now += 2
    assert client.get(url).status_code == 403, "超過 6 小時的影片網址要失效"
    assert client.get(video_url(client, sid)).status_code == 200, "重新拿結果就有新的網址可以看"


def test_the_expiry_time_cannot_be_extended_by_editing_the_url(open_site, video) -> None:
    clock = Clock()
    client = open_site(clock=clock)
    sid = submit(client).json()["id"]
    url = video_url(client, sid)
    expires = int(url.split("e=")[1].split("&")[0])

    clock.now += 7 * 3600
    stretched = url.replace(f"e={expires}", f"e={expires + 86400}")
    assert client.get(stretched).status_code == 403, "自己改網址上的到期時間不能延長"
