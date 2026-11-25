"""Flask API for the AgroWatch dashboard (AgroWatch v2, Python 3.15).

Run locally:  python app.py
Production:   gunicorn app:app --bind 0.0.0.0:$PORT --timeout 300
"""
import logging
import os
from datetime import date

import ee
from dotenv import load_dotenv

load_dotenv()  # must run before importing modules that read env vars

from flask import Flask, g, jsonify, request, send_from_directory  # noqa: E402
from flask_cors import CORS  # noqa: E402
from werkzeug.exceptions import HTTPException  # noqa: E402

import authentication as auth  # noqa: E402
import gee_intel as intel  # noqa: E402
import gee_processor as gee  # noqa: E402
import methodology  # noqa: E402

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("agrowatch")

FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))
# The API also serves the dashboard, so `python app.py` + http://localhost:5000 is all you need.
app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="")
origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
CORS(app, origins=origins)

auth.check_auth_config()
auth.init_earth_engine()


def _args(src=None):
    """Validate the shared filters: province, district (or a custom bbox), year, season, crop.

    `season` may be kharif, rabi or a custom window c<MMDD>_<MMDD>. A season that is still running is
    shortened to the data that exist (see gee.resolve_season) and `g.season_info` describes that."""
    src = request.args if src is None else src
    today = date.today()
    raw_season = str(src.get("season") or "kharif").lower()
    gee.season_cfg(raw_season)                       # raises ValueError for an unknown or invalid season
    try:
        year = int(src.get("year", today.year - 1))
    except (TypeError, ValueError):
        raise ValueError("'year' must be a number like 2025")
    if not gee.FIRST_YEAR <= year <= today.year:
        raise ValueError(f"'year' must be between {gee.FIRST_YEAR} and {today.year}")

    provinces = gee.list_provinces()
    province = src.get("province") or None
    if not provinces:
        province = None
    elif province and province not in provinces:
        raise ValueError(f"Unknown province '{province}'")

    district = src.get("district") or None
    if district and not gee.parse_bbox(district) and district not in gee.list_districts(province):
        raise ValueError(f"Unknown district '{district}'")
    crop = str(src.get("crop", "1")) != "0"
    season, info = gee.resolve_season(year, raw_season)
    g.season_info = info
    return province, district, year, season, crop


def _asi_opts(src=None):
    src = request.args if src is None else src
    return intel.parse_asi_opts(src.get("w"), str(src.get("pca", "0")) == "1")


def _alpha(src=None):
    src = request.args if src is None else src
    try:
        a = float(src.get("alpha", 0.5))
    except (TypeError, ValueError):
        raise ValueError("'alpha' must be a number between 0 and 1")
    if not 0 <= a <= 1:
        raise ValueError("'alpha' must be between 0 and 1")
    return round(a, 3)


def _with_info(payload):
    """Attach the season window and partial-season flag to a response."""
    info = getattr(g, "season_info", None)
    return {**payload, "season_info": info} if info else payload


def _stats(province, district, year, season):
    """Cached stats, with the completeness flag taken from the requested season, not the shortened window."""
    st = dict(gee.get_stats(province, district, year, season))
    st["complete"] = not g.season_info["partial"]
    return st


EXPORTS_ENABLED = os.getenv("EXPORTS_ENABLED", "false").lower() == "true"


