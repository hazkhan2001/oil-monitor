"""
run_daily.py: the morning job. GitHub Actions runs this once a day.

    1. PortWatch transits            -> Chokepoints_Daily
    2. New Sentinel-1 scenes:
         sea boxes   -> ship detection -> AIS match -> SAR_Detections, Dark_Candidates
         tank farms  -> storage index                 -> Storage_Index
    3. EIA Cushing stocks            -> EIA_Cushing, Storage_Calibration
    4. Predict tomorrow's passes     -> Upcoming_Passes (+ state for the AIS recorder)
    5. Summary + Run_Log

LESSON: each step is wrapped in try/except so one failure (say EIA's site is
down) doesn't stop the others. The failure is written to Run_Log, and the
script exits with code 1 at the end, which makes GitHub mark the run red
and email you. "Fail loudly, but finish what you can."

Run locally:  python run_daily.py            (writes CSVs to output/sheet/)
Options:      python run_daily.py --only portwatch,sar
"""

from __future__ import annotations

import argparse
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

import config
from monitor import ais, copernicus, detect, landmask, passes, portwatch, report, state, storage, visual
from monitor.sheets import SheetWriter

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
AIS_DIR = state.STATE_DIR / "ais"
NOW = datetime.now(timezone.utc)
LOG: list[dict] = []


def log(step: str, status: str, detail: str = "") -> None:
    print(f"[{status:7}] {step}: {detail}")
    LOG.append({"run_utc": NOW.strftime("%Y-%m-%d %H:%M"), "step": step, "status": status, "detail": detail[:500]})


# --------------------------------------------------------------------------
def step_portwatch(sheet: SheetWriter) -> None:
    names = [a.portwatch for a in config.AOIS if a.portwatch]
    df, warnings = portwatch.pull(names, first_year=NOW.year - 1)
    n = sheet.upsert("Chokepoints_Daily", df, keys=["date", "portname"], sort_by=["portname", "date"])
    latest = pd.to_datetime(df["date"]).max().date() if len(df) else "none"
    log("portwatch", "ok", f"{len(df)} rows fetched, latest day {latest}, tab now {n} rows")
    for w in warnings:
        log("portwatch", "warning", w)


# --------------------------------------------------------------------------
def load_ais_near(when: datetime, aoi_key: str) -> pd.DataFrame:
    frames = []
    for path in sorted(AIS_DIR.glob("*.csv.gz")):
        try:
            start = datetime.strptime(path.name[:13], "%Y%m%dT%H%M").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if abs((start - when).total_seconds()) < 3 * 3600:
            frames.append(pd.read_csv(path))
    if not frames:
        return pd.DataFrame(columns=ais.AIS_COLUMNS)
    df = pd.concat(frames, ignore_index=True)
    return df[df["aoi"] == aoi_key]


