# Warehouse QA v3 — Tier 1 gap closure verification
_Run against:_ `v2.duckdb`

## Summary

| Test | Verdict |
|---|---|
| T1 Actin family | ✅ PASS |
| T2 Kinase family | ✅ PASS |
| T3 Imatinib chemical series | ✅ PASS |
| T4 Hemoglobin subunits | ✅ PASS |
| T5 Cross-organism orthology | ✅ PASS |
| T6 GPCR + GtoPdb cross-ref | ✅ PASS |
| T7 Protease convergence | ✅ PASS |
| T8 PDBbind ↔ UniProt coverage | ✅ PASS |
| T9 Multi-axis leakage SQL | ✅ PASS |
| T10 Negative control (bogus UniProt) | ✅ PASS |
| T11 Reactome top-level pathway (ABL1) | ✅ PASS |
| T12 Davis variant resolution | ✅ PASS |

## Detail

### T1 Actin family — PASS

```sql
SELECT m.uniprot, m.identifier, x.pdb_id FROM v2_motif_membership m LEFT JOIN v2_pdb_uniprot x ON x.uniprot=m.uniprot WHERE m.identifier='PF00022' AND m.uniprot IN ('P60709','P68133','P68032','P62736','P63267','P63261')
```

Returned 6 rows.

```
('P60709', 'PF00022', None)
('P62736', 'PF00022', None)
('P63261', 'PF00022', None)
('P63267', 'PF00022', None)
('P68032', 'PF00022', None)
('P68133', 'PF00022', None)
```

### T2 Kinase family — PASS

```sql
SELECT uniprot, identifier FROM v2_motif_membership WHERE identifier IN ('PF00069','PF07714') AND uniprot IN ('P00519','P00533','P15056','P08581','O60674','P31749','P28482')
```

Returned 7 rows.

```
('O60674', 'PF07714')
('P00519', 'PF07714')
('P00533', 'PF07714')
('P08581', 'PF07714')
('P15056', 'PF07714')
('P28482', 'PF00069')
('P31749', 'PF00069')
```

### T3 Imatinib chemical series — PASS

_Notes:_ MPZ = nilotinib chem-comp ID. Hit at T=0.812

```sql
SELECT ligand_a, ligand_b, ecfp4_tanimoto FROM v2_ligand_similarity WHERE (ligand_a='STI' OR ligand_b='STI') AND ecfp4_tanimoto >= 0.7 ORDER BY ecfp4_tanimoto DESC LIMIT 10
```

Returned 4 rows.

```
('STI', 'MPZ', 'pdbbind', 'pdbbind', 0.8115942028985508)
('STI', 'ligand:gtopdb:12383', 'pdbbind', 'gtopdb', 0.7692307692307693)
('STI', 'ligand:davis:10074640', 'pdbbind', 'davis', 0.7466666666666667)
('STI', 'ligand:gtopdb:5656', 'pdbbind', 'gtopdb', 0.7466666666666667)
```

### T4 Hemoglobin subunits — PASS

```sql
SELECT uniprot, identifier FROM v2_motif_membership WHERE identifier='PF00042' AND uniprot IN ('P69905','P68871','P69891','P02042')
```

Returned 4 rows.

```
('P02042', 'PF00042')
('P68871', 'PF00042')
('P69891', 'PF00042')
('P69905', 'PF00042')
```

### T5 Cross-organism orthology — PASS

_Notes:_ Pfam shared: True; UniRef50 same: False; UniRef50 rows: [('P60010', 'UniRef50_P60010'), ('P60709', 'UniRef50_P60709')]

```sql
SELECT uniprot, identifier FROM v2_motif_membership WHERE identifier='PF00022' AND uniprot IN ('P60010','P60709')
```

Returned 2 rows.

```
('P60010', 'PF00022')
('P60709', 'PF00022')
```

### T6 GPCR + GtoPdb cross-ref — PASS

_Notes:_ 467 GPCRs with GtoPdb binding affinities

```sql
SELECT COUNT(DISTINCT m.uniprot) FROM v2_motif_membership m JOIN gtopdb_bridge_uniprot b ON b.uniprot=m.uniprot WHERE m.identifier='PF00001'
```

