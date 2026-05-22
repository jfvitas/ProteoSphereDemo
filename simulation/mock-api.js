/* ProteoSphere Model Studio — Simulation mock API
 *
 * Installs interceptors on window.fetch and window.EventSource so the
 * React app makes no real network requests.  Returns canned-but-realistic
 * data for every /api/v2/* endpoint the GUI touches.
 *
 * The simulation is deterministic per launched run (seed → mulberry32),
 * so the medical-school admissions demo plays back the same curve on a
 * fixed seed and visibly different curves when the user randomises.
 */
(function () {
  'use strict';

  // ───────── Deterministic PRNG (mulberry32) ─────────
  function mulberry32(seed) {
    let s = seed >>> 0;
    return function () {
      s = (s + 0x6D2B79F5) >>> 0;
      let t = s;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function gauss(rnd) {
    // Box–Muller
    let u = 0, v = 0;
    while (u === 0) u = rnd();
    while (v === 0) v = rnd();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  }

  // ───────── State ─────────
  const STATE = {
    runs: {},
    lastRunId: null,
    promotions: {},
    seqPromotion: 0,
    seqRun: 4192,
    bootTime: Date.now(),
  };

  // ───────── Canned reference data ─────────
  // Real Davis/KIBA kinases + accessions
  const PROTEIN_SEED = [
    ['ABL1',   'P00519', 1130, 'Tyrosine kinase ABL1',                        'TK · Abl',   23],
    ['BRAF',   'P15056', 766,  'B-Raf proto-oncogene',                        'STK · RAF',  41],
    ['EGFR',   'P00533', 1210, 'Epidermal growth factor receptor',            'TK · ErbB',  127],
    ['JAK2',   'O60674', 1132, 'Janus kinase 2',                              'TK · JAK',   31],
    ['MET',    'P08581', 1390, 'Hepatocyte growth factor receptor',           'TK · Met',   58],
    ['MAPK1',  'P28482', 360,  'Mitogen-activated protein kinase 1 (ERK2)',   'STK · MAPK', 92],
    ['AKT1',   'P31749', 480,  'RAC-alpha serine/threonine-protein kinase',   'STK · AGC',  44],
    ['SRC',    'P12931', 536,  'Proto-oncogene tyrosine-protein kinase Src',  'TK · Src',   78],
    ['CDK2',   'P24941', 298,  'Cyclin-dependent kinase 2',                   'STK · CMGC', 314],
    ['BTK',    'Q06187', 659,  'Bruton tyrosine kinase',                      'TK · Tec',   86],
    ['ALK',    'Q9UM73', 1620, 'Anaplastic lymphoma kinase',                  'TK · Alk',   29],
    ['FLT3',   'P36888', 993,  'Receptor-type tyrosine-protein kinase FLT3',  'TK · PDGFR', 31],
    ['KIT',    'P10721', 976,  'Mast/stem cell growth factor receptor',       'TK · PDGFR', 47],
    ['ROS1',   'P08922', 2347, 'Proto-oncogene tyrosine-protein kinase ROS',  'TK · ROS',   11],
    ['JAK1',   'P23458', 1154, 'Janus kinase 1',                              'TK · JAK',   24],
    ['JAK3',   'P52333', 1124, 'Janus kinase 3',                              'TK · JAK',   18],
    ['TYK2',   'P29597', 1187, 'Non-receptor tyrosine-protein kinase TYK2',   'TK · JAK',   12],
    ['PIK3CA', 'P42336', 1068, 'PI3K catalytic subunit α',                    'PI3K',       38],
    ['MTOR',   'P42345', 2549, 'Serine/threonine-protein kinase mTOR',        'STK · PIKK', 27],
    ['CDK4',   'P11802', 303,  'Cyclin-dependent kinase 4',                   'STK · CMGC', 84],
    ['CDK6',   'Q00534', 326,  'Cyclin-dependent kinase 6',                   'STK · CMGC', 51],
    ['CDK7',   'P50613', 346,  'Cyclin-dependent kinase 7',                   'STK · CMGC', 27],
    ['CDK9',   'P50750', 372,  'Cyclin-dependent kinase 9',                   'STK · CMGC', 41],
    ['LCK',    'P06239', 509,  'Tyrosine-protein kinase Lck',                 'TK · Src',   72],
    ['SYK',    'P43405', 635,  'Tyrosine-protein kinase SYK',                 'TK · Syk',   65],
    ['FGFR1',  'P11362', 822,  'Fibroblast growth factor receptor 1',         'TK · FGFR',  133],
    ['FGFR2',  'P21802', 821,  'Fibroblast growth factor receptor 2',         'TK · FGFR',  91],
    ['FGFR3',  'P22607', 806,  'Fibroblast growth factor receptor 3',         'TK · FGFR',  62],
    ['FGFR4',  'P22455', 802,  'Fibroblast growth factor receptor 4',         'TK · FGFR',  39],
    ['VEGFR2', 'P35968', 1356, 'Vascular endothelial growth factor receptor 2','TK · VEGFR',71],
    ['MEK1',   'Q02750', 393,  'Dual specificity MAPK kinase 1 (MAP2K1)',     'STK · STE',  64],
    ['MEK2',   'P36507', 400,  'Dual specificity MAPK kinase 2 (MAP2K2)',     'STK · STE',  31],
    ['GSK3B',  'P49841', 420,  'Glycogen synthase kinase-3 beta',             'STK · CMGC', 132],
    ['GSK3A',  'P49840', 483,  'Glycogen synthase kinase-3 alpha',            'STK · CMGC', 22],
    ['ERBB2',  'P04626', 1255, 'Receptor tyrosine-protein kinase erbB-2',     'TK · ErbB',  84],
    ['ERBB3',  'P21860', 1342, 'Receptor tyrosine-protein kinase erbB-3',     'TK · ErbB',  16],
    ['ERBB4',  'Q15303', 1308, 'Receptor tyrosine-protein kinase erbB-4',     'TK · ErbB',  19],
    ['INSR',   'P06213', 1382, 'Insulin receptor',                            'TK · InsR',  29],
    ['IGF1R',  'P08069', 1367, 'Insulin-like growth factor 1 receptor',       'TK · InsR',  47],
    ['PDGFRA', 'P16234', 1089, 'Platelet-derived growth factor receptor α',   'TK · PDGFR', 22],
    ['PDGFRB', 'P09619', 1106, 'Platelet-derived growth factor receptor β',   'TK · PDGFR', 26],
    ['AURKA',  'O14965', 403,  'Aurora kinase A',                             'STK · Other',64],
    ['AURKB',  'Q96GD4', 344,  'Aurora kinase B',                             'STK · Other',41],
    ['PLK1',   'P53350', 603,  'Polo-like kinase 1',                          'STK · Other',38],
    ['CHEK1',  'O14757', 476,  'Serine/threonine-protein kinase Chk1',        'STK · Other',48],
    ['CHEK2',  'O96017', 543,  'Serine/threonine-protein kinase Chk2',        'STK · Other',23],
    ['ATM',    'Q13315', 3056, 'ATM serine/threonine kinase',                 'STK · PIKK', 19],
    ['ATR',    'Q13535', 2644, 'ATR serine/threonine kinase',                 'STK · PIKK', 9],
    ['PRKCA',  'P17252', 672,  'Protein kinase C alpha',                      'STK · AGC',  25],
    ['ROCK1',  'Q13464', 1354, 'Rho-associated protein kinase 1',             'STK · AGC',  29],
    ['CAMK2A', 'Q9UQM7', 478,  'Calcium/calmodulin-dep kinase II alpha',      'STK · CAMK', 31],
    ['MAPK14', 'Q16539', 360,  'p38α MAP kinase',                             'STK · MAPK', 184],
    ['MAPK8',  'P45983', 427,  'JNK1',                                        'STK · MAPK', 71],
    ['MAPK9',  'P45984', 424,  'JNK2',                                        'STK · MAPK', 22],
    ['MAPK10', 'P53779', 464,  'JNK3',                                        'STK · MAPK', 14],
    ['RPS6KB1','P23443', 525,  'Ribosomal protein S6 kinase β-1 (p70S6K)',    'STK · AGC',  41],
    ['IKBKB',  'O14920', 756,  'IκB kinase β',                                'STK · Other',39],
    ['NEK2',   'P51955', 445,  'NIMA-related kinase 2',                       'STK · Other',12],
    ['CSF1R',  'P07333', 972,  'Macrophage colony-stimulating factor receptor','TK · PDGFR',23],
    ['EPHA2',  'P29317', 976,  'Ephrin type-A receptor 2',                    'TK · Eph',   24],
    ['EPHB4',  'P54760', 987,  'Ephrin type-B receptor 4',                    'TK · Eph',   14],
    ['TBK1',   'Q9UHD2', 729,  'TANK-binding kinase 1',                       'STK · Other',29],
    ['DYRK1A', 'Q13627', 763,  'Dual specificity tyrosine kinase DYRK1A',     'STK · CMGC', 22],
    ['CLK1',   'P49759', 484,  'Dual specificity kinase CLK1',                'STK · CMGC', 18],
    ['HCK',    'P08631', 526,  'Tyrosine-protein kinase HCK',                 'TK · Src',   18],
    ['FYN',    'P06241', 537,  'Tyrosine-protein kinase Fyn',                 'TK · Src',   24],
    ['YES1',   'P07947', 543,  'Tyrosine-protein kinase Yes',                 'TK · Src',   16],
    ['ABL2',   'P42684', 1182, 'Tyrosine-protein kinase ABL2 (ARG)',          'TK · Abl',   18],
    ['HER2',   'P04626', 1255, 'Erbb2 / Her2',                                'TK · ErbB',  84],
  ];
  const LIGAND_SEED = [
    ['Imatinib',     'CC1=C(C=C(C=C1)NC(=O)C2=CC=C(C=C2)CN3CCN(CC3)C)NC4=NC=CC(=N4)C5=CN=CC=C5', 493.6, 0.54, 312, 'ChEMBL'],
    ['Dasatinib',    'CC1=CC=C(C=C1)NC2=NC(=NC(=C2)C3=CN(N=C3)C)NC4=C(N=CN=C4)Cl',                488.0, 0.42, 280, 'ChEMBL'],
    ['Nilotinib',    'Cc1cn(-c2cc(NC(=O)c3ccc(C)c(Nc4nccc(-c5cccnc5)n4)c3)cc(C(F)(F)F)c2)cn1',    529.5, 0.31, 261, 'ChEMBL'],
    ['Sorafenib',    'CNC(=O)c1cc(Oc2ccc(NC(=O)Nc3ccc(Cl)c(C(F)(F)F)c3)cc2)ccn1',                  464.8, 0.55, 188, 'ChEMBL'],
    ['Sunitinib',    'CCN(CC)CCNC(=O)c1c(C)[nH]c(/C=C2\\C(=O)Nc3ccc(F)cc32)c1C',                   398.5, 0.62, 154, 'ChEMBL'],
    ['Erlotinib',    'COCCOc1cc2ncnc(Nc3cccc(C#C)c3)c2cc1OCCOC',                                   393.4, 0.66, 220, 'ChEMBL'],
    ['Gefitinib',    'COc1cc2ncnc(Nc3ccc(F)c(Cl)c3)c2cc1OCCCN1CCOCC1',                             446.9, 0.66, 188, 'ChEMBL'],
    ['Lapatinib',    'CS(=O)(=O)CCNCc1oc(-c2ccc3ncnc(Nc4ccc(OCc5cccc(F)c5)c(Cl)c4)c3c2)cc1',       581.1, 0.32, 145, 'ChEMBL'],
    ['Ibrutinib',    'C=CC(=O)N1CCC[C@@H](C1)N2C3=NC=NC(=C3C(=N2)C4=CC=C(C=C4)OC5=CC=CC=C5)N',     440.5, 0.51, 132, 'ChEMBL'],
    ['Acalabrutinib','CC#CC(=O)N1CCC[C@H]1C2=NC(=C3N2C=CN=C3N)C4=CC=C(C=C4)C(=O)NC5=CC=CC=N5',     465.5, 0.55, 71,  'ChEMBL'],
    ['Crizotinib',   'C[C@@H](Oc1cc(c(cn1)c1cnn(c1)C1CCNCC1)Cl)c1c(Cl)cc(F)cc1Cl',                 450.3, 0.40, 91,  'ChEMBL'],
    ['Ceritinib',    'CC(C)c1cc(Nc2ncc(Cl)c(Nc3ccccc3S(=O)(=O)C(C)C)n2)cc(C(C)C)c1OC(C)C',         558.1, 0.31, 41,  'ChEMBL'],
    ['Alectinib',    'CCC1=CC2=C(C=C1C#N)C(=O)c3cc(ccc3N2)C1(C)CCC(CC1)N1CCOCC1',                  482.6, 0.40, 32,  'ChEMBL'],
    ['Brigatinib',   'COc1cc(N2CCC(CC2)N(C)C)c(NC(=O)C(C)(C)P(=O)(C)C)cc1Nc1ncc(Cl)c(-c2ccccc2P(C)(C)=O)n1', 584.1, 0.13, 19, 'ChEMBL'],
    ['Lorlatinib',   'CC1OC2=CN=C(N1)c1ccc(F)c(c1)C(F)(F)c1ccc(cn1)C(=O)N(C)C',                    406.4, 0.61, 12,  'ChEMBL'],
    ['Vemurafenib',  'CCCS(=O)(=O)Nc1ccc(F)c(C(=O)c2c[nH]c3ncc(-c4ccc(Cl)cc4)cc23)c1F',            489.9, 0.51, 71,  'ChEMBL'],
    ['Dabrafenib',   'CC(C)(C)c1nc(-c2cccc(NS(=O)(=O)c3c(F)cccc3F)c2F)c(-c2ccnc(N)n2)s1',          519.6, 0.40, 64,  'ChEMBL'],
    ['Trametinib',   'CC1(C)N(C(=O)Nc2ccc(I)cc2F)c2cc(N3CCN(C)CC3=O)nc3c2C1=CC(=O)N3C',            615.4, 0.31, 51,  'ChEMBL'],
    ['Vandetanib',   'COc1cc2ncnc(Nc3ccc(Br)cc3F)c2cc1OCC1CCN(C)CC1',                              475.4, 0.50, 38,  'ChEMBL'],
    ['Axitinib',     'CNC(=O)c1ccccc1Sc1ccc2[nH]nc(/C=C/c3ccncc3)c2c1',                            386.5, 0.71, 47,  'ChEMBL'],
    ['Pazopanib',    'CC1=C(N(N=C1)c1ccc2nc(C)nc(N(C)c3ccc(C(=N)N)cc3)c2c1)NS(=O)(=O)C',          437.5, 0.42, 41,  'ChEMBL'],
    ['Regorafenib',  'CNC(=O)c1cc(Oc2ccc(NC(=O)Nc3ccc(Cl)c(C(F)(F)F)c3)cc2F)ccn1',                 482.8, 0.55, 38,  'ChEMBL'],
    ['Cabozantinib', 'COc1cc2nccc(Oc3ccc(NC(=O)C4(C(=O)Nc5ccc(F)cc5)CC4)cc3F)c2cc1OC',             501.5, 0.52, 27,  'ChEMBL'],
    ['Tofacitinib',  'CC1CCN(C(=O)CC#N)CC1N(C)c1ncnc2[nH]ccc12',                                   312.4, 0.71, 71,  'ChEMBL'],
    ['Ruxolitinib',  'C[C@H](CC#N)n1cc(-c2ncnc3[nH]ccc23)cn1',                                     306.4, 0.78, 81,  'ChEMBL'],
    ['Baricitinib',  'CCS(=O)(=O)N1CC(CC#N)(n2cc(-c3ncnc4[nH]ccc34)cn2)C1',                        371.4, 0.66, 64,  'ChEMBL'],
    ['Palbociclib',  'CC(=O)c1c(C)c2cnc(Nc3ccc(N4CCNCC4)nc3)nc2n(C1=O)C1CCCC1',                    447.5, 0.43, 87,  'ChEMBL'],
    ['Ribociclib',   'Cc1cc2c(C(=O)N(C)C)cn(-c3ccc(N4CCNCC4)nc3)c2cn1',                            434.5, 0.49, 41,  'ChEMBL'],
    ['Abemaciclib',  'CCN1CCN(Cc2ccc(Nc3nccc(-c4cnc5cc(F)c(F)cc5c4-c4ccncc4)n3)cn2)CC1',           506.5, 0.34, 33,  'ChEMBL'],
    ['Olaparib',     'O=C(c1ccc(Cc2nnc(=O)[nH]2)c(F)c1)N1CCN(C(=O)C2CC2)CC1',                       434.5, 0.54, 21,  'ChEMBL'],
    ['Bortezomib',   'CC(C)C[C@H](NC(=O)[C@H](Cc1ccccc1)NC(=O)c1cnccn1)B(O)O',                     384.2, 0.50, 14,  'PubChem'],
    ['Carfilzomib',  'O=C(NC(CC1CCCCC1)C(=O)NC(CC1CCCCC1)C(=O)NC(CCCCNC(=O)OC(C)(C)C)C(=O)NC(CC1CCCCC1)C(=O)NC(C)C)C(=O)OC', 719.9, 0.10, 8, 'PubChem'],
    ['Staurosporine', 'CN[C@H]1[C@H](OC)[C@H]2O[C@@H]3n4c5ccccc5c5c6CNC(=O)c6c6c7ccccc7n2c6c45', 466.5, 0.64, 481, 'ChEMBL'],
    ['SB-203580',    'Cc1c(-c2ccc(S(C)=O)cc2)nc(-c2ccncc2)n1Cc1ccc(F)cc1',                         377.4, 0.71, 142, 'PubChem'],
    ['PD-98059',     'COc1ccc(cc1Nc1ncc(N)cn1)O',                                                  267.3, 0.84, 84,  'PubChem'],
    ['U-0126',       'NC(=N\\C#N)/N=N/C(N)=N/C#N',                                                 380.5, 0.32, 64,  'PubChem'],
    ['Wortmannin',   'CC(=O)O[C@H]1C2=C3C(=O)OCC3=CC(=O)[C@@]3(C)[C@@H]2[C@@H]2OC(=O)CC[C@]12C',   428.4, 0.41, 41,  'PubChem'],
    ['LY-294002',    'O=c1cc(-c2ccccc2)oc2ccc3ccccc3c12',                                          307.3, 0.81, 68,  'PubChem'],
    ['Rapamycin',    'C[C@@H]1CC[C@H]2C[C@H](\\C(=C\\C=C\\C=C\\[C@@H](C[C@@H](C(=O)[C@@H](C(/C=C/C(=O)O1)C)O)OC)C)/C)OC(=O)C[C@H](OC)C2', 914.2, 0.04, 19, 'PubChem'],
    ['Ku-55933',     'O=C(Cc1ccc(c(c1)Cl)Cl)N1CCC(CC1)c1nc2c(o1)cnc2',                              395.3, 0.59, 22,  'PubChem'],
    ['BX-795',       'OC[C@@H](NC(=O)c1ccc(Oc2cccc(c2)Cl)nc1)C(=O)Nc1ccc(F)c(c1)c1ccncc1',         591.5, 0.32, 14,  'PubChem'],
    ['Bosutinib',    'COc1cc(Nc2ncnc(c2C#N)Nc2cc(Cl)c(Cl)cc2OC)cc(c1OCCCN1CCN(C)CC1)F',            530.4, 0.41, 32,  'ChEMBL'],
    ['Ponatinib',    'CN1CCN(Cc2ccc(C(=O)Nc3ccc(C)c(C#Cc4ncc5n4ccc4cnccc54)c3)cc2C(F)(F)F)CC1',    532.6, 0.39, 41,  'ChEMBL'],
    ['Foretinib',    'CN(C)CCN(C(=O)c1ccc(-c2cc3ncnc(Oc4ccc(Cl)c(C#N)c4F)c3cc2OC)cc1)CC1CC1',      632.6, 0.25, 12,  'ChEMBL'],
    ['Foretinib2',   'COc1cc2ncc(C#N)c(Oc3ccc(NC(=O)C4(C(=O)Nc5ccc(F)cc5)CC4)cc3F)c2cc1OCCCN1CCOCC1', 632.7, 0.21, 8, 'ChEMBL'],
  ];
  const TEMPLATES = [
    { id: 'baseline_mlp',   label: 'Baseline MLP',        flavour: 'Lightweight ESM+ECFP feature MLP — best for sanity checks and as a cost-of-no-architecture floor.', hparams: { epochs: 25, batch_size: 256, lr: 3e-4, weight_decay: 1e-5 } },
    { id: 'deepdta',        label: 'DeepDTA',             flavour: 'Öztürk et al. 2018 — twin 1D-CNNs over SMILES + protein chars. Published Davis Pearson ≈ 0.881.', hparams: { epochs: 40, batch_size: 256, lr: 1e-3 } },
    { id: 'conplex',        label: 'ConPLex',             flavour: 'Singh et al. 2023 — ESM-2 + Morgan-FP fusion with contrastive negatives. Strong cold-target.', hparams: { epochs: 30, batch_size: 128, lr: 3e-4 } },
    { id: 'drugban',        label: 'DrugBAN',             flavour: 'Bao et al. 2023 — bilinear-attention between GNN ligand + CNN protein. SOTA on BindingDB.', hparams: { epochs: 30, batch_size: 64, lr: 5e-4 } },
    { id: 'graphdta',       label: 'GraphDTA',            flavour: 'Nguyen et al. 2021 — GIN over molecular graph + CNN protein. Family of 4 GNN variants.', hparams: { epochs: 40, batch_size: 128, lr: 5e-4 } },
    { id: 'moltrans',       label: 'MolTrans',            flavour: 'Huang et al. 2021 — sub-structural pattern tokeniser + interaction transformer.', hparams: { epochs: 35, batch_size: 64, lr: 1e-4 } },
    { id: 'ppi_gnn_siamese',label: 'PPI GNN (siamese)',   flavour: 'Symmetric two-tower GNN for protein-protein binding (binary). Trained against HIPPIE.', hparams: { epochs: 25, batch_size: 64, lr: 3e-4 } },
    { id: 'struct_gnn_dta', label: 'Structure-aware GNN', flavour: 'Pocket-graph GNN with E3-equivariant fold awareness. AlphaFold structures, contacts ≤ 8 Å.', hparams: { epochs: 50, batch_size: 32, lr: 2e-4 } },
    { id: 'tabular_mlp',    label: 'Tabular MLP',         flavour: 'Pure descriptor MLP — Mordred + ProtBert pooled. The honest small-data baseline.', hparams: { epochs: 30, batch_size: 256, lr: 5e-4 } },
    { id: 'thermo_mlp',     label: 'Thermo MLP',          flavour: 'Adds melting-point + flexibility features. Improves cold-target by ~3 Pearson points.', hparams: { epochs: 30, batch_size: 256, lr: 3e-4 } },
    { id: 'flow',           label: 'Flow builder (custom)', flavour: 'Free-form DAG built in the canvas. Compiles to nn.Module via flow_compiler.', hparams: { epochs: 25, batch_size: 128, lr: 3e-4 } },
  ];

  const FEATURIZER_CATALOG = {
    n_featurizers: 18,
    n_integrated: 12,
    items: [
      { id: 'esm2_650m_mean',  axis: 'protein', dim: 1280, cost: 'moderate', integrated: true,  requires: ['fair-esm','torch'], short_desc: 'ESM-2 650M mean-pooled embedding', long_desc: 'Frozen ESM-2 650M encoder; mean-pooled per residue → 1280-dim protein embedding. Cached against the warehouse keyed by sequence SHA.' },
      { id: 'esm2_3b_mean',    axis: 'protein', dim: 2560, cost: 'heavy',    integrated: true,  requires: ['fair-esm','torch'], short_desc: 'ESM-2 3B mean-pooled (heavy)', long_desc: 'ESM-2 3B parameter encoder. Slow but the strongest single sequence-only signal we ship.' },
      { id: 'protbert_mean',   axis: 'protein', dim: 1024, cost: 'moderate', integrated: true,  requires: ['transformers'],     short_desc: 'ProtBert (Rostlab) mean-pool', long_desc: 'ProtBert masked-LM embedding mean-pooled across residues. Comparable to ESM-2 650M but trained on a different corpus.' },
      { id: 'protbert_cls',    axis: 'protein', dim: 1024, cost: 'moderate', integrated: true,  requires: ['transformers'],     short_desc: 'ProtBert [CLS] token only',   long_desc: 'ProtBert [CLS]-pooled embedding. Cheaper than mean-pooling at the cost of some recall on long sequences.' },
      { id: 'aa_onehot',       axis: 'protein', dim: 21,   cost: 'trivial',  integrated: true,  requires: [],                   short_desc: 'Per-residue one-hot (21d)',   long_desc: 'Honest baseline — one-hot encoded amino-acid alphabet. Used inside DeepDTA + GraphDTA.' },
      { id: 'kmer_aa_3',       axis: 'protein', dim: 8000, cost: 'fast',     integrated: true,  requires: [],                   short_desc: 'AA 3-mer frequency vector',  long_desc: '20³ = 8000-dim k-mer composition vector. Pre-deep-learning baseline; cheap and surprisingly OK for cold-target.' },
      { id: 'pocket_graph',    axis: 'protein', dim: 256,  cost: 'heavy',    integrated: false, requires: ['pdbfixer','dssp'],  short_desc: 'AF/PDB pocket-graph features', long_desc: 'Pocket-residue graph with contact + secondary-structure annotations. Requires DSSP + a structure for every protein.' },
      { id: 'ecfp4_2048',      axis: 'ligand',  dim: 2048, cost: 'trivial',  integrated: true,  requires: ['rdkit'],            short_desc: 'ECFP4 Morgan fingerprint',   long_desc: 'Standard Morgan/circular fingerprint, radius 2. The default ligand featurizer for tree-based baselines.' },
      { id: 'maccs_keys',      axis: 'ligand',  dim: 166,  cost: 'trivial',  integrated: true,  requires: ['rdkit'],            short_desc: 'MACCS 166-bit keys',          long_desc: 'Public MDL key set. Highly interpretable but lower capacity than ECFP.' },
      { id: 'molformer_mean',  axis: 'ligand',  dim: 768,  cost: 'moderate', integrated: true,  requires: ['transformers'],     short_desc: 'MolFormer pre-trained mean-pool', long_desc: 'IBM MolFormer — SMILES transformer, mean-pooled. Strong general-purpose ligand embedding.' },
      { id: 'mordred_2d',      axis: 'ligand',  dim: 1613, cost: 'fast',     integrated: true,  requires: ['mordred','rdkit'],  short_desc: 'Mordred 2D descriptors',      long_desc: 'Full Mordred descriptor set restricted to 2D-computable features (1613 of 1826). NaN-imputed and standardised.' },
      { id: 'rdkit_2d',        axis: 'ligand',  dim: 200,  cost: 'trivial',  integrated: true,  requires: ['rdkit'],            short_desc: 'RDKit 2D descriptor block',   long_desc: 'RDKit Descriptors module — molecular weight, logP, HBA/HBD, TPSA, etc.' },
      { id: 'graph_gin',       axis: 'ligand',  dim: 128,  cost: 'fast',     integrated: false, requires: ['torch_geometric'],  short_desc: 'GIN learnt graph embedding',   long_desc: 'Graph isomorphism network features. Requires torch_geometric, not installed in the demo wheel.' },
      { id: 'cross_attention', axis: 'interaction', dim: 512, cost: 'moderate', integrated: true, requires: ['torch'],          short_desc: 'Cross-attention fusion block', long_desc: 'Trainable cross-attention head producing a joint pair embedding. Used by ConPLex + DrugBAN.' },
      { id: 'bilinear',        axis: 'interaction', dim: 256, cost: 'fast',     integrated: true, requires: ['torch'],          short_desc: 'Bilinear interaction map',     long_desc: 'Outer-product fusion with low-rank factorisation. The fast cousin of cross-attention.' },
      { id: 'thermo_block',    axis: 'protein',     dim: 32,  cost: 'fast',     integrated: false, requires: ['pyrosetta'],     short_desc: 'Thermo / flexibility block',    long_desc: 'Rosetta-derived Tm, ΔΔG, flexibility features. Requires PyRosetta — license-gated.' },
      { id: 'pinder_pocket',   axis: 'protein',     dim: 384, cost: 'heavy',    integrated: false, requires: ['pinder'],        short_desc: 'PINDER pocket embedding',       long_desc: 'PINDER protein–protein interface embedding. Heavy + requires extra deps.' },
      { id: 'rosetta_dG',      axis: 'interaction', dim: 16,  cost: 'heavy',    integrated: false, requires: ['pyrosetta'],     short_desc: 'Rosetta scored ΔG features',    long_desc: 'PyRosetta-scored binding-energy decomposition (16 channels). License-gated.' },
    ],
  };

  // ───────── Family generators (paginated) ─────────
  function _allProteins() {
    const out = [];
    for (let i = 0; i < PROTEIN_SEED.length; i++) {
      const p = PROTEIN_SEED[i];
      out.push({ uniprot: p[1], name: p[0], organism: 'Homo sapiens', len: p[2], pdbs: p[5], family: p[4], tier: i < 50 ? 'release' : 'preview', short_desc: p[3] });
    }
    // Pad to ~600 rows with synthetic but plausible orphans, so pagination feels real.
    for (let i = PROTEIN_SEED.length; i < 240; i++) {
      const fam = ['STK · CMGC','TK · Src','TK · ErbB','STK · AGC','TK · JAK','STK · MAPK'][i % 6];
      out.push({ uniprot: 'P' + String(10000 + i * 37).slice(-5), name: 'KIN_' + String(i).padStart(4, '0'),
                 organism: 'Homo sapiens', len: 250 + (i * 7) % 800, pdbs: (i * 3) % 30, family: fam,
                 tier: i < 200 ? 'release' : 'preview', short_desc: 'Kinome reference orphan #' + i });
    }
    return out;
  }
  function _allLigands() {
    const out = [];
    for (let i = 0; i < LIGAND_SEED.length; i++) {
      const l = LIGAND_SEED[i];
      out.push({ id: 'lig_' + l[0].toLowerCase(), name: l[0], smiles: l[1], mw: l[2], qed: l[3], n_pairs: l[4], source: l[5], tier: 'release' });
    }
    for (let i = LIGAND_SEED.length; i < 180; i++) {
      out.push({ id: 'lig_' + String(i).padStart(5, '0'), name: 'Compound-' + i, smiles: 'CC(C)Nc1ccccc1N', mw: 180 + (i * 11) % 320, qed: 0.4 + ((i * 7) % 50) / 100, n_pairs: (i * 5) % 80, source: ['ChEMBL','BindingDB','PubChem'][i % 3], tier: i < 120 ? 'release' : 'preview' });
    }
    return out;
  }
  function _allEdges() {
    const proteins = _allProteins();
    const ligands = _allLigands();
    const acts = ['pKi', 'pIC50', 'pKd'];
    const sources = ['ChEMBL', 'BindingDB', 'PDBbind', 'Davis', 'KIBA'];
    const out = [];
    for (let i = 0; i < 800; i++) {
      const p = proteins[i % proteins.length];
      const l = ligands[(i * 13) % ligands.length];
      out.push({
        protein: p.name + ' (' + p.uniprot + ')',
        ligand: l.name,
        act: acts[i % 3],
        value: (5.0 + ((i * 31) % 580) / 100).toFixed(2),
        src: sources[i % sources.length],
        year: 2010 + (i % 15),
      });
    }
    return out;
  }
  function _allStructures() {
    const proteins = _allProteins();
    const out = [];
    for (let i = 0; i < 320; i++) {
      const p = proteins[i % proteins.length];
      out.push({
        pdb: ('0' + String.fromCharCode(65 + (i % 26)) + String.fromCharCode(65 + ((i * 3) % 26)) + String.fromCharCode(65 + ((i * 7) % 26))).slice(-4).toUpperCase(),
        title: p.name + ' · ' + (i % 2 === 0 ? 'kinase domain with inhibitor' : 'apo holoenzyme'),
        resolution: (1.4 + (i % 17) / 10).toFixed(2) + ' Å',
        method: i % 5 === 0 ? 'Predicted' : 'X-ray',
        ligand: LIGAND_SEED[i % LIGAND_SEED.length][0].slice(0, 4).toUpperCase(),
        year: 2001 + (i % 24),
      });
    }
    return out;
  }
  function _allMotifs() {
    return [
      { name: 'Pkinase domain (PF00069)',   src: 'Pfam', n: 5142, ex: 'ABL1' },
      { name: 'PKinase_Tyr (PF07714)',      src: 'Pfam', n: 2814, ex: 'EGFR' },
      { name: 'SH2 domain (PF00017)',       src: 'Pfam', n: 1108, ex: 'SRC' },
      { name: 'SH3 domain (PF00018)',       src: 'Pfam', n: 893,  ex: 'SRC' },
      { name: 'IPR000719 — kinase domain',  src: 'InterPro', n: 5318, ex: 'BRAF' },
      { name: 'IPR020635 — TyrKc',          src: 'InterPro', n: 2918, ex: 'JAK2' },
      { name: 'GO:0004672 (kinase activity)',src: 'GO',  n: 5402, ex: 'MAPK1' },
      { name: 'PROSITE PS00107 ATP-binding',src: 'PROSITE', n: 4910, ex: 'CDK2' },
      { name: 'IPR011009 — Kinase-like',    src: 'InterPro', n: 5901, ex: 'AKT1' },
      { name: 'IPR017441 — ATP-binding site',src: 'InterPro', n: 4731, ex: 'CDK2' },
      { name: 'Pkinase_C (PF00433)',        src: 'Pfam', n: 731, ex: 'PRKCA' },
      { name: 'Activation loop motif',      src: 'KinBase', n: 4188, ex: 'BRAF' },
    ];
  }
  function _allSources() {
    return [
      { id: 'bindingdb',  name: 'BindingDB',  kind: 'public',  rows: 2_780_000, scope: 'protein-ligand affinities (Ki/IC50/Kd)', updated: '2026-04-08' },
      { id: 'chembl',     name: 'ChEMBL 34',  kind: 'public',  rows: 21_440_000, scope: 'bioactivities + targets',               updated: '2026-03-21' },
      { id: 'pdbbind',    name: 'PDBbind 2024', kind: 'access-controlled', rows: 23_104, scope: 'crystal complexes with measured Kd', updated: '2026-02-12' },
      { id: 'davis',      name: 'Davis kinase panel', kind: 'public', rows: 30_056, scope: 'kinase-inhibitor Kd matrix (442×72)', updated: '2008-12-01' },
      { id: 'kiba',       name: 'KIBA',       kind: 'public',  rows: 118_254, scope: 'unified KIBA-score kinase activity',       updated: '2014-05-11' },
      { id: 'pdb',        name: 'PDB',        kind: 'public',  rows: 218_400, scope: 'experimental 3D structures',                updated: '2026-05-15' },
      { id: 'uniprot',    name: 'UniProtKB',  kind: 'public',  rows: 262_400_000, scope: 'canonical sequences + annotations',   updated: '2026-04-01' },
      { id: 'pfam',       name: 'Pfam 36',    kind: 'public',  rows: 21_980, scope: 'protein families + HMMs',                  updated: '2025-09-05' },
      { id: 'interpro',   name: 'InterPro 99',kind: 'public',  rows: 47_120, scope: 'integrated protein signatures',            updated: '2026-02-08' },
      { id: 'string',     name: 'STRING 12',  kind: 'public',  rows: 84_220_000, scope: 'protein-protein interactions',          updated: '2025-11-04' },
      { id: 'hippie',     name: 'HIPPIE 2.3', kind: 'public',  rows: 770_400, scope: 'curated PPI with confidence scores',       updated: '2025-08-19' },
      { id: 'pinder',     name: 'PINDER',     kind: 'public',  rows: 251_000, scope: 'protein-protein docking benchmark',        updated: '2025-12-04' },
      { id: 'plinder',    name: 'PLINDER',    kind: 'public',  rows: 449_000, scope: 'protein-ligand docking benchmark',         updated: '2025-12-04' },
      { id: 'alphafold',  name: 'AlphaFold DB',kind: 'public', rows: 214_700_000, scope: 'predicted structures',                  updated: '2026-04-22' },
    ];
  }
  function _allReleases() {
    return [
      { id: 'v2026.04', version: 'v2026.04', current: true,  status: 'live · pinned', published: '2026-04-12', n_sources: 14, n_rows: 22_640_000, n_leakage_groups: 1827 },
      { id: 'v2026.03', version: 'v2026.03', current: false, status: 'archived',      published: '2026-03-08', n_sources: 14, n_rows: 22_410_000, n_leakage_groups: 1814 },
      { id: 'v2026.02', version: 'v2026.02', current: false, status: 'archived',      published: '2026-02-05', n_sources: 13, n_rows: 22_188_000, n_leakage_groups: 1791 },
      { id: 'v2026.01', version: 'v2026.01', current: false, status: 'archived',      published: '2026-01-09', n_sources: 13, n_rows: 21_970_000, n_leakage_groups: 1775 },
      { id: 'v2025.12', version: 'v2025.12', current: false, status: 'archived',      published: '2025-12-10', n_sources: 13, n_rows: 21_780_000, n_leakage_groups: 1759 },
      { id: 'v2025.11', version: 'v2025.11', current: false, status: 'archived',      published: '2025-11-11', n_sources: 12, n_rows: 21_510_000, n_leakage_groups: 1742 },
    ];
  }

  // ───────── Helpers ─────────
  function jsonResponse(obj, status = 200) {
    return new Response(JSON.stringify(obj), {
      status,
      headers: { 'Content-Type': 'application/json' },
    });
  }
  function paginate(rows, q, page, perPage) {
    let filtered = rows;
    if (q) {
      const ql = String(q).toLowerCase();
      filtered = rows.filter(r => {
        for (const k in r) {
          if (typeof r[k] === 'string' && r[k].toLowerCase().includes(ql)) return true;
        }
        return false;
      });
    }
    const start = (page - 1) * perPage;
    return { rows: filtered.slice(start, start + perPage), total: filtered.length };
  }

  // ───────── Run lifecycle ─────────
  function createRun(payload) {
    STATE.seqRun += 1;
    const runId = 'run_' + STATE.seqRun + '_sim';
    const hparams = (payload && payload.hparams) || {};
    const epochs = Math.max(1, Math.min(80, parseInt(hparams.epochs) || 25));
    const seed = parseInt(hparams.seed) || 4192;
    const benchmark = hparams.benchmark || 'davis';
    const template = (payload && payload.effective_config && (payload.effective_config.template_label || payload.effective_config.template_id)) || 'DeepDTA';
    const templateId = (payload && payload.effective_config && payload.effective_config.template_id) || 'deepdta';
    STATE.runs[runId] = {
      id: runId,
      status: 'queued',
      hparams: { ...hparams, epochs, seed, benchmark },
      template_id: templateId,
      template_label: template,
      effective_config: (payload && payload.effective_config) || null,
      created_at: Date.now(),
      cancelled: false,
    };
    STATE.lastRunId = runId;
    return { run_id: runId, status: 'queued', stream_url: '/api/v2/pipeline/runs/' + runId + '/stream' };
  }

  // Synthesise per-epoch metrics. Curve targets Pearson ≈ 0.881 for seed=0;
  // ±0.03 jitter for other seeds. Loss ~0.85 → ~0.28 by epoch 5+.
  function epochMetrics(run, epoch) {
    const rnd = mulberry32(run.hparams.seed ^ (epoch * 9001));
    const total = run.hparams.epochs;
    const t = epoch / Math.max(1, total);
    const decay = Math.exp(-2.2 * t);
    const trainLoss = 0.28 + 0.57 * decay + 0.015 * (gauss(rnd));
    const valLoss   = trainLoss + 0.06 + 0.012 * (gauss(rnd));
    const valPearsonTarget = 0.881 + ((run.hparams.seed % 11) - 5) * 0.006;
    const valPearson = Math.max(0, Math.min(0.99, valPearsonTarget * (1 - 0.4 * Math.exp(-3.4 * t)) + 0.005 * gauss(rnd)));
    const valRMSE  = 0.26 + 0.6 * decay + 0.01 * gauss(rnd);
    const valCI    = 0.55 + 0.34 * (1 - Math.exp(-3.0 * t)) + 0.004 * gauss(rnd);
    const valMAE   = 0.20 + 0.45 * decay + 0.005 * gauss(rnd);
    const lr       = (run.hparams.lr || 1e-3) * (0.5 + 0.5 * Math.cos((epoch / total) * Math.PI));
    return {
      type: 'epoch',
      epoch,
      total_epochs: total,
      train_loss: +trainLoss.toFixed(5),
      val_loss:   +valLoss.toFixed(5),
      val_rmse:   +valRMSE.toFixed(5),
      val_pearson: +valPearson.toFixed(5),
      val_ci: +valCI.toFixed(5),
      val_mae: +valMAE.toFixed(5),
      lr,
      task: 'regression',
      elapsed_s: 0, // filled in by streamer
      eta_s: 0,
    };
  }
  function finalSummary(run) {
    const rnd = mulberry32(run.hparams.seed);
    const jitter = ((run.hparams.seed % 11) - 5) * 0.004;
    const wall = 60 * (run.hparams.epochs / 5);
    return {
      type: 'final',
      status: 'completed',
      task: 'regression',
      test_pearson:  +(0.881 + jitter).toFixed(4),
      test_spearman: +(0.870 + jitter).toFixed(4),
      test_rmse:     +(0.265 - jitter / 2).toFixed(4),
      test_ci:       +(0.878 + jitter / 2).toFixed(4),
      test_r2:       +(0.776 + jitter).toFixed(4),
      test_auc_pki6: +(0.901 + jitter / 2).toFixed(4),
      test_mae:      +(0.188).toFixed(4),
      wall_time_s:   wall,
      n_params: 1_240_000,
      benchmark:    run.hparams.benchmark || 'davis',
      split_policy: run.hparams.split_policy || 'cold-target',
      n_train:      24045,
      device:       'cuda',
    };
  }

  // ───────── EventSource shim ─────────
  // Whitelist /api/v2/pipeline/runs/<id>/stream — everything else falls
  // through to the native EventSource (none used in this app).
  const NativeEventSource = window.EventSource;
  class MockEventSource {
    constructor(url) {
      this.url = url;
      this.readyState = 0; // CONNECTING
      this.onopen = null;
      this.onmessage = null;
      this.onerror = null;
      this._listeners = {};
      this._timers = [];
      this._closed = false;
      this._start();
    }
    addEventListener(name, fn) {
      (this._listeners[name] = this._listeners[name] || []).push(fn);
    }
    removeEventListener(name, fn) {
      const arr = this._listeners[name];
      if (arr) this._listeners[name] = arr.filter(f => f !== fn);
    }
    _emit(name, data) {
      if (this._closed) return;
      const ev = { data: typeof data === 'string' ? data : JSON.stringify(data), type: name };
      if (name === 'message' && typeof this.onmessage === 'function') this.onmessage(ev);
      (this._listeners[name] || []).forEach(fn => { try { fn(ev); } catch {} });
    }
    _later(fn, ms) {
      const id = setTimeout(() => { if (!this._closed) fn(); }, ms);
      this._timers.push(id);
    }
    _start() {
      const match = /\/api\/v2\/pipeline\/runs\/([^\/]+)\/stream/.exec(this.url);
      if (!match) {
        this._later(() => { this.readyState = 2; if (this.onerror) this.onerror({}); }, 30);
        return;
      }
      const runId = decodeURIComponent(match[1]);
      const run = STATE.runs[runId];
      if (!run) {
        this._later(() => { this.readyState = 2; if (this.onerror) this.onerror({}); }, 30);
        return;
      }
      // open
      this._later(() => {
        this.readyState = 1;
        if (typeof this.onopen === 'function') this.onopen({});
      }, 50);

      // Total ~60s for 5 epochs → 12s per epoch by default. Scale linearly.
      const epochsTotal = run.hparams.epochs;
      const perEpochMs = Math.max(1200, Math.min(15000, 60000 / Math.max(1, Math.min(8, epochsTotal))));
      const batchesPerEpoch = 20;
      const batchMs = perEpochMs / (batchesPerEpoch + 2);

      // Initial logs (real-world cadence: bursty at start)
      const bootLogs = [
        { level: 'info', text: 'Loading ' + (run.hparams.benchmark || 'davis') + ' via warehouse loader…' },
        { level: 'info', text: 'ESM-2 cache resolved: 442 hits, 0 computed, 0 zero-fallbacks. dim=1280' },
        { level: 'info', text: 'Dataset ready (' + (run.hparams.benchmark || 'davis') + '): 24,045 train / 3,005 val / 3,006 test (label range 5.0…10.5)' },
        { level: 'info', text: 'Compiling ' + run.template_label + ' (params=1.24M, optimiser=adamw, scheduler=cosine, amp=fp16)' },
        { level: 'info', text: 'CPU-only training: pinned torch to 4 intra-op threads (no CUDA device visible).' },
        { level: 'info', text: 'Epoch budget: ' + epochsTotal + '  ·  batch_size=' + (run.hparams.batch_size || 128) + '  ·  lr=' + (run.hparams.lr || 1e-3).toExponential(2) + '  ·  seed=' + run.hparams.seed },
      ];
      let cursor = 200;
      this._later(() => this._emit('message', { type: 'status', status: 'running' }), cursor);
      run.status = 'running';
      cursor += 200;
      bootLogs.forEach((l, i) => {
        this._later(() => this._emit('message', { type: 'log', level: l.level, text: l.text, t: Date.now() }), cursor + i * 280);
      });
      cursor += bootLogs.length * 280 + 200;

      // Per-epoch loop
      const t0 = Date.now();
      for (let epoch = 1; epoch <= epochsTotal && !run.cancelled; epoch++) {
        const epochStart = cursor;
        for (let b = 1; b <= batchesPerEpoch; b++) {
          const at = epochStart + b * batchMs;
          this._later(() => {
            const rnd = mulberry32(run.hparams.seed ^ (epoch * 31) ^ b);
            const loss = 0.28 + 0.57 * Math.exp(-2.2 * (epoch / epochsTotal)) + 0.04 * gauss(rnd);
            this._emit('message', { type: 'batch', epoch, batch: b, total_batches: batchesPerEpoch, loss: +loss.toFixed(5) });
          }, at);
        }
        // Epoch landing event
        const epochAt = epochStart + perEpochMs;
        this._later(() => {
          const m = epochMetrics(run, epoch);
          m.elapsed_s = (Date.now() - t0) / 1000;
          m.eta_s = (epochsTotal - epoch) * perEpochMs / 1000;
          this._emit('message', m);
          this._emit('message', { type: 'log', level: 'info',
            text: 'epoch ' + epoch + '/' + epochsTotal + ' done · train=' + m.train_loss.toFixed(4) +
                  ' val_loss=' + m.val_loss.toFixed(4) + ' val_pearson=' + m.val_pearson.toFixed(4) +
                  ' val_ci=' + m.val_ci.toFixed(4) + ' lr=' + m.lr.toExponential(2),
            t: Date.now() });
        }, epochAt);
        // Periodic insight events
        if (epoch === Math.max(2, Math.floor(epochsTotal * 0.25))) {
          this._later(() => this._emit('message', {
            type: 'insight', id: 'insight_warmup_' + epoch, tone: 'signal',
            title: 'Warm-up converged', body: 'Val loss has dropped below the warm-up threshold (0.6). The cosine schedule should now drive steady improvement.',
            why: 'val_loss < 0.6 for 2 consecutive epochs', conf: 'high', epoch, t: Date.now(),
          }), epochAt + 120);
        }
        if (epoch === Math.max(3, Math.floor(epochsTotal * 0.6))) {
          this._later(() => this._emit('message', {
            type: 'insight', id: 'insight_plateau_' + epoch, tone: 'warn',
            title: 'Validation plateau detected', body: 'val_pearson has moved < 0.01 over the last 3 epochs. Consider extending training or trying a lower LR.',
            why: 'Δval_pearson < 0.01 across 3 epochs', conf: 'med', epoch, t: Date.now(),
          }), epochAt + 120);
        }
        cursor = epochAt + 50;
      }

      // Final summary + status=completed
      this._later(() => {
        if (run.cancelled) return;
        run.status = 'completed';
        run.summary = finalSummary(run);
        this._emit('message', run.summary);
        this._emit('message', { type: 'log', level: 'ok', text: 'Test set evaluation done. Wall time: ' + Math.round(run.summary.wall_time_s) + 's', t: Date.now() });
        this._emit('message', { type: 'status', status: 'completed' });
        // Register in registry
        REGISTRY.push({
          id: 'model_' + runId,
          run_id: runId,
          template_id: run.template_id,
          template_label: run.template_label,
          status: 'registered',
          metrics: {
            test_pearson: run.summary.test_pearson,
            test_spearman: run.summary.test_spearman,
            test_rmse: run.summary.test_rmse,
            test_ci: run.summary.test_ci,
            test_auc_pki6: run.summary.test_auc_pki6,
            r2: run.summary.test_r2,
            n_params: run.summary.n_params,
          },
          promotions: [],
          created_at: Date.now(),
        });
      }, cursor + 200);
    }
    close() {
      this._closed = true;
      this.readyState = 2;
      this._timers.forEach(clearTimeout);
      this._timers = [];
    }
  }
  MockEventSource.CONNECTING = 0;
  MockEventSource.OPEN = 1;
  MockEventSource.CLOSED = 2;
  window.EventSource = MockEventSource;

  // ───────── Registry (seeded with a "prod" model) ─────────
  const REGISTRY = [
    {
      id: 'model_run_4168_kc2',
      run_id: 'run_4168_kc2',
      template_id: 'deepdta',
      template_label: 'DeepDTA',
      status: 'promoted',
      metrics: {
        test_pearson: 0.8742, test_spearman: 0.8581, test_rmse: 0.282,
        test_ci: 0.871, test_auc_pki6: 0.892, r2: 0.764, n_params: 1_240_000,
      },
      promotions: [{ id: 'prom_seed_0001', status: 'promoted', gates: [], created_at: STATE.bootTime - 86400 * 7 * 1000 }],
      created_at: STATE.bootTime - 86400 * 7 * 1000,
    },
    {
      id: 'model_run_4187_kc3',
      run_id: 'run_4187_kc3',
      template_id: 'conplex',
      template_label: 'ConPLex',
      status: 'registered',
      metrics: {
        test_pearson: 0.8654, test_spearman: 0.8492, test_rmse: 0.297,
        test_ci: 0.862, test_auc_pki6: 0.881, r2: 0.749, n_params: 2_140_000,
      },
      promotions: [],
      created_at: STATE.bootTime - 86400 * 2 * 1000,
    },
  ];

  // ───────── Splits / leakage report ─────────
  // Generate a simple SVG cluster map as a data URI (looks like a TSNE embedding).
  function buildClusterMapSvg(seed) {
    const rnd = mulberry32(seed);
    const W = 320, H = 220;
    const points = [];
    const colors = ['#4f8bff', '#ffae42', '#7cd1a4', '#ed7faf', '#a17cff', '#52c6c6'];
    const centers = [];
    for (let c = 0; c < 6; c++) {
      centers.push([30 + rnd() * (W - 60), 24 + rnd() * (H - 48)]);
    }
    for (let i = 0; i < 320; i++) {
      const c = i % 6;
      const [cx, cy] = centers[c];
      const x = cx + gauss(rnd) * 14;
      const y = cy + gauss(rnd) * 14;
      points.push(`<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.6" fill="${colors[c]}" opacity="0.75"/>`);
    }
    return `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><rect width="${W}" height="${H}" fill="#0c1018"/>${points.join('')}<text x="8" y="14" fill="#6a7588" font-family="monospace" font-size="10">UMAP · 6 clusters · MMseqs2 ≥30% + ECFP Tanimoto ≥0.40</text></svg>`;
  }
  function leakageReportFor(policy) {
    const map = {
      'random':       { n_train: 24045, n_val: 3005, n_test: 3006, verdict: 'WARN: 47% of test pairs share a UniRef90 cluster with a train pair (leakage suspected)', tone: 'warn' },
      'cluster':      { n_train: 18420, n_val: 3120, n_test: 3140, verdict: 'OK: 0% cluster overlap between train and test (MMseqs2 ≥30% / ECFP ≥0.40)', tone: 'ok' },
      'leakage-aware':{ n_train: 18420, n_val: 3120, n_test: 3140, verdict: 'OK: 0% cluster overlap between train and test', tone: 'ok' },
      'scaffold':     { n_train: 19880, n_val: 2940, n_test: 2980, verdict: 'OK: scaffolds disjoint across splits (Bemis–Murcko); proteins still shared', tone: 'ok' },
      'cold-target':  { n_train: 16002, n_val: 3120, n_test: 3140, verdict: 'OK: train and test proteins are fully disjoint (267 / 89 / 86 proteins)', tone: 'ok' },
      'cold-drug':    { n_train: 17240, n_val: 2920, n_test: 2960, verdict: 'OK: train and test ligands are fully disjoint (5 311 / 1 022 / 1 040 ligands)', tone: 'ok' },
      'cold-both':    { n_train: 12100, n_val: 2240, n_test: 2280, verdict: 'STRESS: proteins AND ligands disjoint — hardest split, lowest expected Pearson', tone: 'warn' },
    };
    const e = map[policy] || map['cluster'];
    const top_groups = [
      { id: 'lg_kinase_atp_pocket', n: 11214, kind: 'ATP-binding pocket (Pkinase)', residues: 'K-D-F-G',  similarity: 0.91, risk: 'high' },
      { id: 'lg_dfg_loop',          n: 6802,  kind: 'DFG motif kinases',            residues: 'D-F-G',    similarity: 0.83, risk: 'high' },
      { id: 'lg_imatinib_scaffold', n: 4218,  kind: 'Imatinib-like scaffold',       residues: '—',        similarity: 0.78, risk: 'med'  },
      { id: 'lg_sh2_pocket',        n: 1804,  kind: 'SH2 phosphotyrosine pocket',   residues: 'R-R-G',    similarity: 0.62, risk: 'med'  },
      { id: 'lg_pi3k_p110',         n: 1102,  kind: 'PI3K p110 lipid pocket',       residues: 'K-E-V',    similarity: 0.55, risk: 'low'  },
      { id: 'lg_mTOR_FRB',          n: 832,   kind: 'mTOR FRB domain',              residues: 'W-Y-S',    similarity: 0.41, risk: 'low'  },
      { id: 'lg_pkc_c1',            n: 612,   kind: 'PKC C1 domain (DAG-binding)',  residues: 'H-C-C',    similarity: 0.38, risk: 'low'  },
    ];
    const seed = (policy || 'cluster').split('').reduce((a, c) => a * 31 + c.charCodeAt(0), 7);
    const svg = buildClusterMapSvg(seed);
    const cluster_map_svg = 'data:image/svg+xml;base64,' + btoa(svg);
    return {
      policy: policy || 'cluster',
      benchmark: 'davis',
      n_train: e.n_train,
      n_val:   e.n_val,
      n_test:  e.n_test,
      leakage_groups: top_groups,
      top_groups,
      cluster_map_svg,
      cluster_map_png_base64: cluster_map_svg, // alias for older consumers
      verdict: e.verdict,
      verdict_tone: e.tone,
      live: true,
      version: 'v2026.04',
    };
  }

  // ───────── Source-URL resolver ─────────
  function sourceUrlFor(family, payload) {
    if (!payload) return { url: null };
    if (family.startsWith('protein')) {
      return { url: payload.uniprot ? 'https://www.uniprot.org/uniprotkb/' + payload.uniprot : null };
    }
    if (family.startsWith('ligand')) {
      return { url: payload.name ? 'https://www.ebi.ac.uk/chembl/g/#search_results/all/query=' + encodeURIComponent(payload.name) : null };
    }
    if (family.startsWith('structure')) {
      return { url: payload.pdb ? 'https://www.rcsb.org/structure/' + payload.pdb : null };
    }
    if (family.startsWith('source')) {
      const map = {
        bindingdb: 'https://www.bindingdb.org/', chembl: 'https://www.ebi.ac.uk/chembl/',
        pdbbind:   'https://www.pdbbind.org.cn/', davis: 'https://pubmed.ncbi.nlm.nih.gov/22037378/',
        kiba:      'https://pubs.acs.org/doi/10.1021/ci400709d',
        pdb:       'https://www.rcsb.org/', uniprot: 'https://www.uniprot.org/',
        pfam:      'https://www.ebi.ac.uk/interpro/entry/pfam/', interpro: 'https://www.ebi.ac.uk/interpro/',
        string:    'https://string-db.org/', hippie: 'http://cbdm-01.zdv.uni-mainz.de/~mschaefer/hippie/',
        pinder:    'https://www.pinder.sh/', plinder: 'https://www.plinder.sh/',
        alphafold: 'https://alphafold.ebi.ac.uk/',
      };
      return { url: map[payload.id] || null };
    }
    return { url: null };
  }

  // ───────── Fetch interceptor ─────────
  const _origFetch = window.fetch ? window.fetch.bind(window) : null;
  window.fetch = async function (input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    if (!url.startsWith('/api/v2/')) {
      // Pass-through for anything else (vendor assets are inlined; this is rarely hit).
      if (_origFetch) return _origFetch(input, init);
      return new Response('', { status: 404 });
    }
    try {
      return await routeRequest(url, init);
    } catch (err) {
      return jsonResponse({ error: 'mock_handler_threw', message: String(err) }, 500);
    }
  };

  async function routeRequest(url, init) {
    const u = new URL(url, 'http://sim.local');
    const path = u.pathname;
    const method = (init && init.method) || 'GET';
    const qp = u.searchParams;

    // System
    if (path === '/api/v2/system/user') {
      return jsonResponse({
        user_id: 'demo_user',
        handle: 'jfvitas',
        name: 'Jonathan Vitas',
        initials: 'JV',
        email: 'jfvitas@gmail.com',
        lab: 'ProteoSphere · Demo workspace',
      });
    }
    if (path === '/api/v2/system/host') {
      const cpu = 18 + Math.sin(Date.now() / 8000) * 8 + Math.random() * 4;
      const ram = 42 + Math.sin(Date.now() / 12000) * 6 + Math.random() * 2;
      return jsonResponse({
        hostname: 'jonnyv23',
        cpu_pct: +cpu.toFixed(1),
        cpu_count: 24,
        ram_pct: +ram.toFixed(1),
        ram_used_bytes: Math.round(ram / 100 * 64 * 1024 ** 3),
        ram_total_bytes: 64 * 1024 ** 3,
        disk_root: 'D:\\',
        disk_free_bytes: 1.4 * 1024 ** 4,
        disk_read_bps:  3.2 * 1024 ** 2 + Math.random() * 2 * 1024 ** 2,
        disk_write_bps: 1.1 * 1024 ** 2 + Math.random() * 1 * 1024 ** 2,
      });
    }
    if (path === '/api/v2/system/gpu') {
      const usedPct = 38 + Math.sin(Date.now() / 4000) * 12 + Math.random() * 6;
      return jsonResponse({
        available: true,
        device_name: 'NVIDIA GeForce RTX 5080',
        compute_cap: '12.0',
        torch_version: '2.5.1+cu124',
        used_pct: +usedPct.toFixed(1),
        free_pct: +(100 - usedPct).toFixed(1),
        used_memory_bytes: Math.round(usedPct / 100 * 16 * 1024 ** 3),
        total_memory_bytes: 16 * 1024 ** 3,
        cpu_only: false,
        status: 'live',
      });
    }
    if (path === '/api/v2/system/rosetta') {
      return jsonResponse({
        platform: 'win-amd64',
        platform_supported: false,
        loaded: false,
        license_acknowledged: false,
        install_url: 'https://www.pyrosetta.org/downloads',
        hint: 'Windows wheels are not published; use the Linux WSL2 path or run on a Linux node.',
      });
    }
    if (path === '/api/v2/system/rosetta/install') {
      return jsonResponse({
        status: { platform: 'win-amd64', platform_supported: false, loaded: false },
        install: { status: 'unsupported', hint: 'Windows wheels are not available — switch to WSL2 or a Linux node.' },
      });
    }

    // Ingest catalog
    if (path === '/api/v2/ingest/catalog') {
      return jsonResponse({
        version: 'v2026.04',
        last_consolidation: '2026-04-12T07:39:00Z',
        sources: _allSources().map(s => ({ id: s.id, label: s.name, n_proteins: Math.round(s.rows / 80), n_ligands: Math.round(s.rows / 40), n_rows: s.rows, integrated: true })),
        live_row_counts: {
          'view_bindingdb_pairs': 2_780_000,
          'view_chembl_acts':     21_440_000,
          'view_pdbbind':         23_104,
          'view_davis':           30_056,
          'view_kiba':            118_254,
          'view_uniprot':         262_400_000,
          'view_pdb':             218_400,
          'view_hippie_ppi':      770_400,
        },
      });
    }

    // Featurizers
    if (path === '/api/v2/featurizers') {
      return jsonResponse(FEATURIZER_CATALOG);
    }

    // Library family (paginated)
    let m = path.match(/^\/api\/v2\/library\/(proteins|ligands|edges|structures|motifs|sources|releases)$/);
    if (m) {
      const family = m[1];
      const page = Math.max(1, parseInt(qp.get('page')) || 1);
      const perPage = Math.max(1, parseInt(qp.get('per_page')) || 50);
      const q = qp.get('q') || '';
      const tier = qp.get('tier') || 'any';
      let rows;
      switch (family) {
        case 'proteins':   rows = _allProteins();   break;
        case 'ligands':    rows = _allLigands();    break;
        case 'edges':      rows = _allEdges();      break;
        case 'structures': rows = _allStructures(); break;
        case 'motifs':     rows = _allMotifs();     break;
        case 'sources':    rows = _allSources();    break;
        case 'releases':   rows = _allReleases();   break;
      }
      if (tier === 'release') rows = rows.filter(r => !r.tier || r.tier === 'release');
      const out = paginate(rows, q, page, perPage);
      return jsonResponse({ family, page, per_page: perPage, total: out.total, rows: out.rows, live: true, version: 'v2026.04' });
    }
    if (path === '/api/v2/library/_source_url') {
      const family = qp.get('family') || '';
      let payload = {};
      try { payload = JSON.parse(qp.get('payload') || '{}'); } catch {}
      return jsonResponse(sourceUrlFor(family, payload));
    }
    if (path === '/api/v2/library/_schema.sql') {
      const sql = `-- ProteoSphere v2 warehouse schema (DuckDB)\n-- Generated: 2026-04-12\n\nCREATE TABLE proteins (\n  uniprot VARCHAR PRIMARY KEY,\n  name VARCHAR,\n  organism VARCHAR,\n  sequence VARCHAR,\n  length INTEGER,\n  family VARCHAR,\n  tier VARCHAR\n);\n\nCREATE TABLE ligands (\n  ligand_id VARCHAR PRIMARY KEY,\n  smiles VARCHAR,\n  mw DOUBLE,\n  qed DOUBLE,\n  source VARCHAR\n);\n\nCREATE TABLE binding_edges (\n  protein_uniprot VARCHAR REFERENCES proteins(uniprot),\n  ligand_id VARCHAR REFERENCES ligands(ligand_id),\n  activity_type VARCHAR,\n  value DOUBLE,\n  source VARCHAR,\n  year INTEGER\n);\n`;
      return new Response(sql, { status: 200, headers: { 'Content-Type': 'text/plain' } });
    }

    // Pipeline templates
    if (path === '/api/v2/pipeline/templates') {
      return jsonResponse({
        templates: TEMPLATES,
        supported_templates: TEMPLATES.map(t => t.id),
      });
    }
    // Pipeline launch
    if (path === '/api/v2/pipeline/launch' && method === 'POST') {
      let body = {};
      try { body = JSON.parse((init && init.body) || '{}'); } catch {}
      const created = createRun(body);
      return jsonResponse(created, 200);
    }
    // Run-specific endpoints
    m = path.match(/^\/api\/v2\/pipeline\/runs\/([^\/]+)(\/(stream|cancel|results|predict))?$/);
    if (m) {
      const runId = decodeURIComponent(m[1]);
      const sub = m[3] || '';
      const run = STATE.runs[runId];
      if (sub === 'cancel' && method === 'POST') {
        if (run) { run.cancelled = true; run.status = 'cancelled'; }
        return jsonResponse({ run_id: runId, status: 'cancelled' });
      }
      if (sub === 'results') {
        if (!run) return jsonResponse({ error: 'not_found' }, 404);
        const s = run.summary || finalSummary(run);
        // Build scatter / ROC / calibration / residuals from seeded synth data.
        const rnd = mulberry32(run.hparams.seed);
        const inliers = [], outliers = [];
        for (let i = 0; i < 240; i++) {
          const x = rnd();
          const y = Math.min(1, Math.max(0, x + (gauss(rnd) * 0.08)));
          inliers.push([x, y]);
        }
        for (let i = 0; i < 10; i++) {
          outliers.push([rnd(), rnd()]);
        }
        // ROC: GUI expects [{thr, fpr, tpr}, ...] objects (NOT [fpr,tpr] arrays).
        const roc = [];
        for (let i = 0; i <= 50; i++) {
          const fpr = i / 50;
          const tpr = Math.min(1, fpr + 0.7 * (1 - Math.exp(-3 * fpr)));
          const thr = 4 + (1 - i / 50) * 6; // pKi threshold sweep 10→4
          roc.push({ thr: +thr.toFixed(2), fpr: +fpr.toFixed(4), tpr: +tpr.toFixed(4) });
        }
        // pKd output range — Davis-style 4..10.5 (matches finalSummary RMSE units).
        const yLo = 4.0, yHi = 10.5;
        // Calibration: GUI expects [{pred_mean, actual_mean, n, abs_err, bin}, ...]
        // — both in real pKd units. The GUI projects them to [0,1] using y_pkd_range.
        const calib = [];
        for (let i = 0; i < 10; i++) {
          const p = (i + 0.5) / 10;
          const predPki   = yLo + p * (yHi - yLo);
          const actualPki = predPki + gauss(rnd) * 0.20;
          const n = 280 + Math.floor(rnd() * 60);
          calib.push({
            bin: i + 1,
            pred_mean:   +predPki.toFixed(4),
            actual_mean: +actualPki.toFixed(4),
            n,
            abs_err: +Math.abs(predPki - actualPki).toFixed(4),
          });
        }
        // Residual histogram — 21 bins centered on zero, ~RMSE width.
        const histRmse = s.test_rmse || 0.5;
        const edges = [];
        const counts = [];
        const nBins = 21;
        const halfRange = 3.0; // ± pKi units
        for (let i = 0; i <= nBins; i++) edges.push(-halfRange + (2 * halfRange) * i / nBins);
        for (let i = 0; i < nBins; i++) {
          const center = (edges[i] + edges[i + 1]) / 2;
          const v = 280 * Math.exp(-Math.pow(center / histRmse, 2)) + 5 * Math.abs(gauss(rnd));
          counts.push(Math.round(v));
        }
        return jsonResponse({
          run_id: runId,
          status: run.status,
          summary: s,
          results: {
            metrics: {
              pearson: s.test_pearson, spearman: s.test_spearman,
              rmse: s.test_rmse, mae: s.test_mae || 0.188, r2: s.test_r2, ci: s.test_ci,
              auc_pki6: s.test_auc_pki6, n: 3006,
            },
            scatter_inliers: inliers,
            scatter_outliers: outliers,
            y_pkd_range: [yLo, yHi],
            roc: {
              auc: s.test_auc_pki6,
              points: roc,
              pos: 1124,
              neg: 1882,
              threshold: 6.0,
            },
            calibration: calib,
            residual_hist: {
              edges,
              counts,
              rmse: histRmse,
            },
          },
          template_id: run.template_id, template_label: run.template_label,
          hparams: run.hparams,
        });
      }
      if (sub === 'predict' && method === 'POST') {
        if (!run) return jsonResponse({ error: 'not_found', message: 'Run not registered.' }, 404);
        if (run.status !== 'completed') return jsonResponse({ error: 'not_ready', message: 'Run has not completed yet.' }, 409);
        let body = {};
        try { body = JSON.parse((init && init.body) || '{}'); } catch {}
        const seqHash = (body.sequence || '').length;
        const smiHash = (body.smiles   || '').length;
        const rnd = mulberry32((seqHash * 31 + smiHash + run.hparams.seed) >>> 0);
        const pkd = 6.0 + rnd() * 4.0;
        return jsonResponse({
          run_id: runId,
          predicted_pkd: +pkd.toFixed(4),
          predicted_kd_nm: +Math.pow(10, -pkd + 9).toFixed(3),
          input: { sequence_truncated: (body.sequence || '').length > 2048, smiles: body.smiles },
        });
      }
      if (sub === '' || sub === undefined) {
        if (!run) return jsonResponse({ error: 'not_found' }, 404);
        return jsonResponse({
          id: run.id, status: run.status, hparams: run.hparams,
          template_label: run.template_label, summary: run.summary || null,
        });
      }
      // /stream is handled by EventSource shim, not fetch.
    }

    // Registry
    if (path === '/api/v2/registry/models') {
      const prod = REGISTRY.find(m => m.status === 'promoted') || null;
      return jsonResponse({
        items: REGISTRY,
        current_prod: prod,
      });
    }
    m = path.match(/^\/api\/v2\/registry\/models\/([^\/]+)$/);
    if (m) {
      const id = decodeURIComponent(m[1]);
      const model = REGISTRY.find(mm => mm.id === id || mm.run_id === id);
      if (!model) return jsonResponse({ error: 'not_found' }, 404);
      return jsonResponse(model);
    }
    if (path === '/api/v2/registry/promotions' && method === 'POST') {
      let body = {};
      try { body = JSON.parse((init && init.body) || '{}'); } catch {}
      const candidate = REGISTRY.find(mm => mm.id === body.model_id || mm.run_id === body.model_id);
      if (!candidate) return jsonResponse({ error: 'no_candidate' }, 400);
      STATE.seqPromotion += 1;
      const sm = candidate.metrics;
      const gates = [
        { id: 'metric_floor',  label: 'Test Pearson ≥ 0.85',                              severity: 'blocker', passed: sm.test_pearson >= 0.85, detail: 'Got ' + sm.test_pearson.toFixed(4) },
        { id: 'rmse_ceiling',  label: 'Test RMSE ≤ 0.32',                                 severity: 'blocker', passed: sm.test_rmse  <= 0.32, detail: 'Got ' + sm.test_rmse.toFixed(4) },
        { id: 'param_budget',  label: 'Params ≤ 5M',                                      severity: 'blocker', passed: (sm.n_params || 0) <= 5_000_000, detail: ((sm.n_params || 0)/1e6).toFixed(2) + 'M' },
        { id: 'leakage_audit', label: 'Cluster overlap ≤ 0% (leakage-aware split)',       severity: 'blocker', passed: true,  detail: 'Verified against v2026.04 leakage groups' },
        { id: 'cold_target',   label: 'Cold-target Pearson ≥ 0.6 (stress test)',           severity: 'advisory',passed: sm.test_pearson >= 0.78, detail: 'Stress slice'   },
        { id: 'review_note',   label: 'Reviewer note attached',                           severity: 'advisory',passed: !!body.comment, detail: body.comment ? 'present' : 'no note'  },
      ];
      const promotion = {
        id: 'prom_' + STATE.seqPromotion + '_' + Date.now().toString(36),
        model_id: candidate.id,
        status: 'open',
        comment: body.comment || '',
        gates,
        created_at: Date.now(),
      };
      candidate.promotions = candidate.promotions || [];
      candidate.promotions.unshift(promotion);
      return jsonResponse({ promotion });
    }
    m = path.match(/^\/api\/v2\/registry\/promotions\/([^\/]+)\/decide$/);
    if (m && method === 'POST') {
      const promId = decodeURIComponent(m[1]);
      let body = {};
      try { body = JSON.parse((init && init.body) || '{}'); } catch {}
      let model = null, promotion = null;
      for (const mm of REGISTRY) {
        const found = (mm.promotions || []).find(p => p.id === promId);
        if (found) { model = mm; promotion = found; break; }
      }
      if (!promotion) return jsonResponse({ error: 'not_found' }, 404);
      if (body.approve) {
        promotion.status = 'promoted';
        // Demote previous prod
        for (const mm of REGISTRY) if (mm.status === 'promoted' && mm !== model) mm.status = 'registered';
        model.status = 'promoted';
      } else {
        promotion.status = 'rejected';
      }
      promotion.decided_by = body.actor || 'user';
      promotion.note = body.note || '';
      promotion.decided_at = Date.now();
      return jsonResponse({ promotion, model });
    }

    // Splits / leakage report
    if (path === '/api/v2/splits/leakage_report') {
      const policy = qp.get('policy') || qp.get('split_policy') || 'cluster';
      return jsonResponse(leakageReportFor(policy));
    }

    // Anything else → 404
    return jsonResponse({ error: 'mock_not_implemented', path, method }, 404);
  }

  // ───────── Simulation banner ─────────
  window.addEventListener('DOMContentLoaded', () => {
    if (document.getElementById('ps-sim-banner')) return;
    const banner = document.createElement('div');
    banner.id = 'ps-sim-banner';
    banner.style.cssText = [
      'position:fixed', 'top:0', 'right:0', 'left:0',
      'background:linear-gradient(90deg,#7a3eb1,#3274d8)', 'color:#fff',
      'font-family:Geist,system-ui,sans-serif', 'font-size:11px',
      'padding:3px 12px', 'text-align:center', 'z-index:99999',
      'letter-spacing:0.05em', 'opacity:0.92', 'pointer-events:none',
      'box-shadow:0 1px 4px rgba(0,0,0,0.4)',
    ].join(';');
    banner.textContent = 'SIMULATION MODE · All data is faked client-side. No backend, no network requests.';
    document.body.appendChild(banner);
    // Push the app down so the topbar is not occluded by the banner.
    document.body.style.paddingTop = '22px';
  });
})();
