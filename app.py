"""
Reconciliation Test Data Generator — Flask Backend
Run: python app.py
Open: http://localhost:5050
"""

import json
import os
import sys
import yaml
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify, send_file, render_template_string

sys.path.insert(0, str(Path(__file__).parent))
from generators.nfs_atm import generate
from generators.matrix_generator import generate_matrix
from generators.visa_matrix_generator import generate_visa_matrix
from generators.idfc.orchestrator import generate_idfc_visa
from generators.idfc.nfs_acq_orchestrator import generate_idfc_nfs_acq
from generators.idfc.mc_orchestrator import generate_idfc_mc
from generators.hyosung_ej import generate_hyosung_ej

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

app = Flask(__name__)


# ─────────────────────────────────────────────
# USE CASE LOADER
# ─────────────────────────────────────────────

def load_all_use_cases() -> list:
    uc_dir = BASE_DIR / "use_cases"
    use_cases = []
    for f in sorted(uc_dir.glob("*.yaml")):
        with open(f, encoding="utf-8") as fh:
            uc = yaml.safe_load(fh)
        use_cases.append({
            "id": uc.get("id"),
            "name": uc.get("name"),
            "description": uc.get("description", ""),
            "domain": uc.get("domain", ""),
            "channel": uc.get("channel", ""),
            "bank_role": uc.get("bank_role", ""),
            "network": uc.get("network", ""),
            "status": uc.get("status", "placeholder"),
            "participant_files": uc.get("participant_files", []),
            "scenarios": uc.get("scenarios", []),
        })
    return use_cases


# ─────────────────────────────────────────────
# API ROUTES
# ─────────────────────────────────────────────

import re as _re

def _validate_gl_accounts(gl_list):
    """Returns (ok: bool, error: str|None). Validates each GL is 8-16 digits, not all zeros."""
    if not gl_list:
        return True, None
    if len(gl_list) > 2:
        return False, "At most 2 GL accounts allowed (ATM GL, POS GL)"
    for gl in gl_list:
        gl = str(gl).strip()
        if not _re.fullmatch(r'\d{8,16}', gl):
            return False, f"GL '{gl}' must be 8–16 digits only"
        if all(c == '0' for c in gl):
            return False, f"GL '{gl}' cannot be all zeros"
    return True, None

@app.route("/api/use-cases")
def get_use_cases():
    return jsonify(load_all_use_cases())


@app.route("/api/use-cases/<uc_id>")
def get_use_case(uc_id):
    ucs = load_all_use_cases()
    for uc in ucs:
        if uc["id"] == uc_id:
            return jsonify(uc)
    return jsonify({"error": "Not found"}), 404


@app.route("/api/generate", methods=["POST"])
def run_generate():
    data = request.json or {}
    use_case_id = data.get("use_case_id")
    volume = int(data.get("volume", 50))
    tran_date_str = data.get("tran_date", "")

    if not use_case_id:
        return jsonify({"error": "use_case_id required"}), 400

    # Only NFS ATM issuer is active
    ucs = {uc["id"]: uc for uc in load_all_use_cases()}
    if use_case_id not in ucs:
        return jsonify({"error": f"Unknown use case: {use_case_id}"}), 400
    if ucs[use_case_id]["status"] == "placeholder":
        return jsonify({"error": "This use case is not yet implemented. Coming in the next phase."}), 400

    if tran_date_str:
        try:
            tran_date = datetime.strptime(tran_date_str, "%Y-%m-%d")
        except ValueError:
            tran_date = datetime.today()
    else:
        tran_date = datetime.today()

    if volume < 1 or volume > 10000:
        return jsonify({"error": "Volume must be between 1 and 10000"}), 400

    try:
        if use_case_id == "visa_pos_issuer":
            from generators.visa_pos import generate as generate_visa
            result = generate_visa(use_case_id, volume=volume, tran_date=tran_date,
                                   output_dir=OUTPUT_DIR)
        else:
            result = generate(use_case_id, volume=volume, tran_date=tran_date,
                              output_dir=OUTPUT_DIR)
        return jsonify(result)
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/download/<run_id>")
def download_zip(run_id):
    zip_path = OUTPUT_DIR / f"recon_testdata_{run_id}.zip"
    if not zip_path.exists():
        return jsonify({"error": "File not found. Generate first."}), 404
    return send_file(zip_path, as_attachment=True,
                     download_name=zip_path.name,
                     mimetype="application/zip")


