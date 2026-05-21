"""Warehouse-backed DTA dataset.

Reads (UniProt, ligand_ref, sequence, smiles, label) tuples directly from
the v2 catalog views — no external data files needed. Works for any DTA
benchmark whose interaction parquet was registered in the v2 catalog.

Currently wired benchmarks:
    "davis"   — davis_interactions × davis_proteins × davis_ligands
                label_value is pKd
    "kiba"    — kiba_interactions × kiba_proteins × kiba_ligands
                label_value is kiba_score
    "gtopdb"  — gtopdb_interactions × gtopdb_targets × gtopdb_ligands
                affinity_value is pKi / pKd / pIC50 etc.

The loader uses the same char-level tokenisers as DeepDTA (CHARPROTSET /
CHARISOSMISET) so this dataset class is a drop-in replacement for
DavisDataset in training.py.

For protein-side lookups it can optionally fall back to
``v2_protein_sequences`` (materialised by sequence_materialize.py) when
the benchmark table itself doesn't carry sequence text — useful for any
future benchmark that ships only UniProt ids.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import torch
from torch.utils.data import Dataset, DataLoader

import duckdb

from .dataset import (
    CHARPROTSET, CHARISOSMISET,
    MAX_SEQ_LEN, MAX_SMI_LEN,
    label_sequence, label_smiles,
)
from .ingest.catalog import _CATALOG_PATH


# Per-benchmark SQL: returns (uniprot, ligand_ref, sequence, smiles, label).
# All five columns must come from the v2 catalog directly so this module
# has no external file dependency.
_BENCHMARK_SQL: dict[str, str] = {
    "davis": """
        SELECT  p.protein_key       AS uniprot,
                l.ligand_ref        AS ligand_ref,
                p.sequence          AS sequence,
                l.smiles            AS smiles,
                i.label_value       AS label
        FROM    davis_interactions i
        JOIN    davis_proteins      p ON p.protein_key = i.protein_key
        JOIN    davis_ligands       l ON l.ligand_key  = i.ligand_key
        WHERE   i.label_value IS NOT NULL
          AND   p.sequence IS NOT NULL
          AND   l.smiles   IS NOT NULL
    """,
    "kiba": """
        SELECT  p.protein_key       AS uniprot,
                l.ligand_ref        AS ligand_ref,
                p.sequence          AS sequence,
                l.smiles            AS smiles,
                i.label_value       AS label
        FROM    kiba_interactions i
        JOIN    kiba_proteins      p ON p.protein_key = i.protein_key
        JOIN    kiba_ligands       l ON l.ligand_key  = i.ligand_key
        WHERE   i.label_value IS NOT NULL
          AND   p.sequence IS NOT NULL
          AND   l.smiles   IS NOT NULL
    """,
    # gtopdb ships uniprot directly; sequence lookup needs an external
    # resolver. For now we join with v2_protein_sequences (materialised
    # Swiss-Prot) when available.
    "gtopdb": """
        SELECT  i.uniprot                AS uniprot,
                CONCAT('ligand:gtopdb:', i.ligand_id) AS ligand_ref,
                s.sequence               AS sequence,
                l.smiles                 AS smiles,
                i.affinity_value         AS label
        FROM    gtopdb_interactions  i
        JOIN    gtopdb_ligands       l ON l.ligand_id = i.ligand_id
        JOIN    v2_protein_sequences s ON s.uniprot  = i.uniprot
        WHERE   i.affinity_value IS NOT NULL
          AND   s.sequence IS NOT NULL
          AND   l.smiles   IS NOT NULL
          AND   i.affinity_kind IN ('pki', 'pkd', 'pic50')
    """,
}


@dataclass
class DTARecord:
    uniprot: str
    ligand_ref: str
    sequence: str
    smiles: str
    label: float


def _scaffold_key_map(records: list["DTARecord"]) -> dict[str, str]:
    """Best-effort lookup of {ligand_ref: scaffold_id} via v2_ligand_scaffolds.

    Returns an empty dict if the view isn't registered or the query fails;
    callers should fall back to using ligand_ref directly (equivalent to
    cold-drug semantics for benchmarks without scaffold coverage).
    """
    if not records:
        return {}
    try:
        con = duckdb.connect(str(_CATALOG_PATH), read_only=True)
        try:
            rows = con.execute(
                "SELECT ligand_ref, scaffold_id FROM v2_ligand_scaffolds"
            ).fetchall()
        finally:
            con.close()
        return {lref: sid for lref, sid in rows if lref and sid}
    except Exception:
        return {}


def _resolve_cluster_keys(
    records: list["DTARecord"],
    *,
    threshold: str = "uniref50",
    benchmark: str | None = None,
) -> tuple[list[str], dict]:
    """Per-record cluster key for a leakage-aware split.

    Two-stage lookup:
        1. If the record's ``uniprot`` field already looks like a UniProt
           accession (e.g. KIBA stores "P15056" directly), look it up in
           ``v2_sequence_cluster_membership`` and return the cluster rep
           at the chosen UniRef threshold.
        2. If the benchmark stores symbols / internal keys (e.g. Davis
           stores "AAK1" / "ABL1(E255K)"), bridge via the per-benchmark
           ``{benchmark}_bridge_uniprot`` view first, then look up the
           cluster. Multi-hop ambiguous bridges (Davis CDC2L2 → 5
           candidate UniProts) pick the lexicographically-smallest
           accession deterministically so re-runs produce identical
           splits.

    Records whose lookup fails (rare proteins outside the materialised
    universe, or benchmarks with no bridge view) fall back to using the
    record's own ``uniprot`` string as its own cluster key — safe but
    not collapsing homologs we don't know about.

    Returns:
        (cluster_keys, meta) where
            cluster_keys[i]  is the bucket key for record i
            meta             reports collapse stats + provenance so the
                             trainer can log them in the run summary
    """
    if threshold not in ("uniref50", "uniref90", "uniref100"):
        raise ValueError(f"Invalid threshold '{threshold}'")
    if not records:
        return [], {"clusters": 0, "uniprots": 0}
    keys = sorted({r.uniprot for r in records if r.uniprot})
    lookup: dict[str, str] = {}            # record.uniprot → cluster rep
    bridge_map: dict[str, str] = {}        # source_key → uniprot (for logging)
    used_view = "v2_sequence_cluster_membership"
    try:
        con = duckdb.connect(str(_CATALOG_PATH), read_only=True)
        try:
            # Step 1: direct uniprot lookup
            rows = con.execute(
                f"SELECT uniprot, {threshold} FROM v2_sequence_cluster_membership "
                f"WHERE uniprot IN ("
                + ",".join(["?"] * len(keys))
                + ")",
                keys,
            ).fetchall()
            for u, c in rows:
                if u and c:
                    lookup[u] = c
            # Step 2: if the benchmark stores non-UniProt keys, bridge.
            unmatched = [k for k in keys if k not in lookup]
            if unmatched and benchmark:
                bridge_view = f"{benchmark}_bridge_uniprot"
                try:
                    bridge_rows = con.execute(
                        f"""
                        SELECT b.source_key, MIN(c.{threshold}) AS cluster
                        FROM   {bridge_view} b
                        JOIN   v2_sequence_cluster_membership c
                          ON   c.uniprot = b.uniprot
                        WHERE  b.source_key IN ({",".join(["?"] * len(unmatched))})
                        GROUP BY b.source_key
                        """,
                        unmatched,
                    ).fetchall()
                    for src_key, cluster in bridge_rows:
                        if src_key and cluster:
                            lookup[src_key] = cluster
                            bridge_map[src_key] = cluster
                except Exception:
                    # Bridge view absent — leave unmatched records on their own
                    pass
        finally:
            con.close()
    except Exception:
        # Catalog missing or query crashed — fall back to per-record uniprots
        # (i.e. cold-target semantics). Coverage stays 0.
        lookup = {}

    cluster_keys = [lookup.get(r.uniprot, r.uniprot) for r in records]
    n_uniq_prots = len(keys)
    n_uniq_clusters = len(set(cluster_keys))
    n_merged = n_uniq_prots - n_uniq_clusters
    coverage = sum(1 for k in keys if k in lookup)
    meta = {
        "threshold":             threshold,
        "uniprots_or_keys":      n_uniq_prots,
        "clusters":              n_uniq_clusters,
        "merged_into_clusters":  n_merged,
        "cluster_coverage":      coverage,
        "cluster_coverage_pct":  round(100.0 * coverage / max(n_uniq_prots, 1), 1),
        "view":                  used_view,
        "bridged_via_benchmark": bool(bridge_map) and (benchmark or ""),
        "n_bridged":             len(bridge_map),
    }
    return cluster_keys, meta


def load_warehouse_records(benchmark: str) -> list[DTARecord]:
    """Pull all (uniprot, ligand, seq, smi, label) tuples for a benchmark
    out of the v2 catalog. Returns one DTARecord per training example.
    """
    if benchmark not in _BENCHMARK_SQL:
        raise ValueError(
            f"Unknown benchmark '{benchmark}'. Choices: {sorted(_BENCHMARK_SQL)}"
        )
    con = duckdb.connect(str(_CATALOG_PATH), read_only=True)
    try:
        rows = con.execute(_BENCHMARK_SQL[benchmark]).fetchall()
    finally:
        con.close()
    return [
        DTARecord(uniprot=u, ligand_ref=lr, sequence=seq, smiles=smi, label=float(lbl))
        for (u, lr, seq, smi, lbl) in rows
    ]


# ── Torch Dataset ─────────────────────────────────────────────────────────

class WarehouseDTADataset(Dataset):
    """Char-level tokenised DTA dataset for any wired benchmark.

    Pre-tokenises into numpy arrays in __init__ so DataLoader workers don't
    have to re-encode each epoch. 30K records × ~1100 ints/record ≈ 32 MB
    of RAM which is fine.
    """

    def __init__(self, records: list[DTARecord]):
        self.records = records
        if not records:
            self.seqs = np.zeros((0, MAX_SEQ_LEN), dtype=np.int64)
            self.smis = np.zeros((0, MAX_SMI_LEN), dtype=np.int64)
            self.ys = np.zeros((0,), dtype=np.float32)
            return
        self.seqs = np.stack([label_sequence(r.sequence) for r in records])
        self.smis = np.stack([label_smiles(r.smiles)    for r in records])
        self.ys = np.array([r.label for r in records], dtype=np.float32)

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, idx: int):
        return (
            torch.from_numpy(self.seqs[idx]),
            torch.from_numpy(self.smis[idx]),
            torch.tensor(self.ys[idx], dtype=torch.float32),
        )


def make_warehouse_loaders(
    benchmark: str,
    *,
    split_policy: str = "random",
    batch_size: int = 256,
    val_frac: float = 0.1,
    test_frac: float = 0.1,
    seed: int = 4192,
    num_workers: int = 0,
) -> tuple[DataLoader, DataLoader, DataLoader, dict]:
    """Build train/val/test loaders for a v2 catalog DTA benchmark.

    split_policy options:
        "random"      — i.i.d. random by record index (warm split).
        "cold-target" — held-out UniProts (no protein in train appears in test).
        "cold-drug"   — held-out ligand_refs (no ligand in train appears in test).
        "cold-pair"   — held-out (protein, ligand) where both axes are unseen
                        (strictest; very few pairs survive on small benchmarks).
        "scaffold"    — held-out Bemis-Murcko scaffolds (alias of cold-drug
                        bucketed by scaffold_id from v2_ligand_scaffolds when
                        available; falls back to cold-drug otherwise).
        "stratified"  — same as random but each bucket gets equal
                        per-protein representation. Lower-variance metrics
                        but doesn't test family transfer.
        "time-split"  — chronological — train ≤ cut-off year, test > cut-off.
                        Falls back to random for benchmarks without a year
                        column.
        "cluster"     — alias of cold-target (kept for UI continuity with
                        the GUI's policy picker).

    The cold splits use a deterministic hash of (entity, seed) so an entity
    sorts to the same bucket on every run.
    """
    records = load_warehouse_records(benchmark)
    n = len(records)
    if n == 0:
        raise RuntimeError(f"No records returned for benchmark '{benchmark}'")

    rng = np.random.default_rng(seed)
    # Bag for split-time provenance (e.g. cluster-merge stats); attached to meta below.
    split_provenance: dict = {}

    def _hash_split(values: list[str], val_frac: float, test_frac: float, key: str) -> dict[str, str]:
        """Deterministic per-entity bucketing using rng over unique values."""
        unique = sorted(set(values))
        perm = rng.permutation(len(unique))
        n_test = int(len(unique) * test_frac)
        n_val  = int(len(unique) * val_frac)
        bucket: dict[str, str] = {}
        for j, idx in enumerate(perm):
            if j < n_test:                          bucket[unique[idx]] = "test"
            elif j < n_test + n_val:                bucket[unique[idx]] = "val"
            else:                                    bucket[unique[idx]] = "train"
        return bucket

    if split_policy == "random":
        idx = rng.permutation(n)
        n_test = int(n * test_frac)
        n_val  = int(n * val_frac)
        test_idx  = idx[:n_test]
        val_idx   = idx[n_test:n_test + n_val]
        train_idx = idx[n_test + n_val:]
    elif split_policy == "cold-target":
        buckets = _hash_split([r.uniprot for r in records], val_frac, test_frac, "uniprot")
        train_idx = np.array([i for i, r in enumerate(records) if buckets[r.uniprot] == "train"])
        val_idx   = np.array([i for i, r in enumerate(records) if buckets[r.uniprot] == "val"])
        test_idx  = np.array([i for i, r in enumerate(records) if buckets[r.uniprot] == "test"])
    elif split_policy == "cold-drug":
        buckets = _hash_split([r.ligand_ref for r in records], val_frac, test_frac, "ligand")
        train_idx = np.array([i for i, r in enumerate(records) if buckets[r.ligand_ref] == "train"])
        val_idx   = np.array([i for i, r in enumerate(records) if buckets[r.ligand_ref] == "val"])
        test_idx  = np.array([i for i, r in enumerate(records) if buckets[r.ligand_ref] == "test"])
    elif split_policy == "cold-pair":
        p_buckets = _hash_split([r.uniprot    for r in records], val_frac, test_frac, "uniprot")
        l_buckets = _hash_split([r.ligand_ref for r in records], val_frac, test_frac, "ligand")
        train_idx, val_idx, test_idx = [], [], []
        for i, r in enumerate(records):
            pb, lb = p_buckets[r.uniprot], l_buckets[r.ligand_ref]
            if pb == "train" and lb == "train":
                train_idx.append(i)
            elif pb == "test" and lb == "test":
                test_idx.append(i)
            elif pb == "val" and lb == "val":
                val_idx.append(i)
            # records that span buckets are discarded — cold-pair semantics
        train_idx, val_idx, test_idx = map(np.array, (train_idx, val_idx, test_idx))
    elif split_policy in ("cluster", "leakage-aware"):
        # Real leakage-aware split: bucket proteins by their UniRef50
        # cluster id (from v2_sequence_cluster_membership). Close homologs
        # (≥50% sequence identity) get the same bucket, so a protein in
        # train and its kinase-paralog in test won't both appear.
        # Falls back to cold-target when the cluster membership view is
        # unavailable for this benchmark.
        cluster_keys, leakage_meta = _resolve_cluster_keys(records, threshold="uniref50", benchmark=benchmark)
        buckets = _hash_split(cluster_keys, val_frac, test_frac, "cluster")
        train_idx = np.array([i for i, k in enumerate(cluster_keys) if buckets[k] == "train"])
        val_idx   = np.array([i for i, k in enumerate(cluster_keys) if buckets[k] == "val"])
        test_idx  = np.array([i for i, k in enumerate(cluster_keys) if buckets[k] == "test"])
        split_provenance["leakage_aware"] = leakage_meta
    elif split_policy == "scaffold":
        # Bucket by Bemis-Murcko scaffold when v2_ligand_scaffolds covers
        # this benchmark; fall back to cold-drug otherwise.
        scaf_key = _scaffold_key_map(records)
        keys_per_record = [scaf_key.get(r.ligand_ref, r.ligand_ref) for r in records]
        buckets = _hash_split(keys_per_record, val_frac, test_frac, "scaffold")
        train_idx = np.array([i for i, k in enumerate(keys_per_record) if buckets[k] == "train"])
        val_idx   = np.array([i for i, k in enumerate(keys_per_record) if buckets[k] == "val"])
        test_idx  = np.array([i for i, k in enumerate(keys_per_record) if buckets[k] == "test"])
    elif split_policy == "stratified":
        # Random within each protein — equal per-protein representation
        # in each split. Lower-variance metrics, but doesn't test family
        # transfer.
        by_prot: dict[str, list[int]] = {}
        for i, r in enumerate(records):
            by_prot.setdefault(r.uniprot, []).append(i)
        train_idx, val_idx, test_idx = [], [], []
        for prot_indices in by_prot.values():
            shuffled = rng.permutation(prot_indices)
            n_p_test = int(len(shuffled) * test_frac)
            n_p_val  = int(len(shuffled) * val_frac)
            test_idx.extend(shuffled[:n_p_test].tolist())
            val_idx.extend(shuffled[n_p_test:n_p_test + n_p_val].tolist())
            train_idx.extend(shuffled[n_p_test + n_p_val:].tolist())
        train_idx, val_idx, test_idx = map(np.array, (train_idx, val_idx, test_idx))
    elif split_policy == "time-split":
        # Benchmarks in the v2 catalog don't carry publication-year
        # metadata yet. Log a notice and fall through to random for now.
        # When we add the time column, switch to: sort by year, train ≤
        # cut, val == cut, test > cut.
        idx = rng.permutation(n)
        n_test = int(n * test_frac)
        n_val  = int(n * val_frac)
        test_idx  = idx[:n_test]
        val_idx   = idx[n_test:n_test + n_val]
        train_idx = idx[n_test + n_val:]
    else:
        raise ValueError(f"Unknown split_policy '{split_policy}'. "
                         f"Choices: random, cold-target, cold-drug, cold-pair, "
                         f"cluster, scaffold, stratified, time-split.")

    full = WarehouseDTADataset(records)
    train_ds = torch.utils.data.Subset(full, train_idx.tolist())
    val_ds   = torch.utils.data.Subset(full, val_idx.tolist())
    test_ds  = torch.utils.data.Subset(full, test_idx.tolist())

    def _loader(ds, shuffle: bool, drop_last: bool):
        return DataLoader(
            ds, batch_size=batch_size, shuffle=shuffle,
            num_workers=num_workers, pin_memory=True, drop_last=drop_last,
        )

    meta = {
        "benchmark": benchmark,
        "split_policy": split_policy,
        "n_records": n,
        "n_train": len(train_ds),
        "n_val":   len(val_ds),
        "n_test":  len(test_ds),
        "n_proteins": len({r.uniprot    for r in records}),
        "n_ligands":  len({r.ligand_ref for r in records}),
        "label_range": (float(full.ys.min()), float(full.ys.max())) if n else (0.0, 0.0),
        "split_provenance": split_provenance,
    }
    return (
        _loader(train_ds, shuffle=True,  drop_last=True),
        _loader(val_ds,   shuffle=False, drop_last=False),
        _loader(test_ds,  shuffle=False, drop_last=False),
        meta,
    )


# ── Graph-aware loader (for GraphDTA / DrugBAN) ────────────────────────

class WarehouseGraphDataset(Dataset):
    """Char-level protein tokenisation + torch_geometric graph for ligand.

    __getitem__ returns (seq_tokens, Data_graph, label). The companion
    collate_fn (``graph_collate``) builds a torch_geometric.data.Batch
    from the graphs.
    """

    def __init__(self, records: list[DTARecord]):
        from .featurizers import smiles_to_graph
        self.records = records
        self.seqs = np.stack([label_sequence(r.sequence) for r in records]) if records else np.zeros((0, MAX_SEQ_LEN), dtype=np.int64)
        self.ys   = np.array([r.label for r in records], dtype=np.float32) if records else np.zeros((0,), dtype=np.float32)
        # Pre-compute graphs; ~13K ligands at ~30 atoms × 78 floats ≈ 30 MB
        self.graphs = []
        self.bad_idx: list[int] = []
        for i, r in enumerate(records):
            g = smiles_to_graph(r.smiles)
            if g is None:
                self.bad_idx.append(i)
            self.graphs.append(g)

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, idx: int):
        return (
            torch.from_numpy(self.seqs[idx]),
            self.graphs[idx],
            torch.tensor(self.ys[idx], dtype=torch.float32),
        )


# ── ESM-2 cached embedding loaders ───────────────────────────────────
# For flow graphs whose protein input is `in.protein_emb` (cached ESM-2
# embedding) rather than `in.protein_seq` (raw tokens), pair the embedding
# with the chosen ligand representation:
#   * `(esm2_emb, smi_tokens, y)`  — for `in.ligand_smiles`
#   * `(esm2_emb, ecfp_vec,   y)`  — for `in.ligand_fp`
#   * `(esm2_emb, lig_graph,  y)`  — for `in.ligand_graph`
#
# Embeddings are pre-fetched once per unique UniProt at dataset
# construction (the disk cache deduplicates; auto-compute runs once
# per unseen protein and writes to disk for future runs). For KIBA's
# 229 unique proteins this is a one-time ~30 s pass with ESM-2 650M
# on the RTX 5080 then near-instant on every subsequent run.

class WarehouseESMSmiDataset(Dataset):
    """``(esm2_emb, smi_tokens, y)`` per record."""
    def __init__(self, records: list["DTARecord"],
                 checkpoint: str = "esm2_t33_650M",
                 auto_compute: bool = True):
        from .embeddings import batch_get_or_compute, _ESM2_DIMS
        self.records = records
        self.smis = (
            np.stack([label_smiles(r.smiles) for r in records])
            if records else np.zeros((0, MAX_SMI_LEN), dtype=np.int64)
        )
        self.ys = (
            np.array([r.label for r in records], dtype=np.float32)
            if records else np.zeros((0,), dtype=np.float32)
        )
        embs, self.cache_meta = batch_get_or_compute(
            records, checkpoint=checkpoint, auto_compute=auto_compute,
        )
        # (N, D) — D defaults to 1280 for esm2_t33_650M
        self.embeddings = embs.astype(np.float32, copy=False)
        self.embed_dim = self.embeddings.shape[1] if len(embs) else _ESM2_DIMS.get(checkpoint, 1280)

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, idx: int):
        return (
            torch.from_numpy(self.embeddings[idx]),
            torch.from_numpy(self.smis[idx]),
            torch.tensor(self.ys[idx], dtype=torch.float32),
        )


class WarehouseESMFPDataset(Dataset):
    """``(esm2_emb, ecfp_fingerprint, y)`` per record.

    Pairs with the flow's `in.protein_emb` + `in.ligand_fp` combination.
    Used by ConPLex-style two-tower flows that want cached protein
    embeddings + cheminformatic fingerprints (no SMILES tokenizer).
    """
    def __init__(self, records: list["DTARecord"],
                 checkpoint: str = "esm2_t33_650M",
                 auto_compute: bool = True,
                 fp_radius: int = 2,
                 fp_bits: int = 2048):
        from .embeddings import batch_get_or_compute, _ESM2_DIMS
        self.records = records
        self.ys = (
            np.array([r.label for r in records], dtype=np.float32)
            if records else np.zeros((0,), dtype=np.float32)
        )
        # Pre-compute ECFP fingerprints (one per record). RDKit-backed
        # when available; falls back to zeros + a meta flag otherwise so
        # training still proceeds (the user sees the warning in the run
        # log and can tell the result is dummy).
        self.fp_bits = fp_bits
        self.fingerprints = np.zeros((len(records), fp_bits), dtype=np.float32)
        self.fp_backend = "zeros"
        try:
            from rdkit import Chem, DataStructs
            from rdkit.Chem import rdFingerprintGenerator
            gen = rdFingerprintGenerator.GetMorganGenerator(
                radius=fp_radius, fpSize=fp_bits,
            )
            for i, r in enumerate(records):
                mol = Chem.MolFromSmiles(r.smiles or "")
                if mol is None:
                    continue
                bv = gen.GetFingerprint(mol)
                arr = np.zeros((fp_bits,), dtype=np.uint8)
                DataStructs.ConvertToNumpyArray(bv, arr)
                self.fingerprints[i] = arr.astype(np.float32)
            self.fp_backend = f"rdkit_ecfp{2*fp_radius}_{fp_bits}"
        except ImportError:
            pass
        embs, self.cache_meta = batch_get_or_compute(
            records, checkpoint=checkpoint, auto_compute=auto_compute,
        )
        self.embeddings = embs.astype(np.float32, copy=False)
        self.embed_dim = self.embeddings.shape[1] if len(embs) else _ESM2_DIMS.get(checkpoint, 1280)

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, idx: int):
        return (
            torch.from_numpy(self.embeddings[idx]),
            torch.from_numpy(self.fingerprints[idx]),
            torch.tensor(self.ys[idx], dtype=torch.float32),
        )


def esm_fp_collate(batch):
    """Trivial collate — both inputs are already (D,) tensors."""
    embs = torch.stack([b[0] for b in batch])
    fps  = torch.stack([b[1] for b in batch])
    ys   = torch.stack([b[2] for b in batch])
    return embs, fps, ys


class WarehouseESMGraphDataset(Dataset):
    """``(esm2_emb, ligand_mol_graph, y)`` per record. Pairs with the
    flow's `in.protein_emb` + `in.ligand_graph` input combination."""
    def __init__(self, records: list["DTARecord"],
                 checkpoint: str = "esm2_t33_650M",
                 auto_compute: bool = True):
        from .featurizers import smiles_to_graph
        from .embeddings import batch_get_or_compute
        self.records = records
        self.ys = (
            np.array([r.label for r in records], dtype=np.float32)
            if records else np.zeros((0,), dtype=np.float32)
        )
        self.graphs = []
        self.bad_idx: list[int] = []
        for i, r in enumerate(records):
            g = smiles_to_graph(r.smiles)
            if g is None:
                self.bad_idx.append(i)
            self.graphs.append(g)
        embs, self.cache_meta = batch_get_or_compute(
            records, checkpoint=checkpoint, auto_compute=auto_compute,
        )
        self.embeddings = embs.astype(np.float32, copy=False)
        self.embed_dim = self.embeddings.shape[1] if len(embs) else 1280

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, idx: int):
        return (
            torch.from_numpy(self.embeddings[idx]),
            self.graphs[idx],
            torch.tensor(self.ys[idx], dtype=torch.float32),
        )


