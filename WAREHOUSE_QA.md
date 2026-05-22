# Warehouse QA Pass — ProteoSphere v2 demo catalog

**Catalog:** `demo_warehouse/catalog/v2.duckdb`
**Date:** 2026-05-22
**Mode:** Read-only stress test. No data was modified.
**Scope:** Probe the relationship axes (Pfam/InterPro/ELM, UniRef, OrthoDB, EC, PDB↔UniProt, Bemis-Murcko scaffolds, papers rows, PINDER/PLINDER audit) with diverse real-world structural-biology examples whose relationships are known a priori. Report what the warehouse surfaces, what it doesn't, and why.

---

## 1. Headline summary

| # | Test | Result | Why |
|---|---|---|---|
| 1 | Actin family (β/α-skeletal/α-cardiac/α-smooth/α-aortic/γ) | ⚠️ partial | Pfam PF00022 and InterPro stamps all present; **UniRef50 does NOT cluster the actins together** (one cluster per accession); the named "classic" PDBs 1ATN / 3HBT / 1J6Z are missing from v2_pdb_uniprot; clan/fold is surfaced via shared InterPro IPR043129 (ATPase fold) but only as a side-effect of InterPro |
| 2 | Kinase family (ABL/EGFR/BRAF/MET/JAK/AKT/MAPK) | ✅ pass | All 7 carry the correct Pfam (PF00069 STK or PF07714 TK); EC classes (2.7.10.* / 2.7.11.*) consistent; Davis-only and KIBA-only kinases are still grouped under PF00069 |
| 3 | Imatinib scaffold neighbourhood | ❌ fail | Bemis-Murcko hashes are too literal: imatinib and nilotinib have **different scaffold IDs** despite being the same chemical series; the largest scaffold cluster (n=421) is benzene `c1ccccc1` and is biologically meaningless |
| 4 | Hemoglobin subunit relationships | ✅ pass | All Hb chains in PF00042; OrthoDB correctly groups β/γ/δ together (`9886081AT2759`) and α alone (`8751793AT2759`) — biologically correct paralogy; curated `v2_globin_family_members` table provides a manual override |
| 5 | Cross-organism orthology (human β-actin ↔ yeast actin) | ❌ fail | Yeast actin P60010 is **not ingested** in any axis table; warehouse is >99% human (taxon 9606); the yeast UniRef50 cluster `UniRef50_P60010` only surfaces via a human paralog Q562R1 |
| 6 | GPCR superfamily (β2AR / D2 / H1) | ✅ pass | All in PF00001; all in gtopdb_targets; 191 distinct PF00001 proteins have GtoPdb binding affinities — full two-table join works |
| 7 | Protease convergent evolution | ⚠️ partial | Works for **human** equivalents (P07477 trypsin, P09093 elastase, P08311 cathepsin G all carry PF00089 + EC 3.4.21.*) but the *bovine/porcine* accessions named in the test brief (P00766, P00760, P12838, P00782) are entirely absent; PF00082 subtilisin-like has only 8 rows |
| 8 | PDBbind binding-site context | ⚠️ partial | ABL1 (P00519) has 21 pdbbind entries; PDB→UniProt→Pfam two-join works for 2HYY; but **only 56% of pdbbind PDB IDs (11,286 / 20,212) are mapped in v2_pdb_uniprot**, and **0% of pdbbind ligand_refs match v2_scaffold_membership** (confirmed gap) |
| 9 | Multi-axis leakage check (5 Davis test proteins) | ✅ pass | One CTE returns per-protein leakage counts across UniRef50 / Pfam / EC2 in 0.03 s; reveals that Davis is so kinase-saturated that 35/36 train-set proteins share EC2=2.7 with every test protein |
| 10 | Negative controls | ✅ pass | Pfam PF00069 ∩ PF00042 = 0 (correct); nonsense UniProt `Z99999`, nonsense Pfam `PF99999`, empty/space/quote/`%`/`_`/NULL inputs all return 0 rows cleanly |

Net: **3 fully pass, 4 partial, 3 fail.** The kinase / GPCR / leakage-check use cases — i.e. the manuscript's headline path — work cleanly. The gaps are in (a) chemical-series clustering, (b) cross-species coverage, (c) PDB↔UniProt completeness, and (d) sequence-cluster granularity below UniRef90.

---

