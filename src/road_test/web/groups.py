"""組別代碼：存在路考資料夾的 groups.json（{"01": "bako-mite", ...}），由管理頁產生或重設。

每次請求都重讀，所以手改 groups.json 也不用重開。寫入時先寫暫存檔再換掉，寫到一半當機也不會壞掉。
代碼是課堂上唸給各組抄的：兩段、各兩個「子音＋母音」音節，例如 bako-mite，只用不容易聽錯、看錯的字母。
比對時不分大小寫、不管空白和連字號，學生抄成 BAKO MITE 也收。
"""
import json
import secrets
from pathlib import Path

CONSONANTS = "bdfghkmnpst"  # 不用 l/r（唸起來分不清）、c/q/x/j/v/w/y/z（唸法不直覺）
VOWELS = "aeiou"


def new_code() -> str:
    """例如 bako-mite：55⁴ ≈ 915 萬種，配合每組同時一件、每次重設都換，課堂用夠了。"""
    syllable = lambda: secrets.choice(CONSONANTS) + secrets.choice(VOWELS)  # noqa: E731
    return f"{syllable()}{syllable()}-{syllable()}{syllable()}"


def normalize(code: str) -> str:
    return "".join(c for c in code.lower() if c.isalnum())


def group_name(n: int) -> str:
    """組別名稱：01、02…（會放在 HTTP 標頭 X-Group，要英數字）。"""
    return f"{n:02d}"


class GroupCodes:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, codes: dict[str, str]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(sorted(codes.items())), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def matches(self, group: str, code: str) -> bool:
        right = self.load().get(group)
        return bool(right and code) and normalize(right) == normalize(code)

    def reset(self, group: str) -> str:
        """給這組一個新代碼（舊的馬上失效）。"""
        codes = self.load()
        codes[group] = new_code()
        self.save(codes)
        return codes[group]

    def reset_all(self) -> dict[str, str]:
        codes = {g: new_code() for g in self.load()}
        self.save(codes)
        return codes

    def set_count(self, count: int) -> dict[str, str]:
        """組別改成 01…count：原本就有的組保留代碼，新的組產生代碼，多出來的組刪掉（路考紀錄還在）。"""
        old = self.load()
        codes = {g: old.get(g) or new_code() for g in map(group_name, range(1, count + 1))}
        self.save(codes)
        return codes