def process_sea_scene(aoi, scene, raster, memory) -> tuple[dict, pd.DataFrame]:
    px = aoi.resolution_m
    sea = landmask.sea_mask(raster, aoi.land_buffer_m, px)
    dets = detect.detect_ships(raster, sea, px)
    dets = detect.flag_persistent(aoi.key, dets, memory)

    ais_raw = load_ais_near(scene.start, aoi.key) if aoi.ais else pd.DataFrame(columns=ais.AIS_COLUMNS)
    ais_now = ais.positions_at(ais_raw, scene.start, config.AIS_MAX_TIME_GAP_MIN)
    if len(ais_now):  # only AIS ships actually inside the box at pass time
        lon0, lat0, lon1, lat1 = aoi.bbox
        ais_now = ais_now[ais_now["lat_pred"].between(lat0, lat1) & ais_now["lon_pred"].between(lon0, lon1)].reset_index(drop=True)
    dets = ais.match(dets, ais_now)
    dets = ais.score_dark(dets, n_ais_ships=len(ais_now) if aoi.ais and len(ais_raw) else 0)

    moving = dets[~dets["persistent"]] if len(dets) else dets
    summary = {
        "scene_time": scene.start.strftime("%Y-%m-%d %H:%M"), "aoi": aoi.key, "scene_id": scene.scene_id,
        "platform": scene.platform, "orbit_direction": scene.orbit_direction,
        "relative_orbit": scene.relative_orbit,
        "sea_km2": round(sea.sum() * (px / 1000) ** 2, 1),
        "n_detections": len(dets), "n_vessels": len(moving),
        "n_large_150m": int((moving["length_m"] >= 150).sum()) if len(moving) else 0,
        "n_persistent": int(dets["persistent"].sum()) if len(dets) else 0,
        "ais_recorded": bool(len(ais_raw)), "ais_ships_in_box": len(ais_now),
        "n_matched": int(dets["mmsi"].notna().sum()) if len(dets) else 0,
        "n_dark": int(dets["dark_candidate"].sum()) if len(dets) else 0,
        "n_dark_high": int((dets["dark_confidence"] == "high").sum()) if len(dets) else 0,
        "pu_spent": round(raster.pu_spent, 2),
    }
    stem = f"{aoi.key}_{scene.scene_id}"
    visual.quicklook(raster, dets, OUT / "quicklooks" / f"{stem}.png")
    dark = dets[dets["dark_candidate"]].copy() if len(dets) else dets
    for i, (_, d) in enumerate(dark.iterrows()):
        visual.crop(raster, d, OUT / "crops" / f"{stem}_{i:03d}.png")
    if len(dark):
        dark.insert(0, "scene_time", summary["scene_time"])
        dark.insert(1, "aoi", aoi.key)
        dark.insert(2, "scene_id", scene.scene_id)
        dark["crop_file"] = [f"{stem}_{i:03d}.png" for i in range(len(dark))]
        dark = dark.drop(columns=["row", "col", "mmsi", "ais_name", "match_dist_m", "dark_candidate"], errors="ignore")
    return summary, dark


def step_sar(sheet: SheetWriter) -> None:
    tokens = copernicus.TokenCache()
    if not tokens.configured:
        log("sentinel1", "skipped", "no CDSE_CLIENT_ID / CDSE_CLIENT_SECRET set")
        return
    processed = state.load("processed", {})
    pu_usage = state.load("pu_usage", {})
    memory = state.load("persistent", {})
    month = NOW.strftime("%Y-%m")
    sar_rows, dark_rows, storage_rows = [], [], []
    try:
        for aoi in config.AOIS:
            try:
                scenes = copernicus.search_scenes(aoi.bbox, NOW - timedelta(days=config.SCENE_LOOKBACK_DAYS), NOW)
            except Exception as exc:
                log(f"search {aoi.key}", "error", repr(exc))
                continue
            new = [s for s in scenes if f"{aoi.key}|{s.scene_id}" not in processed]
            for scene in new:
                est = copernicus.estimate_pu(aoi.bbox, aoi.resolution_m)
                if pu_usage.get(month, 0) + est > config.MONTHLY_PU_BUDGET:
                    log(f"sar {aoi.key}", "skipped", f"monthly PU budget reached ({pu_usage.get(month, 0):.0f})")
                    break
                try:
                    raster = copernicus.fetch_raster(scene, aoi.bbox, aoi.resolution_m, tokens)
                except Exception as exc:
                    log(f"fetch {aoi.key} {scene.scene_id}", "error", repr(exc))
                    continue
                pu_usage[month] = round(pu_usage.get(month, 0) + raster.pu_spent, 2)
                processed[f"{aoi.key}|{scene.scene_id}"] = NOW.strftime("%Y-%m-%d")
                if raster.valid.mean() < 0.2:
                    log(f"sar {aoi.key} {scene.scene_id}", "skipped", "scene barely covers the box")
                    continue
                if aoi.kind == "tankfarm":
                    m = storage.farm_metrics(raster)
                    storage_rows.append({"scene_time": scene.start.strftime("%Y-%m-%d %H:%M"), "aoi": aoi.key,
                                         "scene_id": scene.scene_id, "orbit_direction": scene.orbit_direction,
                                         "relative_orbit": scene.relative_orbit, **m})
                    visual.quicklook(raster, None, OUT / "quicklooks" / f"{aoi.key}_{scene.scene_id}.png")
                    log(f"storage {aoi.key}", "ok", f"{scene.scene_id} bright_frac={m.get('bright_frac')}")
                else:
                    summary, dark = process_sea_scene(aoi, scene, raster, memory)
                    sar_rows.append(summary)
                    if len(dark):
                        dark_rows.append(dark)
                    log(f"sar {aoi.key}", "ok", f"{scene.scene_id}: {summary['n_vessels']} vessels, "
                        f"{summary['n_persistent']} persistent, {summary['n_dark']} dark candidates "
                        f"(AIS ships in box: {summary['ais_ships_in_box']})")
    finally:  # save memory even if something above crashed
        cutoff = (NOW - timedelta(days=30)).strftime("%Y-%m-%d")
        state.save("processed", {k: v for k, v in processed.items() if v >= cutoff})
        state.save("pu_usage", pu_usage)
        state.save("persistent", memory)

    if sar_rows:
        sheet.upsert("SAR_Detections", pd.DataFrame(sar_rows), keys=["aoi", "scene_id"], sort_by=["scene_time", "aoi"])
        sheet.replace("SAR_Detections", report.add_sar_zscores(sheet.read("SAR_Detections")))
    if dark_rows:
        sheet.upsert("Dark_Candidates", pd.concat(dark_rows, ignore_index=True),
                     keys=["aoi", "scene_id", "lat", "lon"], sort_by=["scene_time", "aoi"])
    if storage_rows:
        sheet.upsert("Storage_Index", pd.DataFrame(storage_rows), keys=["aoi", "scene_id"], sort_by=["scene_time", "aoi"])
        sheet.replace("Storage_Index", storage.add_orbit_zscores(sheet.read("Storage_Index")))
    log("sentinel1", "ok", f"{len(sar_rows)} sea scenes, {len(storage_rows)} tank-farm scenes; "
        f"PU used this month: {pu_usage.get(month, 0):.0f} / {config.MONTHLY_PU_BUDGET}")


