"""
kpi_monitoring_engine_v1.py

Reference Python calculation engine for KPI Monitoring Agent V1.

This file contains deterministic calculation logic. Database read/write adapters are placeholders
and must be connected to MCP or a direct database layer by the implementation team.

The GPT agent should use this logic, not improvise scoring rules.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple
import math
import statistics
from datetime import datetime


@dataclass
class KPIResult:
    result_date: str
    raw_value: float
    target_value: Optional[float] = None
    lower_limit: Optional[float] = None
    upper_limit: Optional[float] = None
    period_label: Optional[str] = None


@dataclass
class KPIContext:
    kpi_id: int
    kpi_target_allocation_id: int
    kpi_name: str
    target_direction: str  # "up" or "down"
    importance: Optional[Any] = None
    urgency: Optional[Any] = None
    kpi_nature: Optional[str] = "mixed"
    owner_people_id: Optional[int] = None
    owner_role_id: Optional[int] = None
    current_escalation_level: int = 0
    previous_status: Optional[Dict[str, Any]] = None
    actions: List[Dict[str, Any]] = field(default_factory=list)
    relationships: List[Dict[str, Any]] = field(default_factory=list)
    knowledge_base: Dict[str, Any] = field(default_factory=dict)


@dataclass
class KPIAnalysis:
    kpi_id: int
    kpi_target_allocation_id: int
    current_status: str
    business_status: str
    performance_score: float
    performance_gap: float
    performance_trend: str
    priority_score: float
    priority_level: str
    escalation_level: int
    escalation_reason: str
    spc_status: str
    spc_signal_count: int
    periods_degraded: int
    periods_without_improvement: int
    recurrence_count: int
    action_status: str
    open_action_count: int
    late_action_count: int
    ineffective_action_count: int
    root_cause_category: str
    root_cause_confidence: float
    analysis_details: Dict[str, Any]


def normalize_1_3_7_9(value: Any, default: int = 7) -> Tuple[int, Optional[str]]:
    if value is None:
        return default, f"missing value defaulted to {default}"
    if isinstance(value, (int, float)):
        v = int(value)
        if v in [1, 3, 7, 9]:
            return v, None
        # map arbitrary 1-10 scale if needed
        if v <= 2:
            return 1, f"value {value} mapped to 1"
        if v <= 5:
            return 3, f"value {value} mapped to 3"
        if v <= 8:
            return 7, f"value {value} mapped to 7"
        return 9, f"value {value} mapped to 9"
    text = str(value).strip().lower()
    mapping = {
        "low": 1, "faible": 1,
        "medium": 3, "moderate": 3, "moyen": 3, "modere": 3, "modéré": 3,
        "high": 7, "elevated": 7, "élevé": 7, "eleve": 7,
        "critical": 9, "critique": 9, "urgent": 9,
        "1": 1, "3": 3, "7": 7, "9": 9
    }
    if text in mapping:
        return mapping[text], None
    return default, f"unrecognized value {value!r} defaulted to {default}"


def oriented_risk(value: float, direction: str) -> float:
    direction = (direction or "").lower()
    if direction == "up":
        return -value
    return value


def is_degraded(value: float, target: Optional[float], direction: str,
                lower_limit: Optional[float] = None, upper_limit: Optional[float] = None) -> bool:
    direction = (direction or "").lower()
    if target is not None:
        if direction == "up":
            return value < target
        return value > target
    # fallback to limits
    if upper_limit is not None and value > upper_limit:
        return True
    if lower_limit is not None and value < lower_limit:
        return True
    return False


def calculate_performance_factor(latest: KPIResult, direction: str) -> Tuple[float, float, List[str]]:
    warnings = []
    value = latest.raw_value
    target = latest.target_value
    gap_ratio = 0.0

    if target is not None and abs(target) > 1e-9:
        if direction.lower() == "up":
            gap_ratio = max(0.0, (target - value) / abs(target))
        else:
            gap_ratio = max(0.0, (value - target) / abs(target))
    else:
        warnings.append("target missing or zero; using acceptable limits fallback")
        if direction.lower() == "up":
            if latest.lower_limit is not None:
                gap_ratio = max(0.0, (latest.lower_limit - value) / max(abs(latest.lower_limit), 1.0))
            else:
                gap_ratio = 1.0 if value <= 0 else 0.0
                warnings.append("lower limit missing; used severity fallback")
        else:
            if latest.upper_limit is not None:
                gap_ratio = max(0.0, (value - latest.upper_limit) / max(abs(latest.upper_limit), 1.0))
            else:
                gap_ratio = 1.0 if value > 0 else 0.0
                warnings.append("upper limit missing; used severity fallback")

    if gap_ratio <= 0:
        factor = 0.8
    elif gap_ratio <= 0.05:
        factor = 1.05
    elif gap_ratio <= 0.10:
        factor = 1.20
    elif gap_ratio <= 0.20:
        factor = 1.60
    elif gap_ratio <= 0.40:
        factor = 2.50
    else:
        factor = 4.00

    performance_score = max(0.0, min(100.0, gap_ratio * 100.0))
    return factor, performance_score, warnings


def count_periods_degraded(results_chronological: List[KPIResult], direction: str) -> int:
    count = 0
    for r in reversed(results_chronological):
        if is_degraded(r.raw_value, r.target_value, direction, r.lower_limit, r.upper_limit):
            count += 1
        else:
            break
    return count


def count_without_improvement(results_chronological: List[KPIResult], direction: str) -> int:
    if len(results_chronological) < 2:
        return 0
    risks = [oriented_risk(r.raw_value, direction) for r in results_chronological]
    count = 0
    # count consecutive periods where latest risk did not improve vs prior
    for i in range(len(risks) - 1, 0, -1):
        if risks[i] >= risks[i - 1]:
            count += 1
        else:
            break
    return count


def linear_slope(values: List[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mean_x = statistics.mean(xs)
    mean_y = statistics.mean(values)
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values)) / denom


def calculate_trend_factor(results_chronological: List[KPIResult], direction: str) -> Tuple[float, str, Dict[str, Any]]:
    if len(results_chronological) < 6:
        return 1.0, "insufficient_data", {"warning": "fewer than 6 periods"}

    last12 = results_chronological[-12:]
    risks = [oriented_risk(r.raw_value, direction) for r in last12]
    slope = linear_slope(risks)

    # consecutive improvement/deterioration
    improving = 0
    deteriorating = 0
    for i in range(len(risks) - 1, 0, -1):
        if risks[i] < risks[i - 1]:
            improving += 1
            if deteriorating:
                break
        elif risks[i] > risks[i - 1]:
            deteriorating += 1
            if improving:
                break
        else:
            break

    details = {"risk_slope_12": slope, "consecutive_improving": improving, "consecutive_deteriorating": deteriorating}

    if improving >= 6:
        return 0.70, "solid_improvement", details
    if improving >= 3 or slope < -0.01:
        return 0.85, "probable_improvement", details
    if deteriorating >= 6:
        return 1.70, "confirmed_deterioration", details
    if deteriorating >= 3 or slope > 0.01:
        return 1.30, "probable_deterioration", details
    return 1.00, "stable", details


def calculate_duration_factor(urgency: int, periods_degraded: int, improving: bool = False) -> float:
    table = {
        9: [1.5, 2.0, 2.5, 3.0],
        7: [1.2, 1.5, 2.0, 2.5],
        3: [1.0, 1.2, 1.5, 2.0],
        1: [1.0, 1.0, 1.2, 1.5],
    }
    arr = table.get(urgency, table[7])
    if periods_degraded <= 0:
        factor = 0.8
    elif periods_degraded == 1:
        factor = arr[0]
    elif periods_degraded == 2:
        factor = arr[1]
    elif periods_degraded == 3:
        factor = arr[2]
    else:
        factor = arr[3]
    if improving and factor > 1:
        factor = max(1.0, factor - 0.3)
    return factor


def evaluate_action_factor(actions: List[Dict[str, Any]]) -> Tuple[float, str, Dict[str, Any]]:
    if not actions:
        return 2.0, "no_action", {"open_action_count": 0, "late_action_count": 0, "ineffective_action_count": 0}

    open_count = 0
    late_count = 0
    ineffective_count = 0
    statuses = []

    for a in actions:
        status = str(a.get("status") or "").lower()
        effectiveness_status = str(a.get("effectiveness_status") or "").lower()
        is_late = bool(a.get("is_late", False))
        if status not in ["done", "cancelled", "closed"]:
            open_count += 1
        if is_late:
            late_count += 1
        if effectiveness_status == "ineffective":
            ineffective_count += 1
        statuses.append((status, effectiveness_status, is_late))

    if ineffective_count > 0:
        return 1.7, "ineffective", {"open_action_count": open_count, "late_action_count": late_count, "ineffective_action_count": ineffective_count, "actions": statuses}
    if late_count > 0:
        return 1.7, "late", {"open_action_count": open_count, "late_action_count": late_count, "ineffective_action_count": ineffective_count, "actions": statuses}
    if any((s[0] in ["planned", "open"] for s in statuses)):
        return 1.8, "planned_not_started", {"open_action_count": open_count, "late_action_count": late_count, "ineffective_action_count": ineffective_count, "actions": statuses}
    if any((s[0] in ["in_progress", "ongoing"] for s in statuses)):
        return 1.2, "in_progress", {"open_action_count": open_count, "late_action_count": late_count, "ineffective_action_count": ineffective_count, "actions": statuses}
    if any((s[1] in ["effective", "expected_effect", "better_than_expected"] for s in statuses)):
        return 0.8, "effective", {"open_action_count": open_count, "late_action_count": late_count, "ineffective_action_count": ineffective_count, "actions": statuses}

    return 1.2, "action_present", {"open_action_count": open_count, "late_action_count": late_count, "ineffective_action_count": ineffective_count, "actions": statuses}


def calculate_spc_status(results_chronological: List[KPIResult], direction: str) -> Tuple[str, int, Dict[str, Any]]:
    if len(results_chronological) < 6:
        return "insufficient_data", 0, {"warning": "fewer than 6 periods"}

    last12 = results_chronological[-12:]
    values = [r.raw_value for r in last12]
    risks = [oriented_risk(v, direction) for v in values]
    signals = []

    if len(values) >= 2:
        moving_ranges = [abs(values[i] - values[i - 1]) for i in range(1, len(values))]
        mr_bar = statistics.mean(moving_ranges) if moving_ranges else 0
        sigma = mr_bar / 1.128 if mr_bar > 0 else (statistics.stdev(values) if len(values) > 1 else 0)
    else:
        sigma = 0

    mean = statistics.mean(values)

    if sigma > 0:
        ucl = mean + 3 * sigma
        lcl = mean - 3 * sigma
        if any(v > ucl or v < lcl for v in values):
            signals.append("point_outside_control_limits")

        # 2 of 3 beyond 2 sigma
        for i in range(len(values) - 2):
            window = values[i:i+3]
            if sum(v > mean + 2 * sigma or v < mean - 2 * sigma for v in window) >= 2:
                signals.append("2_of_3_beyond_2sigma")
                break

        # 4 of 5 beyond 1 sigma
        for i in range(len(values) - 4):
            window = values[i:i+5]
            if sum(v > mean + sigma or v < mean - sigma for v in window) >= 4:
                signals.append("4_of_5_beyond_1sigma")
                break

    if len(risks) >= 8:
        avg_risk = statistics.mean(risks)
        if all(r > avg_risk for r in risks[-8:]) or all(r < avg_risk for r in risks[-8:]):
            signals.append("8_points_same_side_average")

    _, trend_status, trend_details = calculate_trend_factor(results_chronological, direction)
    if trend_status in ["confirmed_deterioration"]:
        signals.append("confirmed_deterioration")
    elif trend_status in ["solid_improvement"]:
        signals.append("solid_improvement")

    if "point_outside_control_limits" in signals:
        status = "out_of_control"
    elif "confirmed_deterioration" in signals:
        status = "confirmed_deterioration"
    elif "solid_improvement" in signals:
        status = "solid_improvement"
    elif len(signals) > 0:
        status = "probable_deterioration"
    else:
        status = "under_control"

    return status, len(signals), {"signals": signals, "sigma_method": "I-MR or stdev fallback", "sigma": sigma, "mean": mean, **trend_details}


def determine_current_status(latest: KPIResult, direction: str, performance_score: float, spc_status: str) -> Tuple[str, str]:
    degraded = is_degraded(latest.raw_value, latest.target_value, direction, latest.lower_limit, latest.upper_limit)
    if not degraded:
        return "Green", "good"
    if performance_score >= 80 or spc_status == "out_of_control":
        return "Critical", "critical"
    if performance_score >= 40:
        return "Red", "bad"
    return "Yellow", "warning"


def priority_level(score: float) -> str:
    if score >= 80:
        return "Critical"
    if score >= 60:
        return "Very High"
    if score >= 40:
        return "High"
    if score >= 20:
        return "Medium"
    return "Low"


def normalize_priority(raw: float, raw_max: float) -> float:
    if raw_max <= 0:
        return 0.0
    return round(max(0.0, min(100.0, (raw / raw_max) * 100.0)), 2)


def calculate_escalation_level(context: KPIContext,
                               periods_without_improvement: int,
                               trend_status: str,
                               action_status: str,
                               current_status: str,
                               urgency: int) -> Tuple[int, str]:
    current_level = int(context.current_escalation_level or 0)

    # Progressive decrease when improving
    if trend_status in ["solid_improvement", "probable_improvement"]:
        if current_level > 0 and (trend_status == "solid_improvement" or current_status == "Green"):
            return max(0, current_level - 1), "escalation decreased due to sustained improvement"
        return current_level, "no escalation increase because KPI is improving"

    if current_status == "Green":
        if current_level > 0:
            return max(0, current_level - 1), "escalation decreased because KPI is within limits"
        return current_level, "no escalation: KPI within limits"

    base_threshold = {9: 1, 7: 2, 3: 3, 1: 4}.get(urgency, 2)
    modifier = 0
    if action_status in ["no_action", "late", "ineffective", "planned_not_started"]:
        modifier -= 1
    threshold = max(1, base_threshold + modifier)

    if periods_without_improvement >= threshold:
        return current_level + 1, f"escalation increased: no improvement for {periods_without_improvement} periods, threshold {threshold}"
    return current_level, f"no escalation increase: no improvement periods {periods_without_improvement}, threshold {threshold}"


def analyze_kpi_allocation(context: KPIContext, results_chronological: List[KPIResult]) -> KPIAnalysis:
    warnings = []
    if not results_chronological:
        raise ValueError("No KPI results available")

    latest = results_chronological[-1]

    importance, w = normalize_1_3_7_9(context.importance, default=7)
    if w:
        warnings.append(f"importance: {w}")
    urgency, w = normalize_1_3_7_9(context.urgency, default=7)
    if w:
        warnings.append(f"urgency: {w}")

    performance_factor, performance_score, perf_warnings = calculate_performance_factor(latest, context.target_direction)
    warnings.extend(perf_warnings)

    periods_degraded = count_periods_degraded(results_chronological, context.target_direction)
    periods_without_improvement = count_without_improvement(results_chronological, context.target_direction)

    trend_factor, trend_status, trend_details = calculate_trend_factor(results_chronological, context.target_direction)
    improving = trend_status in ["solid_improvement", "probable_improvement"]
    duration_factor = calculate_duration_factor(urgency, periods_degraded, improving=improving)

    action_factor, action_status, action_details = evaluate_action_factor(context.actions)
    spc_status, spc_count, spc_details = calculate_spc_status(results_chronological, context.target_direction)

    current_status, business_status = determine_current_status(latest, context.target_direction, performance_score, spc_status)
    new_escalation, escalation_reason = calculate_escalation_level(
        context, periods_without_improvement, trend_status, action_status, current_status, urgency
    )

    # theoretical max: 9 * 9 * 4 * 2 * 3 * 2 * 6 = 11664 if escalation max factor considered 6
    raw_max = 9 * 9 * 4.0 * 2.0 * 3.0 * 2.0 * 6.0
    raw = importance * urgency * performance_factor * trend_factor * duration_factor * action_factor * (new_escalation + 1)
    score = normalize_priority(raw, raw_max)

    details = {
        "data_quality_warnings": warnings,
        "priority_calculation": {
            "raw": raw,
            "raw_max": raw_max,
            "normalized": score,
            "factors": {
                "importance": importance,
                "urgency": urgency,
                "performance_factor": performance_factor,
                "trend_factor": trend_factor,
                "duration_factor": duration_factor,
                "action_factor": action_factor,
                "escalation_factor": new_escalation + 1,
            },
        },
        "trend_details": trend_details,
        "spc_details": spc_details,
        "action_details": action_details,
        "linked_downstream_kpis": context.relationships,
        "comment": build_comment(context, current_status, score, new_escalation, escalation_reason, action_status, trend_status),
        "calculated_at": datetime.utcnow().isoformat() + "Z",
    }

    root_cause_category = infer_root_cause_category(context, action_status)
    root_cause_confidence = 0.3 if root_cause_category == "Unknown" else 0.6

    return KPIAnalysis(
        kpi_id=context.kpi_id,
        kpi_target_allocation_id=context.kpi_target_allocation_id,
        current_status=current_status,
        business_status=business_status,
        performance_score=performance_score,
        performance_gap=performance_score,
        performance_trend=trend_status,
        priority_score=score,
        priority_level=priority_level(score),
        escalation_level=new_escalation,
        escalation_reason=escalation_reason,
        spc_status=spc_status,
        spc_signal_count=spc_count,
        periods_degraded=periods_degraded,
        periods_without_improvement=periods_without_improvement,
        recurrence_count=0,
        action_status=action_status,
        open_action_count=action_details.get("open_action_count", 0),
        late_action_count=action_details.get("late_action_count", 0),
        ineffective_action_count=action_details.get("ineffective_action_count", 0),
        root_cause_category=root_cause_category,
        root_cause_confidence=root_cause_confidence,
        analysis_details=details,
    )


def infer_root_cause_category(context: KPIContext, action_status: str) -> str:
    # V1 simple placeholder. The agent should improve this using kpi_relationship and knowledge base.
    kb = context.knowledge_base or {}
    if kb.get("probable_root_cause_category"):
        return kb["probable_root_cause_category"]
    if context.relationships:
        return str(context.relationships[0].get("root_cause_category") or "Unknown")
    return "Unknown"


def build_comment(context: KPIContext, current_status: str, score: float, escalation: int,
                  escalation_reason: str, action_status: str, trend_status: str) -> str:
    return (
        f"{context.kpi_name} for allocation {context.kpi_target_allocation_id} is {current_status} "
        f"with priority {score:.0f}/100. Trend is {trend_status}. "
        f"Escalation level is {escalation}: {escalation_reason}. "
        f"Action status is {action_status}. Recommended next step: confirm the operational cause and validate a corrective action with the responsible owner."
    )


# Database adapter placeholders

def upsert_monitoring_status(analysis: KPIAnalysis, context: KPIContext) -> None:
    """
    Implement with MCP write capability or DB SQL.

    Upsert key:
      (kpi_id, kpi_target_allocation_id)

    Update monitoring fields and analysis_details.
    """
    raise NotImplementedError


def insert_monitoring_history_if_needed(new_analysis: KPIAnalysis, previous_status: Optional[Dict[str, Any]]) -> None:
    """
    Insert history only if significant change:
      status changed
      priority_level changed
      priority score delta >= 10
      escalation changed
      action_status changed
      root_cause_category changed
    """
    raise NotImplementedError


if __name__ == "__main__":
    # Example local test
    ctx = KPIContext(
        kpi_id=1,
        kpi_target_allocation_id=101,
        kpi_name="Production Backlog",
        target_direction="down",
        importance=9,
        urgency=7,
        current_escalation_level=1,
        actions=[],
    )
    vals = [800, 900, 1000, 1100, 1200, 1250, 1230, 1210, 1190, 1180, 1170, 1160]
    results = [KPIResult(result_date=f"2026-W{i+1}", raw_value=v, target_value=500) for i, v in enumerate(vals)]
    a = analyze_kpi_allocation(ctx, results)
    print(a)
