from src.services.style_presets import (
    PRESET_BRAND_EMPHASIS,
    PRESET_GRAMMAR_ONLY,
    PRESET_LOCAL_GRAMMAR_V2,
    PRESET_PURE_ADAPTIVE,
    get_style_preset,
)


def test_dynamic_preset_is_defensive_and_has_benchmark_controls():
    preset = get_style_preset(PRESET_BRAND_EMPHASIS)
    preset["caption"]["font_size"] = 1
    fresh = get_style_preset(PRESET_BRAND_EMPHASIS)

    assert fresh["caption"]["font_size"] == 54
    assert fresh["caption"]["max_lines"] == 1
    assert fresh["semantic_stickers"]["enabled"] is True
    assert fresh["sound_effects"]["enabled"] is True
    assert fresh["broll_policy"]["library_first"] is True
    assert fresh["top_brand_header"] is None
    assert fresh["big_emphasis_layer"] is None


def test_unknown_preset_falls_back_to_pure_adaptive():
    assert get_style_preset("not-a-real-preset")["preset_id"] == PRESET_PURE_ADAPTIVE


def test_local_grammar_v2_disables_external_visuals_and_bgm():
    preset = get_style_preset(PRESET_LOCAL_GRAMMAR_V2)
    assert preset["external_visuals"] == {
        "allow_broll_video": False,
        "allow_ai_video": False,
        "allow_generated_images": False,
        "allow_network_search": False,
        "allow_bgm": False,
    }
    assert preset["camera_grammar"]["allowed"]
    assert preset["semantic_symbols"]["max_per_minute"] == 6
    assert preset["rhythm"]["playback_rate"] == 1.08


def test_grammar_only_polish_uses_light_caption_keyline_and_local_sfx_profiles():
    preset = get_style_preset(PRESET_GRAMMAR_ONLY)
    assert 2 <= preset["caption"]["outline_width"] <= 3
    assert 0.8 <= preset["caption"]["shadow"] <= 1.5
    assert "number_badge" not in preset["semantic_symbols"]["allowed"]
    assert set(preset["sound_effects"]["profiles"]) == {
        "pop_soft", "tick_soft", "whoosh_soft", "impact_soft", "success_ping", "warning_tick",
    }