@app.route("/api/manifest/<run_id>")
def get_manifest(run_id):
    manifest_path = OUTPUT_DIR / f"manifest_{run_id}.json"
    if not manifest_path.exists():
        return jsonify({"error": "Manifest not found"}), 404
    with open(manifest_path, encoding="utf-8") as f:
        return jsonify(json.load(f))


@app.route("/api/preview/<run_id>/<file_type>")
def preview_file(run_id, file_type):
    """Return first 10 rows of a generated file. Works for both scenario and matrix runs."""
    # Try all manifest filename patterns
    for prefix in ["manifest_visa_matrix_", "manifest_matrix_", "manifest_"]:
        manifest_path = OUTPUT_DIR / f"{prefix}{run_id}.json"
        if manifest_path.exists():
            break
    else:
        return jsonify({"error": "Run not found"}), 404

    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    file_name = manifest.get("files", {}).get(file_type)
    if not file_name:
        return jsonify({"error": f"File type '{file_type}' not in this run"}), 404

    file_path = OUTPUT_DIR / file_name
    if not file_path.exists():
        return jsonify({"error": "File not found on disk"}), 404

    with open(file_path, encoding="ascii", errors="replace") as f:
        lines = f.readlines()

    preview_lines = [l.rstrip("\n") for l in lines[:10]]
    return jsonify({
        "file": file_name,
        "total_lines": len(lines),
        "preview": preview_lines,
    })


@app.route("/api/generate-matrix", methods=["POST"])
def run_generate_matrix():
    data = request.json or {}
    volume   = int(data.get("volume", 500))
    ok_pct   = float(data.get("ok_pct", 99.0))
    tran_date_str = data.get("tran_date", "")

    if volume < 70 or volume > 50000:
        return jsonify({"error": "Volume must be between 70 and 50000"}), 400
    if not (0 <= ok_pct <= 100):
        return jsonify({"error": "ok_pct must be between 0 and 100"}), 400

    if tran_date_str:
        try:
            tran_date = datetime.strptime(tran_date_str, "%Y-%m-%d")
        except ValueError:
            tran_date = datetime.today()
    else:
        tran_date = datetime.today()

    try:
        result = generate_matrix(volume=volume, ok_pct=ok_pct,
                                 tran_date=tran_date, output_dir=OUTPUT_DIR)
        return jsonify(result)
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/download-matrix/<run_id>")
def download_matrix_zip(run_id):
    zip_path = OUTPUT_DIR / f"recon_matrix_{run_id}.zip"
    if not zip_path.exists():
        return jsonify({"error": "File not found"}), 404
    return send_file(zip_path, as_attachment=True,
                     download_name=zip_path.name, mimetype="application/zip")


@app.route("/api/generate-visa-matrix", methods=["POST"])
def run_generate_visa_matrix():
    data = request.json or {}
    volume        = int(data.get("volume", 500))
    ok_pct        = float(data.get("ok_pct", 99.0))
    tran_date_str = data.get("tran_date", "")

    if volume < 70 or volume > 50000:
        return jsonify({"error": "Volume must be between 70 and 50000"}), 400
    if not (0 <= ok_pct <= 100):
        return jsonify({"error": "ok_pct must be between 0 and 100"}), 400

    if tran_date_str:
        try:
            tran_date = datetime.strptime(tran_date_str, "%Y-%m-%d")
        except ValueError:
            tran_date = datetime.today()
    else:
        tran_date = datetime.today()

    try:
        result = generate_visa_matrix(volume=volume, ok_pct=ok_pct,
                                      tran_date=tran_date, output_dir=OUTPUT_DIR)
        return jsonify(result)
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/download-visa-matrix/<run_id>")
def download_visa_matrix_zip(run_id):
    zip_path = OUTPUT_DIR / f"visa_matrix_{run_id}.zip"
    if not zip_path.exists():
        return jsonify({"error": "File not found. Generate first."}), 404
    return send_file(zip_path, as_attachment=True,
                     download_name=zip_path.name, mimetype="application/zip")


