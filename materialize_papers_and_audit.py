"""Resumable papers_rows + pinder_plinder_audit materializer.

The first pass (materialize_full_demo_warehouse.py) handles motif,
scaffold, pdb_uniprot, and globin family. This script handles the two
artifact-heavy axes that are slow because of CSV parsing variance and
many small JSON files:

  * papers_rows / papers_metadata — uses DuckDB's native ``read_csv``
    with auto-detection per file, falls back to Python csv.DictReader
    for files that can't be auto-parsed. Much faster than executemany.
  * pinder_plinder_audit / pinder_plinder_axis_overlap — loaded from
    ``artifacts/status/pinder_plinder_cross_audit_expanded_summary.json``.

Re-runnable: each table is DROP'd and recreated.
"""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path

import duckdb


HERE = Path(__file__).resolve().parent
WAREHOUSE = HERE / "demo_warehouse" / "catalog" / "v2.duckdb"
PROTEOSPHERE_ROOT = Path("D:/documents/ProteoSphereV2")
PAPERS_ROOT = PROTEOSPHERE_ROOT / "docs" / "manuscripts" / "proteosphere_paper" / "datasets"
AUDIT_ROOT = PROTEOSPHERE_ROOT / "artifacts" / "status"

SNAPSHOT_ID = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _drop(con, name):
    # Try table first, then view. DuckDB raises if the object exists
    # under the other kind.
    con.execute(f"DROP TABLE IF EXISTS {name}")
    try:
        con.execute(f"DROP VIEW IF EXISTS {name}")
    except Exception:
        pass


def _scan_header(path: Path) -> tuple[str, str, str, list[str], int]:
    """Return (paper_id, family, generated_at, comment_lines, data_offset)."""
    paper_id = path.stem.replace("_rows", "")
    family = ""
    generated_at = ""
    comments: list[str] = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        while True:
            pos = f.tell()
            ln = f.readline()
            if not ln:
                break
            if ln.startswith("#"):
                s = ln.rstrip("\n")
                comments.append(s)
                if s.startswith("# paper_id:"):
                    paper_id = s.split(":", 1)[1].strip()
                elif s.startswith("# family:"):
                    family = s.split(":", 1)[1].strip()
                elif s.startswith("# generated_at:"):
                    generated_at = s.split(":", 1)[1].strip()
            else:
                return paper_id, family, generated_at, comments, pos
    return paper_id, family, generated_at, comments, -1


# Bound the warehouse size: any single paper roster larger than this
# cap is sampled — first ``ROW_CAP_PER_PAPER`` rows kept, the rest
# summarised in papers_metadata.n_rows_total (so reviewers can still
# see the true count). 25K rows per paper × 58 papers caps the table
# at ~1.45M rows worst case, comfortably under the 250 MB budget.
ROW_CAP_PER_PAPER = 25_000

# Pre-read sanity cap. Any CSV bigger than this is read with a hard
# byte limit (file truncated at this many bytes, then CSV-parsed).
# 25K capped rows from the start of a CSV ≈ 8-12 MB of text for the
# DTI families. 30 MB byte cap is generous and avoids the 242 MB
# tefdta2024_rows.csv pathological case.
BYTES_CAP_PER_CSV = 30 * 1024 * 1024


