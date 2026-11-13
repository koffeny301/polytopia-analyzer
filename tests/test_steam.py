"""Тесты установщика строки запуска Steam (веха 6.4)."""

import json
from pathlib import Path

import pytest

from polytopia_analyzer import steam

VDF = '''"UserLocalConfigStore"
{
	"Software"
	{
		"Valve"
		{
			"Steam"
			{
				"apps"
				{
					"874390"
					{
						"LastPlayed"		"1794524800"
						"Playtime"		"27922"
					}
					"881100"
					{
						"Playtime"		"177"
					}
				}
			}
		}
	}
}
'''


@pytest.fixture()
def cfg(tmp_path):
    p = tmp_path / "localconfig.vdf"
    p.write_text(VDF, encoding="utf-8")
    return p


@pytest.fixture()
def shim(tmp_path):
    p = tmp_path / "libpolyread_dump.so"
    p.write_bytes(b"\x7fELF fake")
    return p


@pytest.fixture(autouse=True)
def state_file(tmp_path, monkeypatch):
    monkeypatch.setattr(steam, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(steam, "is_steam_running", lambda: False)
    monkeypatch.setattr(steam, "start_steam", lambda: None)
    return tmp_path / "state.json"


def test_read_launch_options_absent(cfg):
    assert steam.read_launch_options(cfg) is None


def test_write_and_read(cfg):
    steam.write_launch_options(cfg, steam.APPID, "LD_PRELOAD=/x.so %command%")
    assert steam.read_launch_options(cfg) == "LD_PRELOAD=/x.so %command%"
    text = cfg.read_text(encoding="utf-8")
    assert text.count('"LaunchOptions"') == 1
    assert '"881100"' in text and '"Playtime"\t\t"177"' in text  # чужой блок цел
    assert text.rstrip().endswith("}")  # структура не сломана


def test_write_replaces_existing(cfg):
    steam.write_launch_options(cfg, steam.APPID, "old %command%")
    steam.write_launch_options(cfg, steam.APPID, "new %command%")
    assert steam.read_launch_options(cfg) == "new %command%"
    assert cfg.read_text(encoding="utf-8").count('"LaunchOptions"') == 1


def test_write_none_removes(cfg):
    steam.write_launch_options(cfg, steam.APPID, "x %command%")
    steam.write_launch_options(cfg, steam.APPID, None)
    assert steam.read_launch_options(cfg) is None


def test_unknown_appid_raises(cfg):
    with pytest.raises(steam.SteamConfigError):
        steam.write_launch_options(cfg, "999999", "x")


def test_launch_line_shape(shim):
    line = steam.launch_line(shim, Path("/tmp/inbox"))
    assert line.startswith("POLYREAD_DUMP=/tmp/inbox LD_PRELOAD=")
    assert str(shim) in line
    assert line.endswith("%command%")


def test_enable_and_disable_roundtrip(cfg, shim):
    res = steam.enable(cfg, shim=shim)
    assert res["ok"] and res["previous"] is None
    assert steam.read_launch_options(cfg) == steam.launch_line(shim)
    assert Path(res["backup"]).is_file()

    res2 = steam.disable(cfg)
    assert res2["ok"] and res2["restored"] is None
    assert steam.read_launch_options(cfg) is None


def test_enable_keeps_previous_value(cfg, shim):
    steam.write_launch_options(cfg, steam.APPID, "gamemoderun %command%")
    steam.enable(cfg, shim=shim)
    assert "libpolyread_dump.so" in steam.read_launch_options(cfg)
    steam.disable(cfg)
    assert steam.read_launch_options(cfg) == "gamemoderun %command%"


def test_enable_refuses_without_shim(cfg, tmp_path):
    with pytest.raises(FileNotFoundError):
        steam.enable(cfg, shim=tmp_path / "nope.so")


def test_enable_refuses_when_steam_running(cfg, shim, monkeypatch):
    monkeypatch.setattr(steam, "is_steam_running", lambda: True)
    with pytest.raises(steam.SteamRunningError):
        steam.enable(cfg, shim=shim)
    assert steam.read_launch_options(cfg) is None  # ничего не записали


def test_enable_force_writes_even_if_running(cfg, shim, monkeypatch):
    monkeypatch.setattr(steam, "is_steam_running", lambda: True)
    res = steam.enable(cfg, shim=shim, force=True)
    assert res["steam_was_running"] and not res["steam_stopped"]
    assert steam.read_launch_options(cfg) == steam.launch_line(shim)


def test_disable_refuses_foreign_value(cfg, shim):
    steam.enable(cfg, shim=shim)
    steam.write_launch_options(cfg, steam.APPID, "чужое %command%")
    with pytest.raises(steam.SteamConfigError):
        steam.disable(cfg)
    assert steam.read_launch_options(cfg) == "чужое %command%"


def test_disable_force_replaces_foreign_value(cfg, shim):
    steam.enable(cfg, shim=shim)
    steam.write_launch_options(cfg, steam.APPID, "чужое %command%")
    steam.disable(cfg, force=True)
    assert steam.read_launch_options(cfg) is None


def test_status_reports_installed(cfg, shim):
    st = steam.status(cfg, shim=shim)
    assert not st.installed and st.shim_present
    steam.enable(cfg, shim=shim)
    st = steam.status(cfg, shim=shim)
    assert st.installed and st.current == st.expected
    assert st.to_dict()["config"] == str(cfg)


def test_state_file_written(cfg, shim, state_file):
    steam.enable(cfg, shim=shim)
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["appid"] == steam.APPID
    assert data["installed"] == steam.launch_line(shim)
    steam.disable(cfg)
    assert json.loads(state_file.read_text(encoding="utf-8")) == {}


def test_manual_instructions_contain_line(shim):
    text = steam.manual_instructions(shim)
    assert "Параметры запуска" in text
    assert "%command%" in text
    assert "Откат" in text


# ---------- предпроверка окружения (0.5.0) ----------

def _make_steam_tree(tmp_path: Path) -> Path:
    """Правдоподобное дерево Steam: корень, библиотека, конфиг профиля."""
    root = tmp_path / "steam"
    (root / "userdata" / "1" / "config").mkdir(parents=True)
    (root / "userdata" / "1" / "config" / "localconfig.vdf").write_text(VDF, encoding="utf-8")
    (root / "steamapps").mkdir(parents=True)
    return root


def test_steam_library_roots_parses_vdf(tmp_path, monkeypatch):
    root = _make_steam_tree(tmp_path)
    extra = tmp_path / "library2"
    extra.mkdir()
    (root / "steamapps" / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n'
        % str(root).replace("\\", "\\\\") +
        '\t"1"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n' % str(extra).replace("\\", "\\\\"),
        encoding="utf-8")
    monkeypatch.setattr(steam, "steam_roots", lambda: [root])
    roots = steam.steam_library_roots()
    assert root in roots and extra in roots


def test_game_dirs_env_first(tmp_path, monkeypatch):
    override = tmp_path / "custom-game"
    monkeypatch.setenv("POLYTOPIA_DIR", str(override))
    monkeypatch.setattr(steam, "steam_roots", lambda: [])
    dirs = steam.game_dirs()
    assert dirs[0] == override


def test_detect_reports_environment(tmp_path, monkeypatch, cfg, shim):
    root = _make_steam_tree(tmp_path)
    game = root / "steamapps" / "common" / steam.GAME_DIR_NAME
    game.mkdir(parents=True)
    (game / steam.NATIVE_MARKER).write_bytes(b"elf")
    monkeypatch.delenv("POLYTOPIA_DIR", raising=False)
    monkeypatch.setattr(steam, "steam_roots", lambda: [root])
    monkeypatch.setattr(steam, "config_paths",
                        lambda: [root / "userdata" / "1" / "config" / "localconfig.vdf"])
    monkeypatch.setattr(steam, "shim_install_path", lambda: shim)
    d = steam.detect()
    assert d["steam_found"] and d["config_found"]
    assert d["game_found"] and d["game_native"] and d["game_dir"] == str(game)
    assert d["shim_present"] and d["shim_path"] == str(shim)
    assert d["installed"] is False
    assert "%command%" in d["expected"] and "%command%" in d["manual"]


def test_detect_proton_build_not_native(tmp_path, monkeypatch, shim):
    root = _make_steam_tree(tmp_path)
    game = root / "steamapps" / "common" / steam.GAME_DIR_NAME
    game.mkdir(parents=True)
    (game / "Polytopia.exe").write_bytes(b"mz")  # Windows-сборка через Proton
    monkeypatch.delenv("POLYTOPIA_DIR", raising=False)
    monkeypatch.setattr(steam, "steam_roots", lambda: [root])
    monkeypatch.setattr(steam, "config_paths", lambda: [])
    monkeypatch.setattr(steam, "shim_install_path", lambda: shim)
    d = steam.detect()
    assert d["game_found"] is True and d["game_native"] is False
    assert d["config_found"] is False


def test_detect_no_steam_no_game(tmp_path, monkeypatch):
    monkeypatch.delenv("POLYTOPIA_DIR", raising=False)
    monkeypatch.setattr(steam, "steam_roots", lambda: [])
    monkeypatch.setattr(steam, "shim_install_path", lambda: tmp_path / "нет.so")
    d = steam.detect()
    assert d["steam_found"] is False and d["game_found"] is False
    assert d["shim_present"] is False and d["installed"] is False