@app.route("/api/generate-idfc-visa", methods=["POST"])
def run_generate_idfc_visa():
    data = request.json or {}
    volume                 = int(data.get("volume", 20))
    tran_date_str          = data.get("tran_date", "")
    bank_id                = data.get("bank_id", "idfc")
    ok_pct                 = float(data.get("ok_pct", 95.0))
    selected_scenarios     = data.get("selected_scenarios", None)
    custom_scenarios       = data.get("custom_scenarios", []) or []
    gl_accounts            = [str(g).strip() for g in (data.get("gl_accounts") or [])]
    no_cbsmcw_duplicates   = bool(data.get("no_cbsmcw_duplicates", True))
    num_days               = max(1, min(5, int(data.get("num_days", 1))))
    _valid_mm = {"none", "lower", "higher"}
    late_network_mismatches = [
        m for m in (data.get("late_network_mismatches") or ["none"]) if m in _valid_mm
    ] or ["none"]

    if volume < 1 or volume > 1000000:
        return jsonify({"error": "Volume must be between 1 and 1,000,000"}), 400
    if not (0 <= ok_pct <= 100):
        return jsonify({"error": "ok_pct must be between 0 and 100"}), 400
    gl_ok, gl_err = _validate_gl_accounts(gl_accounts)
    if not gl_ok:
        return jsonify({"error": gl_err}), 400

    if tran_date_str:
        try:
            tran_date = datetime.strptime(tran_date_str, "%Y-%m-%d")
        except ValueError:
            tran_date = datetime.today()
    else:
        tran_date = datetime.today()

    try:
        result = generate_idfc_visa(
            use_case_id="idfc_visa_issuer",
            bank_id=bank_id,
            volume=volume,
            ok_pct=ok_pct,
            tran_date=tran_date,
            output_dir=OUTPUT_DIR,
            selected_scenarios=selected_scenarios,
            custom_scenarios=custom_scenarios,
            gl_accounts=gl_accounts or None,
            no_cbsmcw_duplicates=no_cbsmcw_duplicates,
            num_days=num_days,
            late_network_mismatches=late_network_mismatches,
        )
        return jsonify(result)
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/idfc-visa-scenarios")
def get_idfc_visa_scenarios():
    uc_path = BASE_DIR / "use_cases" / "idfc_visa_issuer.yaml"
    with open(uc_path, encoding="utf-8") as f:
        uc = yaml.safe_load(f)
    # Group scenarios by their group letter (A-M); preserve insertion order
    groups: dict = {}
    for sc in uc.get("scenarios", []):
        grp = sc.get("group", "")
        if grp not in groups:
            groups[grp] = []
        groups[grp].append({
            "code": sc.get("code", sc["id"]),
            "group": grp,
            "id": sc["id"],
            "name": sc["name"],
            "tran_type": sc.get("tran_type", ""),
            "tran_category": sc.get("tran_category", "D"),
            "action": sc.get("action", ""),
            "is_ok": sc.get("is_ok", False),
            "file_states": sc.get("file_states", {}),
            "note": sc.get("note", ""),
        })
    return jsonify({"groups": groups, "total": sum(len(v) for v in groups.values())})


@app.route("/api/download-idfc-visa/<run_id>")
def download_idfc_visa_zip(run_id):
    zip_path = OUTPUT_DIR / f"idfc_visa_{run_id}.zip"
    if not zip_path.exists():
        return jsonify({"error": "File not found"}), 404
    return send_file(zip_path, as_attachment=True,
                     download_name=zip_path.name, mimetype="application/zip")


