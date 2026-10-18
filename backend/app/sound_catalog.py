"""Curated common sounds mapped to Apple Sound Analysis classifications."""

from .models import SoundPreset, SoundTarget


# Apple exposes the authoritative list at runtime through
# SNClassifySoundRequest.knownClassifications. The iOS client must intersect
# these candidates with that list before presenting presets on a device.
SOUND_PRESETS = (
    SoundPreset(
        id="doorbell",
        title="Doorbell",
        description="A doorbell, chime, or entry buzzer.",
        apple_classifications=["doorbell", "door_bell", "door_chime", "buzzer"],
    ),
    SoundPreset(
        id="siren",
        title="Siren",
        description="An emergency vehicle or civil-defense siren.",
        apple_classifications=["siren", "emergency_siren", "police_siren"],
    ),
    SoundPreset(
        id="fire_alarm",
        title="Fire alarm",
        description="A building fire alarm.",
        apple_classifications=["fire_alarm", "alarm"],
    ),
    SoundPreset(
        id="smoke_alarm",
        title="Smoke alarm",
        description="A smoke detector or smoke alarm tone.",
        apple_classifications=["smoke_alarm", "smoke_detector_smoke_alarm"],
    ),
    SoundPreset(
        id="door_knock",
        title="Door knock",
        description="Knocking or tapping at a door.",
        apple_classifications=["knock", "door_knock", "knocking"],
    ),
    SoundPreset(
        id="dog_bark",
        title="Dog barking",
        description="A dog bark or repeated barking.",
        apple_classifications=["dog", "dog_bark", "bark", "bow_wow"],
    ),
    SoundPreset(
        id="baby_crying",
        title="Baby crying",
        description="A baby or infant crying.",
        apple_classifications=["baby_crying", "crying_sobbing", "infant_cry"],
    ),
    SoundPreset(
        id="car_horn",
        title="Car horn",
        description="A car or other vehicle horn.",
        apple_classifications=["car_horn", "vehicle_horn", "honking"],
    ),
)

_PRESETS_BY_ID = {str(preset.id): preset for preset in SOUND_PRESETS}


def get_sound_preset(preset_id: str) -> SoundPreset:
    return _PRESETS_BY_ID[preset_id]


def sound_target_for_preset(preset_id: str) -> SoundTarget:
    preset = get_sound_preset(preset_id)
    return SoundTarget(
        label=str(preset.id),
        aliases=preset.apple_classifications,
    )


def describe_sound_presets(preset_ids: list[str]) -> str:
    titles = [get_sound_preset(preset_id).title for preset_id in preset_ids]
    if len(titles) == 1:
        return titles[0]
    return ", ".join(titles[:-1]) + f" or {titles[-1]}"