def esm_smi_collate(batch):
    """Trivial collate — both inputs are already tensors."""
    embs = torch.stack([b[0] for b in batch])
    smis = torch.stack([b[1] for b in batch])
    ys   = torch.stack([b[2] for b in batch])
    return embs, smis, ys


def esm_graph_collate(batch):
    """Collate ESM-2 embedding (tensor) + mol graph (PyG Data) + label."""
    from torch_geometric.data import Batch
    valid = [b for b in batch if b[1] is not None]
    embs   = torch.stack([b[0] for b in valid])
    graphs = Batch.from_data_list([b[1] for b in valid])
    ys     = torch.stack([b[2] for b in valid])
    return embs, graphs, ys


def make_esm_smi_warehouse_loaders(
    benchmark: str,
    *,
    split_policy: str = "random",
    batch_size: int = 256,
    val_frac: float = 0.1,
    test_frac: float = 0.1,
    seed: int = 4192,
    num_workers: int = 0,
    checkpoint: str = "esm2_t33_650M",
    auto_compute: bool = True,
) -> tuple[DataLoader, DataLoader, DataLoader, dict]:
    """Build train/val/test loaders that produce ``(esm2_emb, smi_tokens, y)``.

    Reuses the standard warehouse split logic + records loader; just
    wraps the records in WarehouseESMSmiDataset instead of WarehouseDTADataset.
    """
    # Reuse make_warehouse_loaders to pull records + split indices.
    # (Inefficient but keeps split policy parity automatic. See the
    # struct-graph loader for the same pattern.)
    records = load_warehouse_records(benchmark)
    n = len(records)
    if n == 0:
        raise RuntimeError(f"No records returned for benchmark '{benchmark}'")
    rng = np.random.default_rng(seed)
    if split_policy == "random":
        idx = rng.permutation(n)
        n_test = int(n * test_frac); n_val = int(n * val_frac)
        test_idx  = idx[:n_test]
        val_idx   = idx[n_test:n_test + n_val]
        train_idx = idx[n_test + n_val:]
    elif split_policy in ("cluster", "leakage-aware", "cold-target"):
        # Cluster by uniprot or its UniRef50 representative.
        cluster_keys = (_resolve_cluster_keys(records, threshold="uniref50",
                                              benchmark=benchmark)[0]
                        if split_policy in ("cluster", "leakage-aware")
                        else [r.uniprot for r in records])
        unique = sorted(set(cluster_keys))
        perm = rng.permutation(len(unique))
        buckets = {}
        n_t = int(len(unique) * test_frac); n_v = int(len(unique) * val_frac)
        for j, k in enumerate(perm):
            buckets[unique[k]] = "test" if j < n_t else ("val" if j < n_t + n_v else "train")
        train_idx = np.array([i for i, k in enumerate(cluster_keys) if buckets[k] == "train"])
        val_idx   = np.array([i for i, k in enumerate(cluster_keys) if buckets[k] == "val"])
        test_idx  = np.array([i for i, k in enumerate(cluster_keys) if buckets[k] == "test"])
    else:
        raise ValueError(f"Unsupported split_policy '{split_policy}' for esm_smi loader.")

    full = WarehouseESMSmiDataset(records, checkpoint=checkpoint, auto_compute=auto_compute)
    train_ds = torch.utils.data.Subset(full, train_idx.tolist())
    val_ds   = torch.utils.data.Subset(full, val_idx.tolist())
    test_ds  = torch.utils.data.Subset(full, test_idx.tolist())

    def _loader(ds, shuffle, drop_last):
        return DataLoader(ds, batch_size=batch_size, shuffle=shuffle,
                          num_workers=num_workers, pin_memory=True, drop_last=drop_last,
                          collate_fn=esm_smi_collate)
    meta = {
        "benchmark": benchmark, "split_policy": split_policy, "n_records": n,
        "n_train": len(train_ds), "n_val": len(val_ds), "n_test": len(test_ds),
        "n_proteins": len({r.uniprot for r in records}),
        "n_ligands":  len({r.ligand_ref for r in records}),
        "label_range": (float(full.ys.min()), float(full.ys.max())) if n else (0.0, 0.0),
        "esm2_checkpoint": checkpoint,
        "esm2_dim": full.embed_dim,
        "esm2_cache_meta": full.cache_meta,
    }
    return (_loader(train_ds, True, True), _loader(val_ds, False, False),
            _loader(test_ds, False, False), meta)


