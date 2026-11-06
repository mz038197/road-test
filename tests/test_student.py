"""發給學生的訓練程式（student/train_yolo.py 和 Colab 版）：不用設定組別和 CONF，輸出固定檔名的模型檔，類別名稱先檢查。

測試不裝 ultralytics（很大）：用一個假的 ultralytics 模組，只確認程式會把匯出的模型存成固定檔名。
"""
import importlib.util
import json
import re
import sys
import types
from pathlib import Path

import pytest

STUDENT = Path(__file__).parents[1] / "student"
FIXED_NAME = "road-test-model.onnx"


def load_program():
    spec = importlib.util.spec_from_file_location("train_yolo", STUDENT / "train_yolo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_dataset(folder: Path, names) -> Path:
    data = folder / "data.yaml"
    data.write_text(json.dumps({"names": names}), encoding="utf-8")  # JSON 也是合法的 YAML
    return data


class FakeYOLO:
    """假的 ultralytics.YOLO：train 記下參數，export 寫出一個假的 ONNX。"""

    def __init__(self, weights):
        self.trainer = types.SimpleNamespace(best=Path("runs/best.pt"))
        self.names = {0: "car", 1: "pedestrian", 2: "bicyclist", 3: "sign"}

    def train(self, **kwargs):
        pass

    def val(self, **kwargs):
        return types.SimpleNamespace(box=types.SimpleNamespace(ap50=[0.5, 0.4, 0.3, 0.2], ap_class_index=[0, 1, 2, 3]))

    def export(self, **kwargs):
        out = Path("runs/best.onnx")
        out.parent.mkdir(exist_ok=True)
        out.write_bytes(b"onnx")
        return str(out)


@pytest.fixture
def fake_ultralytics(monkeypatch):
    monkeypatch.setitem(sys.modules, "ultralytics", types.SimpleNamespace(YOLO=FakeYOLO))


def test_training_writes_the_model_under_a_fixed_name_without_group_or_conf(tmp_path, monkeypatch, fake_ultralytics):
    monkeypatch.chdir(tmp_path)
    program = load_program()
    data = write_dataset(tmp_path, ["bicyclist", "car", "pedestrian", "sign"])

    upload = program.train(data=str(data), model="yolo26n.pt", epochs=1, imgsz=640, batch=-1)

    assert Path(upload).name == FIXED_NAME, "不分組別，跑完都是同一個檔名，直接上傳"
    assert (tmp_path / FIXED_NAME).read_bytes() == b"onnx", "匯出的 ONNX 要複製到執行的資料夾"
    assert not hasattr(program, "GROUP") and not hasattr(program, "CONF"), "組別和 CONF 交件時才填，訓練程式不用設定"


@pytest.mark.parametrize("names", [["car", "person", "bicyclist", "sign"], {0: "Car", 1: "pedestrian", 2: "bicyclist", 3: "sign"},
                                   ["car", "pedestrian", "bicyclist"]])
def test_wrong_class_names_stop_before_training(tmp_path, monkeypatch, fake_ultralytics, names):
    monkeypatch.chdir(tmp_path)
    program = load_program()
    data = write_dataset(tmp_path, names)

    with pytest.raises(SystemExit, match="類別名稱"):
        program.train(data=str(data), model="yolo26n.pt", epochs=1, imgsz=640, batch=-1)
    assert not (tmp_path / FIXED_NAME).exists(), "類別不對就不要訓練，也不要產生模型檔"


def notebook_code() -> list[str]:
    nb = json.loads((STUDENT / "train_yolo_colab.ipynb").read_text(encoding="utf-8"))
    return ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]


def test_the_colab_notebook_runs_the_same_program_and_downloads_the_fixed_name() -> None:
    cells = notebook_code()
    program = (STUDENT / "train_yolo.py").read_text(encoding="utf-8")

    assert any(cell.strip() == program.strip() for cell in cells), "Colab 版的訓練那一格要跟 train_yolo.py 一模一樣"
    assert f'files.download("{FIXED_NAME}")' in cells[-1], "最後一格下載固定檔名的模型檔"
    assert not any(re.search(r"^(GROUP|CONF)\s*=", c, re.M) for c in cells), "Colab 版也不用設定組別和 CONF"
