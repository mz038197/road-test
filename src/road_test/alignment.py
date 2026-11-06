"""影片幀和標註幀怎麼對齊（老師準備路考資料時用）

把影片幀縮到 480×360，跟 CamVid 標註用的原圖比平均像素差：對上的那幀約 2～4，相鄰幀約 13～26。
實際比對影片的程式在 scripts/align_video.py，這裡只放判斷規則。
"""
from dataclasses import dataclass

# 相鄰幀的平均像素差至少要比對上的那幀多這麼多，才算分得清楚是哪一幀。
# 對上的那幀只剩壓縮雜訊（約 1～3）；車子幾乎停住時相鄰幀也很像，所以看差距，不看倍數。
CLEAR_GAP = 1.5


@dataclass(frozen=True)
class Match:
    video_frame: int
    diff: float  # 對上那幀的平均像素差
    neighbour_diff: float  # 前後兩幀裡比較像的那幀的平均像素差

    @property
    def clear(self) -> bool:
        return self.neighbour_diff - self.diff >= CLEAR_GAP


def best_match(diffs: dict[int, float]) -> Match:
    """diffs：影片幀號 → 跟某張標註原圖的平均像素差。"""
    frame = min(diffs, key=diffs.get)
    neighbours = [diffs[n] for n in (frame - 1, frame + 1) if n in diffs]
    return Match(frame, diffs[frame], min(neighbours))


def offset(matches: dict[int, Match]) -> int:
    """matches：標註幀號 → 對上的影片幀。回傳位移（影片幀號 − 標註幀號）。

    對得清楚的標註位移都要一樣，不然就停下來。分不出是哪一幀的（停車時畫面幾乎沒動）不投票：
    前後幾幀畫面都一樣，照位移取哪一幀都對。
    """
    clear = {label: m.video_frame - label for label, m in matches.items() if m.clear}
    if not clear:
        raise SystemExit("每一張標註都分不出對上哪一幀，沒辦法決定位移")
    shifts = list(clear.values())
    common = max(set(shifts), key=shifts.count)
    bad = [
        f"f{label:05d}（對上影片第 {matches[label].video_frame} 幀，差 {matches[label].diff:.1f}，"
        f"相鄰幀 {matches[label].neighbour_diff:.1f}）"
        for label, shift in sorted(clear.items())
        if shift != common
    ]
    if bad:
        raise SystemExit(f"這幾張標註對不上位移 {common:+d}：" + "、".join(bad))
    return common
