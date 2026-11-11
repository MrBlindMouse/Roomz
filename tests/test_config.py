"""Settings read from mpd.conf, with optional environment overrides."""

from pathlib import Path

from app.config import (
    fifo_path,
    library_base,
    mpd_config_problems,
    mpd_fields_from_conf,
    mpd_host,
    mpd_port,
)


def _write_conf(
    path: Path, *, music: str, fifo: str, port: int = 6600, bind: str = "127.0.0.1"
) -> None:
    path.write_text(
        f'music_directory "{music}"\n'
        f'bind_to_address "{bind}"\n'
        f'port "{port}"\n'
        "audio_output {\n"
        '    type "fifo"\n'
        f'    path "{fifo}"\n'
        "}\n",
        encoding="utf-8",
    )


def test_reads_shared_settings_from_conf_text() -> None:
    music, fifo, bind, port = mpd_fields_from_conf("""
        # music_directory "/ignored"
        music_directory "/data/music"
        bind_to_address "127.0.0.1"
        port "6006"
        audio_output {
            type "alsa"
            path "/dev/null"
        }
        audio_output {
            type "fifo"
            name "Roomz"
            path "/tmp/roomz-audio.fifo"
        }
        """)
    assert music == "/data/music"
    assert fifo == "/tmp/roomz-audio.fifo"
    assert bind == "127.0.0.1"
    assert port == 6006


def test_settings_come_from_the_config_file(monkeypatch, tmp_path: Path) -> None:
    library = tmp_path / "library"
    fifo = tmp_path / "audio.fifo"
    conf = tmp_path / "mpd.conf"
    _write_conf(conf, music=str(library), fifo=str(fifo), port=6006, bind="127.0.0.1")
    monkeypatch.setenv("ROOMZ_MPD_CONF", str(conf))
    for name in ("ROOMZ_LIBRARY_BASE", "ROOMZ_FIFO", "MPD_HOST", "MPD_PORT"):
        monkeypatch.delenv(name, raising=False)
    assert library_base() == library.resolve()
    assert fifo_path() == fifo.resolve()
    assert mpd_host() == "127.0.0.1"
    assert mpd_port() == 6006
    assert mpd_config_problems() == []


def test_environment_overrides_the_config_file(monkeypatch, tmp_path: Path) -> None:
    conf = tmp_path / "mpd.conf"
    _write_conf(conf, music="/conf/music", fifo="/tmp/conf.fifo", port=6600)
    library = tmp_path / "override"
    monkeypatch.setenv("ROOMZ_MPD_CONF", str(conf))
    monkeypatch.setenv("ROOMZ_LIBRARY_BASE", str(library))
    monkeypatch.setenv("MPD_PORT", "9")
    assert library_base() == library.resolve()
    assert mpd_port() == 9


def test_reports_a_missing_config(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ROOMZ_MPD_CONF", str(tmp_path / "missing.conf"))
    problems = mpd_config_problems()
    assert len(problems) == 1
    assert "not found" in problems[0]


def test_reports_an_incomplete_config(monkeypatch, tmp_path: Path) -> None:
    conf = tmp_path / "mpd.conf"
    conf.write_text('port "6600"\n', encoding="utf-8")
    monkeypatch.setenv("ROOMZ_MPD_CONF", str(conf))
    problems = mpd_config_problems()
    assert any("music_directory" in problem for problem in problems)
    assert any("fifo" in problem for problem in problems)