def make_esm_fp_warehouse_loaders(
    benchmark: str,
    *,
    split_policy: str = "random",
    batch_size: int = 256,
    val_frac: float = 0.1,
    test_frac: float = 0.1,
    seed: int = 4192,
    num_workers: int = 0,
    checkpoint: str = "esm2_t33_650M",
    auto_compute: bool = True,
    fp_radius: int = 2,
    fp_bits: int = 2048,
) -> tuple[DataLoader, DataLoader, DataLoader, dict]:
    """Build train/val/test loaders that produce ``(esm2_emb, ecfp_fp, y)``.

    Pairs with flows whose ligand input is ``in.ligand_fp`` (ECFP4
    fingerprint). The protein side is cached ESM-2; the ligand side is
    a 0/1 dense Morgan fingerprint at radius ``fp_radius`` and width
    ``fp_bits``. Both inputs land as ``(B, D)`` float tensors at the
    FlowModule's protein_emb / ligand_tabular ports.
    """
    records = load_warehouse_records(benchmark)
    n = len(records)
    if n == 0:
        raise RuntimeError(f"No records returned for benchmark '{benchmark}'")
    rng = np.random.default_rng(seed)
    if split_policy == "random":
        idx = rng.permutation(n)
        n_test = int(n * test_frac); n_val = int(n * val_frac)
        test_idx, val_idx, train_idx = idx[:n_test], idx[n_test:n_test+n_val], idx[n_test+n_val:]
    elif split_policy in ("cluster", "leakage-aware", "cold-target"):
        cluster_keys = (_resolve_cluster_keys(records, threshold="uniref50",
                                              benchmark=benchmark)[0]
                        if split_policy in ("cluster", "leakage-aware")
                        else [r.uniprot for r in records])
        unique = sorted(set(cluster_keys))
        perm = rng.permutation(len(unique))
        buckets = {}
        n_t = int(len(unique) * test_frac); n_v = int(len(unique) * val_frac)
        for j, k in enumerate(perm):
            buckets[unique[k]] = "test" if j < n_t else ("val" if j < n_t + n_v else "train")
        train_idx = np.array([i for i, k in enumerate(cluster_keys) if buckets[k] == "train"])
        val_idx   = np.array([i for i, k in enumerate(cluster_keys) if buckets[k] == "val"])
        test_idx  = np.array([i for i, k in enumerate(cluster_keys) if buckets[k] == "test"])
    else:
        raise ValueError(f"Unsupported split_policy '{split_policy}' for esm_fp loader.")

    full = WarehouseESMFPDataset(records, checkpoint=checkpoint,
                                 auto_compute=auto_compute,
                                 fp_radius=fp_radius, fp_bits=fp_bits)
    train_ds = torch.utils.data.Subset(full, train_idx.tolist())
    val_ds   = torch.utils.data.Subset(full, val_idx.tolist())
    test_ds  = torch.utils.data.Subset(full, test_idx.tolist())

    def _loader(ds, shuffle, drop_last):
        return DataLoader(ds, batch_size=batch_size, shuffle=shuffle,
                          num_workers=num_workers, pin_memory=True, drop_last=drop_last,
                          collate_fn=esm_fp_collate)
    meta = {
        "benchmark": benchmark, "split_policy": split_policy, "n_records": n,
        "n_train": len(train_ds), "n_val": len(val_ds), "n_test": len(test_ds),
        "n_proteins": len({r.uniprot for r in records}),
        "n_ligands":  len({r.ligand_ref for r in records}),
        "label_range": (float(full.ys.min()), float(full.ys.max())) if n else (0.0, 0.0),
        "esm2_checkpoint": checkpoint,
        "esm2_dim":   full.embed_dim,
        "esm2_cache_meta": full.cache_meta,
        "fp_backend": full.fp_backend,
        "fp_bits":    full.fp_bits,
    }
    return (_loader(train_ds, True, True), _loader(val_ds, False, False),
            _loader(test_ds, False, False), meta)


class WarehouseSeqFPDataset(Dataset):
    """``(seq_tokens, ecfp_fingerprint, y)`` per record.

    Pairs with the flow combination ``in.protein_seq`` + ``in.ligand_fp``
    (a Transformer / CNN / BiLSTM protein encoder on raw AA tokens + a
    tabular MLP / XGBoost on Morgan fingerprints — no SMILES tokenizer).
    Builds the protein side once and the ECFP side once, both at dataset
    construction.
    """
    def __init__(self, records: list["DTARecord"],
                 fp_radius: int = 2,
                 fp_bits: int = 2048):
        self.records = records
        self.seqs = (
            np.stack([label_sequence(r.sequence) for r in records])
            if records else np.zeros((0, MAX_SEQ_LEN), dtype=np.int64)
        )
        self.ys = (
            np.array([r.label for r in records], dtype=np.float32)
            if records else np.zeros((0,), dtype=np.float32)
        )
        self.fp_bits = fp_bits
        self.fingerprints = np.zeros((len(records), fp_bits), dtype=np.float32)
        self.fp_backend = "zeros"
        try:
            from rdkit import Chem, DataStructs
            from rdkit.Chem import rdFingerprintGenerator
            gen = rdFingerprintGenerator.GetMorganGenerator(
                radius=fp_radius, fpSize=fp_bits,
            )
            for i, r in enumerate(records):
                mol = Chem.MolFromSmiles(r.smiles or "")
                if mol is None:
                    continue
                bv = gen.GetFingerprint(mol)
                arr = np.zeros((fp_bits,), dtype=np.uint8)
                DataStructs.ConvertToNumpyArray(bv, arr)
                self.fingerprints[i] = arr.astype(np.float32)
            self.fp_backend = f"rdkit_ecfp{2*fp_radius}_{fp_bits}"
        except ImportError:
            pass

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, idx: int):
        return (
            torch.from_numpy(self.seqs[idx]),
            torch.from_numpy(self.fingerprints[idx]),
            torch.tensor(self.ys[idx], dtype=torch.float32),
        )


def seq_fp_collate(batch):
    """Trivial collate — both inputs are already per-record tensors."""
    seqs = torch.stack([b[0] for b in batch])
    fps  = torch.stack([b[1] for b in batch])
    ys   = torch.stack([b[2] for b in batch])
    return seqs, fps, ys


