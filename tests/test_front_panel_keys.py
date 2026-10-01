from dataclasses import FrozenInstanceError

import pytest

from sds200.commands import HOLD_KEY_CODES, PressKey
from sds200.front_panel_keys import FRONT_PANEL_KEYS, FrontPanelKey, front_panel_inventory


def test_complete_reference_inventory_preserves_order_and_unique_codes():
    expected = tuple("MFL1234567890.E><^VQYABCZTR")
    assert len(expected) == 27
    assert tuple(key.code.value for key in FRONT_PANEL_KEYS) == expected
    assert tuple(key.value for key in FrontPanelKey) == expected
    assert len(set(key.code for key in FRONT_PANEL_KEYS)) == 27
    for key in FRONT_PANEL_KEYS:
        assert key.label and key.context_note
        assert all(32 <= ord(character) < 127 for character in key.label + key.context_note)


@pytest.mark.parametrize("model", [None, "", "SDS200", "SDS150", "SDS100E", "BCD436HP"])
def test_models_without_explicit_reference_columns_never_inherit_support(model):
    inventory = front_panel_inventory(model)
    assert len(inventory) == 27
    assert all(entry.reference_status == "model_not_listed" for entry in inventory)
    assert all(entry.available is False and entry.unavailable_reason for entry in inventory)


@pytest.mark.parametrize("model", ["SDS100", " sds100 ", "BCD536HP", "bcd536hp"])
def test_reference_membership_never_enables_general_key_dispatch(model):
    inventory = front_panel_inventory(model)
    assert len(inventory) == 27
    assert all(entry.available is False for entry in inventory)
    assert any(entry.reference_status == "listed" for entry in inventory)
    assert all(entry.unavailable_reason for entry in inventory)


def test_sds100_absent_keys_and_backlight_alias_do_not_change_other_models():
    sds100 = {entry.definition.code: entry for entry in front_panel_inventory("SDS100")}
    assert sds100[FrontPanelKey.VOLUME_PUSH].label == "Backlight"
    absent = {key for key, entry in sds100.items() if entry.reference_status == "absent_for_model"}
    assert absent == {FrontPanelKey.SQUELCH_PUSH, FrontPanelKey.SERVICE_TYPE}
    desktop = {entry.definition.code: entry for entry in front_panel_inventory("BCD536HP")}
    assert all(entry.reference_status == "listed" for entry in desktop.values())
    assert desktop[FrontPanelKey.VOLUME_PUSH].label == "Volume-knob push"
    assert len(front_panel_inventory()) == len(FRONT_PANEL_KEYS) == 27


@pytest.mark.parametrize("model", [None, "SDS100", "BCD536HP", "SDS200"])
def test_soft_keys_do_not_assume_scanning_context_or_hold_target(model):
    inventory = {entry.definition.code: entry for entry in front_panel_inventory(model)}
    codes = (FrontPanelKey.SOFT_1, FrontPanelKey.SOFT_2, FrontPanelKey.SOFT_3)
    for number, code in enumerate(codes, start=1):
        assert inventory[code].label == f"Soft key {number}"
        assert "current scanner soft-key label" in inventory[code].definition.context_note


def test_inventory_is_immutable_and_not_a_wire_command():
    inventory = front_panel_inventory("SDS200")
    entry = inventory[0]
    with pytest.raises(FrozenInstanceError):
        entry.label = "Enabled"
    with pytest.raises((FrozenInstanceError, TypeError)):
        entry.available = True
    with pytest.raises(FrozenInstanceError):
        entry.definition.code = FrontPanelKey.ENTER_YES
    assert not hasattr(entry, "wire") and not hasattr(entry.definition, "wire")
    assert front_panel_inventory("SDS200") == inventory


@pytest.mark.parametrize("model", [True, 1, b"SDS100", [], {}])
def test_invalid_model_types_are_rejected_without_reflecting_inputs(model):
    with pytest.raises(TypeError, match="^Scanner model must be text or unavailable[.]$"):
        front_panel_inventory(model)


@pytest.mark.parametrize(
    "model",
    ["SDS100\x00", "SDS100\r\nMENU", "<script>private</script>", "ſDS100", "SDS100" + " " * 100],
)
def test_unknown_model_text_is_never_reflected_or_interpreted(model):
    inventory = front_panel_inventory(model)
    assert all(entry.reference_status == "model_not_listed" for entry in inventory)
    assert model not in repr(inventory)


@pytest.mark.parametrize(
    "definition", FRONT_PANEL_KEYS, ids=lambda definition: definition.code.name
)
def test_new_inventory_does_not_widen_existing_hold_command_allowlist(definition):
    assert HOLD_KEY_CODES == ("F", "A", "B", "C")
    code = definition.code.value
    if code in HOLD_KEY_CODES:
        assert PressKey(code).wire == f"KEY,{code},P"
    else:
        with pytest.raises(ValueError, match="Hold-related key code"):
            PressKey(code)
