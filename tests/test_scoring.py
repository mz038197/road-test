import json

import pytest

from road_test.scoring import score

CAR = [100, 100, 200, 160]

def _answers(*frames: list[dict]) -> dict:
    """每個參數是一個計分幀的答案東西清單。"""
    return {"sequence": "Test", "frames": [{"name": f"Test_f{i:05d}.png", "objects": objs} for i, objs in enumerate(frames)]}


def _obj(cls: str, box: list[int], relevant: bool = True) -> dict:
    return {"cls": cls, "box": box, "relevant": relevant}


def _det(cls: str, box: list[int], conf: float = 0.9) -> dict:
    return {"cls": cls, "box": box, "conf": conf}


def test_seeing_everything_without_false_alarms_is_100() -> None:
    answers = _answers([_obj("car", CAR)], [_obj("car", CAR)])
    detections = [[_det("car", CAR)], [_det("car", CAR)]]

    result = score(answers, detections, conf=0.35)

    assert result["score"] == 100, "全都看見、沒有誤判應該是 100 分"
    assert result["deducted"] == 0
    assert result["log"] == [], "沒有扣分，考官紀錄應該是空的"


def test_seeing_nothing_is_0_and_logged_when_it_disappears() -> None:
    answers = _answers([_obj("pedestrian", CAR)], [_obj("pedestrian", CAR)], [], [])

    result = score(answers, [[], [], [], []], conf=0.35)

    assert result["score"] == 0, "什麼都沒看見應該是 0 分"
    assert result["full"] == 5, "一個行人扣滿是 5 分"
    assert result["log"] == [
        {"frame": 2, "time": 2, "cls": "pedestrian", "kind": "漏看", "reaction": None, "penalty": 5}
    ], "一直沒看見的行人要在消失的那一幀扣滿，反應時間是 None"


@pytest.mark.parametrize(
    ("seen_at", "penalty"),
    [(1, 0), (2, 1.5), (3, 3), (4, 3)],
    ids=["0秒不扣", "1秒扣一半", "2秒扣滿", "3秒扣滿"],
)
def test_reaction_time_decides_the_penalty(seen_at: int, penalty: float) -> None:
    # 騎車的人第 0 幀太遠，第 1 幀起變成必看，一直到第 5 幀
    answers = _answers(
        [_obj("bicyclist", CAR, relevant=False)], *[[_obj("bicyclist", CAR)] for _ in range(5)]
    )
    detections = [[] for _ in range(6)]
    detections[seen_at] = [_det("bicyclist", CAR)]

    result = score(answers, detections, conf=0.35)

    reaction = seen_at - 1
    expected = [{"frame": seen_at, "time": seen_at, "cls": "bicyclist", "kind": "漏看", "reaction": reaction, "penalty": penalty}]
    assert result["log"] == (expected if penalty else []), f"慢 {reaction} 秒應該扣 {penalty} 分，而且在看見那一幀結算"
    assert result["deducted"] == penalty


def test_seeing_it_before_it_becomes_relevant_counts_as_0_seconds() -> None:
    answers = _answers([_obj("car", CAR, relevant=False)], [_obj("car", CAR, relevant=False)], [_obj("car", CAR)], [_obj("car", CAR)])
    detections = [[_det("car", CAR)], [], [], []]

    result = score(answers, detections, conf=0.35)

    assert result["log"] == [], "還很遠的時候就看見，算 0 秒，不扣分"
    assert result["score"] == 100


def test_losing_it_after_seeing_it_is_not_penalized() -> None:
    answers = _answers(*[[_obj("pedestrian", CAR)] for _ in range(5)])
    detections = [[_det("pedestrian", CAR)], [], [], [], []]

    result = score(answers, detections, conf=0.35)

    assert result["log"] == [], "看見之後跟丟，不再扣分"


GHOST = [300, 50, 340, 130]


def test_the_same_false_alarm_is_penalized_once() -> None:
    answers = _answers([_obj("car", CAR)], [_obj("car", CAR)], [_obj("car", CAR)], [_obj("car", CAR)])
    # 同一個鬼影連續三幀都在，稍微往右移
    ghost = [[300 + 5 * i, 50, 340 + 5 * i, 130] for i in range(3)]
    detections = [[_det("car", CAR), _det("pedestrian", ghost[0])], [_det("car", CAR), _det("pedestrian", ghost[1])],
                  [_det("car", CAR), _det("pedestrian", ghost[2])], [_det("car", CAR)]]

    result = score(answers, detections, conf=0.35)

    assert result["log"] == [
        {"frame": 0, "time": 0, "cls": "pedestrian", "kind": "誤判", "reaction": None, "penalty": 1}
    ], "同一個誤判的東西連續三幀，只在第一次出現時扣 1 分"
    assert result["full"] == 2
    assert result["score"] == 50


def test_framing_something_not_relevant_is_not_a_false_alarm() -> None:
    answers = _answers([_obj("sign", GHOST, relevant=False)])

    result = score(answers, [[_det("sign", GHOST)]], conf=0.35)

    assert result["log"] == [], "框到太遠、太小的東西不算誤判"


def _missed(result: dict) -> float:
    return sum(e["penalty"] for e in result["log"] if e["kind"] == "漏看")