@app.route("/api/idfc-nfs-scenarios")
def get_idfc_nfs_scenarios():
    uc_path = BASE_DIR / "use_cases" / "idfc_nfs_acquirer.yaml"
    with open(uc_path, encoding="utf-8") as f:
        uc = yaml.safe_load(f)
    groups: dict = {}
    for sc in uc.get("scenarios", []):
        grp = sc.get("group", "")
        if grp not in groups:
            groups[grp] = []
        groups[grp].append({
            "code": sc.get("code", sc["id"]),
            "group": grp,
            "id": sc["id"],
            "name": sc["name"],
            "tran_type": sc.get("tran_type", "ACQ_ATM"),
            "tran_category": sc.get("tran_category", "D"),
            "action": sc.get("action", ""),
            "is_ok": sc.get("is_ok", False),
            "file_states": sc.get("file_states", {}),
            "note": sc.get("note", ""),
        })
    return jsonify({"groups": groups, "total": sum(len(v) for v in groups.values())})


@app.route("/api/generate-idfc-nfs", methods=["POST"])
def run_generate_idfc_nfs():
    data = request.json or {}
    volume             = int(data.get("volume", 20))
    tran_date_str      = data.get("tran_date", "")
    bank_id            = data.get("bank_id", "idfc")
    ok_pct             = float(data.get("ok_pct", 95.0))
    selected_scenarios = data.get("selected_scenarios", None)
    custom_scenarios   = data.get("custom_scenarios", []) or []
    gl_accounts        = [str(g).strip() for g in (data.get("gl_accounts") or [])]

    if volume < 1 or volume > 1000000:
        return jsonify({"error": "Volume must be between 1 and 1,000,000"}), 400
    if not (0 <= ok_pct <= 100):
        return jsonify({"error": "ok_pct must be between 0 and 100"}), 400
    gl_ok, gl_err = _validate_gl_accounts(gl_accounts)
    if not gl_ok:
        return jsonify({"error": gl_err}), 400

    if tran_date_str:
        try:
            tran_date = datetime.strptime(tran_date_str, "%Y-%m-%d")
        except ValueError:
            tran_date = datetime.today()
    else:
        tran_date = datetime.today()

    try:
        result = generate_idfc_nfs_acq(
            use_case_id="idfc_nfs_acquirer",
            bank_id=bank_id,
            volume=volume,
            ok_pct=ok_pct,
            tran_date=tran_date,
            output_dir=OUTPUT_DIR,
            selected_scenarios=selected_scenarios,
            custom_scenarios=custom_scenarios,
            gl_accounts=gl_accounts or None,
        )
        return jsonify(result)
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/download-idfc-nfs/<run_id>")
def download_idfc_nfs_zip(run_id):
    zip_path = OUTPUT_DIR / f"idfc_nfs_{run_id}.zip"
    if not zip_path.exists():
        return jsonify({"error": "File not found"}), 404
    return send_file(zip_path, as_attachment=True,
                     download_name=zip_path.name, mimetype="application/zip")


@app.route("/api/idfc-mc-scenarios")
def get_idfc_mc_scenarios():
    uc_path = BASE_DIR / "use_cases" / "idfc_mc_issuer.yaml"
    with open(uc_path, encoding="utf-8") as f:
        uc = yaml.safe_load(f)
    groups: dict = {}
    for sc in uc.get("scenarios", []):
        grp = sc.get("group", "")
        if grp not in groups:
            groups[grp] = []
        groups[grp].append({
            "code": sc.get("code", sc["id"]),
            "group": grp,
            "id": sc["id"],
            "name": sc["name"],
            "tran_category": sc.get("tran_category", "D"),
            "action": sc.get("action", ""),
            "is_ok": sc.get("is_ok", False),
            "file_states": sc.get("file_states", {}),
            "note": sc.get("note", ""),
        })
    return jsonify({"groups": groups, "total": sum(len(v) for v in groups.values())})


