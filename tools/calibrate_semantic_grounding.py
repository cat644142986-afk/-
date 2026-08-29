from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from python.semantic_grounding_eval import (  # noqa: E402
    box_iou,
    evaluate_grounding_predictions,
    load_grounding_manifest,
)


def _threshold_prediction(prediction: dict, target_count: int, threshold: float) -> dict:
    raw_candidates = prediction.get("candidates")
    raw_candidates = raw_candidates if isinstance(raw_candidates, list) else []
    candidates = [
        candidate for candidate in raw_candidates
        if float(candidate.get("confidence") or 0) >= threshold
    ][:target_count]
    if len(candidates) >= target_count:
        status = "candidates"
    elif raw_candidates:
        status = "low_confidence"
    else:
        status = str(prediction.get("status") or "no_match")
    return {
        "status": status,
        "candidates": candidates,
        "elapsed_ms": prediction.get("elapsed_ms", 0),
    }


def _review_policy_prediction(
    prediction: dict,
    target_count: int,
    trusted_threshold: float,
    review_threshold: float,
) -> dict:
    raw_candidates = prediction.get("candidates")
    raw_candidates = raw_candidates if isinstance(raw_candidates, list) else []
    trusted = [
        candidate for candidate in raw_candidates
        if float(candidate.get("confidence") or 0) >= trusted_threshold
    ][:target_count]
    review = [
        {**candidate, "origin": "automatic-review"}
        for candidate in raw_candidates
        if review_threshold <= float(candidate.get("confidence") or 0) < trusted_threshold
    ][:max(0, target_count - len(trusted))]
    status = "candidates" if len(trusted) >= target_count else "low_confidence"
    return {
        "status": status,
        "candidates": trusted,
        "review_candidates": review,
        "elapsed_ms": prediction.get("elapsed_ms", 0),
    }


def _candidate_scores(manifest: dict, predictions: dict) -> dict:
    true_scores: list[float] = []
    false_scores: list[float] = []
    by_case = []
    iou_threshold = float(manifest.get("gates", {}).get("iou_threshold", 0.5))
    for case in manifest["cases"]:
        expected = [item["bbox"] for item in case.get("expected") or []]
        prediction = predictions.get(case["id"]) or {}
        for candidate in prediction.get("candidates") or []:
            score = float(candidate.get("confidence") or 0)
            best_iou = max(
                (box_iou(candidate["bbox"], box) for box in expected),
                default=0.0,
            )
            target = true_scores if best_iou >= iou_threshold else false_scores
            target.append(score)
            by_case.append({
                "case_id": case["id"],
                "confidence": round(score, 4),
                "best_iou": round(best_iou, 4),
                "matches_expected": best_iou >= iou_threshold,
            })
    return {
        "true_candidate_scores": sorted(round(item, 4) for item in true_scores),
        "false_candidate_scores": sorted(round(item, 4) for item in false_scores),
        "min_true_score": round(min(true_scores), 4) if true_scores else None,
        "max_false_score": round(max(false_scores), 4) if false_scores else None,
        "candidates": by_case,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sweep confidence thresholds over one preserved low-threshold grounding run."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument(
        "--thresholds",
        default="0.40,0.45,0.50,0.55,0.60,0.65,0.70,0.75",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--trusted-threshold", type=float, default=0.75)
    parser.add_argument("--review-threshold", type=float, default=0.60)
    args = parser.parse_args()
    manifest = load_grounding_manifest(args.manifest)
    predictions = json.loads(args.predictions.read_text(encoding="utf-8"))
    thresholds = sorted({float(item) for item in args.thresholds.split(",")})
    if not thresholds or min(thresholds) < 0 or max(thresholds) > 1:
        parser.error("thresholds must be between 0 and 1")
    if not 0 <= args.review_threshold <= args.trusted_threshold <= 1:
        parser.error("review policy must satisfy 0 <= review <= trusted <= 1")
    sweeps = []
    for threshold in thresholds:
        filtered = {
            case["id"]: _threshold_prediction(
                predictions.get(case["id"]) or {},
                int(case["target_count"]),
                threshold,
            )
            for case in manifest["cases"]
        }
        report = evaluate_grounding_predictions(manifest, filtered)
        sweeps.append({
            "threshold": threshold,
            "passed": report["passed"],
            "metrics": report["metrics"],
            "checks": report["checks"],
        })
    review_predictions = {
        case["id"]: _review_policy_prediction(
            predictions.get(case["id"]) or {},
            int(case["target_count"]),
            args.trusted_threshold,
            args.review_threshold,
        )
        for case in manifest["cases"]
    }
    review_report = evaluate_grounding_predictions(manifest, review_predictions)
    payload = {
        "schema_version": "1.0",
        "corpus_id": manifest["corpus_id"],
        "source_predictions": str(args.predictions),
        "sweeps": sweeps,
        "review_policy": {
            "trusted_threshold": args.trusted_threshold,
            "review_threshold": args.review_threshold,
            "trusted_gate_passed": review_report["passed"],
            "metrics": review_report["metrics"],
            "checks": review_report["checks"],
            "case_results": review_report["case_results"],
        },
        "score_separation": _candidate_scores(manifest, predictions),
        "conclusion": (
            "single_threshold_sufficient"
            if any(item["passed"] for item in sweeps)
            else "single_threshold_insufficient"
        ),
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