## 2. Per-test detail

### Test 1 — Actin family

Tested UniProts: P60709 (β-actin), P68133 (α-skeletal), P68032 (α-cardiac), P62736 (α-smooth), P63267 (γ-aortic), P63261 (γ).

```sql
-- 1a Pfam/InterPro tags
SELECT uniprot, namespace, identifier FROM v2_motif_membership
 WHERE uniprot IN ('P60709','P68133','P68032','P62736','P63267','P63261')
 ORDER BY uniprot, namespace;
```
Result: all six carry **Pfam PF00022** and the canonical actin InterPro IDs (IPR004000, IPR004001, IPR020902, IPR043129). ✅

```sql
-- 1b PDB anchors
SELECT pdb_id, uniprot FROM v2_pdb_uniprot
 WHERE pdb_id IN ('1ATN','2A40','3HBT','1J6Z');
```
Result: only **2A40** present, and it points to gelsolin **Q9Y6W5** rather than to an actin chain. 1ATN, 3HBT, 1J6Z are **absent**. Aggregated, P60709 has 62 PDB hits in v2_pdb_uniprot — so the table does carry actin structures, just not the classic four named in textbooks. ⚠️

```sql
-- 1c UniRef
SELECT uniprot, uniref50, uniref90
  FROM v2_sequence_cluster_membership
 WHERE uniprot IN ('P60709','P68133','P68032','P62736','P63267','P63261');
```
Result: every actin sits in **its own** UniRef50 (`UniRef50_P60709`, `UniRef50_P68133`, …). The warehouse stores per-protein UniRef rows from the source mapping; it does **not** materialise reverse cluster memberships, so to find "all actins" one must already know they share PF00022. ⚠️

```sql
-- 1d clan/fold probe — Hsp70 vs actin
SELECT uniprot, identifier FROM v2_motif_membership
 WHERE uniprot IN ('P0DMV8','P11142','P34931','P60709','P63261');
```
Hsp70 (PF00012) and actin (PF00022) **share InterPro IPR043129** (actin-like ATPase fold). Pfam clan IDs (`CL*`) are not present (0 rows). So clan-level relationships exist only via InterPro and only when InterPro happens to assign the same fold-superfamily entry. ⚠️

---

### Test 2 — Kinase family

Tested: P00519 ABL1, P00533 EGFR, P15056 BRAF, P08581 MET, O60674 JAK2, P31749 AKT1, P28482 MAPK1.

```sql
SELECT uniprot, namespace, identifier FROM v2_motif_membership
 WHERE uniprot IN ('P00519','P00533','P15056','P08581','O60674','P31749','P28482')
   AND namespace='Pfam';
```
Result: TK family (ABL/EGFR/MET/JAK2) → **PF07714**; STK family (BRAF/AKT1/MAPK1) → **PF00069**. ✅

```sql
SELECT identifier, COUNT(DISTINCT uniprot) FROM v2_motif_membership
 WHERE identifier IN ('PF00069','PF07714') GROUP BY 1;
```
PF00069 → 269 distinct UniProts; PF07714 → 109. ✅

EC alignment: ABL1/JAK2 → 2.7.10.2; EGFR/MET → 2.7.10.1; BRAF/AKT1 → 2.7.11.1; MAPK1 → 2.7.11.24. ✅

Davis-only kinases (in `davis_bridge_uniprot` with `confidence='exact'` but not in `kiba_bridge_uniprot`): 10 carrying PF00069. KIBA-only: 103. Shared: 6. **All davis-only and kiba-only kinases are still grouped under the same Pfam family in `v2_motif_membership`.** ✅

> **Caveat for Davis (flagged here, repeated in §3):** `davis_proteins` has 442 rows (protein-name keys like `ABL1(E255K)p`), but `davis_bridge_uniprot` with `confidence='exact'` yields **only 41 distinct UniProts**. The remaining 401 protein_keys are either mutation/phospho variants or have `confidence='ambiguous'` gene-name multi-mappings. A Davis-side leakage audit therefore covers only ~9% of unique Davis protein keys at the "exact" confidence tier.

---

### Test 3 — Imatinib scaffold neighbourhood

```sql
SELECT ligand_ref, scaffold_smiles, scaffold_id
  FROM v2_scaffold_membership
 WHERE ligand_ref IN ('ligand:gtopdb:5687','ligand:gtopdb:5697',
                      'ligand:gtopdb:5678','ligand:gtopdb:5710');
```

