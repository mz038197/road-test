"""網站設定從環境變數來：worker 數、每個 worker 的執行緒、閒置多久自動關機。"""
import pytest

from road_test.web.settings import from_env


def test_four_cores_default_to_two_workers_with_two_threads() -> None:
    s = from_env({}, cpu_count=4)

    assert (s.workers, s.threads) == (2, 2), "沒設定時 4 核心：2 個 worker、每個 2 個執行緒"
    assert s.idle_minutes is None, "本機沒設定閒置時間，網站不會自己關掉"


def test_threads_follow_workers_and_cpu_when_only_workers_is_set() -> None:
    s = from_env({"ROAD_TEST_WORKERS": "4"}, cpu_count=16)

    assert (s.workers, s.threads) == (4, 4), "16 核心、4 個 worker：每個 worker 4 個執行緒"


def test_explicit_values_win() -> None:
    s = from_env({"ROAD_TEST_WORKERS": "1", "ROAD_TEST_THREADS": "3", "ROAD_TEST_IDLE_MINUTES": "5"}, cpu_count=8)

    assert (s.workers, s.threads, s.idle_minutes) == (1, 3, 5), "環境變數有寫就照寫的"


def test_idle_minutes_zero_or_blank_means_never_stop() -> None:
    assert from_env({"ROAD_TEST_IDLE_MINUTES": "0"}, cpu_count=4).idle_minutes is None, "0 分鐘 = 不自己關"
    assert from_env({"ROAD_TEST_IDLE_MINUTES": ""}, cpu_count=4).idle_minutes is None, "空字串 = 沒設定"


def test_unknown_cpu_count_still_gives_one_thread() -> None:
    assert from_env({}, cpu_count=None).threads == 1, "讀不到核心數時至少 1 個執行緒"


@pytest.mark.parametrize("name, value", [
    ("ROAD_TEST_WORKERS", "two"), ("ROAD_TEST_WORKERS", "0"),
    ("ROAD_TEST_THREADS", "-1"), ("ROAD_TEST_IDLE_MINUTES", "soon"),
])
def test_bad_values_stop_with_a_message_naming_the_variable(name: str, value: str) -> None:
    with pytest.raises(SystemExit) as e:
        from_env({name: value}, cpu_count=4)

    assert name in str(e.value), f"錯誤訊息要說是哪個環境變數寫錯：{e.value}"


def test_bigger_machine_gets_more_workers_without_extra_settings() -> None:
    s = from_env({}, cpu_count=16)

    assert (s.workers, s.threads) == (8, 2), "正式路考那天升級成 16 核心：不用改設定就變成 8 個 worker × 2 個執行緒"


def test_upload_limit_defaults_to_100_mb_and_can_be_set() -> None:
    assert from_env({}, cpu_count=4).max_upload_bytes == 100_000_000, "沒設定時模型檔上限 100 MB"
    assert from_env({"ROAD_TEST_MAX_UPLOAD_MB": "250"}, cpu_count=4).max_upload_bytes == 250_000_000
