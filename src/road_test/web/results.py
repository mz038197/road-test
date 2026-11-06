"""把存下來的偵測框交給計分模組，加上影片幀號，變成結果頁要的成績。

#8 的 CONF 滑桿用新的 CONF 重算時，也是呼叫 score_result。
"""
from road_test.scoring import score
from road_test.web.store import unpack


def score_result(answers: dict, detections: dict[int, list], conf: float) -> dict:
    """answers：一場路考的答案；detections：Store.detections() 的結果（至少要有全部的計分幀）。

    回傳計分模組的輸出，另外在考官紀錄每一條、每個計分幀加上
    video_frame（影片幀號）和 video_time（影片的秒數 = 幀號 / fps）。
    計分模組的 frame／time 是「第幾個計分幀」，畫在影片上要用 video_frame。
    """
    frames = [f["frame"] for f in answers["frames"]]
    result = score(answers, [[unpack(b) for b in detections[n]] for n in frames], conf)
    fps = answers["fps"]
    for entry in result["log"] + result["frames"]:
        entry["video_frame"] = frames[entry["frame"]]
        entry["video_time"] = frames[entry["frame"]] / fps
    result["conf"] = conf
    return result
