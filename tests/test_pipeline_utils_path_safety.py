from pathlib import Path

import pytest

from pipeline_utils import (
    assert_path_not_under_roots,
    is_path_under_any_root,
    normalize_windows_path,
)


def test_normalize_windows_path_is_case_insensitive_and_resolved_without_existing():
    path = normalize_windows_path(r"X:\\1000 - 3999\\1234 Project")

    assert str(path).lower().endswith(r"x:\1000 - 3999\1234 project")


def test_is_path_under_any_root_detects_child_path():
    assert is_path_under_any_root(
        Path(r"X:\\1000 - 3999\\1234 Project\\Engineering"),
        [Path(r"X:\\1000 - 3999")],
    )


def test_is_path_under_any_root_rejects_sibling_prefix():
    assert not is_path_under_any_root(
        Path(r"X:\\1000 - 3999 Archive\\1234 Project"),
        [Path(r"X:\\1000 - 3999")],
    )


def test_assert_path_not_under_roots_raises_for_cloud_output_path():
    with pytest.raises(ValueError, match="cloud source root"):
        assert_path_not_under_roots(
            Path(r"J:\\JOBS\\1234 Project\\cache.json"),
            [Path(r"J:\\JOBS")],
            "folder_index_cache",
        )


def test_assert_path_not_under_roots_allows_local_output_path():
    assert_path_not_under_roots(
        Path(r"D:\\WML_Local_Project_Search\\data\\cache\\folder_index.json"),
        [Path(r"J:\\JOBS")],
        "folder_index_cache",
    )