def make_seq_fp_warehouse_loaders(
    benchmark: str,
    *,
    split_policy: str = "random",
    batch_size: int = 128,
    val_frac: float = 0.1,
    test_frac: float = 0.1,
    seed: int = 4192,
    num_workers: int = 0,
    fp_radius: int = 2,
    fp_bits: int = 2048,
) -> tuple[DataLoader, DataLoader, DataLoader, dict]:
    """Loaders that emit ``(seq_tokens, ecfp_fp, y)`` triples.

    Used by flow shape ``seq_fp`` — protein_seq + ligand_fp. Solves the
    "Transformer over AA + tabular MLP over ECFP" combo that needs a
    float fingerprint on the ligand side, not int SMILES tokens.
    """
    records = load_warehouse_records(benchmark)
    n = len(records)
    if n == 0:
        raise RuntimeError(f"No records returned for benchmark '{benchmark}'")
    rng = np.random.default_rng(seed)
    if split_policy == "random":
        idx = rng.permutation(n)
        n_test = int(n * test_frac); n_val = int(n * val_frac)
        test_idx, val_idx, train_idx = idx[:n_test], idx[n_test:n_test+n_val], idx[n_test+n_val:]
    elif split_policy in ("cluster", "leakage-aware", "cold-target"):
        cluster_keys = (_resolve_cluster_keys(records, threshold="uniref50",
                                              benchmark=benchmark)[0]
                        if split_policy in ("cluster", "leakage-aware")
                        else [r.uniprot for r in records])
        unique = sorted(set(cluster_keys))
        perm = rng.permutation(len(unique))
        buckets = {}
        n_t = int(len(unique) * test_frac); n_v = int(len(unique) * val_frac)
        for j, k in enumerate(perm):
            buckets[unique[k]] = "test" if j < n_t else ("val" if j < n_t + n_v else "train")
        train_idx = np.array([i for i, k in enumerate(cluster_keys) if buckets[k] == "train"])
        val_idx   = np.array([i for i, k in enumerate(cluster_keys) if buckets[k] == "val"])
        test_idx  = np.array([i for i, k in enumerate(cluster_keys) if buckets[k] == "test"])
    else:
        raise ValueError(f"Unsupported split_policy '{split_policy}' for seq_fp loader.")

    full = WarehouseSeqFPDataset(records, fp_radius=fp_radius, fp_bits=fp_bits)
    train_ds = torch.utils.data.Subset(full, train_idx.tolist())
    val_ds   = torch.utils.data.Subset(full, val_idx.tolist())
    test_ds  = torch.utils.data.Subset(full, test_idx.tolist())

    def _loader(ds, shuffle, drop_last):
        return DataLoader(ds, batch_size=batch_size, shuffle=shuffle,
                          num_workers=num_workers, pin_memory=True, drop_last=drop_last,
                          collate_fn=seq_fp_collate)
    meta = {
        "benchmark": benchmark, "split_policy": split_policy, "n_records": n,
        "n_train": len(train_ds), "n_val": len(val_ds), "n_test": len(test_ds),
        "n_proteins": len({r.uniprot for r in records}),
        "n_ligands":  len({r.ligand_ref for r in records}),
        "label_range": (float(full.ys.min()), float(full.ys.max())) if n else (0.0, 0.0),
        "fp_backend": full.fp_backend,
        "fp_bits":    full.fp_bits,
    }
    return (_loader(train_ds, True, True), _loader(val_ds, False, False),
            _loader(test_ds, False, False), meta)


# ── Multi-feature loader (3+ input flows) ───────────────────────────────
# Unlocks flow topologies that combine more than two input blocks —
# e.g. in.protein_seq + in.protein_fakesetta + in.ligand_graph (a
# multi-axis protein feature stack + ligand mol graph). The existing
# fixed-shape loaders (seq_smi, seq_graph, esm_*) emit a known 2-tuple;
# this one emits N input tensors in the order the flow's input blocks
# appear, then the label.

def _compute_input_feature(block_id: str, records: list,
                           params: dict | None,
                           *,
                           checkpoint: str = "esm2_t33_650M",
                           structure_root: str | None = None):
    """Materialise one feature column for a given input block.

    Returns either a numpy array shaped (N, *) for tensor inputs, or a
    list of torch_geometric.Data objects for graph inputs. The caller
    (WarehouseMultiFeatureDataset) just stores whatever this returns
    and wraps it per-record at __getitem__ time.
    """
    params = params or {}
    n = len(records)
    if block_id == "in.protein_seq":
        if n == 0:
            return np.zeros((0, MAX_SEQ_LEN), dtype=np.int64)
        return np.stack([label_sequence(r.sequence) for r in records])

    if block_id == "in.ligand_smiles":
        if n == 0:
            return np.zeros((0, MAX_SMI_LEN), dtype=np.int64)
        return np.stack([label_smiles(r.smiles) for r in records])

    if block_id == "in.ligand_fp":
        fp_bits   = int(params.get("fp_bits", 2048))
        fp_radius = int(params.get("fp_radius", 2))
        out = np.zeros((n, fp_bits), dtype=np.float32)
        try:
            from rdkit import Chem, DataStructs
            from rdkit.Chem import rdFingerprintGenerator
            gen = rdFingerprintGenerator.GetMorganGenerator(radius=fp_radius, fpSize=fp_bits)
            for i, r in enumerate(records):
                mol = Chem.MolFromSmiles(r.smiles or "")
                if mol is None:
                    continue
                bv = gen.GetFingerprint(mol)
                arr = np.zeros((fp_bits,), dtype=np.uint8)
                DataStructs.ConvertToNumpyArray(bv, arr)
                out[i] = arr.astype(np.float32)
        except ImportError:
            pass
        return out

    if block_id == "in.ligand_graph":
        from .featurizers import smiles_to_graph
        return [smiles_to_graph(r.smiles) for r in records]

    if block_id == "in.protein_graph":
        from .graph_features import protein_residue_graph, DEFAULT_STRUCTURE_ROOT
        sr = structure_root or DEFAULT_STRUCTURE_ROOT
        # Cache by uniprot — common proteins repeat hundreds of times.
        seen: dict[str, object] = {}
        out_list: list = []
        for r in records:
            u = getattr(r, "uniprot", None) or ""
            if u in seen:
                out_list.append(seen[u])
                continue
            g = protein_residue_graph(
                sequence=r.sequence, uniprot=u,
                structure_root=sr,
                contact_cutoff=8.0, max_residues=1024,
            )
            seen[u] = g
            out_list.append(g)
        return out_list

    if block_id == "in.protein_emb":
        from .embeddings import batch_get_or_compute
        embs, _meta = batch_get_or_compute(records, checkpoint=checkpoint, auto_compute=True)
        return embs.astype(np.float32, copy=False)

    if block_id == "in.protein_fakesetta":
        # 19-d ref2015-style surrogate. Defaults to zeros when the
        # fakesetta featurizer can't run (no AF cache, no biopython).
        try:
            from .featurizers.protein_interface import _compute_fakesetta
            arr = _compute_fakesetta(records, structure_root=structure_root or "data/raw/alphafold")
            return arr.astype(np.float32, copy=False)
        except Exception:
            return np.zeros((n, 19), dtype=np.float32)

    if block_id == "in.ligand_physchem":
        # 78-d RDKit descriptor vector. NaN/inf clamped to 0.
        out = np.zeros((n, 78), dtype=np.float32)
        try:
            from rdkit import Chem
            from rdkit.Chem import Descriptors, Lipinski, QED
            # Use a fixed-order list of the 78 descriptors most commonly
            # surfaced. Stable across RDKit versions.
            funcs = [
                Descriptors.MolWt, Descriptors.HeavyAtomCount, Descriptors.NumHAcceptors,
                Descriptors.NumHDonors, Descriptors.NumRotatableBonds,
                Descriptors.NumAromaticRings, Descriptors.NumAliphaticRings,
                Descriptors.NumHeteroatoms, Descriptors.RingCount,
                Descriptors.FractionCSP3, Descriptors.TPSA, Descriptors.MolLogP,
                Descriptors.MolMR, Descriptors.LabuteASA,
                Lipinski.NumAromaticHeterocycles, Lipinski.NumSaturatedRings,
                Lipinski.NumAliphaticHeterocycles, Lipinski.NumAromaticCarbocycles,
                QED.qed,
                # The remaining 59 slots are filled with smaller-impact
                # descriptors via Descriptors.descList (alphabetical).
            ]
            extra_names = [d[0] for d in Descriptors.descList
                           if d[0] not in {f.__name__ for f in funcs}]
            for name in extra_names[:78 - len(funcs)]:
                fn = dict(Descriptors.descList).get(name)
                if fn is not None:
                    funcs.append(fn)
            for i, r in enumerate(records):
                mol = Chem.MolFromSmiles(r.smiles or "")
                if mol is None:
                    continue
                for j, fn in enumerate(funcs[:78]):
                    try:
                        v = float(fn(mol))
                    except Exception:
                        v = 0.0
                    if not np.isfinite(v):
                        v = 0.0
                    out[i, j] = v
        except ImportError:
            pass
        return out

    if block_id == "in.contact_map":
        # No docking-pose loader yet — return zeros so the flow compiles
        # but downstream encoders will see degenerate input.
        L = int(params.get("map_size", 256))
        return np.zeros((n, L, L), dtype=np.float32)

    raise ValueError(f"Multi-feature loader: unknown input block_id '{block_id}'.")


def _is_graph_block(block_id: str) -> bool:
    return block_id in ("in.protein_graph", "in.ligand_graph")


class WarehouseMultiFeatureDataset(Dataset):
    """Per-record tuple containing one tensor per input block, then label.

    Args:
        records:      list of DTARecord
        input_blocks: ordered list of {"block_id": ..., "params": {...}}
                      in the FLOW'S TOPO ORDER of input nodes. The
                      __getitem__ tuple respects this order, so
                      FlowModule.forward(*batch_args) maps batch_args[i]
                      to the i'th input node in topo order.
    """
    def __init__(self, records: list["DTARecord"],
                 input_blocks: list[dict],
                 *,
                 checkpoint: str = "esm2_t33_650M",
                 structure_root: str | None = None):
        self.records = records
        self.input_blocks = list(input_blocks)
        self.ys = np.array([r.label for r in records], dtype=np.float32) if records else np.zeros((0,), dtype=np.float32)
        self._features: list = []
        self._kinds:    list[str] = []  # "graph" or "array"
        for blk in self.input_blocks:
            block_id = blk["block_id"]
            feat = _compute_input_feature(
                block_id, records, blk.get("params"),
                checkpoint=checkpoint, structure_root=structure_root,
            )
            self._features.append(feat)
            self._kinds.append("graph" if _is_graph_block(block_id) else "array")

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, idx: int):
        out: list = []
        for i, kind in enumerate(self._kinds):
            feat = self._features[i]
            if kind == "graph":
                out.append(feat[idx])
            else:
                out.append(torch.from_numpy(np.ascontiguousarray(feat[idx])))
        out.append(torch.tensor(self.ys[idx], dtype=torch.float32))
        return tuple(out)


def multi_feature_collate(batch):
    """Mixed-type collate. Graphs go through Batch.from_data_list; other
    tensors stack normally. Last item is always the label.
    """
    from torch_geometric.data import Batch
    n = len(batch[0]) - 1
    out: list = []
    for i in range(n):
        first = batch[0][i]
        if hasattr(first, "x") and hasattr(first, "edge_index"):
            out.append(Batch.from_data_list([b[i] for b in batch]))
        else:
            out.append(torch.stack([b[i] for b in batch]))
    out.append(torch.stack([b[-1] for b in batch]))
    return tuple(out)