def materialize_papers(con) -> dict:
    csv_files = sorted(PAPERS_ROOT.glob("*_rows.csv"))
    print(f"[papers] {len(csv_files)} CSVs found (row cap={ROW_CAP_PER_PAPER:,}/paper)",
          flush=True)

    _drop(con, "papers_rows")
    _drop(con, "papers_metadata")
    con.execute(
        """
        CREATE TABLE papers_rows (
            paper_id      VARCHAR,
            family        VARCHAR,
            row_index     BIGINT,
            dataset       VARCHAR,
            split         VARCHAR,
            drug_id       VARCHAR,
            drug_smiles   VARCHAR,
            target_id     VARCHAR,
            target_seq_sha8 VARCHAR,
            affinity_value DOUBLE,
            pdb_id        VARCHAR,
            protein_id    VARCHAR,
            pair_partner_id VARCHAR,
            payload_json  VARCHAR,
            snapshot_id   VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE TABLE papers_metadata (
            paper_id      VARCHAR,
            family        VARCHAR,
            n_rows        BIGINT,
            n_rows_total  BIGINT,  -- true row count in the CSV, even if capped
            csv_path      VARCHAR,
            generated_at  VARCHAR,
            source_lines  VARCHAR,
            row_cap       BIGINT,
            snapshot_id   VARCHAR
        )
        """
    )

    total = 0
    for csv_path in csv_files:
        paper_id, family, generated_at, comments, data_offset = _scan_header(csv_path)
        if data_offset < 0:
            con.execute(
                "INSERT INTO papers_metadata VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (paper_id, family, 0, 0,
                 str(csv_path.relative_to(PROTEOSPHERE_ROOT)).replace("\\", "/"),
                 generated_at, "\n".join(comments), ROW_CAP_PER_PAPER, SNAPSHOT_ID),
            )
            continue

        # Read at most BYTES_CAP_PER_CSV bytes after the data offset to
        # avoid pathological cases (one paper roster is 242 MB).
        file_size = csv_path.stat().st_size
        data_bytes = file_size - data_offset
        truncated_at_bytes = data_bytes > BYTES_CAP_PER_CSV

        rows: list[tuple] = []
        total_in_csv = 0
        with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(data_offset)
            # Pre-read the cap into memory so DictReader can never run
            # past it. Slightly wasteful but safe and predictable.
            text = f.read(BYTES_CAP_PER_CSV)
            # If we truncated mid-record, drop the partial final line.
            if truncated_at_bytes:
                nl = text.rfind("\n")
                if nl > 0:
                    text = text[: nl + 1]
            from io import StringIO
            try:
                reader = csv.DictReader(StringIO(text))
                for idx, row in enumerate(reader):
                    total_in_csv += 1
                    if len(rows) >= ROW_CAP_PER_PAPER:
                        continue
                    rows.append(
                        (
                            paper_id,
                            family,
                            idx,
                            (row.get("dataset") or "")[:200],
                            (row.get("split") or "")[:50],
                            (row.get("drug_id") or "")[:200],
                            (row.get("drug_smiles") or "")[:2000],
                            (row.get("target_id") or "")[:200],
                            (row.get("target_sequence_sha8") or "")[:50],
                            _maybe_float(row.get("affinity_value")),
                            (row.get("pdb_id") or "")[:20],
                            (row.get("protein_id") or "")[:200],
                            (row.get("pair_partner_id") or "")[:200],
                            # payload_json kept short: it bloats the duckdb
                            # ~3-4x for big datasets and the indexed cols
                            # above already cover all the joinable fields.
                            json.dumps(row, ensure_ascii=False, default=str)[:1500],
                            SNAPSHOT_ID,
                        )
                    )
            except Exception as exc:
                print(f"  [warn] {csv_path.name}: {exc}", flush=True)
                rows = []

        n = len(rows)
        if rows:
            # Use Arrow round-trip — orders of magnitude faster than
            # executemany() for tens of thousands of rows.
            import pyarrow as pa
            cols = list(zip(*rows))
            tbl = pa.table({
                "paper_id":        pa.array(cols[0], type=pa.string()),
                "family":          pa.array(cols[1], type=pa.string()),
                "row_index":       pa.array(cols[2], type=pa.int64()),
                "dataset":         pa.array(cols[3], type=pa.string()),
                "split":           pa.array(cols[4], type=pa.string()),
                "drug_id":         pa.array(cols[5], type=pa.string()),
                "drug_smiles":     pa.array(cols[6], type=pa.string()),
                "target_id":       pa.array(cols[7], type=pa.string()),
                "target_seq_sha8": pa.array(cols[8], type=pa.string()),
                "affinity_value":  pa.array(cols[9], type=pa.float64()),
                "pdb_id":          pa.array(cols[10], type=pa.string()),
                "protein_id":      pa.array(cols[11], type=pa.string()),
                "pair_partner_id": pa.array(cols[12], type=pa.string()),
                "payload_json":    pa.array(cols[13], type=pa.string()),
                "snapshot_id":     pa.array(cols[14], type=pa.string()),
            })
            con.register("_papers_rows_batch", tbl)
            con.execute("INSERT INTO papers_rows SELECT * FROM _papers_rows_batch")
            con.unregister("_papers_rows_batch")
            del tbl
        con.execute(
            "INSERT INTO papers_metadata VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (paper_id, family, n, total_in_csv,
             str(csv_path.relative_to(PROTEOSPHERE_ROOT)).replace("\\", "/"),
             generated_at, "\n".join(comments), ROW_CAP_PER_PAPER, SNAPSHOT_ID),
        )
        total += n
        notes = []
        if total_in_csv > n:
            notes.append(f"row-capped from {total_in_csv:,}")
        if truncated_at_bytes:
            notes.append(f"byte-capped at {BYTES_CAP_PER_CSV//1024//1024} MB")
        cap_note = f" ({'; '.join(notes)})" if notes else ""
        print(f"  + {paper_id}: {n:,} rows{cap_note} ({family})", flush=True)

    n_all = con.execute("SELECT COUNT(*) FROM papers_rows").fetchone()[0]
    n_papers = con.execute("SELECT COUNT(*) FROM papers_metadata").fetchone()[0]
    print(f"[papers] papers={n_papers}  total_rows={n_all:,}")
    return {"papers": n_papers, "rows": n_all}


