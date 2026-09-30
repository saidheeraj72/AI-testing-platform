import pytest

from app.browser.elements import ResolutionError, resolve
from app.schemas.action import ActionError
from app.schemas.observation import Element


def el(ref, role="button", name="Edit", context=("list",), frame="main"):
    return Element(ref=ref, role=role, name=name, context=list(context), frame=frame)


def test_same_ref_same_identity():
    target = el("e1")
    assert resolve(target, [target], [el("e1")]).how == "same"


def test_same_node_renamed_in_place():
    target = el("e1", name="Save")
    r = resolve(target, [target], [el("e1", name="Saving…")])
    assert (r.ref, r.how) == ("e1", "renamed")


def test_reused_node_in_other_row_is_not_trusted():
    # Unkeyed list: node e1 now shows a different row. Must find the original row's button.
    target = el("e1", context=["list", 'listitem "Alpha"'])
    fresh = [el("e1", context=["list", 'listitem "Gamma"']), el("e9", context=["list", 'listitem "Alpha"'])]
    r = resolve(target, [target], fresh)
    assert (r.ref, r.how) == ("e9", "by_identity")


def test_rerendered_node_found_by_identity():
    target = el("e1", name="Save", context=["main"])
    r = resolve(target, [target], [el("e7", name="Save", context=["main"])])
    assert (r.ref, r.how) == ("e7", "by_identity")


def test_same_size_group_resolves_by_position():
    source = [el("e1"), el("e2"), el("e3")]
    fresh = [el("e11"), el("e12"), el("e13")]
    r = resolve(source[1], source, fresh)
    assert (r.ref, r.how) == ("e12", "by_position")


def test_changed_group_size_is_ambiguous():
    source = [el("e1"), el("e2")]
    fresh = [el("e11"), el("e12"), el("e13")]
    with pytest.raises(ResolutionError) as info:
        resolve(source[0], source, fresh)
    assert info.value.code == ActionError.AMBIGUOUS_ELEMENT


def test_unique_name_survives_context_change():
    target = el("e1", name="Submit order", context=["main"])
    r = resolve(target, [target], [el("e5", name="Submit order", context=["dialog"])])
    assert (r.ref, r.how) == ("e5", "by_name")


def test_gone():
    target = el("e1", name="Save")
    with pytest.raises(ResolutionError) as info:
        resolve(target, [target], [el("e2", name="Cancel")])
    assert info.value.code == ActionError.ELEMENT_NOT_FOUND


def test_frame_must_match():
    target = el("e1", name="Pay", context=[], frame="iframe-1")
    with pytest.raises(ResolutionError):
        resolve(target, [target], [el("e2", name="Pay", context=[], frame="main")])