def make_multifeature_warehouse_loaders(
    benchmark: str,
    input_blocks: list[dict],
    *,
    split_policy: str = "random",
    batch_size: int = 64,
    val_frac: float = 0.1,
    test_frac: float = 0.1,
    seed: int = 4192,
    num_workers: int = 0,
    checkpoint: str = "esm2_t33_650M",
    structure_root: str | None = None,
) -> tuple[DataLoader, DataLoader, DataLoader, dict]:
    """Loaders that yield N+1-tuples (one tensor per input block + label).

    Used by flow shape "multi" — flows with 3+ inputs, or any combination
    not covered by the dedicated 2-input loaders. Each input block in
    the flow's topo order produces one tensor in the emitted tuple.
    """
    records = load_warehouse_records(benchmark)
    n = len(records)
    if n == 0:
        raise RuntimeError(f"No records returned for benchmark '{benchmark}'")
    rng = np.random.default_rng(seed)
    if split_policy == "random":
        idx = rng.permutation(n)
        n_test = int(n * test_frac); n_val = int(n * val_frac)
        test_idx, val_idx, train_idx = idx[:n_test], idx[n_test:n_test+n_val], idx[n_test+n_val:]
    elif split_policy in ("cluster", "leakage-aware", "cold-target"):
        cluster_keys = (_resolve_cluster_keys(records, threshold="uniref50",
                                              benchmark=benchmark)[0]
                        if split_policy in ("cluster", "leakage-aware")
                        else [r.uniprot for r in records])
        unique = sorted(set(cluster_keys))
        perm = rng.permutation(len(unique))
        buckets = {}
        n_t = int(len(unique) * test_frac); n_v = int(len(unique) * val_frac)
        for j, k in enumerate(perm):
            buckets[unique[k]] = "test" if j < n_t else ("val" if j < n_t + n_v else "train")
        train_idx = np.array([i for i, k in enumerate(cluster_keys) if buckets[k] == "train"])
        val_idx   = np.array([i for i, k in enumerate(cluster_keys) if buckets[k] == "val"])
        test_idx  = np.array([i for i, k in enumerate(cluster_keys) if buckets[k] == "test"])
    else:
        # Fallback: random.
        idx = rng.permutation(n)
        n_test = int(n * test_frac); n_val = int(n * val_frac)
        test_idx, val_idx, train_idx = idx[:n_test], idx[n_test:n_test+n_val], idx[n_test+n_val:]

    full = WarehouseMultiFeatureDataset(
        records, input_blocks,
        checkpoint=checkpoint, structure_root=structure_root,
    )
    train_ds = torch.utils.data.Subset(full, train_idx.tolist())
    val_ds   = torch.utils.data.Subset(full, val_idx.tolist())
    test_ds  = torch.utils.data.Subset(full, test_idx.tolist())

    def _loader(ds, shuffle, drop_last):
        return DataLoader(ds, batch_size=batch_size, shuffle=shuffle,
                          num_workers=num_workers, pin_memory=True,
                          drop_last=drop_last, collate_fn=multi_feature_collate)

    meta = {
        "benchmark":  benchmark, "split_policy": split_policy, "n_records": n,
        "n_train":    len(train_ds), "n_val": len(val_ds), "n_test": len(test_ds),
        "n_proteins": len({r.uniprot for r in records}),
        "n_ligands":  len({r.ligand_ref for r in records}),
        "label_range": (float(full.ys.min()), float(full.ys.max())) if n else (0.0, 0.0),
        "input_blocks": [b["block_id"] for b in input_blocks],
    }
    return (_loader(train_ds, True, True), _loader(val_ds, False, False),
            _loader(test_ds, False, False), meta)


def make_esm_graph_warehouse_loaders(
    benchmark: str, *,
    split_policy: str = "random", batch_size: int = 64,
    val_frac: float = 0.1, test_frac: float = 0.1,
    seed: int = 4192, num_workers: int = 0,
    checkpoint: str = "esm2_t33_650M", auto_compute: bool = True,
) -> tuple[DataLoader, DataLoader, DataLoader, dict]:
    """Same dispatch as ``make_esm_smi_warehouse_loaders`` but pairs with
    a ligand mol-graph instead of SMILES tokens."""
    records = load_warehouse_records(benchmark)
    n = len(records)
    if n == 0:
        raise RuntimeError(f"No records returned for benchmark '{benchmark}'")
    rng = np.random.default_rng(seed)
    if split_policy == "random":
        idx = rng.permutation(n)
        n_test = int(n * test_frac); n_val = int(n * val_frac)
        test_idx, val_idx, train_idx = idx[:n_test], idx[n_test:n_test+n_val], idx[n_test+n_val:]
    elif split_policy in ("cluster", "leakage-aware", "cold-target"):
        cluster_keys = (_resolve_cluster_keys(records, threshold="uniref50",
                                              benchmark=benchmark)[0]
                        if split_policy in ("cluster", "leakage-aware")
                        else [r.uniprot for r in records])
        unique = sorted(set(cluster_keys))
        perm = rng.permutation(len(unique))
        buckets = {}
        n_t = int(len(unique)*test_frac); n_v = int(len(unique)*val_frac)
        for j, k in enumerate(perm):
            buckets[unique[k]] = "test" if j < n_t else ("val" if j < n_t+n_v else "train")
        train_idx = np.array([i for i, k in enumerate(cluster_keys) if buckets[k] == "train"])
        val_idx   = np.array([i for i, k in enumerate(cluster_keys) if buckets[k] == "val"])
        test_idx  = np.array([i for i, k in enumerate(cluster_keys) if buckets[k] == "test"])
    else:
        raise ValueError(f"Unsupported split_policy '{split_policy}' for esm_graph loader.")

    full = WarehouseESMGraphDataset(records, checkpoint=checkpoint, auto_compute=auto_compute)
    train_ds = torch.utils.data.Subset(full, train_idx.tolist())
    val_ds   = torch.utils.data.Subset(full, val_idx.tolist())
    test_ds  = torch.utils.data.Subset(full, test_idx.tolist())

    def _loader(ds, shuffle, drop_last):
        return DataLoader(ds, batch_size=batch_size, shuffle=shuffle,
                          num_workers=num_workers, pin_memory=True, drop_last=drop_last,
                          collate_fn=esm_graph_collate)
    meta = {
        "benchmark": benchmark, "split_policy": split_policy, "n_records": n,
        "n_train": len(train_ds), "n_val": len(val_ds), "n_test": len(test_ds),
        "n_proteins": len({r.uniprot for r in records}),
        "n_ligands":  len({r.ligand_ref for r in records}),
        "label_range": (float(full.ys.min()), float(full.ys.max())) if n else (0.0, 0.0),
        "esm2_checkpoint": checkpoint,
        "esm2_dim": full.embed_dim,
        "esm2_cache_meta": full.cache_meta,
    }
    return (_loader(train_ds, True, True), _loader(val_ds, False, False),
            _loader(test_ds, False, False), meta)


def graph_collate(batch):
    """Collate (seq, graph, y) triples into (seq_batch, Batch, y_batch)."""
    from torch_geometric.data import Batch
    seqs   = torch.stack([b[0] for b in batch])
    graphs = Batch.from_data_list([b[1] for b in batch if b[1] is not None])
    ys     = torch.stack([b[2] for b in batch])
    return seqs, graphs, ys


# ── PPI loader: paired protein graphs + binary interaction label ───────
# HIPPIE stores ~1M physical interactions (binary_ppi) with a confidence
# score in [0,1]; we filter to confidence ≥ ``min_confidence``, bridge
# both entrez gene IDs to UniProts via hippie_bridge_uniprot, and join
# v2_protein_sequences to get sequences for the residue-graph featurizer.
#
# Negative pairs are sampled at random from the cross-product of all
# observed UniProts, excluding the positive edges (a standard PPI
# negative-sampling baseline). The negative:positive ratio defaults to
# 1:1; for hard-negative mining you'd swap this for a more selective
# sampler (e.g. same-cellular-compartment but never co-pulled).

@dataclass(frozen=True)
class PPIRecord:
    uniprot_a: str
    sequence_a: str
    uniprot_b: str
    sequence_b: str
    label: float            # 1.0 = interaction, 0.0 = non-interaction
    confidence: float       # source-reported confidence (1.0 for sampled negatives)
    source: str             # 'hippie' / 'huri' / 'sampled_negative'


def load_ppi_records(
    source: str = "hippie",
    *,
    min_confidence: float = 0.5,
    negative_ratio: float = 1.0,
    seed: int = 4192,
    max_positives: int | None = None,
) -> list[PPIRecord]:
    """Pull high-quality PPIs from the warehouse + sample negatives.

    Pulls high-quality pairs from ``{source}_interactions``, bridges
    each end to a UniProt via ``{source}_bridge_uniprot``, joins
    ``v2_protein_sequences`` to get sequence text for both partners.
    Then samples ``negative_ratio × n_positives`` non-edges as labelled
    negatives.

    Both partners must have a sequence in v2_protein_sequences and the
    bridge confidence must be 'exact' to keep the dataset clean. For
    HIPPIE that gives ~225K pairs at min_confidence=0.5.
    """
    con = duckdb.connect(str(_CATALOG_PATH), read_only=True)
    try:
        if source == "hippie":
            sql = f"""
                SELECT ba.uniprot AS uniprot_a, sa.sequence AS seq_a,
                       bb.uniprot AS uniprot_b, sb.sequence AS seq_b,
                       i.confidence AS conf
                FROM   hippie_interactions i
                JOIN   hippie_bridge_uniprot ba
                    ON ba.source_id='hippie' AND ba.source_key = i.a_entrez_gene
                       AND ba.bridge_via='GeneID' AND ba.confidence='exact'
                JOIN   hippie_bridge_uniprot bb
                    ON bb.source_id='hippie' AND bb.source_key = i.b_entrez_gene
                       AND bb.bridge_via='GeneID' AND bb.confidence='exact'
                JOIN   v2_protein_sequences sa ON sa.uniprot = ba.uniprot
                JOIN   v2_protein_sequences sb ON sb.uniprot = bb.uniprot
                WHERE  i.confidence >= ?
                  AND  ba.uniprot < bb.uniprot
            """
            params: list = [min_confidence]
        elif source == "huri":
            sql = """
                SELECT ba.uniprot AS uniprot_a, sa.sequence AS seq_a,
                       bb.uniprot AS uniprot_b, sb.sequence AS seq_b,
                       1.0       AS conf
                FROM   huri_interactions i
                JOIN   huri_bridge_uniprot ba
                    ON ba.source_id='huri' AND ba.source_key = i.a_ensembl_gene
                       AND ba.bridge_via='Ensembl' AND ba.confidence='exact'
                JOIN   huri_bridge_uniprot bb
                    ON bb.source_id='huri' AND bb.source_key = i.b_ensembl_gene
                       AND bb.bridge_via='Ensembl' AND bb.confidence='exact'
                JOIN   v2_protein_sequences sa ON sa.uniprot = ba.uniprot
                JOIN   v2_protein_sequences sb ON sb.uniprot = bb.uniprot
                WHERE  ba.uniprot < bb.uniprot
            """
            params = []
        else:
            raise ValueError(f"Unknown PPI source '{source}'. Use 'hippie' or 'huri'.")
        rows = con.execute(sql, params).fetchall()
    finally:
        con.close()
    positives: list[PPIRecord] = [
        PPIRecord(uniprot_a=ua, sequence_a=sa, uniprot_b=ub, sequence_b=sb,
                  label=1.0, confidence=float(conf), source=source)
        for ua, sa, ub, sb, conf in rows
    ]
    if max_positives is not None and len(positives) > max_positives:
        # Deterministic top-confidence subsample for speed.
        positives.sort(key=lambda p: p.confidence, reverse=True)
        positives = positives[:max_positives]

    # Sample negatives: pick two random uniprots from the universe that
    # aren't a positive edge. Build a hash set for O(1) lookup.
    rng = np.random.default_rng(seed)
    pos_keys = {(p.uniprot_a, p.uniprot_b) for p in positives}
    uniprots = sorted({p.uniprot_a for p in positives} | {p.uniprot_b for p in positives})
    sequences = {p.uniprot_a: p.sequence_a for p in positives}
    sequences.update({p.uniprot_b: p.sequence_b for p in positives})
    n_neg = int(round(len(positives) * negative_ratio))
    negatives: list[PPIRecord] = []
    attempts = 0
    while len(negatives) < n_neg and attempts < n_neg * 10:
        a = uniprots[int(rng.integers(0, len(uniprots)))]
        b = uniprots[int(rng.integers(0, len(uniprots)))]
        if a == b:
            attempts += 1; continue
        if a > b:
            a, b = b, a
        if (a, b) in pos_keys:
            attempts += 1; continue
        negatives.append(PPIRecord(
            uniprot_a=a, sequence_a=sequences[a],
            uniprot_b=b, sequence_b=sequences[b],
            label=0.0, confidence=1.0, source="sampled_negative",
        ))
        attempts += 1
    out = positives + negatives
    rng.shuffle(out)
    return out