def _count(result: dict, kind: str) -> int:
    return sum(1 for e in result["log"] if e["kind"] == kind)


def test_raising_conf_never_adds_false_alarms_or_removes_misses() -> None:
    car2 = [400, 200, 470, 250]
    answers = _answers(*[[_obj("car", CAR), _obj("car", car2), _obj("sign", GHOST, relevant=False)] for _ in range(4)])
    # 鬼影中間那幀信心很低：CONF 提高時不能因此被拆成兩個誤判
    detections = [
        [_det("car", CAR, 0.3), _det("pedestrian", [10, 10, 40, 80], 0.9), _det("car", car2, 0.6)],
        [_det("car", CAR, 0.8), _det("pedestrian", [12, 10, 42, 80], 0.1), _det("sign", GHOST, 0.9)],
        [_det("pedestrian", [14, 10, 44, 80], 0.9), _det("bicyclist", [250, 250, 290, 330], 0.4)],
        [_det("car", car2, 0.5)],
    ]

    results = [score(answers, detections, conf=c) for c in (0.05, 0.2, 0.35, 0.5, 0.7, 0.95)]

    for low, high in zip(results, results[1:]):
        assert _count(high, "誤判") <= _count(low, "誤判"), "CONF 提高，誤判不應該變多"
        assert _count(high, "漏看") >= _count(low, "漏看"), "CONF 提高，漏看不應該變少"
        assert _missed(high) >= _missed(low), "CONF 提高，漏看扣的分不應該變少"
    assert _count(results[0], "誤判") == 2, "低 CONF：鬼影行人和騎車的人各一次"
    assert _count(results[-1], "誤判") == 0


def test_each_scoring_frame_marks_seen_waiting_and_false_alarms() -> None:
    far = [_obj("pedestrian", CAR, relevant=False)]
    answers = _answers(far, [_obj("pedestrian", CAR)], [_obj("pedestrian", CAR)], [_obj("pedestrian", CAR)], [])
    detections = [[], [], [_det("car", GHOST, 0.2)], [_det("pedestrian", CAR), _det("car", GHOST, 0.9)], []]

    frames = score(answers, detections, conf=0.35)["frames"]

    assert [f["frame"] for f in frames] == [0, 1, 2, 3, 4]
    assert frames[0]["marks"] == [], "還不是必看的東西不畫判定"
    assert frames[1]["marks"] == [{"color": "red", "cls": "pedestrian", "box": CAR, "waiting": 0}], "剛變成必看、還沒看見：紅色，計時 0 秒"
    assert frames[2]["marks"] == [{"color": "red", "cls": "pedestrian", "box": CAR, "waiting": 1}], "低於 CONF 的框不算，也不畫誤判"
    assert frames[3]["marks"] == [
        {"color": "green", "cls": "pedestrian", "box": CAR},
        {"color": "orange", "cls": "car", "box": GHOST},
    ], "看見了是綠色；誤判是橘色"
    assert frames[4]["marks"] == []


def test_log_entries_are_in_settlement_order_and_json_ready() -> None:
    ped = [300, 200, 340, 300]
    answers = _answers(
        [_obj("car", CAR), _obj("pedestrian", ped)],
        [_obj("car", CAR), _obj("pedestrian", ped)],
        [_obj("car", CAR), _obj("pedestrian", ped)],
        [],
    )
    detections = [[], [_det("sign", GHOST)], [_det("pedestrian", ped)], []]

    result = score(answers, detections, conf=0.35)

    assert [(e["frame"], e["kind"], e["cls"]) for e in result["log"]] == [
        (1, "誤判", "sign"), (2, "漏看", "pedestrian"), (3, "漏看", "car"),
    ], "考官紀錄要照結算的時間排"
    for entry in result["log"]:
        assert set(entry) == {"frame", "time", "cls", "kind", "reaction", "penalty"}, "每條都要有時間、類別、種類、反應時間、扣分"
    assert json.loads(json.dumps(result, ensure_ascii=False)) == result, "輸出要能直接轉成 JSON"


def test_two_answer_boxes_in_one_frame_are_two_things_even_if_both_touch_one_box_before() -> None:
    # 第 0 幀只有一台車；第 1 幀那台車旁邊又冒出一台，兩個框都跟第 0 幀那台重疊
    beside = [150, 100, 250, 160]
    answers = _answers([_obj("car", CAR)], [_obj("car", CAR), _obj("car", beside)], [_obj("car", CAR), _obj("car", beside)])

    result = score(answers, [[], [], []], conf=0.35)

    assert result["full"] == 4, "同一幀的兩個框是兩台車，扣滿是 2 + 2 = 4，不能併成一台"
    assert _count(result, "漏看") == 2, "兩台都沒看見，要各記一次漏看"


def test_two_false_alarms_in_one_frame_are_two_even_if_both_touch_one_before() -> None:
    answers = _answers([], [], [])
    ghost, beside = [300, 50, 340, 130], [320, 50, 360, 130]
    detections = [[_det("pedestrian", ghost)], [_det("pedestrian", ghost), _det("pedestrian", beside)],
                  [_det("pedestrian", ghost), _det("pedestrian", beside)]]

    result = score(answers, detections, conf=0.35)

    assert _count(result, "誤判") == 2, "同一幀兩個誤判框是兩個東西，各扣 1 分"
