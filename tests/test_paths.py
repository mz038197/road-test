from pathlib import Path

import pytest

from road_test import paths


def test_road_test_videos_live_in_the_data_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROAD_TEST_DATA", str(tmp_path))

    assert paths.videos_dir() / "0006R0.mp4" == tmp_path / "videos" / "0006R0.mp4", "路考影片放在 ROAD_TEST_DATA/videos/"
