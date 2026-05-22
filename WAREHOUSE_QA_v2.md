# Warehouse QA Pass v2 — ProteoSphere v2 expanded catalog

**Catalog:** `demo_warehouse/catalog/v2.duckdb`
**Date:** 2026-05-22
**Scope:** Re-run of WAREHOUSE_QA.md tests after SIFTS + Swiss-Prot + IntAct/BioGRID/STRING/Reactome + Pfam clans + M-CSA + AlphaFold materialization.

**Table count:** 41
**Warehouse file size:** 0.34 GB

## Per-table row counts

| Table | Rows | Distinct UniProts (if applicable) |
|---|---:|---:|
| davis_bridge_uniprot | 1,650 | 1,434 |
| davis_interactions | 30,056 |  |
| davis_ligands | 68 |  |
| davis_proteins | 442 |  |
| gtopdb_bridge_uniprot | 2,581 | 2,581 |
| gtopdb_interactions | 23,904 | 2,581 |
| gtopdb_ligands | 13,727 |  |
| gtopdb_targets | 3,369 |  |
| hippie_bridge_uniprot | 34,055 | 32,277 |
| huri_bridge_uniprot | 34,691 | 34,555 |
| ingest_runs | 1 |  |
| kiba_bridge_uniprot | 229 | 229 |
| kiba_interactions | 118,253 |  |
| kiba_ligands | 2,111 |  |
| kiba_proteins | 229 |  |
| papers_metadata | 58 |  |
| papers_rows | 1,140,931 |  |
| pdbbind_interactions | 24,804 |  |
| pinder_plinder_audit | 6 |  |
| pinder_plinder_axis_overlap | 42 |  |
| s_3did_bridge_uniprot | 10,723 | 0 |
| v2_alphafold_models | 574,627 | 574,627 |
| v2_biogrid_interactions | 2,490,982 |  |
| v2_davis_variant_resolution | 442 | 217 |
| v2_ec_class_membership | 315,226 | 279,501 |
| v2_globin_family_members | 19 | 19 |
| v2_go_membership | 3,358,100 | 553,982 |
| v2_intact_interactions | 675,272 |  |
| v2_mcsa_catalytic_sites | 5,248 | 1,038 |
| v2_motif_membership | 3,549,332 | 557,185 |
| v2_ortholog_cluster_membership | 339,705 | 307,868 |
| v2_pdb_uniprot | 1,341,638 | 72,957 |
| v2_pdbbind_ligand_xref | 3,920 |  |
| v2_pfam_clan_membership | 27,481 |  |
| v2_protein_entry | 574,627 | 574,627 |
| v2_reactome_pathway_membership | 476,278 | 91,189 |
| v2_residue_annotations | 1,946,075 | 323,253 |
| v2_scaffold_edges_summary | 1,484 |  |
| v2_scaffold_membership | 16,320 |  |
| v2_sequence_cluster_membership | 71,056 | 57,391 |
| v2_string_interactions | 999,972 |  |

## Test 1 — Actin family

Pfam PF00022 + InterPro coverage:
```
  P60709  PF00022=2  InterPro=8
  P62736  PF00022=2  InterPro=4
  P63261  PF00022=2  InterPro=8
  P63267  PF00022=2  InterPro=8
  P68032  PF00022=2  InterPro=8
  P68133  PF00022=4  InterPro=8
```
Classic actin PDBs:
```
  1atn -> 2 UniProt(s)
  1j6z -> 1 UniProt(s)
  2a40 -> 3 UniProt(s)
  3hbt -> 1 UniProt(s)
```
Yeast actin P60010 PF00022 hit: True

## Test 2 — Kinase family

```
  O60674: PF07714
  P00519: PF07714
  P00533: PF07714
  P08581: PF07714
  P15056: PF07714
  P28482: PF00069
  P31749: PF00069
```

## Test 3 — Imatinib scaffold neighborhood

Scaffolds for imatinib/nilotinib/dasatinib/bosutinib:
```
  ligand:gtopdb:5710 -> 4799fe572adadf90
  ligand:gtopdb:5678 -> d93371b6b436ffad
  ligand:gtopdb:5687 -> 353dda1dd535e266
  ligand:gtopdb:5697 -> f742e62def92b584
```
PDBbind ligands now reachable via scaffolds: 2,795

## Test 4 — Hemoglobin subunits

```
  P69892 -> 9886081AT2759
  P69892 -> 9886081AT2759
  P69905 -> 8751793AT2759
  P69905 -> 8751793AT2759
  P68871 -> 9886081AT2759
  P68871 -> 9886081AT2759
  P02042 -> 9886081AT2759
  P02042 -> 9886081AT2759
  P69891 -> 9886081AT2759
  P69891 -> 9886081at2759
  P69892 -> 9886081at2759
  P02042 -> 9886081at2759
  P68871 -> 9886081at2759
  P69905 -> 8751793at2759
```

## Test 5 — Cross-organism orthology (yeast actin P60010 ↔ human ACTB P60709)

Yeast actin P60010 axis coverage:
```
  motif: 5
  seq_cluster: 0
  orth: 1
  ec: 1
  pdb: 35
  go: 24
  entry: 1
```
yeast P60010 UniRef50 vs human P60709 UniRef50: None
yeast P60010 OrthoDB vs human P60709 OrthoDB: ('5132116at2759', '9816605AT2759')
shared Pfam PF00022: True

## Test 6 — GPCR superfamily

PF00001 GPCRs with GtoPdb affinities: 453

## Test 7 — Protease convergent evolution

Human + non-human protease Pfam coverage:
```
  P00760: PF00089
  P00766: PF00089
  P00782: PF00082
  P07477: PF00089
  P08311: PF00089
  P09093: PF00089
```

## Test 8 — PDBbind binding-site context

PDBbind PDB->UniProt coverage: 19,773 / 20,212 = 97.8%

## Test 10 — Negative controls

Nonsense identifiers return: 0 rows (expected 0)

## New axis coverage (Swiss-Prot + interactions)

Top 20 taxa in v2_protein_entry:
```
  taxon=      9606    20,431  Homo sapiens
  taxon=     10090    17,252  Mus musculus
  taxon=      3702    16,418  Arabidopsis thaliana
  taxon=     10116     8,226  Rattus norvegicus
  taxon=    559292     6,733  Saccharomyces cerevisiae (strain ATCC 204508 / S288c)
  taxon=      9913     6,052  Bos taurus
  taxon=    284812     5,129  Schizosaccharomyces pombe (strain 972 / ATCC 24843)
  taxon=     83333     4,531  Escherichia coli (strain K12)
  taxon=      6239     4,499  Caenorhabditis elegans
  taxon=     39947     4,197  Oryza sativa subsp. japonica
  taxon=    224308     4,191  Bacillus subtilis (strain 168)
  taxon=     44689     4,163  Dictyostelium discoideum
  taxon=      7227     3,868  Drosophila melanogaster
  taxon=      8355     3,514  Xenopus laevis
  taxon=      7955     3,369  Danio rerio
  taxon=     83332     2,338  Mycobacterium tuberculosis (strain ATCC 25618 / H37Rv)
  taxon=      9031     2,314  Gallus gallus
  taxon=      9601     2,218  Pongo abelii
  taxon=     83334     2,047  Escherichia coli O157:H7
  taxon=     83331     1,899  Mycobacterium tuberculosis (strain CDC 1551 / Oshkosh)
```

