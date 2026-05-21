from __future__ import annotations

from pathlib import Path

from proteosphere.evaluator import evaluate_dataset
from proteosphere.model import DatasetManifest
from proteosphere.splitter import split_dataset
from proteosphere.warehouse import Warehouse


def _warehouse(tmp_path: Path) -> Warehouse:
    return Warehouse.open(tmp_path / "reference_library")


def test_protein_pair_overlap_detects_direct_and_accession_root(tmp_path: Path) -> None:
    manifest = DatasetManifest.from_dict(
        {
            "manifest_id": "pair-test",
            "entity_kind": "protein_pair",
            "records": [
                {"record_id": "tr", "split": "train", "protein_a": "P00698", "protein_b": "P00766"},
                {"record_id": "te", "split": "test", "protein_a": "P00698", "protein_b": "Q9NZD4"},
            ],
        }
    )

    report = evaluate_dataset(manifest, _warehouse(tmp_path))

    assert report["verdict"] == "unsafe_for_training"
    assert "DIRECT_OVERLAP" in report["reason_codes"]
    assert "ACCESSION_ROOT_OVERLAP" in report["reason_codes"]


def test_structure_pair_overlap_is_chain_order_invariant(tmp_path: Path) -> None:
    manifest = DatasetManifest.from_dict(
        {
            "manifest_id": "structure-test",
            "entity_kind": "structure_pair",
            "records": [
                {
                    "record_id": "tr",
                    "split": "train",
                    "pdb_id": "4EQ6",
                    "protein_a": "A",
                    "protein_b": "B",
                },
                {
                    "record_id": "te",
                    "split": "test",
                    "pdb_id": "4EQ6",
                    "protein_a": "B",
                    "protein_b": "A",
                },
            ],
        }
    )

    report = evaluate_dataset(manifest, _warehouse(tmp_path))

    assert report["overlap_metrics"]["structure_pair_overlap_count"] == 1
    assert "DIRECT_OVERLAP" in report["reason_codes"]


def test_splitter_keeps_shared_accession_component_together(tmp_path: Path) -> None:
    manifest = DatasetManifest.from_dict(
        {
            "manifest_id": "split-test",
            "entity_kind": "protein_pair",
            "records": [
                {"record_id": "ab", "protein_a": "P00698", "protein_b": "P00766"},
                {"record_id": "ac", "protein_a": "P00698", "protein_b": "Q9NZD4"},
                {"record_id": "de", "protein_a": "P01112", "protein_b": "P01051"},
                {"record_id": "fg", "protein_a": "P69905", "protein_b": "P68871"},
            ],
        }
    )

    result = split_dataset(
        manifest,
        _warehouse(tmp_path),
        policy="accession_grouped",
        fractions=(0.5, 0.25, 0.25),
        seed=1337,
        resplit=False,
    )

    assert result["status"] == "ready"
    records = result["manifest"]["records"]
    split_by_id = {record["record_id"]: record["split"] for record in records}
    assert split_by_id["ab"] == split_by_id["ac"]
    assert result["diagnostics"]["group_crossing_count"] == 0


def test_splitter_blocks_existing_splits_without_resplit(tmp_path: Path) -> None:
    manifest = DatasetManifest.from_dict(
        {
            "manifest_id": "already-split",
            "entity_kind": "protein_pair",
            "records": [
                {"record_id": "tr", "split": "train", "protein_a": "P00698", "protein_b": "P00766"},
                {"record_id": "te", "split": "test", "protein_a": "P01112", "protein_b": "P01051"},
            ],
        }
    )

    result = split_dataset(
        manifest,
        _warehouse(tmp_path),
        policy="accession_grouped",
        fractions=(0.8, 0.1, 0.1),
        seed=1337,
        resplit=False,
    )

    assert result["status"] == "blocked"
    assert "pass --resplit" in result["blockers"][0]
