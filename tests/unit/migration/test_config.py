"""Config sanity tests (aisw-0a5 P1.4)."""

from __future__ import annotations

from ai_steward_wiki.migration.config import (
    CATEGORY_MAP,
    DROP_DIRS,
    PRIORITY_MAP,
    USER_MAPPINGS,
    find_project_mapping,
    find_user_mapping,
)


def test_all_user_mappings_unique_telegram_ids() -> None:
    ids = [m.telegram_id for m in USER_MAPPINGS]
    assert len(ids) == len(set(ids)), f"duplicate telegram_id in mappings: {ids}"


def test_exactly_one_admin() -> None:
    admins = [m for m in USER_MAPPINGS if m.role == "admin"]
    assert len(admins) == 1
    assert admins[0].telegram_id == 763463467


def test_all_template_ids_in_supported_set() -> None:
    allowed = {"medical", "budget", "investment", "career", "_default"}
    for m in USER_MAPPINGS:
        for p in m.projects:
            assert (
                p.template_id in allowed
            ), f"unknown template_id {p.template_id!r} for {m.display_name}/{p.source_project}"


def test_drop_dirs_disjoint_from_project_sources() -> None:
    project_names = {
        p.source_project for m in USER_MAPPINGS for p in m.projects if p.source_project is not None
    }
    overlap = DROP_DIRS & project_names
    assert not overlap, f"DROP_DIRS overlaps with active project sources: {overlap}"


def test_lookup_helpers() -> None:
    gena = find_user_mapping(763463467)
    assert gena is not None
    assert gena.display_name == "Геннадий"

    assert find_user_mapping(999_999_999) is None

    health = find_project_mapping(763463467, "Health")
    assert health is not None
    assert health.target_wiki == "Medical"
    assert health.template_id == "medical"

    root_planner = find_project_mapping(763463467, None)
    assert root_planner is not None
    assert root_planner.target_wiki == "Default"


def test_category_and_priority_maps_cover_legacy_values() -> None:
    # All old category values that appeared in prescan (medication, event,
    # task, reminder) plus dead ones (block, todo) must map.
    for k in ("medication", "event", "task", "reminder", "block", "todo"):
        assert k in CATEGORY_MAP

    for k in ("low", "none", "medium", "high"):
        assert k in PRIORITY_MAP
