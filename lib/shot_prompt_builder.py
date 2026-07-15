"""Shot prompt builder — converts structured shot language into provider-optimized prompts.

Uses a 5-layer framework based on professional cinematography prompting research:
  Layer 1: Camera (lens, depth of field)
  Layer 2: Movement (shot size, camera movement)
  Layer 3: Subject (description + texture keywords)
  Layer 4: Lighting (lighting key, color temperature)
  Layer 5: Style (adapted from playbook, not verbatim)

This replaces the old approach of prepending a fixed playbook image_prompt_prefix
to every scene description, which made all scenes look the same.
"""

from __future__ import annotations

from typing import Any


# Mapping from shot_language enums to natural language for prompting
_SHOT_SIZE_PHRASES = {
    "extreme_wide": "extreme wide shot showing vast environment",
    "wide": "wide shot capturing full scene",
    "medium_wide": "medium-wide shot framing subject with surroundings",
    "medium": "medium shot from waist up",
    "medium_close": "medium close-up from chest up",
    "close_up": "close-up focusing on face or detail",
    "extreme_close_up": "extreme close-up on fine detail",
    "over_shoulder": "over-the-shoulder perspective",
    "insert": "insert shot of specific detail",
    "establishing": "establishing shot setting the location",
}

_MOVEMENT_PHRASES = {
    "static": "locked-off static camera",
    "pan_left": "smooth pan to the left",
    "pan_right": "smooth pan to the right",
    "tilt_up": "gentle tilt upward",
    "tilt_down": "gentle tilt downward",
    "dolly_in": "slow dolly in toward subject",
    "dolly_out": "slow dolly out from subject",
    "tracking_left": "tracking shot moving left alongside subject",
    "tracking_right": "tracking shot moving right alongside subject",
    "crane_up": "crane shot rising upward",
    "crane_down": "crane shot descending",
    "handheld": "handheld camera with natural movement",
    "steadicam": "smooth steadicam following movement",
    "whip_pan": "fast whip pan",
    "orbital": "orbital camera circling subject",
    "zoom_in": "slow zoom in",
    "zoom_out": "slow zoom out",
    "rack_focus": "rack focus shift between foreground and background",
}

_LIGHTING_PHRASES = {
    "high_key": "bright high-key lighting, minimal shadows",
    "low_key": "dramatic low-key lighting with deep shadows",
    "natural": "natural ambient lighting",
    "golden_hour": "warm golden hour sunlight",
    "blue_hour": "cool blue hour twilight",
    "tungsten_warm": "warm tungsten interior lighting",
    "neon": "neon-lit with vibrant color spill",
    "silhouette": "backlit silhouette",
    "rim_lit": "rim lighting highlighting edges",
    "volumetric": "volumetric light with visible rays",
    "overcast_soft": "soft overcast diffused light",
}

_DOF_PHRASES = {
    "shallow": "shallow depth of field with bokeh",
    "medium": "medium depth of field",
    "deep": "deep focus with everything sharp",
}

_COLOR_TEMP_PHRASES = {
    "cool": "cool blue-toned color palette",
    "neutral": "neutral balanced colors",
    "warm": "warm amber-toned color palette",
    "mixed": "mixed color temperatures for contrast",
}


def build_shot_prompt(
    scene: dict[str, Any],
    style_context: dict[str, Any] | None = None,
) -> str:
    """Convert a scene with structured shot language into a generation prompt.

    Args:
        scene: Scene dict from scene_plan (with shot_language, description,
               texture_keywords, etc.)
        style_context: Optional playbook-derived style info with keys like
                       'generation_prefix', 'visual_language', 'mood'.

    Returns:
        A natural-language prompt optimized for image/video generation.
    """
    sl = scene.get("shot_language", {})
    layers: list[str] = []

    # Layer 1: Camera — lens and depth of field
    camera_parts = []
    if sl.get("lens_mm"):
        camera_parts.append(f"{sl['lens_mm']}mm lens")
    if sl.get("depth_of_field"):
        camera_parts.append(_DOF_PHRASES.get(sl["depth_of_field"], ""))
    if camera_parts:
        layers.append(", ".join(filter(None, camera_parts)))

    # Layer 2: Movement — shot size and camera movement
    movement_parts = []
    if sl.get("shot_size"):
        movement_parts.append(_SHOT_SIZE_PHRASES.get(sl["shot_size"], sl["shot_size"]))
    if sl.get("camera_movement") and sl["camera_movement"] != "static":
        movement_parts.append(_MOVEMENT_PHRASES.get(sl["camera_movement"], sl["camera_movement"]))
    if movement_parts:
        layers.append(", ".join(movement_parts))

    # Layer 3: Subject — the scene description + texture keywords
    description = scene.get("description", "")
    texture = scene.get("texture_keywords", [])
    subject_parts = [description]
    if texture:
        subject_parts.append(", ".join(texture))
    layers.append(". ".join(filter(None, subject_parts)))

    # Layer 4: Lighting — lighting key and color temperature
    lighting_parts = []
    if sl.get("lighting_key"):
        lighting_parts.append(_LIGHTING_PHRASES.get(sl["lighting_key"], sl["lighting_key"]))
    if sl.get("color_temperature"):
        lighting_parts.append(_COLOR_TEMP_PHRASES.get(sl["color_temperature"], ""))
    if lighting_parts:
        layers.append(", ".join(filter(None, lighting_parts)))

    # Layer 5: Style — adapted from playbook (NOT verbatim prefix)
    if style_context:
        mood = style_context.get("mood", "")
        visual_lang = style_context.get("visual_language", {})
        style_hint = visual_lang.get("aesthetic", "") or mood
        if style_hint:
            layers.append(f"Style: {style_hint}")

    return ". ".join(filter(None, layers))