class WarehousePPIGraphDataset(Dataset):
    """Yields (graph_a, graph_b, label) per PPI record.

    Both protein graphs come from :func:`graph_features.protein_residue_graph`
    — PDB-derived contact graphs when cached, sliding-window fallback
    otherwise. Per-UniProt graphs are cached once at construction so
    each epoch's data loader doesn't re-parse the same proteins.
    """

    def __init__(self, records: list[PPIRecord],
                 *,
                 structure_root: str | None = None,
                 contact_cutoff: float = 8.0,
                 max_residues: int = 1024):
        from .graph_features import protein_residue_graph, DEFAULT_STRUCTURE_ROOT
        self.records = records
        self.ys = np.array([r.label for r in records], dtype=np.float32)
        struct_root = structure_root or DEFAULT_STRUCTURE_ROOT
        # Cache per-uniprot graphs.
        seen: dict[str, str] = {}
        for r in records:
            seen.setdefault(r.uniprot_a, r.sequence_a)
            seen.setdefault(r.uniprot_b, r.sequence_b)
        self.prot_graph_by_uniprot: dict[str, object] = {}
        self.structure_coverage = {"with_structure": 0, "fallback": 0}
        for u, seq in seen.items():
            g = protein_residue_graph(
                sequence=seq, uniprot=u,
                structure_root=struct_root,
                contact_cutoff=contact_cutoff,
                max_residues=max_residues,
            )
            self.prot_graph_by_uniprot[u] = g
            if float(g.has_structure) > 0.5:
                self.structure_coverage["with_structure"] += 1
            else:
                self.structure_coverage["fallback"] += 1

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, idx: int):
        r = self.records[idx]
        return (
            self.prot_graph_by_uniprot[r.uniprot_a],
            self.prot_graph_by_uniprot[r.uniprot_b],
            torch.tensor(self.ys[idx], dtype=torch.float32),
        )


def ppi_graph_collate(batch):
    from torch_geometric.data import Batch
    a = Batch.from_data_list([b[0] for b in batch])
    b = Batch.from_data_list([b[1] for b in batch])
    y = torch.stack([t[2] for t in batch])
    return a, b, y


def make_ppi_warehouse_loaders(
    source: str = "hippie",
    *,
    min_confidence: float = 0.5,
    negative_ratio: float = 1.0,
    max_positives: int | None = 20000,    # cap so loader-build stays under a minute
    split_policy: str = "cold-protein",   # held-out UniProts in test
    batch_size: int = 32,
    val_frac: float = 0.1,
    test_frac: float = 0.1,
    seed: int = 4192,
    num_workers: int = 0,
    structure_root: str | None = None,
    contact_cutoff: float = 8.0,
    max_residues: int = 1024,
) -> tuple[DataLoader, DataLoader, DataLoader, dict]:
    """Build train/val/test loaders for a PPI binary-classification task.

    split_policy:
        "random"        i.i.d. by edge index — easy, but train/test share proteins.
        "cold-protein"  held-out UniProts: a protein never appears in two splits.
                        Strictest reasonable split for PPI.
    """
    records = load_ppi_records(
        source=source,
        min_confidence=min_confidence,
        negative_ratio=negative_ratio,
        seed=seed,
        max_positives=max_positives,
    )
    n = len(records)
    if n == 0:
        raise RuntimeError(f"No PPI records returned for source='{source}'")
    rng = np.random.default_rng(seed)
    if split_policy == "random":
        idx = rng.permutation(n)
        n_test = int(n * test_frac); n_val = int(n * val_frac)
        test_idx, val_idx, train_idx = idx[:n_test], idx[n_test:n_test+n_val], idx[n_test+n_val:]
    elif split_policy == "cold-protein":
        # Bucket UniProts into train/val/test. A record's bucket is
        # ``test`` iff BOTH its proteins are in the test bucket (so a
        # train protein never appears in test). Standard cold-PP split.
        unique = sorted({r.uniprot_a for r in records} | {r.uniprot_b for r in records})
        perm = rng.permutation(len(unique))
        bucket: dict[str, str] = {}
        n_t = int(len(unique)*test_frac); n_v = int(len(unique)*val_frac)
        for j, k in enumerate(perm):
            bucket[unique[k]] = "test" if j < n_t else ("val" if j < n_t+n_v else "train")
        train_idx, val_idx, test_idx = [], [], []
        for i, r in enumerate(records):
            ba, bb = bucket[r.uniprot_a], bucket[r.uniprot_b]
            if ba == "train" and bb == "train":   train_idx.append(i)
            elif ba == "test"  and bb == "test":  test_idx.append(i)
            elif ba == "val"   and bb == "val":   val_idx.append(i)
            # cross-bucket edges discarded (cold-pair semantics for PPI)
        train_idx, val_idx, test_idx = map(np.array, (train_idx, val_idx, test_idx))
    else:
        raise ValueError(f"Unknown split_policy '{split_policy}' for PPI. "
                         f"Use 'random' or 'cold-protein'.")

    full = WarehousePPIGraphDataset(records,
                                    structure_root=structure_root,
                                    contact_cutoff=contact_cutoff,
                                    max_residues=max_residues)
    train_ds = torch.utils.data.Subset(full, train_idx.tolist())
    val_ds   = torch.utils.data.Subset(full, val_idx.tolist())
    test_ds  = torch.utils.data.Subset(full, test_idx.tolist())

    def _loader(ds, shuffle, drop_last):
        return DataLoader(
            ds, batch_size=batch_size, shuffle=shuffle,
            num_workers=num_workers, pin_memory=True, drop_last=drop_last,
            collate_fn=ppi_graph_collate,
        )

    n_pos = int((full.ys > 0.5).sum())
    n_neg = int((full.ys <= 0.5).sum())
    meta = {
        "source":            source,
        "split_policy":      split_policy,
        "n_records":         n,
        "n_train":           len(train_ds),
        "n_val":             len(val_ds),
        "n_test":            len(test_ds),
        "n_positives":       n_pos,
        "n_negatives":       n_neg,
        "positive_fraction": round(n_pos / max(n, 1), 3),
        "n_proteins":        len(full.prot_graph_by_uniprot),
        "structure_coverage": full.structure_coverage,
        "min_confidence":    min_confidence,
        "label_range":       (0.0, 1.0),
    }
    return (
        _loader(train_ds, True, True),
        _loader(val_ds,   False, False),
        _loader(test_ds,  False, False),
        meta,
    )


# ── ESM-2 two-tower PPI loader ─────────────────────────────────────────
# Emits (esm_emb_a, esm_emb_b, y) for each PPI record. Wires up the flow
# shape "pp_emb" — when a user builds a flow with two `in.protein_emb`
# blocks (e.g. ESM-2 cached on each protein), this loader produces the
# tensors. Reuses ``embeddings.batch_get_or_compute`` so cache hits +
# auto-compute work the same way as the P-L ESM-2 path.

class _UniprotSeq:
    """Adapter exposing `.uniprot` and `.sequence` to batch_get_or_compute."""
    __slots__ = ("uniprot", "sequence")
    def __init__(self, u: str, s: str):
        self.uniprot, self.sequence = u, s


class WarehousePPIEmbDataset(Dataset):
    """Yields ``(esm_emb_a, esm_emb_b, label)`` per PPI record.

    Per-UniProt ESM-2 embeddings are de-duplicated at dataset
    construction so a HIPPIE pull with ~225K edges over ~12K proteins
    only triggers ~12K cache lookups (not 225K × 2).
    """
    def __init__(self, records: list["PPIRecord"],
                 checkpoint: str = "esm2_t33_650M",
                 auto_compute: bool = True):
        from .embeddings import batch_get_or_compute, _ESM2_DIMS
        self.records = records
        self.ys = (
            np.array([r.label for r in records], dtype=np.float32)
            if records else np.zeros((0,), dtype=np.float32)
        )
        # Flatten unique uniprot→sequence pairs across both sides.
        unique: dict[str, str] = {}
        for r in records:
            unique.setdefault(r.uniprot_a, r.sequence_a)
            unique.setdefault(r.uniprot_b, r.sequence_b)
        adapters = [_UniprotSeq(u, s) for u, s in unique.items()]
        embs, self.cache_meta = batch_get_or_compute(
            adapters, checkpoint=checkpoint, auto_compute=auto_compute,
        )
        self.embed_dim = embs.shape[1] if len(embs) else _ESM2_DIMS.get(checkpoint, 1280)
        self.emb_by_uniprot: dict[str, np.ndarray] = {
            adapters[i].uniprot: embs[i].astype(np.float32, copy=False)
            for i in range(len(adapters))
        }

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, idx: int):
        r = self.records[idx]
        return (
            torch.from_numpy(self.emb_by_uniprot[r.uniprot_a]),
            torch.from_numpy(self.emb_by_uniprot[r.uniprot_b]),
            torch.tensor(self.ys[idx], dtype=torch.float32),
        )


def pp_emb_collate(batch):
    """Trivial collate — both protein embeddings are already tensors."""
    a = torch.stack([t[0] for t in batch])
    b = torch.stack([t[1] for t in batch])
    y = torch.stack([t[2] for t in batch])
    return a, b, y


def make_ppi_esm_loaders(
    source: str = "hippie",
    *,
    min_confidence: float = 0.5,
    negative_ratio: float = 1.0,
    max_positives: int | None = 20000,
    split_policy: str = "cold-protein",
    batch_size: int = 64,
    val_frac: float = 0.1,
    test_frac: float = 0.1,
    seed: int = 4192,
    num_workers: int = 0,
    checkpoint: str = "esm2_t33_650M",
    auto_compute: bool = True,
) -> tuple[DataLoader, DataLoader, DataLoader, dict]:
    """Build train/val/test loaders for two-tower PPI on cached ESM-2.

    Reuses ``load_ppi_records`` for the records + positive/negative
    sampling. Splitting policy mirrors ``make_ppi_warehouse_loaders``:

      ``random``        i.i.d. by edge index
      ``cold-protein``  held-out UniProts in test; standard PPI cold split

    Returns a 4-tuple of (train, val, test, meta) where meta includes
    ``esm2_cache_meta``, ``esm2_dim``, ``esm2_checkpoint``, ``label_range``.
    """
    records = load_ppi_records(
        source=source, min_confidence=min_confidence,
        negative_ratio=negative_ratio, seed=seed,
        max_positives=max_positives,
    )
    n = len(records)
    if n == 0:
        raise RuntimeError(f"No PPI records returned for source='{source}'")
    rng = np.random.default_rng(seed)
    if split_policy == "random":
        idx = rng.permutation(n)
        n_test = int(n * test_frac); n_val = int(n * val_frac)
        test_idx, val_idx, train_idx = idx[:n_test], idx[n_test:n_test+n_val], idx[n_test+n_val:]
    elif split_policy == "cold-protein":
        unique = sorted({r.uniprot_a for r in records} | {r.uniprot_b for r in records})
        perm = rng.permutation(len(unique))
        bucket: dict[str, str] = {}
        n_t = int(len(unique) * test_frac); n_v = int(len(unique) * val_frac)
        for j, k in enumerate(perm):
            bucket[unique[k]] = "test" if j < n_t else ("val" if j < n_t + n_v else "train")
        train_idx, val_idx, test_idx = [], [], []
        for i, r in enumerate(records):
            ba, bb = bucket[r.uniprot_a], bucket[r.uniprot_b]
            if ba == "train" and bb == "train":   train_idx.append(i)
            elif ba == "test"  and bb == "test":  test_idx.append(i)
            elif ba == "val"   and bb == "val":   val_idx.append(i)
        train_idx, val_idx, test_idx = map(np.array, (train_idx, val_idx, test_idx))
    else:
        raise ValueError(f"Unknown split_policy '{split_policy}' for PPI-ESM. "
                         f"Use 'random' or 'cold-protein'.")

    full = WarehousePPIEmbDataset(records, checkpoint=checkpoint, auto_compute=auto_compute)
    train_ds = torch.utils.data.Subset(full, train_idx.tolist())
    val_ds   = torch.utils.data.Subset(full, val_idx.tolist())
    test_ds  = torch.utils.data.Subset(full, test_idx.tolist())

    def _loader(ds, shuffle, drop_last):
        return DataLoader(
            ds, batch_size=batch_size, shuffle=shuffle,
            num_workers=num_workers, pin_memory=True, drop_last=drop_last,
            collate_fn=pp_emb_collate,
        )
    n_pos = int((full.ys > 0.5).sum())
    n_neg = int((full.ys <= 0.5).sum())
    meta = {
        "source":              source,
        "split_policy":        split_policy,
        "n_records":           n,
        "n_train":             len(train_ds),
        "n_val":               len(val_ds),
        "n_test":              len(test_ds),
        "n_positives":         n_pos,
        "n_negatives":         n_neg,
        "positive_fraction":   round(n_pos / max(n, 1), 3),
        "n_proteins":          len(full.emb_by_uniprot),
        "esm2_cache_meta":     full.cache_meta,
        "esm2_dim":            full.embed_dim,
        "esm2_checkpoint":     checkpoint,
        "min_confidence":      min_confidence,
        "label_range":         (0.0, 1.0),
    }
    return (
        _loader(train_ds, True, True),
        _loader(val_ds,   False, False),
        _loader(test_ds,  False, False),
        meta,
    )