# ------------------------------------------------------------------- routes
@app.get("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.get("/api/health")
def health():
    return jsonify(status="ok")


@app.post("/api/login")
def login():
    body = request.get_json(silent=True) or {}
    if not auth.AUTH_ENABLED:
        return jsonify(token="auth-disabled")
    if auth.verify_credentials(body.get("username"), body.get("password")):
        return jsonify(token=auth.create_token(body["username"]))
    return jsonify(error="Invalid credentials"), 401


@app.get("/api/meta")
@auth.token_required
def meta():
    today = date.today()
    layers, groups = intel.layer_meta()
    return jsonify(
        provinces=gee.list_provinces(),
        default_province=os.getenv("DEFAULT_PROVINCE", "Punjab"),
        years=list(range(gee.FIRST_YEAR, today.year + 1)),
        default_year=today.year - 1,
        seasons=[{"id": k, "label": v["label"]} for k, v in gee.SEASONS.items()],
        base=[gee.BASE_START, gee.BASE_END],
        layers=layers, layer_groups=groups,
        classes=gee.CLASSES, vci_classes=gee.VCI_CLASSES, asi_classes=intel.ASI_CLASSES,
        asi={"keys": intel.ASI_KEYS, "names": intel.ASI_NAMES, "weights": intel.ASI_WEIGHTS, "breaks": intel.ASI_BREAKS},
        crop_source=gee.CROP_SOURCE, exports_enabled=EXPORTS_ENABLED,
        analysis_date=date.today().isoformat(),
    )


@app.get("/api/districts")
@auth.token_required
def districts():
    province = request.args.get("province") or None
    if not gee.list_provinces():
        province = None
    return jsonify(districts=gee.list_districts(province))


@app.get("/api/boundary")
@auth.token_required
def boundary():
    province, district, *_ = _args()
    return jsonify(gee.get_boundary(province, district))


@app.get("/api/tiles")
@auth.token_required
def tiles():
    layer = request.args.get("layer", "stress")
    if layer not in intel.LAYERS and layer not in gee.LAYERS:
        raise ValueError(f"Unknown layer '{layer}'")
    province, district, year, season, crop = _args()
    if layer in intel.LAYERS:
        out = intel.get_tiles(layer, province, district, year, season, crop, _asi_opts(), _alpha(), None)
    else:
        out = dict(gee.get_tiles(layer, province, district, year, season, crop))
        meta, _ = intel.layer_meta()
        out["info"] = {k: meta[layer][k] for k in ("dataset", "resolution", "temporal", "nodata", "category")}
        out["period"] = intel.layer_period(layer, year, season)
    return jsonify(_with_info(out))


@app.get("/api/stats")
@auth.token_required
def stats():
    province, district, year, season, _ = _args()
    return jsonify(_with_info(_stats(province, district, year, season)))


@app.get("/api/series")
@auth.token_required
def series():
    province, district, year, season, _ = _args()
    return jsonify(gee.get_series(province, district, year, season))


@app.get("/api/rainfall")
@auth.token_required
def rainfall():
    province, district, year, _, _ = _args()
    return jsonify(gee.get_rain_monthly(province, district, year))


@app.get("/api/ranking")
@auth.token_required
def ranking():
    """Districts of the province ranked by mean ADSI over cropland (worst first)."""
    province, _, year, season, _ = _args()
    return jsonify(gee.get_ranking(province, year, season))


@app.get("/api/insights")
@auth.token_required
def insights():
    province, district, year, season, _ = _args()
    return jsonify(_with_info(gee.build_insights(
        _stats(province, district, year, season),
        gee.get_series(province, district, year, season),
    )))


@app.get("/api/sources")
@auth.token_required
def sources():
    return jsonify(sources=gee.get_sources())


# ------------------------------------------------- AgroWatch Intelligence routes
@app.get("/api/intel/phenology")
@auth.token_required
def intel_phenology():
    province, district, year, season, _ = _args()
    return jsonify(_with_info({**intel.get_phenology(province, district, year, season),
                               "curve": intel.get_phenology_curve(province, district, year, season)}))


@app.get("/api/intel/rain")
@auth.token_required
def intel_rain():
    province, district, year, season, _ = _args()
    return jsonify(_with_info(intel.get_rain_intel(province, district, year, season)))


@app.get("/api/intel/soil")
@auth.token_required
def intel_soil():
    province, district, year, season, _ = _args()
    return jsonify(_with_info(intel.get_soil(province, district, year, season)))


@app.get("/api/intel/et")
@auth.token_required
def intel_et():
    province, district, year, season, _ = _args()
    return jsonify(_with_info(intel.get_et(province, district, year, season)))


@app.get("/api/intel/asi")
@auth.token_required
def intel_asi():
    province, district, year, season, _ = _args()
    out = dict(intel.get_asi(province, district, year, season, _asi_opts()))
    out["complete"] = not g.season_info["partial"]
    return jsonify(_with_info(out))


@app.get("/api/intel/asi_series")
@auth.token_required
def intel_asi_series():
    province, district, year, season, _ = _args()
    return jsonify(_with_info(intel.get_asi_series(province, district, year, season, _asi_opts())))


@app.get("/api/intel/hotspots")
@auth.token_required
def intel_hotspots():
    province, district, year, season, _ = _args()
    return jsonify(_with_info(intel.get_hotspots(province, district, year, season, _asi_opts())))


@app.get("/api/intel/districts")
@auth.token_required
def intel_districts():
    province, _, year, season, _ = _args()
    return jsonify(_with_info(intel.get_districts(province, year, season, _asi_opts())))


@app.get("/api/intel/condition")
@auth.token_required
def intel_condition():
    province, district, year, season, _ = _args()
    return jsonify(_with_info(intel.get_condition_series(province, district, year, season, _alpha())))


@app.get("/api/intel/trend")
@auth.token_required
def intel_trend():
    province, district, year, season, _ = _args()
    series = gee.get_series(province, district, year, season)
    return jsonify(_with_info({
        "trend": intel.trend_from_series(series["rows"]), "years": [series["rows"][0]["year"], series["rows"][-1]["year"]],
        "note": "Mann-Kendall assumes independent seasons; serial correlation can overstate significance. "
                "At least 8 valid seasons are required."}))


@app.get("/api/intel/crop_agreement")
@auth.token_required
def intel_crop_agreement():
    province, district, *_ = _args()
    return jsonify(intel.get_crop_agreement(province, district))


@app.get("/api/intel/classify")
@auth.token_required
def intel_classify():
    province, district, year, season, crop = _args()
    samples = request.args.get("samples")
    if not samples:
        return jsonify(enabled=False, **intel.classification_enabled())
    label = request.args.get("label_field", "class")
    if not label.replace("_", "").isalnum():
        raise ValueError("label_field must be a plain column name")
    return jsonify(_with_info(intel.run_classification(
        province, district, year, season, samples, label, request.args.get("val_samples") or None, crop)))


@app.get("/api/methodology")
def methodology_doc():
    return jsonify(categories=methodology.CATEGORIES, principles=methodology.PRINCIPLES,
                   implementation=methodology.IMPLEMENTATION, indicators=methodology.INDICATORS)


@app.post("/api/export")
@auth.token_required
def export_task():
    if not EXPORTS_ENABLED:
        raise ValueError("Exports are switched off on this server. Set EXPORTS_ENABLED=true in backend/.env.")
    body = request.get_json(silent=True) or {}
    province, district, year, season, crop = _args(body)
    region = body.get("region")
    if region:
        box = gee.parse_bbox("bbox:" + ",".join(str(x) for x in region))
        region = box
    result = intel.start_export(
        body.get("kind", "image"), body.get("layer", "asi"), province, district, year, season, crop,
        _asi_opts(body), _alpha(body), None, scale=body.get("scale"), crs=body.get("crs", "EPSG:4326"),
        description=body.get("description"), folder=body.get("folder", "AgroWatch"), region=region)
    return jsonify(_with_info(result))


# ------------------------------------------------------------------- errors
@app.errorhandler(ValueError)
def bad_request(e):
    return jsonify(error=str(e)), 400


@app.errorhandler(ee.EEException)
def earth_engine_error(e):
    log.error("Earth Engine error: %s", e)
    return jsonify(error="Earth Engine: " + str(e)[:300]), 502


@app.errorhandler(RuntimeError)
def config_error(e):
    log.error("Configuration error: %s", e)
    return jsonify(error=str(e)), 500


@app.errorhandler(HTTPException)
def http_error(e):
    return jsonify(error=e.description), e.code


@app.errorhandler(Exception)
def server_error(e):
    log.exception("Unhandled error")
    return jsonify(error=str(e) if app.debug else "Server error. Check the backend logs."), 500


def start_ngrok(port):
    """Optional: give this server a public https address (set USE_NGROK=true in .env)."""
    from pyngrok import ngrok  # downloads the ngrok program automatically on first use

    token = os.getenv("NGROK_AUTHTOKEN")
    if token:
        ngrok.set_auth_token(token)
    domain = os.getenv("NGROK_DOMAIN") or None
    tunnel = ngrok.connect(port, "http", domain=domain) if domain else ngrok.connect(port, "http")
    url = tunnel.public_url.replace("http://", "https://", 1)
    print("\n" + "=" * 64)
    print(" ngrok public address:", url)
    print(" Put it in frontend/index.html as window.AGROWATCH_API_BASE")
    print(" and add your website to ALLOWED_ORIGINS in .env")
    print("=" * 64 + "\n")
    return url


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG") == "1"
    use_ngrok = os.getenv("USE_NGROK", "false").lower() == "true"
    if use_ngrok:
        start_ngrok(port)
    # The auto-reloader would start a second tunnel, so it is off when ngrok is on.
    app.run(host="0.0.0.0", port=port, debug=debug, use_reloader=debug and not use_ngrok)
    # updated