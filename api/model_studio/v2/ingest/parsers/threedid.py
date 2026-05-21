"""3did (3D interacting domains) parser.

3did_flat is a gzipped custom plain-text format where each record describes a
Pfam-Pfam domain-domain interaction (DDI) observed in PDB.

Record layout (one DDI per record, separated by lines starting with `#=ID`):

    #=ID  <domain_A>  <domain_B>   (PF12345.14@Pfam  PF67890.5@Pfam)
    #=3D  <pdb_id>    <chain_a>:<res_range>   <chain_b>:<res_range>   <z_score>  <p>  <a:b>
    <inter-residue contact lines, ignored for now>
    #=3D  <another PDB observation>
    ...
    #=ID  <next DDI>

We emit two parquet fragments:
    domain_pairs.parquet      one row per DDI
        ddi_id           "ddi:3did:<pfA>_<pfB>" (canonical, sorted)
        pfam_a           "PF12345.14"
        pfam_b           "PF67890.5"
        pfam_a_root      "PF12345" (version stripped)
        pfam_b_root      "PF67890"
        domain_a_name    e.g. "1-cysPrx_C"
        domain_b_name    e.g. "1-cysPrx_C"
        n_pdb_obs        INTEGER  count of #=3D lines under this record
        snapshot_id, source

    pdb_observations.parquet  one row per PDB observation
        obs_id           "ddi_obs:3did:<pfA>_<pfB>:<pdb>:<chain_a>:<chain_b>"
        ddi_id           link back to domain_pairs
        pfam_a, pfam_b   canonical
        pdb_id           e.g. "1n8j"
        chain_a, chain_b
        res_range_a, res_range_b   "153-185"
        z_score          float
        p_score          float
        contact_pair     "a:b" (Pfam-domain contact counts; left as raw text)
        snapshot_id, source
"""

from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

from ..state import SourceState, INGEST_ROOT
from . import register_parser, ParseResult

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
    _HAS_ARROW = True
except Exception:
    _HAS_ARROW = False

SOURCE_ID = "3did"
SOURCE_LABEL = "3did (3D interacting domains)"

_ID_RE = re.compile(
    r"^#=ID\s+(?P<a_name>\S+)\s+(?P<b_name>\S+)\s+\((?P<a_pfam>PF\d+(?:\.\d+)?)@Pfam\s+(?P<b_pfam>PF\d+(?:\.\d+)?)@Pfam\)"
)
_3D_RE = re.compile(
    r"^#=3D\s+(?P<pdb>\S+)\s+(?P<chain_a>[A-Za-z0-9]):(?P<range_a>\d+-\d+)\s+"
    r"(?P<chain_b>[A-Za-z0-9]):(?P<range_b>\d+-\d+)\s+(?P<z>\S+)\s+(?P<p>\S+)\s+(?P<contact>\S+)"
)


def _canonical_ddi_id(pf_a: str, pf_b: str) -> str:
    lo, hi = sorted([pf_a, pf_b])
    return f"ddi:3did:{lo}_{hi}"


def _strip_version(pfam_id: str) -> str:
    return pfam_id.split(".", 1)[0]


def _open_maybe_gz(path: Path):
    """3did ships as .gz. Open transparently."""
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, "rt", encoding="utf-8", errors="replace")