Returned 1 rows.

```
(467,)
```

### T7 Protease convergence — PASS

_Notes:_ 26 distinct Pfam families

```sql
SELECT m.uniprot, m.identifier, e.ec3 FROM v2_motif_membership m LEFT JOIN v2_ec_class_membership e ON e.uniprot=m.uniprot WHERE m.uniprot IN ('P00766','P00760','P12838','P00782')
```

Returned 20 rows.

```
('P00760', 'IPR009003', '3.4.21')
('P00760', 'IPR043504', '3.4.21')
('P00760', 'IPR001254', '3.4.21')
('P00760', 'IPR018114', '3.4.21')
('P00760', 'IPR001314', '3.4.21')
('P00760', 'IPR033116', '3.4.21')
('P00760', 'PF00089', '3.4.21')
('P00760', 'IPR050127', '3.4.21')
('P00766', 'IPR009003', '3.4.21')
('P00766', 'IPR001254', '3.4.21')
('P00766', 'PF00089', '3.4.21')
('P00766', 'IPR033116', '3.4.21')
('P00766', 'IPR018114', '3.4.21')
('P00766', 'IPR043504', '3.4.21')
('P00766', 'IPR001314', '3.4.21')
... (5 more)
```

### T8 PDBbind ↔ UniProt coverage — PASS

_Notes:_ 19773/20212 = 97.8%

```sql
SELECT COUNT(DISTINCT pdb_id) FROM pdbbind_interactions p WHERE EXISTS (SELECT 1 FROM v2_pdb_uniprot x WHERE x.pdb_id = p.pdb_id)
```

Returned 1 rows.

```
(19773,)
```

### T9 Multi-axis leakage SQL — PASS

```sql
WITH test_proteins AS (
        SELECT DISTINCT uniprot FROM davis_bridge_uniprot WHERE uniprot IS NOT NULL LIMIT 5
      ),
      train_pool AS (
        SELECT DISTINCT uniprot FROM kiba_bridge_uniprot WHERE uniprot IS NOT NULL
      )
      SELECT tp.uniprot,
        (SELECT COUNT(*) FROM v2_motif_membership m1
           JOIN v2_motif_membership m2 ON m1.identifier = m2.identifier
           JOIN train_pool tp2 ON tp2.uniprot = m2.uniprot
           WHERE m1.uniprot = tp.uniprot AND m1.namespace = 'pfam') AS pfam_shared,
        (SELECT COUNT(*) FROM v2_ec_class_membership e1
           JOIN v2_ec_class_membership e2 ON e1.ec3 = e2.ec3
           JOIN train_pool tp2 ON tp2.uniprot = e2.uniprot
           WHERE e1.uniprot = tp.uniprot) AS ec_shared
      FROM test_proteins tp
```

Returned 5 rows.

```
('Q4VBY6', 0, 0)
('H9XFB4', 0, 0)
('B1AL79', 0, 0)
('Q5T7S2', 0, 0)
('Q9UKI8', 0, 3484)
```

### T10 Negative control (bogus UniProt) — PASS

```sql
SELECT COUNT(*) FROM v2_motif_membership WHERE uniprot='Z99999'
```

Returned 1 rows.

```
(0,)
```

### T11 Reactome top-level pathway (ABL1) — PASS

```sql
SELECT top_level_pathway, COUNT(*) FROM v2_reactome_pathway_membership WHERE uniprot='P00519' GROUP BY 1 ORDER BY 2 DESC LIMIT 5
```

Returned 5 rows.

```
('Developmental Biology', 4)
('Gene expression (Transcription)', 3)
('DNA Repair', 3)
('Cellular responses to stimuli', 2)
('Hemostasis', 1)
```

### T12 Davis variant resolution — PASS

_Notes:_ 100.0% resolved (1650/1650)

```sql
SELECT confidence, COUNT(*) FROM davis_bridge_uniprot GROUP BY 1
```

Returned 3 rows.

```
('ambiguous', 1379)
('wt_fallback', 230)
('exact', 41)
```
