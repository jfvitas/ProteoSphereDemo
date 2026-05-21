from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .model import DatasetManifest, DatasetRecord
from .evaluator import evaluate_dataset
from .warehouse import Warehouse


def _paper_to_manifest(paper: dict[str, Any]) -> DatasetManifest:
    member_type = str(paper.get("member_type") or "identifier").strip()
    train_members = [str(item).strip() for item in paper.get("train_members") or [] if str(item).strip()]
    val_members = [str(item).strip() for item in paper.get("val_members") or [] if str(item).strip()]
    test_members = [str(item).strip() for item in paper.get("test_members") or [] if str(item).strip()]
    entity_kind = "protein_pair" if member_type in {"accession", "protein", "protein_pair"} else "structure_pair"
    records: list[DatasetRecord] = []
    for split, members in (("train", train_members), ("val", val_members), ("test", test_members)):
        for index, member in enumerate(members, start=1):
            if entity_kind == "structure_pair":
                records.append(
                    DatasetRecord(
                        record_id=f"{split}-{index}-{member}",
                        split=split,
                        pdb_id=member.upper(),
                        protein_a="A",
                        protein_b="B",
                        source_family=";".join(paper.get("source_families") or []),
                        provenance_note=paper.get("claimed_split_description") or "",
                    )
                )
            else:
                records.append(
                    DatasetRecord(
                        record_id=f"{split}-{index}-{member}",
                        split=split,
                        protein_a=member,
                        protein_b=f"paper_member:{member}",
                        protein_accessions=(member,),
                        source_family=";".join(paper.get("source_families") or []),
                        provenance_note=paper.get("claimed_split_description") or "",
                    )
                )
    return DatasetManifest(
        manifest_id=str(paper.get("paper_id") or "paper"),
        title=str(paper.get("title") or paper.get("paper_id") or "paper"),
        task_type=str(paper.get("task_group") or ""),
        label_type="paper_claim",
        entity_kind=entity_kind,
        split_membership_mode=str(paper.get("split_style") or "paper_claim"),
        records=tuple(records),
    )


def evaluate_paper_corpus(payload: dict[str, Any], warehouse: Warehouse) -> dict[str, Any]:
    papers: list[dict[str, Any]] = []
    for paper in payload.get("papers") or []:
        row = {
            "paper_id": paper.get("paper_id"),
            "title": paper.get("title"),
            "doi": paper.get("doi"),
            "claimed_split_description": paper.get("claimed_split_description"),
            "split_style": paper.get("split_style"),
            "claimed_dataset": paper.get("claimed_dataset"),
        }
        has_roster = bool(paper.get("train_members") or paper.get("test_members") or paper.get("val_members"))
        if not has_roster:
            row.update(
                {
                    "verdict": "blocked_pending_mapping",
                    "reason_codes": ["UNRESOLVED_SPLIT_MEMBERSHIP"],
                    "overlap_metrics": {},
                    "blockers": ["No explicit train/test roster was supplied in the paper corpus entry."],
                    "warnings": [],
                    "recommended_action": "Recover official split membership before making a deterministic ProteoSphere call.",
                }
            )
        elif str(paper.get("split_style") or "").strip() == "cross_validation":
            row.update(
                {
                    "verdict": "audit_only_noncanonical",
                    "reason_codes": ["POLICY_MISMATCH"],
                    "overlap_metrics": {},
                    "blockers": [],
                    "warnings": ["Cross-validation claims require fold-level membership before canonical split acceptance."],
                    "recommended_action": "Treat as paper-faithful audit evidence, not as a canonical external holdout.",
                }
            )
        else:
            assessment = evaluate_dataset(_paper_to_manifest(paper), warehouse)
            row.update(
                {
                    "verdict": assessment.get("verdict"),
                    "reason_codes": assessment.get("reason_codes"),
                    "overlap_metrics": assessment.get("overlap_metrics"),
                    "overlap_findings": assessment.get("overlap_findings"),
                    "blockers": assessment.get("blockers"),
                    "warnings": assessment.get("warnings"),
                    "recommended_action": assessment.get("recommended_action"),
                }
            )
        papers.append(row)
    return {
        "artifact_id": "proteosphere_paper_review",
        "generated_at": datetime.now(UTC).isoformat(),
        "schema_id": payload.get("schema_id") or "proteosphere-paper-dataset-corpus-v1",
        "paper_count": len(papers),
        "papers": papers,
    }
