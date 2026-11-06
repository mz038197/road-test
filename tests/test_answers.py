import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from road_test.answers import main

CAR, PEDESTRIAN = 8, 9


def _camvid(tmp_path: Path) -> Path:
    """一個只有兩張 Seq05VD 標註圖的小 CamVid：一台近的車、一個遠的小行人。"""
    camvid = tmp_path / "CamVid"
    (camvid / "testannot").mkdir(parents=True)
    mask = np.zeros((360, 480), dtype=np.uint8)
    mask[100:160, 50:150] = CAR  # 高 60，夠近，必看
    mask[200:205, 300:303] = PEDESTRIAN  # 高 5，太遠，不是必看
    for n in (1, 31):
        Image.fromarray(mask).save(camvid / "testannot" / f"Seq05VD_f{n:05d}.png")
    Image.fromarray(mask).save(camvid / "testannot" / "0001TP_008550.png")
    return camvid


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_writes_answers_for_the_sequence(tmp_path: Path) -> None:
    out = tmp_path / "Seq05VD.json"

    main(["--camvid", str(_camvid(tmp_path)), "--out", str(out)])

    answers = _read(out)
    assert answers["sequence"] == "Seq05VD"
    assert [f["name"] for f in answers["frames"]] == ["Seq05VD_f00001.png", "Seq05VD_f00031.png"], "只能收這一段街道的標註圖"
    objects = answers["frames"][0]["objects"]
    assert {"cls": "car", "box": [100, 200, 300, 320], "relevant": True} in objects, "近的車應該是必看，座標要換成 960×720"
    assert {"cls": "pedestrian", "box": [600, 400, 606, 410], "relevant": False} in objects, "遠的小行人不應該是必看"


def test_answers_are_in_video_size_with_frame_numbers(tmp_path: Path) -> None:
    out = tmp_path / "Seq05VD.json"

    main(["--camvid", str(_camvid(tmp_path)), "--out", str(out)])

    answers = _read(out)
    assert (answers["width"], answers["height"]) == (960, 720), "答案要用路考影片的大小 960×720"
    assert answers["fps"] == 29.97
    assert [f["frame"] for f in answers["frames"]] == [1, 31], "MP4 第 n 幀就是標註 f(n)，frame 要等於標註幀號"


def test_finds_a_sequence_in_the_training_split(tmp_path: Path) -> None:
    camvid = tmp_path / "CamVid"
    (camvid / "trainannot").mkdir(parents=True)
    mask = np.zeros((360, 480), dtype=np.uint8)
    mask[10:50, 10:40] = CAR
    for n in (960, 930):
        Image.fromarray(mask).save(camvid / "trainannot" / f"0006R0_f{n:05d}.png")
    out = tmp_path / "0006R0.json"

    main(["--camvid", str(camvid), "--out", str(out), "--sequence", "0006R0"])

    frames = _read(out)["frames"]
    assert [f["frame"] for f in frames] == [930, 960], "模擬路考 0006R0 的標註在 trainannot，要找得到，而且照幀號排"


def test_reads_folders_from_environment_variables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = tmp_path / "road-test-data"
    monkeypatch.setenv("CAMVID_DIR", str(_camvid(tmp_path)))
    monkeypatch.setenv("ROAD_TEST_DATA", str(data))

    main([])

    assert _read(data / "answers" / "Seq05VD.json")["sequence"] == "Seq05VD", "沒指定 --out 時，答案要存到 ROAD_TEST_DATA/answers/"


def test_explains_missing_environment_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAMVID_DIR", raising=False)

    with pytest.raises(SystemExit, match="CAMVID_DIR"):
        main([])