def _maybe_float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def materialize_pinder_plinder(con) -> dict:
    summary = AUDIT_ROOT / "pinder_plinder_cross_audit_expanded_summary.json"
    if not summary.exists():
        print(f"[pinder_plinder] no summary at {summary}")
        return {"rows": 0}
    data = json.loads(summary.read_text(encoding="utf-8"))
    audits = data.get("audits") or {}

    _drop(con, "pinder_plinder_audit")
    _drop(con, "pinder_plinder_axis_overlap")
    con.execute(
        """
        CREATE TABLE pinder_plinder_audit (
            comparison      VARCHAR PRIMARY KEY,
            verdict         VARCHAR,
            composite       DOUBLE,
            axes_attempted  VARCHAR,
            axes_unavailable VARCHAR,
            snapshot_id     VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE TABLE pinder_plinder_axis_overlap (
            comparison       VARCHAR,
            axis             VARCHAR,
            overlap_count    BIGINT,
            overlap_fraction DOUBLE,
            snapshot_id      VARCHAR
        )
        """
    )

    axes_attempted = ",".join(data.get("axes_attempted", []))
    n_aud = 0
    n_ovl = 0
    for cmp_name, body in audits.items():
        con.execute(
            "INSERT INTO pinder_plinder_audit VALUES (?, ?, ?, ?, ?, ?)",
            (
                cmp_name,
                body.get("verdict") or "",
                float(body.get("composite") or 0.0),
                axes_attempted,
                ",".join(body.get("axes_unavailable", [])),
                SNAPSHOT_ID,
            ),
        )
        n_aud += 1
        for axis, ax in (body.get("axes") or {}).items():
            con.execute(
                "INSERT INTO pinder_plinder_axis_overlap VALUES (?, ?, ?, ?, ?)",
                (
                    cmp_name,
                    axis,
                    int(ax.get("overlap_count") or 0),
                    float(ax.get("overlap_fraction") or 0.0),
                    SNAPSHOT_ID,
                ),
            )
            n_ovl += 1
    print(f"[pinder_plinder] audits={n_aud}  axis_overlap={n_ovl}")
    return {"audits": n_aud, "axis_overlap": n_ovl}


def main() -> int:
    print(f"Warehouse: {WAREHOUSE}")
    con = duckdb.connect(str(WAREHOUSE))
    try:
        stats = {}
        stats["papers"] = materialize_papers(con)
        stats["pinder_plinder"] = materialize_pinder_plinder(con)
        # Stamp ingest_runs.
        con.execute(
            """
            INSERT INTO ingest_runs
              (run_id, source_id, snapshot_id, registered_at,
               row_counts, output_files, sha256, license)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (run_id) DO NOTHING
            """,
            (
                f"papers_audit_{SNAPSHOT_ID}",
                "demo_warehouse_papers_audit",
                SNAPSHOT_ID,
                time.time(),
                json.dumps(stats),
                "papers_rows,papers_metadata,pinder_plinder_audit,pinder_plinder_axis_overlap",
                None,
                "CC-BY-4.0/CC0-mixed",
            ),
        )
    finally:
        con.close()
    print(f"Final: {WAREHOUSE.stat().st_size/1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
