"""炭窑焖烧志业务规则。"""

from __future__ import annotations

from charclamp.domain.models import BurnShift, Clamp, DrawWeighSlip

MIN_PEAK_TEMP_FOR_DRAWN = 400.0
MIN_NET_KG_FOR_DRAW = 50.0


class RuleError(ValueError):
    """业务规则校验失败。"""


def latest_shift_for_clamp(clamp: Clamp) -> BurnShift | None:
    if not clamp.shifts:
        return None
    return max(clamp.shifts, key=lambda s: s.started_at)


def validate_weigh_slip_values(gross_kg: float, tare_kg: float) -> None:
    """
    过磅联数值校验：毛重、皮重均须大于 0，净重（毛重−皮重）不少于 50 千克。
    """
    if gross_kg <= 0:
        raise RuleError(f"毛重须大于 0 千克（当前 {gross_kg:g} 千克）")
    if tare_kg <= 0:
        raise RuleError(f"皮重须大于 0 千克（当前 {tare_kg:g} 千克）")
    if gross_kg - tare_kg < MIN_NET_KG_FOR_DRAW:
        raise RuleError(
            f"净重（毛重 {gross_kg:g} − 皮重 {tare_kg:g}）"
            f"仅 {gross_kg - tare_kg:g} 千克，不得少于 {MIN_NET_KG_FOR_DRAW:g} 千克"
        )


def can_mark_clamp_drawn(
    clamp: Clamp, today_slip: DrawWeighSlip | None
) -> tuple[bool, str]:
    """
    炭窑转为「已出炭」(drawn) 的前提（峰值门槛与过磅联检查同一入口，禁止旁路）：

    1. 最近一条焖烧班次的峰值温度已记录，且 >= 400℃；
    2. 该窑当日（自然日）已有一张合格且未作废的出炭过磅联。
    """
    latest = latest_shift_for_clamp(clamp)
    if latest is None:
        return False, "该窑尚无焖烧班次，不能标记为已出炭"
    if latest.peak_temp_c is None:
        return False, "最近班次尚未记录峰值温度，不能标记为已出炭"
    if latest.peak_temp_c < MIN_PEAK_TEMP_FOR_DRAWN:
        return (
            False,
            f"最近班次峰值温度 {latest.peak_temp_c}℃ 低于 {MIN_PEAK_TEMP_FOR_DRAWN:.0f}℃，不能标记为已出炭",
        )
    if today_slip is None:
        return False, "该窑今日尚无合格且未作废的出炭过磅联，须先到过磅联专页落下联单"
    return True, ""


def assert_can_set_clamp_status(
    clamp: Clamp, new_status: str, today_slip: DrawWeighSlip | None
) -> None:
    allowed = {Clamp.STATUS_STACKED, Clamp.STATUS_BURNING, Clamp.STATUS_DRAWN}
    if new_status not in allowed:
        raise RuleError(f"无效状态：{new_status}")
    if new_status == Clamp.STATUS_DRAWN:
        ok, msg = can_mark_clamp_drawn(clamp, today_slip)
        if not ok:
            raise RuleError(msg)