@app.route("/api/generate-idfc-mc", methods=["POST"])
def run_generate_idfc_mc():
    data = request.json or {}
    volume                 = int(data.get("volume", 20))
    tran_date_str          = data.get("tran_date", "")
    bank_id                = data.get("bank_id", "idfc")
    ok_pct                 = float(data.get("ok_pct", 95.0))
    selected_scenarios     = data.get("selected_scenarios", None)
    custom_scenarios       = data.get("custom_scenarios", []) or []
    gl_accounts            = [str(g).strip() for g in (data.get("gl_accounts") or [])]
    no_cbsmcw_duplicates   = bool(data.get("no_cbsmcw_duplicates", True))

    if volume < 1 or volume > 1000000:
        return jsonify({"error": "Volume must be between 1 and 1,000,000"}), 400
    if not (0 <= ok_pct <= 100):
        return jsonify({"error": "ok_pct must be between 0 and 100"}), 400
    gl_ok, gl_err = _validate_gl_accounts(gl_accounts)
    if not gl_ok:
        return jsonify({"error": gl_err}), 400

    if tran_date_str:
        try:
            tran_date = datetime.strptime(tran_date_str, "%Y-%m-%d")
        except ValueError:
            tran_date = datetime.today()
    else:
        tran_date = datetime.today()

    try:
        result = generate_idfc_mc(
            use_case_id="idfc_mc_issuer",
            bank_id=bank_id,
            volume=volume,
            ok_pct=ok_pct,
            tran_date=tran_date,
            output_dir=OUTPUT_DIR,
            selected_scenarios=selected_scenarios,
            custom_scenarios=custom_scenarios,
            gl_accounts=gl_accounts or None,
            no_cbsmcw_duplicates=no_cbsmcw_duplicates,
        )
        return jsonify(result)
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/download-idfc-mc/<run_id>")
def download_idfc_mc_zip(run_id):
    zip_path = OUTPUT_DIR / f"idfc_mc_{run_id}.zip"
    if not zip_path.exists():
        return jsonify({"error": "File not found"}), 404
    return send_file(zip_path, as_attachment=True,
                     download_name=zip_path.name, mimetype="application/zip")


# ─────────────────────────────────────────────
# HYOSUNG EJ ROUTES
# ─────────────────────────────────────────────

@app.route("/api/generate-ej", methods=["POST"])
def run_generate_ej():
    data = request.json or {}
    atm_type         = data.get("atm_type", "U1")
    tran_date_str    = data.get("tran_date", "")
    num_transactions = int(data.get("num_transactions", 50))
    selected_cases   = data.get("selected_cases", ["sync"])
    bank_id          = data.get("bank_id", "sbi")  # currently only sbi

    if atm_type not in ("U1", "S5"):
        return jsonify({"error": "atm_type must be 'U1' or 'S5'"}), 400
    if num_transactions < 1 or num_transactions > 500:
        return jsonify({"error": "num_transactions must be between 1 and 500"}), 400
    if not selected_cases:
        return jsonify({"error": "At least one case must be selected"}), 400

    if tran_date_str:
        try:
            tran_date = datetime.strptime(tran_date_str, "%Y-%m-%d")
        except ValueError:
            tran_date = datetime.today()
    else:
        tran_date = datetime.today()

    try:
        result = generate_hyosung_ej(
            atm_type=atm_type,
            tran_date=tran_date,
            num_transactions=num_transactions,
            selected_cases=selected_cases,
            output_dir=OUTPUT_DIR,
        )
        return jsonify(result)
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/download-ej/<run_id>")
def download_ej(run_id):
    """Download EJ file directly (not a zip)."""
    import json as _json
    manifest_path = OUTPUT_DIR / f"manifest_ej_{run_id}.json"
    if not manifest_path.exists():
        return jsonify({"error": "EJ run not found"}), 404
    with open(manifest_path, encoding="utf-8") as f:
        manifest = _json.load(f)
    file_name = manifest.get("file_name", "")
    file_path = OUTPUT_DIR / file_name
    if not file_path.exists():
        return jsonify({"error": "EJ file not found on disk"}), 404
    return send_file(file_path, as_attachment=True,
                     download_name=file_name,
                     mimetype="text/plain")


@app.route("/")
def index():
    """Serve the SPA"""
    return render_template_string(open(BASE_DIR / "static" / "index.html", encoding="utf-8").read())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    debug = os.environ.get("FLASK_ENV") != "production"
    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  Reconciliation Test Data Generator")
    print(f"  http://localhost:{port}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
    app.run(host="0.0.0.0", port=port, debug=debug)