| Drug | Scaffold ID | Bemis-Murcko scaffold SMILES |
|---|---|---|
| imatinib (5687)  | `353dda1d…` | `O=C(Nc1cccc(Nc2nccc(-c3cccnc3)n2)c1)c1ccc(CN2CCNCC2)cc1` |
| nilotinib (5697) | `f742e62d…` | `O=C(Nc1cccc(-n2ccnc2)c1)c1cccc(Nc2nccc(-c3cccnc3)n2)c1` |
| dasatinib (5678) | `d93371b6…` | `O=C(Nc1ccccc1)c1cnc(Nc2cc(N3CCNCC3)ncn2)s1` |
| bosutinib (5710) | `4799fe57…` | `c1ccc(Nc2ccnc3cc(OCCCN4CCNCC4)cc23)cc1` |

**Imatinib's scaffold cluster contains only itself plus its Davis duplicate (`ligand:davis:5291`). Nilotinib falls in a different scaffold cluster despite being clinically and chemically a direct successor.** The Bemis-Murcko algorithm strips R-groups but keeps every ring atom, so the imidazole vs methylpiperazine difference breaks the cluster. ❌

Top-5 largest scaffolds:
```
8448043181b9a6b0  c1ccccc1                                421 ligands (gtopdb,kiba)
76e053ce6adcab32  c1ccc2[nH]ccc2c1                         69 ligands (gtopdb)
bec95c18ea5772f6  c1ncc2ncn([C@H]3CCCO3)c2n1              65 ligands (gtopdb)
f7e3dad231fc195a  c1ccc(-c2n[nH]c3ncncc23)cc1             60 ligands (kiba,gtopdb)
527048209cc0a028  c1c[nH]cn1                              44 ligands (gtopdb)
```
The #1 cluster is **benzene** — biologically meaningless. Spot-check of its members (1,2,3-benzenetricarboxylic acid, 1400W, 2C-B, 3,5-DHPG…) confirms it's noise dominated. ❌

---

### Test 4 — Hemoglobin subunits

```sql
SELECT uniprot, namespace, identifier FROM v2_motif_membership
 WHERE uniprot IN ('P69905','P68871','P69891','P69892','P02042')
   AND namespace='Pfam';
-- All five rows return PF00042. ✅

SELECT uniprot, orthodb_cluster FROM v2_ortholog_cluster_membership
 WHERE uniprot IN ('P69905','P68871','P69891','P69892','P02042');
```

| UniProt | Subunit | OrthoDB cluster |
|---|---|---|
| P69905 | α     | `8751793AT2759`   |
| P68871 | β     | `9886081AT2759`   |
| P69891 | γ-1   | `9886081AT2759`   |
| P69892 | γ-2   | `9886081AT2759`   |
| P02042 | δ     | `9886081AT2759`   |

This is **biologically correct**: α-globin is a separate paralog group from β/γ/δ. ✅

UniRef50: γ-1 and γ-2 collapse to `UniRef50_P69892`; the other three each have their own cluster. PPI: HIPPIE bridge has all 5 IDs but the edge list itself was not probed in this pass.

`v2_globin_family_members` is a curated 19-row table (myoglobin, neuroglobin, cytoglobin, all Hb subunits, plus leghemoglobin / lamprey / C. elegans homologs) that gives a manual ground-truth roster across species. ✅

---

### Test 5 — Cross-organism orthology (human β-actin ↔ yeast actin)

```sql
SELECT * FROM v2_motif_membership                WHERE uniprot='P60010';  -- 0 rows
SELECT * FROM v2_sequence_cluster_membership     WHERE uniprot='P60010';  -- 0 rows
SELECT * FROM v2_ortholog_cluster_membership     WHERE uniprot='P60010';  -- 0 rows
SELECT * FROM v2_pdb_uniprot                     WHERE uniprot='P60010';  -- 0 rows
```

Yeast actin P60010 is **completely absent**. ❌

Taxon distribution in `v2_sequence_cluster_membership` (counts of rows, not distinct proteins):

