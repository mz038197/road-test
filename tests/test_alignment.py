import pytest

from road_test.alignment import Match, best_match, offset


def test_best_match_is_the_closest_frame_and_reports_its_neighbours() -> None:
    # 影片第 10～14 幀跟某張標註圖的平均像素差
    diffs = {10: 25.0, 11: 15.0, 12: 2.5, 13: 14.0, 14: 24.0}

    m = best_match(diffs)

    assert m.video_frame == 12, "對上的應該是差最小的那幀"
    assert m.diff == 2.5
    assert m.neighbour_diff == 14.0, "相鄰幀取比較像的那一幀"
    assert m.clear, "對上的差遠小於相鄰幀，應該算對得清楚"


def test_a_match_no_better_than_its_neighbours_is_not_clear() -> None:
    m = best_match({10: 9.0, 11: 8.0, 12: 9.5})

    assert not m.clear, "差不多像的幾幀分不出是哪一幀，不能算對上"


def test_offset_is_video_frame_minus_label_when_every_label_agrees() -> None:
    matches = {930: Match(931, 2.0, 15.0), 960: Match(961, 3.0, 20.0)}

    assert offset(matches) == 1, "影片幀號 = 標註幀號 + 1 時，位移是 1"


def test_offset_refuses_a_label_that_clearly_matches_another_frame() -> None:
    matches = {930: Match(931, 2.0, 15.0), 960: Match(963, 3.0, 20.0), 990: Match(991, 2.0, 15.0)}

    with pytest.raises(SystemExit, match="f00960"):
        offset(matches)


def test_a_still_scene_does_not_decide_the_offset() -> None:
    # 停紅燈時前後幾幀幾乎一樣，最像的那幀可能差一兩幀，對哪一幀畫面都一樣
    matches = {930: Match(931, 2.0, 15.0), 960: Match(962, 2.8, 2.9), 990: Match(991, 2.0, 15.0)}

    assert offset(matches) == 1, "分不出是哪一幀的標註不投票，也不算對不上"


def test_offset_needs_at_least_one_clear_match() -> None:
    with pytest.raises(SystemExit, match="分不出"):
        offset({930: Match(931, 2.8, 2.9)})


def test_a_slow_scene_still_counts_as_clear() -> None:
    # 車子幾乎停住時相鄰幀也很像（0006R0 的 f02340：對上 3.3、相鄰 4.9）
    m = best_match({2339: 7.4, 2340: 6.2, 2341: 3.3, 2342: 4.9, 2343: 6.4})

    assert m.video_frame == 2341
    assert m.clear, "對上的那幀明顯比相鄰幀像，就算車子開得慢也算對上"
