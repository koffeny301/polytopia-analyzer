"""Тесты разбора ссылок на матчи Polytopia."""

import pytest

from polytopia_analyzer.links import (
    extract_game_id,
    mobile_link,
    share_link,
    steam_link,
)

UUID = "a4d531fd-1b81-428c-d81f-08dae2baaab2"


def test_extract_from_share_url():
    assert extract_game_id(f"https://share.polytopia.io/g/{UUID}") == UUID


def test_extract_from_mobile_deeplink():
    assert extract_game_id(f"polytopia://opengame?id={UUID}") == UUID


def test_extract_from_steam_deeplink():
    assert extract_game_id(f"steam://run/874390//opengame?id={UUID}") == UUID


def test_extract_from_bare_uuid_normalizes_case_and_whitespace():
    assert extract_game_id(f"  {UUID.upper()}\n") == UUID


@pytest.mark.parametrize("bad", ["", "https://example.com/g/not-a-uuid", "polytopia://opengame?id=123", None])
def test_extract_invalid_raises(bad):
    with pytest.raises(ValueError, match="извлечь id матча"):
        extract_game_id(bad)


def test_link_builders():
    assert mobile_link(UUID) == f"polytopia://opengame?id={UUID}"
    assert steam_link(UUID) == f"steam://run/874390//opengame?id={UUID}"
    assert share_link(UUID) == f"https://share.polytopia.io/g/{UUID}"