def parse_3did(path: Path, snapshot_id: str) -> tuple[list[dict], list[dict], dict]:
    """Walk the flat file. Emit (domain_pairs, pdb_obs, stats)."""
    pairs: list[dict] = []
    obs: list[dict] = []
    stats = {"records_seen": 0, "obs_seen": 0, "pairs_deduped": 0}
    seen_pairs: dict[str, dict] = {}
    cur_pair: dict | None = None

    with _open_maybe_gz(path) as f:
        for line in f:
            if line.startswith("#=ID"):
                m = _ID_RE.match(line.rstrip())
                if not m:
                    continue
                stats["records_seen"] += 1
                pf_a = m.group("a_pfam")
                pf_b = m.group("b_pfam")
                lo, hi = sorted([pf_a, pf_b])
                ddi_id = _canonical_ddi_id(pf_a, pf_b)
                if ddi_id in seen_pairs:
                    # 3did sometimes has dupe headers across multi-section files
                    cur_pair = seen_pairs[ddi_id]
                    stats["pairs_deduped"] += 1
                    continue
                cur_pair = {
                    "ddi_id": ddi_id,
                    "pfam_a": lo,
                    "pfam_b": hi,
                    "pfam_a_root": _strip_version(lo),
                    "pfam_b_root": _strip_version(hi),
                    "domain_a_name": m.group("a_name") if lo == pf_a else m.group("b_name"),
                    "domain_b_name": m.group("b_name") if lo == pf_a else m.group("a_name"),
                    "n_pdb_obs": 0,
                    "snapshot_id": snapshot_id,
                    "source": SOURCE_ID,
                }
                pairs.append(cur_pair)
                seen_pairs[ddi_id] = cur_pair
                continue
            if line.startswith("#=3D") and cur_pair is not None:
                m = _3D_RE.match(line.rstrip())
                if not m:
                    continue
                cur_pair["n_pdb_obs"] += 1
                stats["obs_seen"] += 1
                # Canonicalize chain order to match cur_pair's pfam ordering
                if cur_pair["pfam_a"] == cur_pair.get("pfam_a"):
                    ch_a, rg_a = m.group("chain_a"), m.group("range_a")
                    ch_b, rg_b = m.group("chain_b"), m.group("range_b")
                else:
                    ch_a, rg_a = m.group("chain_b"), m.group("range_b")
                    ch_b, rg_b = m.group("chain_a"), m.group("range_a")
                try:
                    z = float(m.group("z"))
                except ValueError:
                    z = None
                try:
                    p = float(m.group("p"))
                except ValueError:
                    p = None
                obs.append({
                    "obs_id": f"ddi_obs:3did:{cur_pair['pfam_a']}_{cur_pair['pfam_b']}:{m.group('pdb')}:{ch_a}:{ch_b}",
                    "ddi_id": cur_pair["ddi_id"],
                    "pfam_a": cur_pair["pfam_a"],
                    "pfam_b": cur_pair["pfam_b"],
                    "pdb_id": m.group("pdb"),
                    "chain_a": ch_a,
                    "chain_b": ch_b,
                    "res_range_a": rg_a,
                    "res_range_b": rg_b,
                    "z_score": z,
                    "p_score": p,
                    "contact_pair": m.group("contact"),
                    "snapshot_id": snapshot_id,
                    "source": SOURCE_ID,
                })
    return pairs, obs, stats


def _write_parquet(rows: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_bytes(b"")
        return path
    if _HAS_ARROW:
        keys = list(rows[0].keys())
        cols: dict[str, list] = {k: [] for k in keys}
        for r in rows:
            for k in keys:
                cols[k].append(r.get(k))
        pq.write_table(pa.table(cols), path, compression="zstd")
        return path
    jsonl = path.with_suffix(".jsonl")
    with open(jsonl, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return jsonl


def _parse_impl(state: SourceState, *, snapshot_dir: Path | None = None) -> ParseResult:
    src_dir = Path(state.local_path)
    if not src_dir.exists():
        return ParseResult(SOURCE_ID, state.snapshot_id, {}, {}, {},
                           errors=[f"local_path missing: {src_dir}"])
    candidates = list(src_dir.glob("*.gz")) + list(src_dir.glob("3did_flat*"))
    if not candidates:
        return ParseResult(SOURCE_ID, state.snapshot_id, {}, {}, {},
                           errors=[f"no 3did_flat file under {src_dir}"])
    src = candidates[0]
    out_dir = snapshot_dir or (INGEST_ROOT / "normalized" / "interaction_network" / SOURCE_ID / state.snapshot_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    pairs, obs, stats = parse_3did(src, state.snapshot_id)
    paths = {
        "domain_pairs":      str(_write_parquet(pairs, out_dir / "domain_pairs.parquet")),
        "pdb_observations":  str(_write_parquet(obs,   out_dir / "pdb_observations.parquet")),
    }
    provenance = {
        "claim_type": "ingest",
        "source_id": SOURCE_ID,
        "source_label": SOURCE_LABEL,
        "snapshot_id": state.snapshot_id,
        "sha256": state.sha256,
        "input_paths": [str(src)],
        "output_paths": list(paths.values()),
        "row_counts": {"domain_pairs": len(pairs), "pdb_observations": len(obs)},
        "parse_stats": stats,
        "license": "Open (academic; IRB Barcelona)",
        "url_base": "https://3did.irbbarcelona.org/",
        "notes": "DDIs observed in PDB. Pfam ids are versioned (PF12345.14); pfam_*_root strips the .NN.",
    }
    (out_dir / "manifest.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    return ParseResult(
        source_id=SOURCE_ID,
        snapshot_id=state.snapshot_id,
        row_counts={"domain_pairs": len(pairs), "pdb_observations": len(obs)},
        output_files=paths,
        provenance=provenance,
        warnings=([] if _HAS_ARROW else ["pyarrow not installed; emitted JSONL"]),
    )


register_parser(SOURCE_ID, _parse_impl)