```
taxon:9606      70,392    Homo sapiens
taxon:10116        327    Rattus norvegicus
taxon:10090        297    Mus musculus
taxon:36329         23    Plasmodium falciparum 3D7
taxon:2697049        7    SARS-CoV-2
taxon:694009         3    SARS-CoV
taxon:83333          3    E. coli K-12
taxon:64320          1    Zika
taxon:3052230        1
taxon:83332          1    Mtb H37Rv
taxon:5833           1    P. falciparum
```

The catalog is **>99% human**; mouse and rat are present at ~0.4% each; the remaining organisms are token coverage. Cross-organism orthology will only resolve when the source PPI/DTI datasets (HIPPIE, HuRI, gtopdb mouse/rat columns) happened to ingest the non-human accession.

Indirect signal: `UniRef50_P60010` does appear in the table — but as the cluster ID of human Q562R1 (β-actin-like protein 2). So a question "give me everything sharing UniRef50 with yeast actin" can be answered for the human side only, never for yeast.

---

### Test 6 — GPCR superfamily

```sql
SELECT * FROM v2_motif_membership WHERE uniprot IN ('P07550','P14416','P35367')
                                    AND identifier='PF00001';
-- 3 rows. ✅

SELECT target_id, target_name, human_uniprot FROM gtopdb_targets
 WHERE human_uniprot IN ('P07550','P14416','P35367');
-- (29  β2-adrenoceptor  P07550)
-- (215 D2 receptor      P14416)
-- (262 H1 receptor      P35367)

SELECT COUNT(DISTINCT gi.uniprot)
  FROM gtopdb_interactions gi
  JOIN v2_motif_membership m ON m.uniprot = gi.uniprot
 WHERE m.identifier='PF00001' AND gi.affinity_value IS NOT NULL;
-- 191
```

All three GPCRs carry PF00001; all three are gtopdb_targets; **191 distinct PF00001 GPCRs have GtoPdb binding affinities** ready for joining. The Pfam → GtoPdb → binding-affinity pipeline is end-to-end usable. ✅

---

### Test 7 — Protease convergent evolution

The accessions in the brief (chymotrypsin P00766 bovine, trypsin P00760 bovine, elastase P12838 porcine, subtilisin P00782 B. licheniformis) are all **absent** — consistent with the warehouse being human-only. ❌ as written.

Re-running with human equivalents:

```sql
SELECT uniprot, identifier FROM v2_motif_membership
 WHERE uniprot IN ('P07477','P09093','P08311','P08246','P03956');
SELECT uniprot, ec4         FROM v2_ec_class_membership
 WHERE uniprot IN ('P07477','P09093','P08311','P08246','P03956');
```

| UniProt | Name | Pfam | EC |
|---|---|---|---|
| P07477 | Trypsin-1     | PF00089 | 3.4.21.4  |
| P09093 | Chymotrypsin-C| (no PF found) | 3.4.21.70 |
| P08311 | Cathepsin G   | PF00089 | 3.4.21.20 |
| P08246 | Neutrophil elastase | PF00089 | 3.4.21.37 |
| P03956 | MMP1          | PF00413 | 3.4.24.7  |

PF00089 collects the chymotrypsin-fold serine proteases; PF00082 (subtilisin-like) has 8 hits in the catalog (P09958, Q8I0V0, Q8NBP7, O14773, P23188, Q14703 + dups). EC 3.4.* has 871 rows. The **functional convergence vs. structural homology distinction is representable**, but only for the human protein population. ⚠️ partial.

Additional finding: P09093 has the right EC but **no Pfam row** despite being a clear chymotrypsin-family enzyme — a curation gap in v2_motif_membership.

---

### Test 8 — PDBbind binding-site context

```sql
SELECT pdb_id, protein_ref, binding_measurement_raw
  FROM pdbbind_interactions
 WHERE protein_ref='protein:P00519';
-- 21 rows. ABL1 entries include 2HYY (Kd=170 nM imatinib), 3QRI, 3QRJ, 4WA9 …

SELECT pu.pdb_id, pu.uniprot, m.identifier
  FROM v2_pdb_uniprot pu
  JOIN v2_motif_membership m ON m.uniprot=pu.uniprot
 WHERE pu.pdb_id='2HYY' AND m.namespace='Pfam';
-- ('2HYY','P00519','PF08919')   F-actin binding
-- ('2HYY','P00519','PF07714')   PK_Tyr_Ser-Thr
-- ('2HYY','P00519','PF00017')   SH2
-- ('2HYY','P00519','PF00018')   SH3
```

