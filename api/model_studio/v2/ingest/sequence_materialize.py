"""Materialise UniProt AA sequences for the v2 protein universe.

The legacy warehouse stores ``sequence_index`` (accession → md5 + length)
but **not** the actual amino-acid strings. The architecture leaves them
to JIT fetch at training time. For fully-offline training we materialise
sequences for the v2 universe (~57 K UniProts) up-front.

Storage budget:
    57 K UniProts × ~350 AA average × 1 byte = ~20 MB raw
    Parquet zstd: ~6-8 MB

Fetch strategy:
    UniProt REST batch API: GET /uniprotkb/accessions/<acc> for each acc
    in batches of 100. Rate limit: 50 req/s shared. ~57 K / 100 = 570
    requests; at conservative 5 req/s ≈ 2 minutes total wall time.

Failure modes:
    - Obsolete / merged accessions return 404. Logged, not retried.
    - Network errors: retried up to 3 times with backoff.
    - Cap budget: ~30 MB pulled max; trivially under the 5 TB cap.

Output (one parquet, registered as ``v2_protein_sequences``):
    uniprot          UniProt accession
    sequence         AA string ("MAVL...")
    sequence_length  integer
    sequence_md5     md5 of the sequence (matches legacy sequence_index)
    source_corpus    "swiss-prot" | "trembl" | "uniref-rep"
    fetched_at       ISO-8601 timestamp
    snapshot_id
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

import duckdb

from .state import INGEST_ROOT, get_state
from .catalog import _CATALOG_PATH, _safe_view_name
from .sequence_signatures import collect_uniprot_universe


_UPI_REST = "https://rest.uniprot.org/uniprotkb"
_UPI_STREAM = "https://rest.uniprot.org/uniprotkb/stream"
_UPI_ACCS  = "https://rest.uniprot.org/uniprotkb/accessions"
_USER_AGENT = "ProteoSphere-Ingest/0.2 (proteosphere@users.noreply.github.com)"
_REQ_TIMEOUT = 120
_REQ_PACING_S = 0.2  # between batches
_BATCH_SIZE = 100    # accessions per batch request (URL safety)


def _v2() -> duckdb.DuckDBPyConnection:
    _CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(_CATALOG_PATH))


def _parse_fasta_blocks(text: str) -> list[tuple[str, str, str]]:
    """Returns list of (acc, sequence, source_corpus) from multi-FASTA text.

    Header forms recognised:
        >sp|ACC|NAME ...    → swiss-prot
        >tr|ACC|NAME ...    → trembl
    """
    out: list[tuple[str, str, str]] = []
    cur_acc: str | None = None
    cur_corpus: str = "uniprot"
    cur_seq: list[str] = []

    def _flush():
        nonlocal cur_acc, cur_corpus, cur_seq
        if cur_acc and cur_seq:
            out.append((cur_acc, "".join(cur_seq), cur_corpus))
        cur_acc = None
        cur_seq = []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(">"):
            _flush()
            # Parse ">sp|ACC|NAME ..." or ">tr|ACC|NAME ..."
            parts = line[1:].split("|")
            if len(parts) >= 2:
                cur_corpus = "swiss-prot" if parts[0] == "sp" else ("trembl" if parts[0] == "tr" else "uniprot")
                cur_acc = parts[1]
            else:
                cur_acc = None
                cur_corpus = "uniprot"
        else:
            if cur_acc:
                cur_seq.append(line)
    _flush()
    return out


def _fetch_batch_fasta(accs: list[str], retries: int = 3) -> list[tuple[str, str, str]]:
    """Fetch a batch of UniProt FASTA records via the accessions endpoint.

    UniProt API:
        GET /uniprotkb/accessions?accessions=A,B,C&format=fasta
    Supports up to 1000 accessions per call. Comma-separated, much shorter
    URL than the search-stream variant.
    """
    if not accs:
        return []
    params = urllib.parse.urlencode({
        "accessions": ",".join(accs),
        "format": "fasta",
        "size": str(len(accs)),
    })
    url = f"{_UPI_ACCS}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    delay = 1.0
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=_REQ_TIMEOUT) as resp:
                text = resp.read().decode("utf-8", errors="replace")
            return _parse_fasta_blocks(text)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            if attempt + 1 == retries:
                raise
            time.sleep(delay)
            delay *= 2
    return []


def materialise_sequences(snapshot_id: str | None = None,
                          max_uniprots: int | None = None,
                          resume: bool = True) -> dict:
    """Fetch AA sequences for the v2 UniProt universe.

    Args:
        snapshot_id: write under this snapshot folder (default: utc now).
        max_uniprots: cap for smoke tests; None = all.
        resume: if a partial parquet exists for this snapshot, skip
                accessions already materialised.
    """
    snapshot_id = snapshot_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    universe = collect_uniprot_universe()
    if not universe:
        return {"error": "no v2 UniProts; run bridges first"}
    uniprots = sorted(universe.keys())
    if max_uniprots:
        uniprots = uniprots[:max_uniprots]

    out_dir = (INGEST_ROOT / "normalized" / "protein_sequences"
               / "v2_uniprot_sequences" / snapshot_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "sequences.parquet"

    # Resume: load any sequences we've already cached
    cached: dict[str, dict] = {}
    if resume and out_path.exists():
        try:
            v2 = _v2()
            cached_rows = v2.execute(
                f"SELECT uniprot, sequence, sequence_length, sequence_md5, source_corpus, fetched_at "
                f"FROM read_parquet('{str(out_path).replace(chr(92), '/')}')"
            ).fetchall()
            v2.close()
            for u, seq, l, m, sc, fa in cached_rows:
                cached[u] = {
                    "uniprot": u, "sequence": seq, "sequence_length": l,
                    "sequence_md5": m, "source_corpus": sc, "fetched_at": fa,
                    "snapshot_id": snapshot_id,
                }
        except Exception:
            cached = {}

    pending = [u for u in uniprots if u not in cached]
    rows: list[dict] = list(cached.values())
    stats = {
        "universe_size": len(uniprots),
        "already_cached": len(cached),
        "fetched": 0,
        "missing_404": 0,
        "errors": 0,
        "bytes_pulled_estimate": 0,
    }

    flush_every = 500  # flush partial parquet every N successful fetches
    last_flush = 0

    state = get_state()
    state.reserve(
        source_id="v2_sequence_materialise",
        url=_UPI_REST,
        bytes_expected=len(pending) * 1500,   # rough upper bound per FASTA
        snapshot_id=snapshot_id,
        local_path=str(out_dir),
    )

    try:
        for batch_start in range(0, len(pending), _BATCH_SIZE):
            batch = pending[batch_start:batch_start + _BATCH_SIZE]
            try:
                results = _fetch_batch_fasta(batch)
            except Exception as exc:
                stats["errors"] += 1
                time.sleep(2.0)
                continue
            got_accs = set()
            for acc, seq, corpus in results:
                if not seq or not acc:
                    continue
                md5 = hashlib.md5(seq.encode("utf-8")).hexdigest()
                rows.append({
                    "uniprot": acc,
                    "sequence": seq,
                    "sequence_length": len(seq),
                    "sequence_md5": md5,
                    "source_corpus": corpus,
                    "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "snapshot_id": snapshot_id,
                })
                stats["fetched"] += 1
                stats["bytes_pulled_estimate"] += len(seq) + 200
                got_accs.add(acc)
            # 404-equivalent: accessions in batch that we didn't get back
            stats["missing_404"] += sum(1 for a in batch if a not in got_accs)
            time.sleep(_REQ_PACING_S)

            if stats["fetched"] - last_flush >= flush_every:
                _write_parquet(rows, out_path)
                state.update_progress(
                    "v2_sequence_materialise",
                    stats["bytes_pulled_estimate"],
                )
                last_flush = stats["fetched"]
    finally:
        _write_parquet(rows, out_path)
        try:
            state.mark_verified(
                "v2_sequence_materialise",
                sha256="",
                bytes_pulled=stats["bytes_pulled_estimate"],
                local_path=str(out_path),
            )
        except Exception:
            pass

    # Register view
    view = _safe_view_name("v2", "protein_sequences")
    if rows:
        v2 = _v2()
        try:
            v2.execute(f"DROP VIEW IF EXISTS {view}")
            v2.execute(f"DROP TABLE IF EXISTS {view}")
            v2.execute(
                f"CREATE VIEW {view} AS SELECT * FROM read_parquet('{str(out_path).replace(chr(92), '/')}')"
            )
        finally:
            v2.close()

    audit = {
        "snapshot_id": snapshot_id,
        "output_path": str(out_path),
        "n_sequences": len(rows),
        "stats": stats,
        "view_name": view if rows else None,
    }
    (out_dir / "manifest.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return audit


def _write_parquet(rows: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_bytes(b"")
        return path
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
        keys = list(rows[0].keys())
        cols: dict[str, list] = {k: [] for k in keys}
        for r in rows:
            for k in keys:
                cols[k].append(r.get(k))
        pq.write_table(pa.table(cols), path, compression="zstd")
        return path
    except Exception:
        jsonl = path.with_suffix(".jsonl")
        with open(jsonl, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
        return jsonl
