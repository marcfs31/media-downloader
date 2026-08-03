from __future__ import annotations

from pathlib import Path

from media_downloader.config import (
    get_download_all_default,
    load_config,
    save_config,
    set_download_all_default,
)


class TestConfig:
    def test_missing_file_yields_empty_config(self, tmp_path: Path) -> None:
        assert load_config(tmp_path / "nope.json") == {}

    def test_save_then_load_roundtrips(self, tmp_path: Path) -> None:
        path = tmp_path / "sub" / "config.json"
        save_config({"a": 1}, path)
        assert load_config(path) == {"a": 1}

    def test_corrupt_file_yields_empty_config(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        path.write_text("{not json")
        assert load_config(path) == {}

    def test_download_all_default_is_false_until_set(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        assert get_download_all_default(path) is False
        set_download_all_default(True, path)
        assert get_download_all_default(path) is True

    def test_setting_default_preserves_other_keys(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        save_config({"unrelated": "value"}, path)
        set_download_all_default(True, path)
        config = load_config(path)
        assert config["unrelated"] == "value"
        assert config["download_all_by_default"] is True
