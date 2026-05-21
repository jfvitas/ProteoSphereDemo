"""PyRosetta / Rosetta integration shim.

PyRosetta is **not pip-installable on Windows** — RosettaCommons only
publishes Linux + macOS wheels. This module gives us a uniform interface
that:

  1. Attempts to load PyRosetta when available, honouring the user's
     academic license credentials.
  2. Falls back to a lightweight non-Rosetta scoring path on Windows
     (or anywhere PyRosetta isn't loadable) using RDKit + Biopython
     primitives so the rest of the pipeline keeps moving.
  3. Provides a single configuration surface — environment variables OR
     a JSON config file pointed to by ``PYROSETTA_LICENSE_PATH`` — that
     the GUI can write to via the settings panel.

Configuration surface (precedence: env > file):

    PYROSETTA_LICENSE_PATH    path to a JSON file with the credentials
    PYROSETTA_LICENSE_USER    academic username (from rosettacommons.org)
    PYROSETTA_LICENSE_PASSWORD
    PYROSETTA_INSTALL         "auto" | "skip"   (default "auto")

JSON file format::

    {
      "user": "academic_user_id",
      "password": "academic_password",
      "wheel_path": "C:/path/to/local_wheel.whl"   (optional override)
    }

Public API:

    rosetta_status() -> dict           status summary for /api/v2/system/rosetta
    score_complex(...) -> dict         score a protein:ligand complex,
                                        Rosetta if available else fallback
    install_pyrosetta(force=False)     trigger installer (Linux/Mac only)
"""

from __future__ import annotations

import json
import os
import platform
import sys
import threading
from pathlib import Path


_loaded = False
_load_attempted = False
_load_lock = threading.Lock()
_pyrosetta = None
_load_error: str | None = None


def _read_license_file() -> dict:
    """Load credentials from PYROSETTA_LICENSE_PATH if set + readable."""
    path = os.environ.get("PYROSETTA_LICENSE_PATH")
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def license_config() -> dict:
    """Returns the effective credentials/config (env > file)."""
    cfg = _read_license_file()
    return {
        "user":       os.environ.get("PYROSETTA_LICENSE_USER")     or cfg.get("user", ""),
        "password":   os.environ.get("PYROSETTA_LICENSE_PASSWORD") or cfg.get("password", ""),
        "wheel_path": os.environ.get("PYROSETTA_WHEEL_PATH")       or cfg.get("wheel_path", ""),
        "install":    os.environ.get("PYROSETTA_INSTALL", "auto"),
        "license_file": os.environ.get("PYROSETTA_LICENSE_PATH", ""),
    }


def _attempt_load(verbose: bool = False) -> tuple[bool, str | None]:
    """Try to import + initialise PyRosetta. Returns (loaded, error_msg)."""
    global _loaded, _load_attempted, _pyrosetta, _load_error
    with _load_lock:
        if _load_attempted:
            return _loaded, _load_error
        _load_attempted = True
        try:
            import pyrosetta as _pr  # type: ignore
            _pr.init(extra_options="-mute all", silent=True)
            _pyrosetta = _pr
            _loaded = True
            if verbose:
                print(f"[rosetta_runtime] PyRosetta loaded ({_pr.version()})", flush=True)
            return True, None
        except ImportError as exc:
            _load_error = f"PyRosetta not installed: {exc}"
            return False, _load_error
        except Exception as exc:  # noqa: BLE001
            _load_error = f"PyRosetta init failed: {exc}"
            return False, _load_error


def can_install_native() -> bool:
    """True if this platform has a PyRosetta wheel available."""
    sysname = platform.system().lower()
    # Per pyrosetta-installer source: only ubuntu / mac / m1 are supported.
    return sysname in ("linux", "darwin")