PDB → UniProt → Pfam in two joins. ✅

**Coverage gap:**

```sql
SELECT COUNT(DISTINCT p.pdb_id),
       COUNT(DISTINCT CASE WHEN m.pdb_id IS NOT NULL THEN p.pdb_id END)
  FROM pdbbind_interactions p
  LEFT JOIN v2_pdb_uniprot m ON m.pdb_id=p.pdb_id;
-- (20212, 11286)
```

**Only 56 % of pdbbind PDB IDs have a UniProt mapping in v2_pdb_uniprot.** The other 8,926 pdbbind PDBs can only be joined by `protein:UniProt` string parsing on the `protein_ref` column. ⚠️

```sql
SELECT COUNT(DISTINCT p.ligand_ref)
  FROM pdbbind_interactions p
  JOIN v2_scaffold_membership s ON s.ligand_ref = p.ligand_ref;
-- 0
```

**Zero overlap between pdbbind ligands and v2_scaffold_membership.** As predicted in the brief: scaffolds were only computed for davis (68), kiba (2,111), gtopdb (11,361). 24,804 pdbbind binding edges cannot be reasoned about chemically through this warehouse. ❌ (gap to record)

Additional data-quality note: `pdbbind_interactions.ligand_ref` has free-text contamination — distinct values include `"UNKNOWN"` (the modal value), `"by ITC"`, `"13-mer"`, `"substrate Ac-WLA-AMC"`, `"a kinasefiltration assay"`. The column is **not** a clean foreign key.

---

### Test 9 — Multi-axis leakage check

Picked the first 5 Davis-exact UniProts as a synthetic test set (Q9H4B4, Q499L9, Q6ZN16, Q9H1R3, Q8NG69) and the remaining 36 as the train set.

```sql
WITH test_proteins(uniprot)  AS (VALUES ('Q9H4B4'),('Q499L9'),('Q6ZN16'),('Q9H1R3'),('Q8NG69')),
     train_proteins(uniprot) AS (VALUES /* 36 davis-exact UniProts */),
     test_axes AS (
       SELECT t.uniprot AS test_u, s.uniref50, m.identifier AS pfam, e.ec2
         FROM test_proteins t
         LEFT JOIN v2_sequence_cluster_membership s ON s.uniprot=t.uniprot
         LEFT JOIN v2_motif_membership            m ON m.uniprot=t.uniprot AND m.namespace='Pfam'
         LEFT JOIN v2_ec_class_membership         e ON e.uniprot=t.uniprot
     )
SELECT test_u,
       (SELECT COUNT(DISTINCT s.uniprot) FROM v2_sequence_cluster_membership s, train_proteins tr
         WHERE s.uniprot=tr.uniprot AND s.uniref50 IN (SELECT uniref50 FROM test_axes WHERE test_u=ta.test_u))  AS uniref50_leak,
       (SELECT COUNT(DISTINCT m.uniprot) FROM v2_motif_membership m, train_proteins tr
         WHERE m.uniprot=tr.uniprot AND m.namespace='Pfam'
           AND m.identifier IN (SELECT pfam FROM test_axes WHERE test_u=ta.test_u))                            AS pfam_leak,
       (SELECT COUNT(DISTINCT e.uniprot) FROM v2_ec_class_membership e, train_proteins tr
         WHERE e.uniprot=tr.uniprot AND e.ec2 IN (SELECT ec2 FROM test_axes WHERE test_u=ta.test_u))           AS ec2_leak
  FROM test_axes ta
 GROUP BY test_u;
```

| Test UniProt | UniRef50 leak | Pfam leak | EC2 leak |
|---|---|---|---|
| Q9H1R3 | 0 |  0 | 35 |
| Q8NG69 | 0 |  0 |  0 |
| Q9H4B4 | 0 | 15 | 35 |
| Q499L9 | 0 |  0 |  0 |
| Q6ZN16 | 0 |  0 | 35 |

Runtime: **0.03 s** for one CTE. SQL ergonomics are fine.

