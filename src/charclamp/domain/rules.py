"""炭窑焖烧志业务规则。"""

from __future__ import annotations

from datetime import date

from charclamp.domain.models import BurnShift, Clamp, WeighSlip

MIN_PEAK_TEMP_FOR_DRAWN = 400.0
MIN_NET_KG_FOR_DRAWN = 50.0


class RuleError(ValueError):
    """业务规则校验失败。"""


def latest_shift_for_clamp(clamp: Clamp) -> BurnShift | None:
    if not clamp.shifts:
        return None
    return max(clamp.shifts, key=lambda s: s.started_at)


def validate_slip_weights(gross_kg: float, tare_kg: float) -> None:
    """过磅联计量校验：毛重、皮重均须大于 0，净重（毛减皮）不少于 50 千克。"""
    if gross_kg <= 0:
        raise RuleError(f"毛重须大于 0 千克（本次填写 {gross_kg:g}）")
    if tare_kg <= 0:
        raise RuleError(f"皮重须大于 0 千克（本次填写 {tare_kg:g}）")
    net_kg = gross_kg - tare_kg
    if net_kg < MIN_NET_KG_FOR_DRAWN:
        raise RuleError(
            f"净重（毛重 {gross_kg:g} − 皮重 {tare_kg:g} = {net_kg:g} 千克）"
            f"不足 {MIN_NET_KG_FOR_DRAWN:g} 千克，不能落下合格联"
        )


def validate_new_slip(
    clamp: Clamp,
    weighed_on: date,
    gross_kg: float,
    tare_kg: float,
    weigher: str,
    is_qualified: bool = True,
) -> None:
    """新建过磅联前的全部业务校验。

    计量规则（毛重/皮重 > 0、净重 ≥ 50）对所有联生效；
    「同一窑同一自然日最多一张未作废合格联」只约束合格联。
    """
    if not weigher.strip():
        raise RuleError("司秤人不能为空")
    validate_slip_weights(gross_kg, tare_kg)
    if is_qualified:
        existing = find_valid_slip_for_day(clamp.weigh_slips, weighed_on)
        if existing is not None:
            raise RuleError(
                f"炭窑 {clamp.code} 在 {weighed_on.isoformat()} 已有一张未作废的合格过磅联"
                f"（司秤人 {existing.weigher}，净重 {existing.net_kg:g} 千克），不能重复落联"
            )


def find_valid_slip_for_day(slips: list[WeighSlip], on_date: date) -> WeighSlip | None:
    """某自然日最新的一张「合格且未作废」联；没有则 None。"""
    candidates = [
        s
        for s in slips
        if s.weighed_on == on_date and s.is_qualified and not s.voided
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda s: (s.created_at, s.id))


def can_mark_clamp_drawn(clamp: Clamp, slip: WeighSlip | None) -> tuple[bool, str]:
    """
    炭窑转为「已出炭」(drawn) 的唯一入口守卫，须同时满足：

    1. 最近一条焖烧班次的峰值温度已记录且 >= 400℃（旧门槛，继续生效）；
    2. 该窑当日存在一张合格且未作废的出炭过磅联，且净重不少于 50 千克。

    峰值检查与过磅检查都收在这里，出炭入口禁止旁路。
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

    if slip is None:
        return (
            False,
            "该窑今日尚无合格且未作废的出炭过磅联，请先在「过磅联」专页落联后再标记已出炭",
        )
    if slip.voided or not slip.is_qualified:
        return False, "所用过磅联已作废或不合格，不能据此标记已出炭"
    try:
        validate_slip_weights(slip.gross_kg, slip.tare_kg)
    except RuleError as exc:
        return False, str(exc)
    return True, ""


def assert_can_set_clamp_status(clamp: Clamp, new_status: str, slip: WeighSlip | None = None) -> None:
    allowed = {Clamp.STATUS_STACKED, Clamp.STATUS_BURNING, Clamp.STATUS_DRAWN}
    if new_status not in allowed:
        raise RuleError(f"无效状态：{new_status}")
    if new_status == Clamp.STATUS_DRAWN:
        ok, msg = can_mark_clamp_drawn(clamp, slip)
        if not ok:
            raise RuleError(msg)
