"""MetaSound impact synthesis, authored headlessly (open item L15; the 2026-11-04 display pass, item 26).

Builds /Game/Audio/MS_ImpactSynth through the MetaSound Builder API (UMetaSoundBuilderSubsystem + the editor subsystem's
BuildToAsset), the same nodes a person would place in the graph editor:

    On Play --> AD Envelope (Audio) "skin"  : attack 1 ms, decay SkinDecay      --+
    Noise (white) --> Biquad Filter (low pass at Bright Hz) --> x skin envelope ---+--> + --> x Energy --> Out Mono
    On Play --> AD Envelope (Audio) "body"  : attack 1 ms, decay BodyDecay       --+
    Sine (Pitch Hz) ------------------------------------------> x body envelope --+
    body envelope On Done --> On Finished

Graph inputs (the HUD sets them per strike from the impulse delivered, SlapHUDFx.cpp OnStrike): Energy (0-2), Pitch (Hz,
the flesh-and-bone mode, lower behind a heavier arm), Bright (Hz, the slap's crack, higher for a faster palm), SkinDecay,
BodyDecay (s). A layer on top of the recorded takes, not a replacement: desktop only, spatialised like every one-shot.

Run (editor closed, PowerShell):
    UnrealEditor-Cmd.exe PoSlapUnreal.uproject -run=pythonscript -script="<abs>/Scripts/broadcast_metasound.py" -unattended
"""

import unreal

ASSET_DIR = "/Game/Audio"
ASSET_NAME = "MS_ImpactSynth"
OK = unreal.MetaSoundBuilderResult.SUCCEEDED


def check(result, what):
    if result != OK:
        raise RuntimeError(f"MetaSound builder: {what} failed ({result})")


def cls(namespace, name, variant=""):
    c = unreal.MetasoundFrontendClassName()
    c.set_editor_property("namespace", namespace)
    c.set_editor_property("name", name)
    c.set_editor_property("variant", variant)
    return c


def main():
    subsystem = unreal.get_engine_subsystem(unreal.MetaSoundBuilderSubsystem)
    editor = unreal.get_editor_subsystem(unreal.MetaSoundEditorSubsystem)
    if not subsystem or not editor:
        raise RuntimeError("the MetaSound builder / editor subsystems are not available (the Metasound plugin is off?)")

    for f in ("create_source_builder", "create_float_meta_sound_literal"):
        unreal.log(f"broadcast_metasound: {f}: {getattr(subsystem, f).__doc__.splitlines()[0]}")
    made = subsystem.create_source_builder("SlapImpactSynth", output_format=unreal.MetaSoundOutputAudioFormat.MONO, is_one_shot=True)
    unreal.log(f"broadcast_metasound: create_source_builder -> {made!r}")
    builder, on_play, on_finished, audio_out, result = made
    check(result, "create_source_builder")
    for f in ("add_graph_input_node", "add_node_by_class_name", "find_node_input_by_name", "connect_nodes", "set_node_input_default"):
        unreal.log(f"broadcast_metasound: {f}: {getattr(builder, f).__doc__.splitlines()[0]}")

    def flt(v):
        lit, _ = subsystem.create_float_meta_sound_literal(v)
        return lit

    def graph_input(name, default, data_type="Float"):
        out, r = builder.add_graph_input_node(name, data_type, flt(default))
        check(r, f"graph input {name}")
        return out

    def node(c, major=1):
        h, r = builder.add_node_by_class_name(c, major)
        check(r, f"node {c.get_editor_property('name')}")
        return h

    def pin_in(h, name):
        p, r = builder.find_node_input_by_name(h, name)
        check(r, f"input {name}")
        return p

    def pin_out(h, name):
        p, r = builder.find_node_output_by_name(h, name)
        check(r, f"output {name}")
        return p

    def connect(out_pin, in_pin, what):
        check(builder.connect_nodes(out_pin, in_pin), what)

    def default(h, name, v):
        check(builder.set_node_input_default(pin_in(h, name), flt(v)), f"default {name}")

    energy = graph_input("Energy", 1.0)
    pitch = graph_input("Pitch", 170.0)
    bright = graph_input("Bright", 4500.0)
    skin_decay = graph_input("SkinDecay", 0.07, "Time")   # the envelopes' Decay Time pins are Time, not Float
    body_decay = graph_input("BodyDecay", 0.22, "Time")

    skin_env = node(cls("AD Envelope", "AD Envelope", "Audio"))
    body_env = node(cls("AD Envelope", "AD Envelope", "Audio"))
    for env, decay in ((skin_env, skin_decay), (body_env, body_decay)):
        connect(on_play, pin_in(env, "Trigger"), "on play -> envelope")
        default(env, "Attack Time", 0.001)
        connect(decay, pin_in(env, "Decay Time"), "decay -> envelope")

    noise = node(cls("UE", "Noise", "Audio"))
    lowpass = node(cls("UE", "Biquad Filter", "Audio"), 2)   # the Biquad is at major version 2
    connect(pin_out(noise, "Audio"), pin_in(lowpass, "In"), "noise -> biquad")
    connect(bright, pin_in(lowpass, "Cutoff Frequency"), "bright -> cutoff")

    sine = node(cls("UE", "Sine", "Audio"))
    connect(pitch, pin_in(sine, "Frequency"), "pitch -> sine")

    skin = node(cls("UE", "Multiply", "Audio"))
    body = node(cls("UE", "Multiply", "Audio"))
    lp, r = builder.find_node_output_by_name(lowpass, "Low Pass Filter")   # v2 has one output per response
    if r != OK:
        lp = pin_out(lowpass, "Out")
    connect(lp, pin_in(skin, "PrimaryOperand"), "biquad -> skin x")
    connect(pin_out(skin_env, "Out Envelope"), pin_in(skin, "AdditionalOperands"), "skin env -> skin x")
    connect(pin_out(sine, "Audio"), pin_in(body, "PrimaryOperand"), "sine -> body x")
    connect(pin_out(body_env, "Out Envelope"), pin_in(body, "AdditionalOperands"), "body env -> body x")

    mix = node(cls("UE", "Add", "Audio"))
    connect(pin_out(skin, "Out"), pin_in(mix, "PrimaryOperand"), "skin -> +")
    connect(pin_out(body, "Out"), pin_in(mix, "AdditionalOperands"), "body -> +")

    gain = node(cls("UE", "Multiply", "Audio by Float"))
    connect(pin_out(mix, "Out"), pin_in(gain, "PrimaryOperand"), "+ -> gain")
    connect(energy, pin_in(gain, "AdditionalOperands"), "energy -> gain")
    connect(pin_out(gain, "Out"), audio_out[0], "gain -> out mono")
    connect(pin_out(body_env, "On Done"), on_finished, "body env done -> on finished")

    if unreal.EditorAssetLibrary.does_asset_exist(f"{ASSET_DIR}/{ASSET_NAME}"):
        unreal.EditorAssetLibrary.delete_asset(f"{ASSET_DIR}/{ASSET_NAME}")
    asset, result = editor.build_to_asset(builder, "PoSlap", ASSET_NAME, ASSET_DIR)
    check(result, "build_to_asset")
    unreal.EditorAssetLibrary.save_asset(f"{ASSET_DIR}/{ASSET_NAME}", only_if_is_dirty=False)
    unreal.log(f"broadcast_metasound: built and saved {ASSET_DIR}/{ASSET_NAME}")


main()
