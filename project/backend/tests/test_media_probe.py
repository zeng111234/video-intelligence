from __future__ import annotations

import struct
import subprocess
import shutil
from pathlib import Path

import pytest

from project.backend.app.core import media_probe
from project.backend.app.core.media_probe import probe_media_duration


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", len(payload) + 8, kind) + payload


def _minimal_mp4(*, duration_ticks: int = 3400, timescale: int = 1000) -> bytes:
    movie_header = (
        b"\x00\x00\x00\x00"
        + struct.pack(">II", 0, 0)
        + struct.pack(">II", timescale, duration_ticks)
        + b"\x00" * 24
    )
    return _box(b"ftyp", b"isom\x00\x00\x02\x00isom") + _box(
        b"moov", _box(b"mvhd", movie_header)
    )


def _track(handler: bytes) -> bytes:
    handler_payload = b"\x00\x00\x00\x00" + b"\x00" * 4 + handler + b"\x00" * 12
    return _box(b"trak", _box(b"mdia", _box(b"hdlr", handler_payload)))


def _playable_mp4(*, duration_ticks: int = 3400) -> bytes:
    minimal = _minimal_mp4(duration_ticks=duration_ticks)
    ftyp_size = struct.unpack(">I", minimal[:4])[0]
    movie_header = minimal[ftyp_size + 8 :]
    return (
        minimal[:ftyp_size]
        + _box(b"moov", movie_header + _track(b"vide") + _track(b"soun"))
        + _box(b"mdat", b"\x00\x01\x02\x03")
    )


def test_media_duration_falls_back_to_bounded_mp4_header_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "result.mp4"
    source.write_bytes(_minimal_mp4())
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError()),
    )

    assert probe_media_duration(source) == pytest.approx(3.4)


def _real_audio_video_mp4(tmp_path: Path) -> bytes:
    executable = shutil.which("ffmpeg")
    if executable is None:
        pytest.skip("ffmpeg is required for the real decodability fixture")
    output = tmp_path / "real-av.mp4"
    subprocess.run(
        [
            executable,
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=32x32:r=10:d=0.5",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=8000:duration=0.5",
            "-c:v",
            "mpeg4",
            "-c:a",
            "aac",
            "-shortest",
            "-movflags",
            "+faststart",
            "-y",
            str(output),
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )
    return output.read_bytes()


def _controlled_probe_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "runtime" / "data" / ".media-probe"
    root.parent.mkdir(parents=True, mode=0o700)
    root.parent.chmod(0o700)
    monkeypatch.setattr(media_probe, "MEDIA_PROBE_TEMP_ROOT", root)
    return root


def test_in_memory_acceptance_media_requires_real_ffprobe_and_short_decode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _real_audio_video_mp4(tmp_path)
    probe_root = _controlled_probe_root(tmp_path, monkeypatch)
    external_temp = tmp_path / "untrusted-tmp"
    external_temp.mkdir()
    for name in ("TMPDIR", "TMP", "TEMP"):
        monkeypatch.setenv(name, str(external_temp))
    real_run = subprocess.run
    calls: list[list[str]] = []

    def record_run(command, *args, **kwargs):
        calls.append([str(item) for item in command])
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(media_probe.subprocess, "run", record_run)

    assert probe_media_duration(payload) == pytest.approx(0.5, abs=0.1)
    assert len(calls) == 2
    assert all(
        command[command.index("-protocol_whitelist") + 1] == "file,crypto,data"
        for command in calls
    )
    assert probe_root.is_dir()
    assert list(probe_root.iterdir()) == []
    assert list(external_temp.iterdir()) == []


@pytest.mark.parametrize(
    "payload",
    [
        _minimal_mp4(),
        _playable_mp4()[: -len(_box(b"mdat", b"\x00\x01\x02\x03"))],
        _minimal_mp4() + _track(b"vide") + _box(b"mdat", b"data"),
    ],
)
def test_in_memory_acceptance_media_requires_audio_video_tracks_and_mdat(
    payload: bytes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _controlled_probe_root(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="无法读取素材时长"):
        probe_media_duration(payload)


def test_media_probe_capability_fails_closed_when_a_decoder_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        media_probe,
        "_trusted_media_tool",
        lambda name: "/trusted/ffprobe" if name == "ffprobe" else None,
    )
    monkeypatch.setattr(media_probe, "_media_probe_runtime_ready", lambda: True)

    assert media_probe.media_probe_capability() == {
        "status": "unavailable",
        "enabled": False,
        "missing_configuration": ["ffmpeg"],
    }


def test_media_duration_rejects_invalid_fallback_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "invalid.mp4"
    source.write_bytes(_minimal_mp4(timescale=0))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError()),
    )

    with pytest.raises(ValueError, match="无法读取素材时长"):
        probe_media_duration(source)