# --------------------------------------------------------------------------
def step_eia(sheet: SheetWriter) -> None:
    stocks = storage.fetch_cushing_stocks()
    sheet.upsert("EIA_Cushing", stocks.tail(260), keys=["date"], sort_by=["date"])
    log("eia", "ok", f"latest Cushing week {stocks['date'].max().date()}: {stocks['cushing_kbbl'].iloc[-1]:,.0f} kbbl")
    index = sheet.read("Storage_Index")
    if not index.empty:
        calib = storage.calibrate(index, stocks)
        if not calib.empty:
            sheet.replace("Storage_Calibration", calib)


def step_passes(sheet: SheetWriter) -> None:
    preds = passes.predict(config.AOIS, NOW, horizon_h=36)
    state.save("passes", preds)
    sheet.replace("Upcoming_Passes", pd.DataFrame(preds, columns=["aoi", "time", "platform"]))
    log("passes", "ok", f"{len(preds)} predicted passes in the next 36 h")
    # AIS recordings are only needed until their scene is processed
    for path in AIS_DIR.glob("*.csv.gz"):
        try:
            start = datetime.strptime(path.name[:13], "%Y%m%dT%H%M").replace(tzinfo=timezone.utc)
            if NOW - start > timedelta(days=config.SCENE_LOOKBACK_DAYS + 1):
                path.unlink()
        except ValueError:
            pass


def step_summary(sheet: SheetWriter) -> None:
    summary = report.build_summary(sheet.read("Chokepoints_Daily"), sheet.read("SAR_Detections"),
                                   sheet.read("Storage_Index"), sheet.read("Storage_Calibration"))
    summary.insert(0, "updated_utc", NOW.strftime("%Y-%m-%d %H:%M"))
    sheet.replace("Summary", summary)
    flagged = summary[summary["flags"] != ""]
    log("summary", "ok", f"{len(flagged)} location(s) flagged" +
        ("" if flagged.empty else ": " + " | ".join(f"{r.location}: {r.flags}" for r in flagged.itertuples())))


STEPS = {"portwatch": step_portwatch, "sar": step_sar, "eia": step_eia,
         "passes": step_passes, "summary": step_summary}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="comma-separated steps: " + ",".join(STEPS))
    args = parser.parse_args()
    chosen = args.only.split(",") if args.only else list(STEPS)

    sheet = SheetWriter()
    log("start", "ok", f"sheet mode: {sheet.mode}")
    for name in chosen:
        try:
            STEPS[name](sheet)
        except Exception as exc:
            log(name, "error", f"{exc!r}\n{traceback.format_exc()[-400:]}")
    sheet.append("Run_Log", pd.DataFrame(LOG))
    failed = any(row["status"] == "error" for row in LOG)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
