-- Schema-bridge views to make our v2 demo warehouse speak the schema
-- the paper_evaluator pipeline expects.
--
-- Run against the warehouse copy with:
--   duckdb /d/tmp_eval_test/reference_library/catalog/reference_library.duckdb < schema_bridge_views.sql

-- 1) warehouse_sources -- the evaluator queries this for source registry info
CREATE OR REPLACE VIEW warehouse_sources AS
SELECT
  source_id                                AS source_key,
  source_id                                AS source_name,
  'available'                              AS availability_status,
  CASE
    WHEN source_id IN ('davis','kiba','gtopdb','pdbbind') THEN 'dti_benchmark'
    WHEN source_id IN ('hippie','huri')                   THEN 'ppi'
    WHEN source_id IN ('3did')                            THEN 'domain_interaction'
    ELSE 'reference'
  END                                      AS category,
  'CC-BY 4.0'                              AS license_scope,
  TRUE                                     AS public_export_allowed,
  TRUE                                     AS redistributable,
  'bundled'                                AS retrieval_mode,
  'core'                                   AS scope_tier
FROM (
  SELECT DISTINCT source AS source_id FROM v2_sequence_cluster_membership
  WHERE source IS NOT NULL
);

-- 2) proteins -- canonical protein registry with uniref cluster columns
CREATE OR REPLACE VIEW proteins AS
SELECT
  p.uniprot                       AS uniprot_accession,
  p.entry_name                    AS uniprot_id,
  p.recommended_name              AS protein_name,
  p.organism                      AS organism,
  p.taxon_id                      AS taxon_id,
  p.sequence_length               AS sequence_length,
  s.uniref100                     AS uniref100_cluster,
  s.uniref90                      AS uniref90_cluster,
  s.uniref50                      AS uniref50_cluster,
  s.uniparc                       AS uniparc
FROM v2_protein_entry p
LEFT JOIN (
  SELECT uniprot,
         ANY_VALUE(uniref100) AS uniref100,
         ANY_VALUE(uniref90)  AS uniref90,
         ANY_VALUE(uniref50)  AS uniref50,
         ANY_VALUE(uniparc)   AS uniparc
  FROM v2_sequence_cluster_membership
  GROUP BY uniprot
) s ON s.uniprot = p.uniprot;

-- 3) protein_variants -- Davis variant resolution doubles as variant table
CREATE OR REPLACE VIEW protein_variants AS
SELECT
  davis_key                        AS variant_key,
  base_gene                        AS gene_symbol,
  uniprot                          AS uniprot_accession,
  mutation                         AS mutation_spec,
  phospho                          AS is_phospho,
  region                           AS variant_region,
  CASE WHEN uniprot IS NOT NULL THEN 'joined' ELSE 'unresolved' END AS join_status
FROM v2_davis_variant_resolution;

-- 4) pdb_entries -- PDB ID + uniprot mapping (post-compaction the v2 table is just pdb_id+uniprot)
CREATE OR REPLACE VIEW pdb_entries AS
SELECT DISTINCT
  pdb_id,
  uniprot         AS uniprot_accession,
  NULL            AS pdb_chain,
  NULL::INTEGER   AS uniprot_start,
  NULL::INTEGER   AS uniprot_end
FROM v2_pdb_uniprot;

-- 5) structure_units -- per-chain structure units (one per pdb_id+uniprot pair)
CREATE OR REPLACE VIEW structure_units AS
SELECT
  pdb_id || '_' || uniprot                AS structure_unit_id,
  pdb_id,
  NULL                                    AS pdb_chain,
  uniprot                                 AS uniprot_accession,
  NULL::INTEGER                           AS uniprot_start,
  NULL::INTEGER                           AS uniprot_end
FROM v2_pdb_uniprot;

-- 6) ligands -- union of Davis + KIBA + GtoPdb ligand catalogs
CREATE OR REPLACE VIEW ligands AS
SELECT
  ligand_ref                       AS ligand_id,
  'davis'                          AS source,
  smiles                           AS canonical_smiles,
  NULL                             AS inchi_key
FROM davis_ligands
UNION ALL
SELECT ligand_ref, 'kiba', smiles, NULL FROM kiba_ligands
UNION ALL
SELECT ligand_ref, 'gtopdb', smiles, NULL FROM gtopdb_ligands;