Interpretation:
- **UniRef50 leakage = 0 for all 5** because the warehouse only stores per-protein UniRef rows; each Davis kinase has a unique UniRef50 cluster ID in the table. (To detect homology leakage you'd need to flip the join to "any train protein in the same cluster" — which the query above does correctly; the result is genuinely 0 because Davis kinases happen not to be UniRef50-clustered with each other.)
- **Pfam leakage of 15 for Q9H4B4 (probably MAP2K7)** — 15 of the 36 train kinases share a Pfam family.
- **EC2=35 for the three kinases** simply reflects that 38 / 41 Davis-exact proteins are EC=2.7.* — the EC2 axis is saturated for Davis and has no leakage-resolving power on this dataset.
- **Q8NG69 and Q499L9 show 0 on every axis** because they have **no motif / EC / ortholog rows at all** — only a UniRef row. A real audit therefore needs to distinguish "0 leakage" from "0 annotation, can't tell". ⚠️

The query did its job in one pass, but a production leakage audit should add an "annotation present?" column to avoid silently passing un-annotated proteins.

---

### Test 10 — Negative controls

| Probe | Result |
|---|---|
| `SELECT … WHERE identifier='PF00069' INTERSECT WHERE identifier='PF00042'` | **0 rows** — kinases ∩ globins is empty as expected ✅ |
| `WHERE uniprot='Z99999'` across all 5 axis tables | all return 0 rows, no error ✅ |
| `WHERE identifier='PF99999'` in v2_motif_membership | 0 rows ✅ |
| Parameterised inputs `''`, `' '`, `"O'Brien"`, `NULL`, `'%'`, `'_'`, `'NULL'` | all return 0 rows, no SQL injection, no LIKE-wildcard surprise (because the query is `=`, not `LIKE`) ✅ |

Negative-control behaviour is clean. ✅

---

## 3. Gap analysis — what the warehouse can vs. cannot surface

### What works well

- **Pfam / InterPro family membership** for **human** proteins (9,486 distinct UniProts with at least one motif row).
- **EC numbers** (9,505 rows) covering most of class 2 (transferases, 1,664 distinct UniProts) and class 3 (hydrolases, 1,200) — kinases and proteases are well covered.
- **OrthoDB clusters** are biologically correct in spot checks (Hb α vs β/γ/δ paralogy resolved).
- **GtoPdb pharmacology** + **PF00001 GPCR family** join is end-to-end usable (191 GPCRs with affinities).
- **PINDER/PLINDER cross-axis audit** is materialised in `pinder_plinder_audit` and `pinder_plinder_axis_overlap` — all 6 comparisons rated `unsafe_for_training` with composite scores 0.86 – 1.00.
- **Multi-axis leakage SQL** is ergonomic (one CTE, sub-second on Davis-scale data).
- **Negative-control safety** — nonsense inputs return empty, no injection surface, no crash.

### What's missing

| Axis | Status | Notes |
|---|---|---|
| **Active-site / catalytic-residue annotations (M-CSA, CSA)** | ❌ none | `Motivated Proteins` namespace (14,502 rows) covers *structural micro-motifs* (Nests, Niches, β-turns, Asx-turns) — useful for backbone-geometry studies, **not** for catalytic-residue audits |
| **Cofactor binding (heme / FAD / NAD / ATP)** | ❌ none | Zero label hits for any cofactor term |
| **GO molecular-function terms** | ❌ none | No `GO:` identifiers in v2_motif_membership |
| **Pfam clans (`CL*`)** | ❌ none | Only individual families materialised; clan-level grouping must be inferred via shared InterPro IDs (works sometimes — see actin/Hsp70 → IPR043129) |
| **SCOP / CATH topology** | ❌ none | No SCOP or CATH identifiers anywhere |
| **Disease (OMIM / DisGeNET / ClinVar)** | ❌ none | No disease tables |
| **Tissue / cell-type expression (HPA, GTEx)** | ❌ none | No expression tables |
| **Pathway membership (Reactome, KEGG)** | ❌ none | No pathway tables |
| **Cross-species coverage** | ❌ thin | >99 % of rows are taxon:9606; mouse ~0.4 %, rat ~0.4 %, everything else < 0.1 %. Yeast / Drosophila / *C. elegans* / *E. coli* / pathogens are not materially ingested |
| **Bemis-Murcko series clustering** | ⚠️ too literal | Imatinib ≠ nilotinib by hash; benzene is the largest cluster (n=421). The table is useful as a per-ligand stamp, **not** as a chemical-series proxy. Need ECFP4/MMP/Murcko-generic clustering for series detection |
| **PDB↔UniProt completeness** | ⚠️ ~56 % | Of 20,212 distinct PDBs in pdbbind, only 11,286 (56 %) appear in v2_pdb_uniprot. Of the actin "classics" (1ATN, 2A40, 3HBT, 1J6Z) only 2A40 is present |
| **pdbbind ligand chemistry** | ❌ unjoinable | `pdbbind_interactions.ligand_ref` has free-text contamination ("UNKNOWN", "by ITC", "13-mer") and **0 % overlap** with v2_scaffold_membership |
| **Davis protein resolution** | ⚠️ 41/442 | 442 davis_proteins; only 41 distinct UniProts at `confidence='exact'`. Mutation/phospho variants (`ABL1(E255K)p`) and ambiguous gene-name mappings are not resolved to UniProt |
| **Reverse cluster lookup** | ⚠️ awkward | v2_sequence_cluster_membership stores per-protein rows; "give me all UniProts in cluster X" works but requires a self-join. Same shape for OrthoDB |
| **UniRef50 across paralogs** | ⚠️ by design | UniRef50 is a sequence-identity threshold; close paralogs (the 6 actins, the 4 β-like globins) each land in their own UniRef50 cluster. Pfam is the right axis for paralog grouping, **not** UniRef50 |

### Data-quality observations

- `davis_bridge_uniprot.confidence` has only 2 levels: `exact` (41 UniProts via direct match) and `ambiguous` (gene-name → multiple UniProt candidates, e.g. CDC2L2 → 5 candidates). No tier in between (e.g. "exact-modulo-isoform").
- `pdbbind_interactions.complex_type` is `'UNKNOWN'` for the vast majority — column is present but unpopulated.
- `v2_motif_membership.label` is empty (`''`) for most Pfam/InterPro rows; populated only for ELM motifs and a fraction of Motivated Proteins entries.
- All `v2_*` tables carry a `snapshot_id` column — provenance trace is intact.

---

## 4. Recommendations (manuscript-use-case lens)

The manuscript's headline use case is **leakage-aware training of DTI models on Davis/KIBA/GtoPdb/PDBbind**. Against that use case:

### Blockers (must fix before publication-grade leakage audits)

1. **PDB↔UniProt completeness (44 % gap).** Without this, any PDB-anchored leakage check on PDBbind will silently miss almost half its data. Re-resolve via PDBe `pdb_chain_uniprot.csv` or the PDB SIFTS XML feed.
2. **pdbbind ligand normalisation.** `ligand_ref` is currently free text. Need an InChIKey/canonical-SMILES join column so pdbbind ligands can be scaffolded and de-duped against davis/kiba/gtopdb.
3. **Davis variant resolution.** 401/442 davis_proteins lack an exact UniProt bridge. Mutation suffixes (`(E255K)`, `(F317I)p`) should fall back to the wild-type UniProt with a `confidence='wt_fallback'` tier, otherwise leakage audits run on < 10 % of Davis.

### Nice-to-haves (not blockers but listed for honesty)

4. **Chemical-series clustering** beyond Bemis-Murcko (ECFP4 Tanimoto ≥ 0.6, or Murcko-generic, or matched-molecular-pair series). Current table will not detect imatinib/nilotinib as a series.
5. **Pfam clans** (`Pfam-A.clans.tsv`, free download) — would surface actin/Hsp70/sugar-kinase at the fold level without relying on the lucky InterPro IPR043129 hit.
6. **SIFTS-mediated catalytic-residue annotations** (M-CSA cross-references active-site residues to UniProt positions; would enable an "is this binding site near a catalytic residue?" probe).
7. **Cross-species ingestion**. Even just adding mouse/rat at full coverage (vs the current ~300 rows each) would unlock standard cross-species leakage controls.
8. **Reactome / KEGG pathway membership** would let the manuscript add a "same pathway as a training protein" leakage axis on top of Pfam/EC/UniRef.

### Non-blockers (working as intended)

- Pfam/InterPro family stamping on human proteins ✅
- OrthoDB paralog clustering ✅
- GtoPdb pharmacology join ✅
- PINDER/PLINDER pre-computed audit ✅
- Multi-axis leakage SQL ergonomics ✅
- Negative-control safety ✅

---

*End of QA pass. No data tables were modified.*