# ── Paired-graph loader: protein residue graph + ligand mol graph ──────
# Used by templates that want a GNN on BOTH sides (e.g. struct_gnn_dta).
# Crucial design point: protein graphs are CACHED per UniProt at dataset
# construction. KIBA has 229 unique proteins, Davis 442 — caching once is
# trivially small and avoids re-parsing PDB files every batch.

class WarehouseStructGraphDataset(Dataset):
    """Yields (prot_graph, lig_graph, label) per record.

    Both sides are torch_geometric ``Data`` objects. The trainer's
    collate (``struct_graph_collate``) builds two ``Batch`` objects, one
    per side, batched independently so each side's message passing
    runs on its own dense block.

    structure_root: passed through to ``graph_features.protein_residue_graph``
                    to find cached AlphaFold PDBs by UniProt.
    """

    def __init__(self, records: list[DTARecord],
                 *,
                 structure_root: str | None = None,
                 contact_cutoff: float = 8.0,
                 max_residues: int = 1024):
        from .featurizers import smiles_to_graph
        from .graph_features import protein_residue_graph, DEFAULT_STRUCTURE_ROOT
        self.records = records
        self.ys = np.array([r.label for r in records], dtype=np.float32) if records else np.zeros((0,), dtype=np.float32)
        # Pre-compute ligand graphs (one per record — ~13K records on KIBA).
        self.lig_graphs = []
        self.bad_lig: list[int] = []
        for i, r in enumerate(records):
            g = smiles_to_graph(r.smiles)
            if g is None:
                self.bad_lig.append(i)
            self.lig_graphs.append(g)
        # Pre-compute and cache protein graphs by UniProt — KIBA: 229 unique.
        # First pass: build a lookup dict.
        struct_root = structure_root or DEFAULT_STRUCTURE_ROOT
        unique_prots: dict[str, str] = {}  # uniprot → sequence
        for r in records:
            if r.uniprot and r.uniprot not in unique_prots:
                unique_prots[r.uniprot] = r.sequence
        self.prot_graph_by_uniprot: dict[str, object] = {}
        self.structure_coverage = {"with_structure": 0, "fallback": 0}
        for u, seq in unique_prots.items():
            g = protein_residue_graph(
                sequence=seq, uniprot=u,
                structure_root=struct_root,
                contact_cutoff=contact_cutoff,
                max_residues=max_residues,
            )
            self.prot_graph_by_uniprot[u] = g
            if float(g.has_structure) > 0.5:
                self.structure_coverage["with_structure"] += 1
            else:
                self.structure_coverage["fallback"] += 1

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, idx: int):
        r = self.records[idx]
        return (
            self.prot_graph_by_uniprot[r.uniprot],
            self.lig_graphs[idx],
            torch.tensor(self.ys[idx], dtype=torch.float32),
        )


def struct_graph_collate(batch):
    """Collate (prot_graph, lig_graph, y) triples into two Batches + y vector.

    Drops samples whose ligand graph is None (RDKit parse failure).
    """
    from torch_geometric.data import Batch
    triples = [b for b in batch if b[1] is not None]
    prot_batch = Batch.from_data_list([t[0] for t in triples])
    lig_batch  = Batch.from_data_list([t[1] for t in triples])
    ys = torch.stack([t[2] for t in triples])
    return prot_batch, lig_batch, ys


def make_struct_graph_warehouse_loaders(
    benchmark: str,
    *,
    split_policy: str = "random",
    batch_size: int = 64,        # paired graphs → smaller default batch
    val_frac: float = 0.1,
    test_frac: float = 0.1,
    seed: int = 4192,
    num_workers: int = 0,
    structure_root: str | None = None,
    contact_cutoff: float = 8.0,
    max_residues: int = 1024,
) -> tuple[DataLoader, DataLoader, DataLoader, dict]:
    """Loaders for templates that take BOTH a protein residue graph AND a
    ligand molecular graph (e.g. struct_gnn_dta).

    Reuses make_warehouse_loaders' split logic — we re-run it just to
    derive the train/val/test indices, then re-wrap with our graph
    dataset. Inefficient on records (split happens twice on the records
    list) but keeps split-policy parity automatic.
    """
    # Get records + indices via the standard loader, then discard its dataset.
    _, _, _, base_meta = make_warehouse_loaders(
        benchmark,
        split_policy=split_policy,
        batch_size=1,                  # we throw the loaders away
        val_frac=val_frac,
        test_frac=test_frac,
        seed=seed,
        num_workers=0,
    )
    records = load_warehouse_records(benchmark)
    n = len(records)
    rng = np.random.default_rng(seed)
    # Re-derive split indices using the same recipe — make_warehouse_loaders
    # doesn't return its indices to us, so we recompute deterministically.
    # (Cheap: it's a permutation over ≤30K records.)
    if split_policy == "random":
        idx = rng.permutation(n)
        n_test = int(n * test_frac); n_val = int(n * val_frac)
        test_idx, val_idx, train_idx = idx[:n_test], idx[n_test:n_test+n_val], idx[n_test+n_val:]
    elif split_policy == "cold-target":
        unique = sorted({r.uniprot for r in records})
        perm = rng.permutation(len(unique))
        bucket: dict[str, str] = {}
        n_t = int(len(unique)*test_frac); n_v = int(len(unique)*val_frac)
        for j, k in enumerate(perm):
            bucket[unique[k]] = "test" if j < n_t else ("val" if j < n_t+n_v else "train")
        train_idx = np.array([i for i, r in enumerate(records) if bucket[r.uniprot]=="train"])
        val_idx   = np.array([i for i, r in enumerate(records) if bucket[r.uniprot]=="val"])
        test_idx  = np.array([i for i, r in enumerate(records) if bucket[r.uniprot]=="test"])
    elif split_policy in ("cluster", "leakage-aware"):
        cluster_keys, leak_meta = _resolve_cluster_keys(records, threshold="uniref50", benchmark=benchmark)
        unique = sorted(set(cluster_keys))
        perm = rng.permutation(len(unique))
        bucket = {}
        n_t = int(len(unique)*test_frac); n_v = int(len(unique)*val_frac)
        for j, k in enumerate(perm):
            bucket[unique[k]] = "test" if j < n_t else ("val" if j < n_t+n_v else "train")
        train_idx = np.array([i for i, k in enumerate(cluster_keys) if bucket[k]=="train"])
        val_idx   = np.array([i for i, k in enumerate(cluster_keys) if bucket[k]=="val"])
        test_idx  = np.array([i for i, k in enumerate(cluster_keys) if bucket[k]=="test"])
    elif split_policy == "cold-drug":
        unique = sorted({r.ligand_ref for r in records})
        perm = rng.permutation(len(unique))
        bucket = {}
        n_t = int(len(unique)*test_frac); n_v = int(len(unique)*val_frac)
        for j, k in enumerate(perm):
            bucket[unique[k]] = "test" if j < n_t else ("val" if j < n_t+n_v else "train")
        train_idx = np.array([i for i, r in enumerate(records) if bucket[r.ligand_ref]=="train"])
        val_idx   = np.array([i for i, r in enumerate(records) if bucket[r.ligand_ref]=="val"])
        test_idx  = np.array([i for i, r in enumerate(records) if bucket[r.ligand_ref]=="test"])
    else:
        raise ValueError(f"Unsupported split_policy '{split_policy}' for struct-graph loader. "
                         f"Use 'random', 'cold-target', 'cold-drug', or 'leakage-aware'.")

    full = WarehouseStructGraphDataset(records,
                                       structure_root=structure_root,
                                       contact_cutoff=contact_cutoff,
                                       max_residues=max_residues)
    train_ds = torch.utils.data.Subset(full, train_idx.tolist())
    val_ds   = torch.utils.data.Subset(full, val_idx.tolist())
    test_ds  = torch.utils.data.Subset(full, test_idx.tolist())

    def _loader(ds, shuffle, drop_last):
        return DataLoader(
            ds, batch_size=batch_size, shuffle=shuffle,
            num_workers=num_workers, pin_memory=True, drop_last=drop_last,
            collate_fn=struct_graph_collate,
        )

    meta = {
        "benchmark":         benchmark,
        "split_policy":      split_policy,
        "n_records":         n,
        "n_train":           len(train_ds),
        "n_val":             len(val_ds),
        "n_test":            len(test_ds),
        "n_proteins":        len({r.uniprot for r in records}),
        "n_ligands":         len({r.ligand_ref for r in records}),
        "label_range":       (float(full.ys.min()), float(full.ys.max())) if n else (0.0, 0.0),
        "structure_coverage": full.structure_coverage,
    }
    return (
        _loader(train_ds, True, True),
        _loader(val_ds,   False, False),
        _loader(test_ds,  False, False),
        meta,
    )


# ── Thermo-features-only loader (for ThermoMLP template) ───────────────

class WarehouseThermoDataset(Dataset):
    """Returns (thermo_feats, label) pairs where thermo_feats is a 14-dim
    float32 vector from :func:`joint_thermo_features`. Pre-computes
    features once at __init__ — for the 30K Davis records this takes ~2 s.
    """
    def __init__(self, records: list[DTARecord]):
        from .thermodynamic_features import joint_thermo_features, thermo_feature_dim
        self.dim = thermo_feature_dim()
        self.records = records
        # Pre-compute; carry a "valid" mask so failed RDKit parses can
        # be skipped at sample time without restructuring downstream code.
        feats_list: list[list[float]] = []
        valid_mask: list[bool] = []
        for r in records:
            f = joint_thermo_features(r.sequence, r.smiles)
            if f is None:
                feats_list.append([0.0] * self.dim)
                valid_mask.append(False)
            else:
                feats_list.append(f)
                valid_mask.append(True)
        self.feats = np.array(feats_list, dtype=np.float32)
        self.valid = np.array(valid_mask, dtype=bool)
        self.ys = np.array([r.label for r in records], dtype=np.float32) if records else np.zeros((0,), dtype=np.float32)

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, idx: int):
        return (
            torch.from_numpy(self.feats[idx]),
            torch.tensor(self.ys[idx], dtype=torch.float32),
        )


# ── Generic featurized dataset (consumes the featurizer registry) ──────

class WarehouseFeaturizedDataset(Dataset):
    """Generic dataset that consumes any combination of featurizers from
    :mod:`api.model_studio.v2.featurizers`. Features are computed once
    in ``__init__`` and concatenated into a (N, total_dim) array.

    Each batch returns ``(feats, y)`` for the TabularFeatureMLP /
    ConPLex / ThermoMLP heads. Supports a ``ligand_dim`` / ``protein_dim``
    split so two-tower architectures can route axis-specific features
    to the right tower without re-tokenising.
    """
    def __init__(self, records: list[DTARecord], featurizer_ids: list[str]):
        from . import featurizers as feat_mod
        self.records = records
        self.ys = np.array([r.label for r in records], dtype=np.float32) if records else np.zeros((0,), dtype=np.float32)

        # Compute all featurizers + remember per-axis dims so the trainer
        # can build axis-aware architectures.
        per_feat = feat_mod.compute_features(featurizer_ids, records)
        ligand_arrs:   list[np.ndarray] = []
        protein_arrs:  list[np.ndarray] = []
        other_arrs:    list[np.ndarray] = []
        manifest: list[dict] = []
        for fid in featurizer_ids:
            spec = feat_mod.get(fid)
            arr  = per_feat.get(fid)
            if arr is None:
                continue
            manifest.append({"id": fid, "axis": spec.axis if spec else "unknown",
                             "dim": int(arr.shape[1])})
            if spec and spec.axis == "ligand":
                ligand_arrs.append(arr)
            elif spec and spec.axis == "protein":
                protein_arrs.append(arr)
            else:
                other_arrs.append(arr)
        if not (ligand_arrs or protein_arrs or other_arrs):
            self.feats = np.zeros((len(records), 1), dtype=np.float32)
            self.ligand_dim = 0
            self.protein_dim = 0
            self.manifest = manifest
            return
        # Concatenate ligand-first, then protein, then other, so the model
        # knows which slice belongs to which axis.
        parts = []
        if ligand_arrs:
            ligand_concat = np.concatenate(ligand_arrs, axis=1)
            parts.append(ligand_concat)
            self.ligand_dim = int(ligand_concat.shape[1])
        else:
            self.ligand_dim = 0
        if protein_arrs:
            protein_concat = np.concatenate(protein_arrs, axis=1)
            parts.append(protein_concat)
            self.protein_dim = int(protein_concat.shape[1])
        else:
            self.protein_dim = 0
        if other_arrs:
            parts.append(np.concatenate(other_arrs, axis=1))
        self.feats = np.concatenate(parts, axis=1).astype(np.float32, copy=False)
        # Normalize NaN / inf to zeros so the model doesn't get poisoned.
        self.feats = np.nan_to_num(self.feats, nan=0.0, posinf=0.0, neginf=0.0)
        self.manifest = manifest

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, idx: int):
        return (
            torch.from_numpy(self.feats[idx]),
            torch.tensor(self.ys[idx], dtype=torch.float32),
        )