-- 7) ligand_chemistry_signatures -- scaffolds + ECFP similarity
CREATE OR REPLACE VIEW ligand_chemistry_signatures AS
SELECT
  ligand_ref                                   AS ligand_id,
  ligand_ref                                   AS ligand_ref,
  source                                       AS source,
  scaffold_smiles                              AS scaffold_signature,
  scaffold_id                                  AS scaffold_id,
  canonical_smiles                             AS canonical_smiles,
  (canonical_smiles IS NOT NULL)               AS canonical_smiles_present,
  scaffold_id                                  AS chemical_series_group
FROM v2_scaffold_membership;

-- 8) motif_domain_site_annotations -- Pfam/InterPro + residue annotations
CREATE OR REPLACE VIEW motif_domain_site_annotations AS
SELECT
  uniprot                          AS uniprot_accession,
  namespace                        AS namespace,
  identifier                       AS annotation_id,
  label                            AS annotation_label,
  NULL::INTEGER                    AS start_position,
  NULL::INTEGER                    AS end_position
FROM v2_motif_membership
UNION ALL
SELECT
  uniprot,
  'feature_' || feature_type       AS namespace,
  feature_type                     AS annotation_id,
  description                      AS annotation_label,
  position                         AS start_position,
  end_position                     AS end_position
FROM v2_residue_annotations;

-- 9) protein_ligand_edges -- DTI benchmarks (column names normalized)
CREATE OR REPLACE VIEW protein_ligand_edges AS
SELECT
  edge_id, protein_ref, ligand_ref,
  'davis'      AS interaction_source,
  label_value  AS affinity_value,
  label_kind   AS affinity_metric
FROM davis_interactions
UNION ALL
SELECT edge_id, protein_ref, ligand_ref, 'kiba', label_value, label_kind FROM kiba_interactions
UNION ALL
SELECT edge_id, protein_ref, ligand_ref, 'gtopdb', affinity_value, affinity_kind FROM gtopdb_interactions
UNION ALL
SELECT edge_id, protein_ref, ligand_ref, 'pdbbind',
       CAST(NULL AS DOUBLE), CAST(NULL AS VARCHAR) FROM pdbbind_interactions;

-- 10) protein_protein_edges -- PPI bridges
CREATE OR REPLACE VIEW protein_protein_edges AS
SELECT
  uniprot                          AS protein_a_ref,
  uniprot                          AS protein_b_ref,
  'hippie'                         AS interaction_source
FROM hippie_bridge_uniprot
UNION ALL
SELECT uniprot, uniprot, 'huri'   FROM huri_bridge_uniprot
UNION ALL
SELECT uniprot, uniprot, '3did'   FROM s_3did_bridge_uniprot
UNION ALL
SELECT uniprot_a, uniprot_b, 'biogrid'  FROM v2_biogrid_interactions
UNION ALL
SELECT uniprot_a, uniprot_b, 'intact'   FROM v2_intact_interactions
UNION ALL
SELECT uniprot_a, uniprot_b, 'string'   FROM v2_string_interactions;

-- 11) similarity_signatures -- UniRef + ortholog + Pfam clan + ligand similarity
CREATE OR REPLACE VIEW similarity_signatures AS
SELECT
  uniprot                          AS uniprot_accession,
  CAST(NULL AS VARCHAR)            AS ligand_ref,
  'uniref100'                      AS signature_kind,
  uniref100                        AS signature_value,
  FALSE                            AS canonical_smiles_present,
  CAST(NULL AS VARCHAR)            AS chemical_series_group
FROM v2_sequence_cluster_membership WHERE uniref100 IS NOT NULL
UNION ALL
SELECT uniprot, NULL, 'uniref90', uniref90, FALSE, NULL FROM v2_sequence_cluster_membership WHERE uniref90 IS NOT NULL
UNION ALL
SELECT uniprot, NULL, 'uniref50', uniref50, FALSE, NULL FROM v2_sequence_cluster_membership WHERE uniref50 IS NOT NULL
UNION ALL
SELECT uniprot, NULL, 'orthodb', orthodb_cluster, FALSE, NULL FROM v2_ortholog_cluster_membership WHERE orthodb_cluster IS NOT NULL
UNION ALL
SELECT m.uniprot, NULL, 'pfam_clan', c.clan_id, FALSE, NULL
FROM v2_motif_membership m
JOIN v2_pfam_clan_membership c ON c.pfam_id = m.identifier
WHERE m.namespace = 'pfam' AND m.identifier IS NOT NULL
UNION ALL
-- ligand chemistry signatures (so evaluator sees ligand_ref-keyed similarity entries)
SELECT NULL, ligand_ref, 'scaffold', scaffold_id,
       (canonical_smiles IS NOT NULL), scaffold_id
FROM v2_scaffold_membership;