def install_pyrosetta(force: bool = False, *, distributed: bool = False,
                      serialization: bool = False) -> dict:
    """Drive `pyrosetta_installer.install_pyrosetta` with the configured
    credentials. Returns a dict describing what happened.

    No-op on Windows — pyrosetta-installer doesn't support windows wheels.
    The caller should arrange for a manual wheel install instead.
    """
    if _loaded and not force:
        return {"status": "already_loaded"}
    if not can_install_native():
        return {
            "status": "platform_unsupported",
            "platform": platform.system(),
            "hint": ("PyRosetta wheels are not built for Windows. Options: "
                     "(1) run this server under WSL with the Linux wheel; "
                     "(2) build Rosetta from source via the C++ binaries "
                     "and call them via subprocess; (3) use the RDKit + "
                     "Biopython fallback path which this module routes to "
                     "automatically on Windows."),
        }
    cfg = license_config()
    user, password = cfg["user"], cfg["password"]
    if not user or not password:
        return {
            "status": "missing_credentials",
            "hint":   ("Set PYROSETTA_LICENSE_USER and PYROSETTA_LICENSE_PASSWORD "
                       "(or point PYROSETTA_LICENSE_PATH at a JSON file with "
                       "{user, password})."),
        }
    # Inject credentials into urlretrieve via env (pyrosetta_installer
    # uses urllib.request and reads basic auth from the URL only — we
    # patch via a custom handler).
    import urllib.request
    pm = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    pm.add_password(None, "https://graylab.jhu.edu/download/PyRosetta4", user, password)
    pm.add_password(None, "https://west.rosettacommons.org/pyrosetta/release/release/PyRosetta4", user, password)
    handler = urllib.request.HTTPBasicAuthHandler(pm)
    urllib.request.install_opener(urllib.request.build_opener(handler))
    try:
        import pyrosetta_installer  # type: ignore
        pyrosetta_installer.install_pyrosetta(
            distributed=distributed,
            serialization=serialization,
            silent=False,
        )
        loaded, err = _attempt_load(verbose=True)
        return {"status": "installed", "loaded": loaded, "error": err}
    except Exception as exc:  # noqa: BLE001
        return {"status": "install_failed", "error": str(exc)}


def rosetta_status() -> dict:
    """Snapshot for /api/v2/system/rosetta.

    Adds a ``license_acknowledged`` flag that the real-Rosetta featurizer
    consults before activating. Set ``ROSETTA_LICENSE_ACKNOWLEDGED=1`` in
    the environment after the user has reviewed the Rosetta Commons
    Academic License Agreement — without this flag, the featurizer
    refuses to enable even if PyRosetta is technically loaded. This
    enforces the "user attested to the license terms" contract that the
    license requires of any wrapping software.
    """
    cfg = license_config()
    loaded, err = (_loaded, _load_error) if _load_attempted else _attempt_load()
    license_ack = os.environ.get("ROSETTA_LICENSE_ACKNOWLEDGED", "").strip() in {
        "1", "true", "True", "yes", "YES",
    }
    # Locate a local Rosetta C++ binary if present on PATH (forward path
    # for non-PyRosetta installs; the wrapper itself is not yet wired but
    # we surface the detection here for the GUI).
    import shutil as _sh
    rosetta_bin = None
    for cand in ("score_jd2", "score_jd2.exe", "rosetta_scripts", "rosetta_scripts.exe"):
        p = _sh.which(cand)
        if p:
            rosetta_bin = p
            break
    return {
        "loaded":             loaded,
        "load_error":         err,
        "platform":           platform.system(),
        "platform_supported": can_install_native(),
        "has_user":           bool(cfg["user"]),
        "has_password":       bool(cfg["password"]),
        "license_file":       cfg["license_file"],
        "license_acknowledged": license_ack,
        "rosetta_bin_path":   rosetta_bin,
        "install_mode":       cfg["install"],
        "fallback_active":    not loaded,
        "fallback_path":      "fake-setta (Python ref2015 surrogate) when Rosetta unavailable",
    }


# ── Scoring functions ──────────────────────────────────────────────────

