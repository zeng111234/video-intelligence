"""Generate an original, speech-friendly BGM library without sampled material."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np


SAMPLE_RATE = 44_100
DURATION_SECONDS = 120.0
TARGET_RMS_DB = -18.0


@dataclass(frozen=True)
class TrackSpec:
    slug: str
    title: str
    category: str
    energy: str
    mood: str
    tags: tuple[str, ...]
    bpm: int
    root_midi: int
    mode: str
    timbre: str
    drum_level: float
    seed: int


TRACKS = (
    TrackSpec(
        "clear-insight",
        "清晰思路",
        "理性干货",
        "克制",
        "清晰·专业·轻节奏",
        ("知识", "教程", "讲解", "清晰", "专业", "轻节奏"),
        88,
        50,
        "major",
        "electric_piano",
        0.42,
        1101,
    ),
    TrackSpec(
        "steady-explainer",
        "稳稳讲明白",
        "理性干货",
        "平稳",
        "理性·现代·不抢人声",
        ("口播", "数据", "方法", "理性", "现代", "平稳"),
        94,
        48,
        "major",
        "muted_pluck",
        0.52,
        1102,
    ),
    TrackSpec(
        "shop-stroll",
        "逛店轻律动",
        "轻松日常",
        "平稳",
        "探店·轻快·松弛",
        ("探店", "日常", "轻快", "松弛", "美食", "生活"),
        104,
        55,
        "major",
        "muted_pluck",
        0.66,
        1201,
    ),
    TrackSpec(
        "business-motion",
        "商业向前",
        "商业表达",
        "有推动感",
        "商业·增长·有推动感",
        ("商业", "品牌", "增长", "案例", "成交", "推动"),
        100,
        45,
        "major",
        "electric_piano",
        0.70,
        1301,
    ),
    TrackSpec(
        "digital-breeze",
        "数字微风",
        "科技未来",
        "克制",
        "科技·智能·轻电子",
        ("科技", "AI", "智能", "数字化", "轻电子", "未来"),
        98,
        54,
        "minor",
        "soft_synth",
        0.48,
        1401,
    ),
    TrackSpec(
        "warm-narrative",
        "慢慢说故事",
        "故事叙事",
        "克制",
        "叙事·温暖·留白",
        ("故事", "经历", "回忆", "人物", "温暖", "留白"),
        76,
        50,
        "warm",
        "felt_piano",
        0.20,
        1501,
    ),
    TrackSpec(
        "soft-resonance",
        "轻柔共鸣",
        "情绪共鸣",
        "克制",
        "温柔·治愈·真诚",
        ("情感", "共鸣", "治愈", "真诚", "温柔", "陪伴"),
        72,
        57,
        "minor",
        "felt_piano",
        0.12,
        1601,
    ),
    TrackSpec(
        "small-steps-up",
        "一步一步向上",
        "励志成长",
        "有推动感",
        "成长·希望·积极",
        ("成长", "励志", "希望", "行动", "积极", "向上"),
        102,
        52,
        "major",
        "electric_piano",
        0.72,
        1701,
    ),
    TrackSpec(
        "hidden-clue",
        "线索浮现",
        "悬念揭秘",
        "平稳",
        "悬念·揭秘·克制紧张",
        ("悬念", "揭秘", "真相", "反转", "线索", "克制"),
        86,
        45,
        "suspense",
        "soft_synth",
        0.40,
        1801,
    ),
    TrackSpec(
        "clean-voice-bed",
        "百搭口播底色",
        "通用口播",
        "克制",
        "百搭·干净·人声优先",
        ("通用", "口播", "百搭", "干净", "轻量", "人声优先"),
        90,
        53,
        "major",
        "muted_pluck",
        0.34,
        1901,
    ),
)


PROGRESSIONS = {
    "major": ((0, 4, 7), (7, 11, 14), (9, 12, 16), (5, 9, 12)),
    "minor": ((0, 3, 7), (8, 12, 15), (3, 7, 10), (10, 14, 17)),
    "warm": ((0, 4, 7), (9, 12, 16), (5, 9, 12), (7, 11, 14)),
    "suspense": ((0, 3, 7), (1, 5, 8), (8, 12, 15), (7, 10, 14)),
}


def _frequency(midi_note: float) -> float:
    return 440.0 * (2.0 ** ((midi_note - 69.0) / 12.0))


def _pan_gains(pan: float) -> tuple[float, float]:
    angle = (min(max(pan, -1.0), 1.0) + 1.0) * math.pi / 4.0
    return math.cos(angle), math.sin(angle)


def _envelope(
    length: int,
    *,
    attack_seconds: float,
    release_seconds: float,
    decay: float = 0.0,
) -> np.ndarray:
    envelope = np.ones(length, dtype=np.float32)
    attack = min(length, max(1, int(attack_seconds * SAMPLE_RATE)))
    release = min(length, max(1, int(release_seconds * SAMPLE_RATE)))
    envelope[:attack] *= np.linspace(0.0, 1.0, attack, dtype=np.float32)
    envelope[-release:] *= np.linspace(1.0, 0.0, release, dtype=np.float32)
    if decay > 0:
        envelope *= np.exp(
            -decay * np.linspace(0.0, 1.0, length, dtype=np.float32)
        )
    return envelope


def _tone(
    frequency: float,
    duration: float,
    *,
    timbre: str,
    rng: np.random.Generator,
) -> np.ndarray:
    length = max(1, int(duration * SAMPLE_RATE))
    time = np.arange(length, dtype=np.float32) / SAMPLE_RATE
    phase = 2.0 * np.pi * frequency * time
    if timbre == "felt_piano":
        signal = (
            np.sin(phase)
            + 0.34 * np.sin(2.01 * phase + 0.2)
            + 0.12 * np.sin(3.0 * phase + 0.45)
        )
        signal *= _envelope(
            length, attack_seconds=0.015, release_seconds=0.4, decay=3.1
        )
        signal += rng.normal(0.0, 0.004, length) * np.exp(-11.0 * time)
    elif timbre == "electric_piano":
        signal = (
            np.sin(phase)
            + 0.22 * np.sin(2.0 * phase)
            + 0.08 * np.sin(4.02 * phase)
        )
        signal *= _envelope(
            length, attack_seconds=0.025, release_seconds=0.28, decay=1.8
        )
    elif timbre == "soft_synth":
        signal = (
            np.sin(phase)
            + 0.18 * np.sin(phase * 0.997)
            + 0.12 * np.sin(2.0 * phase)
        )
        signal *= _envelope(
            length, attack_seconds=0.08, release_seconds=0.32, decay=0.8
        )
    elif timbre == "pad":
        signal = (
            np.sin(phase * 0.997)
            + np.sin(phase * 1.003)
            + 0.22 * np.sin(2.0 * phase)
        ) / 2.2
        signal *= _envelope(
            length, attack_seconds=0.34, release_seconds=0.52, decay=0.08
        )
    elif timbre == "bass":
        signal = np.sin(phase) + 0.18 * np.sin(2.0 * phase)
        signal *= _envelope(
            length, attack_seconds=0.012, release_seconds=0.16, decay=1.6
        )
    else:
        signal = np.sin(phase) + 0.16 * np.sin(2.0 * phase)
        signal *= _envelope(
            length, attack_seconds=0.006, release_seconds=0.10, decay=4.2
        )
    return signal.astype(np.float32)


def _mix(
    stereo: np.ndarray,
    mono: np.ndarray,
    start_seconds: float,
    *,
    gain: float,
    pan: float,
) -> None:
    start = int(start_seconds * SAMPLE_RATE)
    if start >= stereo.shape[0]:
        return
    end = min(stereo.shape[0], start + mono.shape[0])
    left_gain, right_gain = _pan_gains(pan)
    stereo[start:end, 0] += mono[: end - start] * gain * left_gain
    stereo[start:end, 1] += mono[: end - start] * gain * right_gain


def _add_kick(stereo: np.ndarray, start: float, gain: float) -> None:
    length = int(0.34 * SAMPLE_RATE)
    time = np.arange(length, dtype=np.float32) / SAMPLE_RATE
    phase = 2.0 * np.pi * (86.0 * time - 36.0 * time * time)
    mono = np.sin(phase) * np.exp(-12.0 * time)
    _mix(stereo, mono, start, gain=gain, pan=0.0)


def _add_rim(
    stereo: np.ndarray,
    start: float,
    gain: float,
    rng: np.random.Generator,
) -> None:
    length = int(0.10 * SAMPLE_RATE)
    time = np.arange(length, dtype=np.float32) / SAMPLE_RATE
    noise = rng.normal(0.0, 1.0, length).astype(np.float32)
    noise[1:] -= 0.82 * noise[:-1]
    mono = (0.55 * noise + 0.45 * np.sin(2 * np.pi * 1650 * time))
    mono *= np.exp(-42.0 * time)
    _mix(stereo, mono, start, gain=gain, pan=0.12)


def _add_shaker(
    stereo: np.ndarray,
    start: float,
    gain: float,
    pan: float,
    rng: np.random.Generator,
) -> None:
    length = int(0.075 * SAMPLE_RATE)
    time = np.arange(length, dtype=np.float32) / SAMPLE_RATE
    noise = rng.normal(0.0, 1.0, length).astype(np.float32)
    noise[1:] -= 0.93 * noise[:-1]
    noise *= np.exp(-48.0 * time)
    _mix(stereo, noise, start, gain=gain, pan=pan)


def _render_track(spec: TrackSpec) -> np.ndarray:
    rng = np.random.default_rng(spec.seed)
    sample_count = int(DURATION_SECONDS * SAMPLE_RATE)
    stereo = np.zeros((sample_count, 2), dtype=np.float32)
    beat = 60.0 / spec.bpm
    bar = beat * 4.0
    progression = PROGRESSIONS[spec.mode]
    bar_count = math.ceil(DURATION_SECONDS / bar)
    melody_pattern = (0, 2, 1, 4, 2, 1, 0, 3)

    for bar_index in range(bar_count):
        bar_start = bar_index * bar
        chord = progression[bar_index % len(progression)]
        chord_notes = [spec.root_midi + offset for offset in chord]
        pad_gain = 0.018 if spec.mode != "suspense" else 0.026
        for note_index, note in enumerate(chord_notes):
            pad = _tone(
                _frequency(note),
                min(bar * 1.05, DURATION_SECONDS - bar_start),
                timbre="pad",
                rng=rng,
            )
            _mix(
                stereo,
                pad,
                bar_start,
                gain=pad_gain,
                pan=(-0.42 + note_index * 0.42),
            )

        for beat_index in (0, 2):
            start = bar_start + beat_index * beat
            bass = _tone(
                _frequency(chord_notes[0] - 12),
                beat * 0.82,
                timbre="bass",
                rng=rng,
            )
            _mix(stereo, bass, start, gain=0.055, pan=0.0)

        arp_order = (0, 1, 2, 1, 0, 1, 2, 1)
        for eighth, note_index in enumerate(arp_order):
            if spec.mode == "warm" and eighth % 2:
                continue
            start = bar_start + eighth * beat / 2.0
            note = chord_notes[note_index] + (12 if eighth in {3, 7} else 0)
            pluck = _tone(
                _frequency(note),
                beat * (0.60 if spec.timbre != "felt_piano" else 1.25),
                timbre=spec.timbre,
                rng=rng,
            )
            _mix(
                stereo,
                pluck,
                start,
                gain=0.040 if spec.energy == "有推动感" else 0.032,
                pan=-0.25 if eighth % 2 == 0 else 0.25,
            )

        if bar_index % 2 == 1:
            degree = melody_pattern[(bar_index // 2) % len(melody_pattern)]
            scale = (0, 2, 4, 7, 9) if spec.mode != "minor" else (0, 3, 5, 7, 10)
            melody_note = spec.root_midi + 12 + scale[degree % len(scale)]
            melody = _tone(
                _frequency(melody_note),
                beat * 1.35,
                timbre=spec.timbre,
                rng=rng,
            )
            _mix(
                stereo,
                melody,
                bar_start + beat * 2.5,
                gain=0.022,
                pan=0.30,
            )

        if spec.drum_level > 0:
            _add_kick(stereo, bar_start, 0.10 * spec.drum_level)
            if spec.energy == "有推动感":
                _add_kick(stereo, bar_start + beat * 2, 0.075 * spec.drum_level)
            for beat_index in (1, 3):
                _add_rim(
                    stereo,
                    bar_start + beat_index * beat,
                    0.035 * spec.drum_level,
                    rng,
                )
            for eighth in range(8):
                _add_shaker(
                    stereo,
                    bar_start + eighth * beat / 2,
                    0.015 * spec.drum_level * (1.15 if eighth % 2 else 0.8),
                    -0.35 if eighth % 2 else 0.35,
                    rng,
                )

    for delay_seconds, gain in ((0.19, 0.13), (0.31, 0.075)):
        delay = int(delay_seconds * SAMPLE_RATE)
        stereo[delay:, 0] += stereo[:-delay, 1] * gain
        stereo[delay:, 1] += stereo[:-delay, 0] * gain

    stereo -= np.mean(stereo, axis=0, keepdims=True)
    stereo = np.tanh(stereo * 1.45) / np.tanh(1.45)
    rms = float(np.sqrt(np.mean(np.square(stereo))) or 1.0)
    target_rms = 10.0 ** (TARGET_RMS_DB / 20.0)
    stereo *= target_rms / rms
    peak = float(np.max(np.abs(stereo)) or 1.0)
    if peak > 0.88:
        stereo *= 0.88 / peak
    fade = int(0.75 * SAMPLE_RATE)
    stereo[:fade] *= np.linspace(0.0, 1.0, fade, dtype=np.float32)[:, None]
    stereo[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)[:, None]
    return stereo


def _write_pcm16(path: Path, stereo: np.ndarray) -> None:
    pcm = np.clip(stereo * 32767.0, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm.tobytes())


def _asset_id(slug: str) -> str:
    digest = hashlib.sha1(f"voiceover-bgm-v1:{slug}".encode()).hexdigest()[:12]
    return f"bgm-{digest}"


def _encode_mp3(wav_path: Path, mp3_path: Path) -> None:
    result = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-v",
            "error",
            "-i",
            str(wav_path),
            "-af",
            "loudnorm=I=-18:TP=-1.5:LRA=7",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
            str(mp3_path),
        ],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0 or not mp3_path.is_file():
        raise RuntimeError((result.stderr or "FFmpeg MP3 encoding failed").strip())


def _retire_existing(directory: Path, generated_ids: set[str]) -> int:
    retired = 0
    for metadata_path in directory.glob("bgm-*.json"):
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("asset_id") in generated_ids or metadata.get("retired"):
            continue
        metadata["retired"] = True
        metadata["retired_reason"] = "已由原创口播音乐库替代，保留供历史任务复现。"
        metadata["retired_at"] = datetime.now().astimezone().isoformat()
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        retired += 1
    return retired


def generate_library(directory: Path, *, retire_existing: bool) -> dict[str, object]:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg is required to encode the generated BGM library.")
    directory.mkdir(parents=True, exist_ok=True)
    now = datetime.now().astimezone()
    generated: list[dict[str, object]] = []
    generated_ids = {_asset_id(spec.slug) for spec in TRACKS}
    with tempfile.TemporaryDirectory(prefix="voiceover-bgm-") as raw_temp:
        temp_dir = Path(raw_temp)
        for spec in TRACKS:
            asset_id = _asset_id(spec.slug)
            wav_path = temp_dir / f"{spec.slug}.wav"
            mp3_path = directory / f"{asset_id}.mp3"
            _write_pcm16(wav_path, _render_track(spec))
            _encode_mp3(wav_path, mp3_path)
            metadata = {
                "asset_id": asset_id,
                "title": spec.title,
                "original_name": f"{spec.slug}.mp3",
                "stored_name": mp3_path.name,
                "media_type": "audio/mpeg",
                "mood": spec.mood,
                "rights_holder": "本项目原创程序合成 · 无第三方采样",
                "rights_confirmed_at": now.isoformat(),
                "created_at": now.isoformat(),
                "duration_seconds": DURATION_SECONDS,
                "voiceover_category": spec.category,
                "energy": spec.energy,
                "tags": list(spec.tags),
                "source_provider": "manual",
                "source_url": "",
                "license_url": "",
                "content_id_risk": "none",
                "generated": True,
                "generation_version": "voiceover-bgm-v1",
                "bpm": spec.bpm,
                "retired": False,
            }
            (directory / f"{asset_id}.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            generated.append(
                {
                    "asset_id": asset_id,
                    "title": spec.title,
                    "category": spec.category,
                    "energy": spec.energy,
                    "bytes": mp3_path.stat().st_size,
                }
            )
    retired = _retire_existing(directory, generated_ids) if retire_existing else 0
    return {"generated": generated, "retired": retired}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--directory",
        type=Path,
        default=Path("data/bgm_library"),
    )
    parser.add_argument("--retire-existing", action="store_true")
    args = parser.parse_args()
    result = generate_library(
        args.directory.resolve(),
        retire_existing=args.retire_existing,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