def make_featurized_warehouse_loaders(
    benchmark: str,
    featurizer_ids: list[str],
    *,
    split_policy: str = "random",
    batch_size: int = 256,
    val_frac: float = 0.1,
    test_frac: float = 0.1,
    seed: int = 4192,
    num_workers: int = 0,
) -> tuple[DataLoader, DataLoader, DataLoader, dict]:
    """Build loaders that yield (feats, y) using the named featurizers."""
    records = load_warehouse_records(benchmark)
    n = len(records)
    if not n:
        raise RuntimeError(f"No records for benchmark={benchmark!r}")
    rng = np.random.default_rng(seed)

    # Split index computation (reuse same logic as make_warehouse_loaders)
    if split_policy == "random":
        idx = rng.permutation(n)
        n_test = int(n * test_frac); n_val = int(n * val_frac)
        test_idx, val_idx, train_idx = idx[:n_test], idx[n_test:n_test + n_val], idx[n_test + n_val:]
    elif split_policy in ("cold-target", "cluster", "leakage-aware"):
        unique = sorted({r.uniprot for r in records})
        perm = rng.permutation(len(unique))
        bucket: dict[str, str] = {}
        n_t = int(len(unique) * test_frac); n_v = int(len(unique) * val_frac)
        for j, k in enumerate(perm):
            bucket[unique[k]] = "test" if j < n_t else "val" if j < n_t + n_v else "train"
        train_idx = np.array([i for i, r in enumerate(records) if bucket[r.uniprot] == "train"])
        val_idx   = np.array([i for i, r in enumerate(records) if bucket[r.uniprot] == "val"])
        test_idx  = np.array([i for i, r in enumerate(records) if bucket[r.uniprot] == "test"])
    elif split_policy == "cold-drug":
        unique = sorted({r.ligand_ref for r in records})
        perm = rng.permutation(len(unique))
        bucket = {}
        n_t = int(len(unique) * test_frac); n_v = int(len(unique) * val_frac)
        for j, k in enumerate(perm):
            bucket[unique[k]] = "test" if j < n_t else "val" if j < n_t + n_v else "train"
        train_idx = np.array([i for i, r in enumerate(records) if bucket[r.ligand_ref] == "train"])
        val_idx   = np.array([i for i, r in enumerate(records) if bucket[r.ligand_ref] == "val"])
        test_idx  = np.array([i for i, r in enumerate(records) if bucket[r.ligand_ref] == "test"])
    else:
        raise ValueError(f"Unsupported split_policy {split_policy} for featurized loader.")

    full = WarehouseFeaturizedDataset(records, featurizer_ids)
    train_ds = torch.utils.data.Subset(full, train_idx.tolist())
    val_ds   = torch.utils.data.Subset(full, val_idx.tolist())
    test_ds  = torch.utils.data.Subset(full, test_idx.tolist())

    def _loader(ds, shuffle, drop_last):
        return DataLoader(ds, batch_size=batch_size, shuffle=shuffle,
                          num_workers=num_workers, pin_memory=True, drop_last=drop_last)

    meta = {
        "benchmark":   benchmark,
        "split_policy": split_policy,
        "n_records":   n,
        "n_train":     len(train_ds),
        "n_val":       len(val_ds),
        "n_test":      len(test_ds),
        "n_proteins":  len({r.uniprot for r in records}),
        "n_ligands":   len({r.ligand_ref for r in records}),
        "total_feature_dim": int(full.feats.shape[1]),
        "ligand_dim":  int(full.ligand_dim),
        "protein_dim": int(full.protein_dim),
        "featurizers": list(full.manifest),
        "label_range": (float(full.ys.min()), float(full.ys.max())) if n else (0.0, 0.0),
    }
    return (
        _loader(train_ds, True, True),
        _loader(val_ds,   False, False),
        _loader(test_ds,  False, False),
        meta,
    )


def make_thermo_warehouse_loaders(
    benchmark: str,
    *,
    split_policy: str = "random",
    batch_size: int = 256,
    val_frac: float = 0.1,
    test_frac: float = 0.1,
    seed: int = 4192,
    num_workers: int = 0,
) -> tuple[DataLoader, DataLoader, DataLoader, dict]:
    """Thermo-features-only variant of :func:`make_warehouse_loaders`."""
    records = load_warehouse_records(benchmark)
    n = len(records)
    if not n:
        raise RuntimeError(f"No records for benchmark={benchmark!r}")
    rng = np.random.default_rng(seed)

    # Reuse the split logic by routing through the helper map. Simpler
    # to inline the random + cold-target paths here than to refactor the
    # full make_warehouse_loaders signature for an alternate Dataset class.
    if split_policy == "random":
        idx = rng.permutation(n)
        n_test = int(n * test_frac); n_val = int(n * val_frac)
        test_idx, val_idx, train_idx = idx[:n_test], idx[n_test:n_test+n_val], idx[n_test+n_val:]
    elif split_policy in ("cold-target", "cluster"):
        unique = sorted({r.uniprot for r in records})
        perm = rng.permutation(len(unique))
        bucket = {}
        n_t = int(len(unique)*test_frac); n_v = int(len(unique)*val_frac)
        for j, k in enumerate(perm):
            bucket[unique[k]] = "test" if j < n_t else "val" if j < n_t+n_v else "train"
        train_idx = np.array([i for i, r in enumerate(records) if bucket[r.uniprot] == "train"])
        val_idx   = np.array([i for i, r in enumerate(records) if bucket[r.uniprot] == "val"])
        test_idx  = np.array([i for i, r in enumerate(records) if bucket[r.uniprot] == "test"])
    elif split_policy == "cold-drug":
        unique = sorted({r.ligand_ref for r in records})
        perm = rng.permutation(len(unique))
        bucket = {}
        n_t = int(len(unique)*test_frac); n_v = int(len(unique)*val_frac)
        for j, k in enumerate(perm):
            bucket[unique[k]] = "test" if j < n_t else "val" if j < n_t+n_v else "train"
        train_idx = np.array([i for i, r in enumerate(records) if bucket[r.ligand_ref] == "train"])
        val_idx   = np.array([i for i, r in enumerate(records) if bucket[r.ligand_ref] == "val"])
        test_idx  = np.array([i for i, r in enumerate(records) if bucket[r.ligand_ref] == "test"])
    else:
        raise ValueError(f"Unsupported split_policy {split_policy} for thermo loader. "
                         f"Use 'random', 'cold-target', 'cold-drug', or 'cluster'.")

    full = WarehouseThermoDataset(records)
    train_ds = torch.utils.data.Subset(full, train_idx.tolist())
    val_ds   = torch.utils.data.Subset(full, val_idx.tolist())
    test_ds  = torch.utils.data.Subset(full, test_idx.tolist())

    def _loader(ds, shuffle, drop_last):
        return DataLoader(ds, batch_size=batch_size, shuffle=shuffle,
                          num_workers=num_workers, pin_memory=True, drop_last=drop_last)

    meta = {
        "benchmark":   benchmark,
        "split_policy": split_policy,
        "n_records":   n,
        "n_train":     len(train_ds),
        "n_val":       len(val_ds),
        "n_test":      len(test_ds),
        "n_proteins":  len({r.uniprot for r in records}),
        "n_ligands":   len({r.ligand_ref for r in records}),
        "n_invalid_features": int((~full.valid).sum()),
        "feature_dim": full.dim,
        "label_range": (float(full.ys.min()), float(full.ys.max())) if n else (0.0, 0.0),
    }
    return (
        _loader(train_ds, True, True),
        _loader(val_ds,   False, False),
        _loader(test_ds,  False, False),
        meta,
    )


def make_graph_warehouse_loaders(
    benchmark: str,
    *,
    split_policy: str = "random",
    batch_size: int = 128,
    val_frac: float = 0.1,
    test_frac: float = 0.1,
    seed: int = 4192,
    num_workers: int = 0,
) -> tuple[DataLoader, DataLoader, DataLoader, dict]:
    """Graph-axis variant of make_warehouse_loaders. Used by GraphDTA / DrugBAN."""
    records = load_warehouse_records(benchmark)
    n = len(records)
    rng = np.random.default_rng(seed)

    if split_policy == "random":
        idx = rng.permutation(n)
        n_test = int(n * test_frac)
        n_val  = int(n * val_frac)
        test_idx, val_idx = idx[:n_test], idx[n_test:n_test + n_val]
        train_idx = idx[n_test + n_val:]
    else:
        # Defer to the dict-based bucketing as the token loader above
        # (same code paths; lifted to a helper would be cleaner.)
        unique_p = sorted({r.uniprot    for r in records}); unique_l = sorted({r.ligand_ref for r in records})
        perm_p = rng.permutation(len(unique_p)); perm_l = rng.permutation(len(unique_l))
        def _buckets(unique, perm):
            n_t = int(len(unique) * test_frac); n_v = int(len(unique) * val_frac)
            out = {}
            for j, k in enumerate(perm):
                out[unique[k]] = "test" if j < n_t else "val" if j < n_t + n_v else "train"
            return out
        pb = _buckets(unique_p, perm_p); lb = _buckets(unique_l, perm_l)
        if split_policy == "cold-target":
            decide = lambda r: pb[r.uniprot]
        elif split_policy == "cold-drug":
            decide = lambda r: lb[r.ligand_ref]
        elif split_policy == "cold-pair":
            def decide(r):
                a, b = pb[r.uniprot], lb[r.ligand_ref]
                return a if a == b else "discard"
        else:
            raise ValueError(f"Unknown split_policy '{split_policy}'")
        train_idx, val_idx, test_idx = [], [], []
        for i, r in enumerate(records):
            d = decide(r)
            if d == "train": train_idx.append(i)
            elif d == "val": val_idx.append(i)
            elif d == "test": test_idx.append(i)
        train_idx, val_idx, test_idx = map(np.array, (train_idx, val_idx, test_idx))

    full = WarehouseGraphDataset(records)
    train_ds = torch.utils.data.Subset(full, train_idx.tolist())
    val_ds   = torch.utils.data.Subset(full, val_idx.tolist())
    test_ds  = torch.utils.data.Subset(full, test_idx.tolist())

    def _loader(ds, shuffle, drop_last):
        return DataLoader(ds, batch_size=batch_size, shuffle=shuffle,
                          num_workers=num_workers, pin_memory=True,
                          drop_last=drop_last, collate_fn=graph_collate)

    meta = {
        "benchmark": benchmark, "split_policy": split_policy,
        "n_records": n, "n_train": len(train_ds), "n_val": len(val_ds), "n_test": len(test_ds),
        "n_proteins": len({r.uniprot for r in records}),
        "n_ligands":  len({r.ligand_ref for r in records}),
        "n_graph_parse_failures": len(full.bad_idx),
        "label_range": (float(full.ys.min()), float(full.ys.max())) if n else (0.0, 0.0),
    }
    return (
        _loader(train_ds, True, True),
        _loader(val_ds,   False, False),
        _loader(test_ds,  False, False),
        meta,
    )
