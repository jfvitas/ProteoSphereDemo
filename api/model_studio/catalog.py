from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from api.model_studio.capabilities import (
    RELEASE_REVIEW_LANES,
    build_action_contract,
    build_capability_registry,
    build_field_help_registry,
    build_lab_catalog,
    build_stepper_definition,
    build_ui_option_registry,
)
from api.model_studio.capabilities import (
    build_design_catalog as build_capability_catalog,
)
from api.model_studio.contracts import (
    ModelStudioPipelineSpec,
    compile_execution_graph,
    pipeline_spec_from_dict,
    validate_pipeline_spec,
)

_DEFAULT_SPEC_RESOURCE = (
    Path(__file__).parent / "resources" / "default_pipeline_spec.json"
)

STUDIO_SCHEMA_VERSION = "model-studio:v2-study-builder"


def build_design_catalog(*, include_lab: bool = False) -> dict[str, Any]:
    catalog = build_capability_catalog(include_lab=include_lab)
    catalog["capability_registry"] = build_capability_registry()
    catalog["ui_option_registry"] = build_ui_option_registry()
    catalog["field_help_registry"] = build_field_help_registry()
    catalog["stepper_definition"] = build_stepper_definition()
    catalog["action_contract"] = build_action_contract()
    catalog["reviewer_lanes"] = list(RELEASE_REVIEW_LANES)
    catalog["catalog_mode"] = "lab" if include_lab else "release"
    return catalog


def build_release_catalog() -> dict[str, Any]:
    return build_design_catalog(include_lab=False)


def default_pipeline_spec() -> ModelStudioPipelineSpec:
    """Return the canonical default :class:`ModelStudioPipelineSpec`.

    The shape lives in ``resources/default_pipeline_spec.json`` (a
    package data file). Keeping it as JSON instead of a 140-line Python
    literal lets non-engineers tune the demo without touching code, and
    lets diffs of the default move through review like a data change.
    """
    raw = json.loads(_DEFAULT_SPEC_RESOURCE.read_text(encoding="utf-8"))
    spec = pipeline_spec_from_dict(raw)
    if spec.schema_version != STUDIO_SCHEMA_VERSION:
        # If the schema version constant moves, the resource file must
        # follow it. Surface the drift loudly rather than ship a spec
        # that the validator will reject downstream.
        raise RuntimeError(
            "Default pipeline spec resource is out of date: schema "
            f"version {spec.schema_version!r} != expected "
            f"{STUDIO_SCHEMA_VERSION!r}. Regenerate "
            f"{_DEFAULT_SPEC_RESOURCE} after a schema bump."
        )
    return spec


def build_workspace_preview() -> dict[str, Any]:
    spec = default_pipeline_spec()
    report = validate_pipeline_spec(spec)
    graph = compile_execution_graph(spec)
    return {
        "pipeline_spec": spec.to_dict(),
        "recommendation_report": report.to_dict(),
        "execution_graph": graph.to_dict(),
        "catalog": build_release_catalog(),
        "lab_catalog": build_lab_catalog(),
        "workspace_sections": [
            "Project Home",
            "Data Strategy Designer",
            "Representation Designer",
            "Pipeline Composer",
            "Execution Console",
            "Analysis and Review",
        ],
    }