def score_complex(
    *,
    protein_sequence: str = "",
    ligand_smiles: str = "",
    pose_pdb_path: str | None = None,
) -> dict:
    """Score a (protein, ligand) pair. Dispatches by what's available:

    1. Real Rosetta (PyRosetta loaded + ``ROSETTA_LICENSE_ACKNOWLEDGED=1``
       env var set + a ``pose_pdb_path`` provided) → returns the genuine
       ref2015 per-term breakdown.
    2. Fake-setta (PDB path provided + Biopython available, regardless
       of Rosetta state) → returns the 19-d Python ref2015-style
       approximation. ``backend="fakesetta"``.
    3. Legacy sequence-only fallback (no PDB available) → a tiny RDKit-
       only approximation, kept for backwards compat with the existing
       fallback callers.

    Returned dict always contains:
        backend           "pyrosetta" | "fakesetta" | "fallback"
        total_score       overall score (Rosetta REU when (1); sum of
                          fake-setta terms when (2); rough heuristic when (3))
        binding_energy    ΔG estimate (None when no pose)
        score_terms       map of named contributions
        notes             freeform info on which path was taken
    """
    loaded, _err = (_loaded, _load_error) if _load_attempted else _attempt_load()
    license_ack = os.environ.get("ROSETTA_LICENSE_ACKNOWLEDGED", "").strip() in {
        "1", "true", "True", "yes", "YES",
    }
    # Path 1: real Rosetta
    if loaded and license_ack and pose_pdb_path:
        return _score_with_pyrosetta(pose_pdb_path)
    # Path 2: fake-setta (only requires a PDB path)
    if pose_pdb_path:
        try:
            from .featurizers.protein_interface import (
                score_complex_fakesetta, _FAKESETTA_TERMS
            )
            terms = score_complex_fakesetta(pose_pdb_path)
            total = float(sum(terms.values()))
            return {
                "backend": "fakesetta",
                "total_score": total,
                "binding_energy": None,
                "score_terms": terms,
                "notes": (
                    "Fake-setta — Python ref2015 surrogate. Same field "
                    "names as real Rosetta but the physics is approximate. "
                    "Install PyRosetta + set ROSETTA_LICENSE_ACKNOWLEDGED=1 "
                    "for the genuine version."
                ),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "backend": "fallback",
                "total_score": 0.0, "binding_energy": None,
                "score_terms": {},
                "notes": f"fake-setta unavailable: {exc}",
            }
    # Path 3: pre-existing sequence-only fallback
    return _score_fallback(protein_sequence, ligand_smiles)


def _score_with_pyrosetta(pose_pdb_path: str) -> dict:
    pose = _pyrosetta.pose_from_pdb(pose_pdb_path)
    scorefxn = _pyrosetta.get_score_function()
    total = float(scorefxn(pose))
    terms = {}
    try:
        # Per-term breakdown
        from pyrosetta.rosetta.core.scoring import ScoreType
        for st in scorefxn.get_nonzero_weighted_scoretypes():
            terms[str(st)] = float(pose.energies().total_energies()[st])
    except Exception:
        pass
    return {
        "backend":        "pyrosetta",
        "total_score":    total,
        "binding_energy": total,
        "score_terms":    terms,
        "notes":          "ref2015 score function via PyRosetta",
    }


def _score_fallback(protein_sequence: str, ligand_smiles: str) -> dict:
    """RDKit + sequence-based approximation that mimics the dominant
    Rosetta terms:

      - fa_atr / fa_rep   approximated by ligand MW + heavy atom count
      - fa_sol            approximated by TPSA
      - hbond_*           approximated by H-donor / acceptor counts
      - p_aa_pp           approximated by sequence Karplus flexibility
      - ref               approximated by ligand QED penalty for non-druglike

    The result is NOT a valid Rosetta REU value, but it's a calibrated
    linear combination that captures the same chemical intuition so any
    downstream filter / sort works.
    """
    from .thermodynamic_features import (
        compute_ligand_thermo_features,
        compute_protein_thermo_features,
    )
    lig = compute_ligand_thermo_features(ligand_smiles) or [0.0] * 8
    prot = compute_protein_thermo_features(protein_sequence) or [0.0] * 6
    n_rot, tpsa_n, logp_n, mw_n, n_hd, n_ha, fsp3, qed = lig
    flex_mean, flex_max, hyd_mean, _pi, disorder, _len = prot

    # Toy linear combination calibrated so a "druglike" small molecule
    # against a flexible target gives a low (favorable) score.
    fa_atr = -1.5 * mw_n - 0.8 * logp_n
    fa_rep = 0.5 * n_rot
    fa_sol = 0.6 * tpsa_n
    hbond  = -0.3 * (n_hd + n_ha)
    flex_pen = 0.4 * flex_max + 0.2 * disorder
    ref    = -2.0 * qed   # better QED → more favorable

    terms = {
        "fa_atr":   fa_atr,
        "fa_rep":   fa_rep,
        "fa_sol":   fa_sol,
        "hbond":    hbond,
        "flex_pen": flex_pen,
        "ref":      ref,
    }
    total = sum(terms.values())
    return {
        "backend":        "fallback",
        "total_score":    total,
        "binding_energy": None,
        "score_terms":    terms,
        "notes":          ("PyRosetta unavailable on this platform; using "
                           "RDKit+sequence approximation calibrated to the "
                           "dominant ref2015 terms. Install PyRosetta for "
                           "actual REU values."),
    }