def build_batch_prompts(
    scenes: list[dict[str, Any]],
    style_context: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Build prompts for all visual scenes in a scene plan.

    Returns list of {scene_id, prompt} dicts.
    """
    results = []
    for scene in scenes:
        # Skip non-visual scene types
        scene_type = scene.get("type", "")
        if scene_type in ("transition",):
            continue
        prompt = build_shot_prompt(scene, style_context)
        results.append({
            "scene_id": scene.get("id", "unknown"),
            "prompt": prompt,
            "hero_moment": scene.get("hero_moment", False),
        })
    return results


def _format_seconds(value: Any) -> str:
    number = float(value)
    return str(int(number)) if number.is_integer() else f"{number:g}"


def _continuity_descriptions(
    refs: list[str], continuity_bible: dict[str, Any]
) -> list[str]:
    """Resolve provider-neutral continuity IDs into concise prompt anchors."""

    indexed: dict[str, dict[str, Any]] = {}
    for collection in ("entities", "locations"):
        for item in continuity_bible.get(collection, []) or []:
            if isinstance(item, dict) and item.get("id"):
                indexed[str(item["id"])] = item
    period = continuity_bible.get("period")
    if isinstance(period, dict) and period.get("id"):
        indexed[str(period["id"])] = period

    descriptions: list[str] = []
    for ref in refs:
        item = indexed.get(str(ref))
        if not item:
            raise ValueError(f"unknown continuity reference: {ref}")
        name = item.get("canonical_name", item.get("label", ref))
        traits = [str(value) for value in item.get("immutable_traits", [])]
        descriptions.append(
            f"{name} ({', '.join(traits)})" if traits else str(name)
        )
    return descriptions


def _bounded_text(parts: list[str], limit: int) -> str:
    """Join complete prompt clauses without cutting a word in half."""

    output = ""
    for part in (value.strip() for value in parts if value and value.strip()):
        candidate = f"{output} {part}".strip()
        if len(candidate) <= limit:
            output = candidate
            continue
        if not output:
            return part[:limit].rsplit(" ", 1)[0].rstrip(" ,;:")
        break
    return output


def _validated_temporal_beats(
    scene: dict[str, Any], spec: dict[str, Any]
) -> list[dict[str, Any]]:
    beats = spec.get("temporal_beats", []) or []
    if not beats:
        raise ValueError("video_prompt_spec.temporal_beats must not be empty")

    clip_duration: float | None = None
    if "start_seconds" in scene and "end_seconds" in scene:
        clip_duration = float(scene["end_seconds"]) - float(scene["start_seconds"])
        if clip_duration <= 0:
            raise ValueError("scene end_seconds must be after start_seconds")

    previous_end = 0.0
    for index, beat in enumerate(beats):
        start = float(beat["start_seconds"])
        end = float(beat["end_seconds"])
        if index == 0 and start != 0:
            raise ValueError("scene-local temporal beats must begin at 0 seconds")
        if start < previous_end or end <= start:
            raise ValueError("scene-local temporal beats must be ordered and non-overlapping")
        if clip_duration is not None and end > clip_duration + 1e-6:
            raise ValueError(
                "scene-local temporal beat exceeds the generated clip duration"
            )
        previous_end = end
    return beats


def build_video_prompt(
    scene: dict[str, Any],
    continuity_bible: dict[str, Any] | None = None,
    *,
    provider: str = "generic",
) -> dict[str, str]:
    """Compile a provider-neutral motion plan into video prompt fields.

    Image-to-video already receives subject, scene, and style from its first
    frame.  This compiler therefore prioritizes temporal action and camera
    choreography, keeps exclusions in ``negative_prompt``, and resolves only
    the continuity anchors explicitly referenced by the scene.
    """

    if provider not in {"generic", "wan-i2v"}:
        raise ValueError(f"unsupported video prompt provider: {provider}")
    spec = scene.get("video_prompt_spec")
    if not isinstance(spec, dict):
        raise ValueError("scene.video_prompt_spec must be an object")

    parts: list[str] = []
    if spec.get("single_shot"):
        parts.append("Generate a single continuous shot.")

    continuity = _continuity_descriptions(
        [str(value) for value in spec.get("continuity_refs", [])],
        continuity_bible or {},
    )
    if continuity:
        parts.append(f"Preserve continuity: {'; '.join(continuity)}.")
    if spec.get("subject_motion"):
        parts.append(f"Subject motion: {spec['subject_motion']}")
    if spec.get("camera_motion"):
        parts.append(f"Camera motion: {spec['camera_motion']}")

    for beat in _validated_temporal_beats(scene, spec):
        start = _format_seconds(beat["start_seconds"])
        end = _format_seconds(beat["end_seconds"])
        parts.append(f"[{start}-{end}s] {str(beat['action']).strip()}")

    if spec.get("foreground_event"):
        parts.append(f"Foreground event: {spec['foreground_event']}")
    if spec.get("visual_payoff"):
        parts.append(f"Visual payoff: {spec['visual_payoff']}")

    if spec.get("caption_safe_area"):
        parts.append(str(spec["caption_safe_area"]).strip())

    prompt_limit = 1500 if provider == "wan-i2v" else 4000
    negative_limit = 500 if provider == "wan-i2v" else 2000
    negative = _bounded_text(
        [", ".join(str(value) for value in spec.get("negative_constraints", []))],
        negative_limit,
    )
    return {
        "prompt": _bounded_text(parts, prompt_limit),
        "negative_prompt": negative,
    }
