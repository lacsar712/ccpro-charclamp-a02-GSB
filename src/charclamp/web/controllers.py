from __future__ import annotations

from datetime import date, datetime
from typing import Any

from litestar import Controller, MediaType, Request, get, post
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Redirect, Template
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from charclamp.domain.models import BurnShift, Clamp, DrawWeighSlip, User, utcnow
from charclamp.domain.rules import (
    RuleError,
    assert_can_set_clamp_status,
    can_mark_clamp_drawn,
    validate_weigh_slip_values,
)
from charclamp.infra.db import SessionLocal
from charclamp.infra.security import verify_password

STATUS_LABELS = {
    Clamp.STATUS_STACKED: "已码窑",
    Clamp.STATUS_BURNING: "焖烧中",
    Clamp.STATUS_DRAWN: "已出炭",
}


def _set_flash(request: Request, message: str, category: str = "ok") -> None:
    data = dict(request.session or {})
    data["flash"] = message
    data["flash_cat"] = category
    request.set_session(data)


def _pop_flash(request: Request) -> tuple[str | None, str | None]:
    data = dict(request.session or {})
    message = data.pop("flash", None)
    category = data.pop("flash_cat", None)
    if message is not None or category is not None:
        request.set_session(data)
    return message, category


def _parse_optional_int(raw: str | None) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _today() -> date:
    """窑场自然日（按服务器本地日历）。"""
    return date.today()


async def _today_valid_slips(db) -> dict[int, DrawWeighSlip]:
    """
    各炭窑「当日（自然日）最新一张合格且未作废」过磅联。
    部分唯一索引保证同窑同日至多一张，这里仍按落联时间倒序取最新。
    时间轴剪影与过磅专页共用同一数据源做对账。
    """
    rows = (
        await db.execute(
            select(DrawWeighSlip)
            .where(
                DrawWeighSlip.weighed_on == _today(),
                DrawWeighSlip.is_qualified.is_(True),
                DrawWeighSlip.is_voided.is_(False),
            )
            .order_by(DrawWeighSlip.created_at.desc())
        )
    ).scalars().all()
    by_clamp: dict[int, DrawWeighSlip] = {}
    for slip in rows:
        by_clamp.setdefault(slip.clamp_id, slip)
    return by_clamp


async def _load_timeline_context(clamp_id: int | None = None) -> dict[str, Any]:
    async with SessionLocal() as db:
        clamps = list(
            (
                await db.execute(
                    select(Clamp)
                    .options(selectinload(Clamp.site), selectinload(Clamp.shifts))
                    .order_by(Clamp.code)
                )
            )
            .scalars()
            .all()
        )
        query = (
            select(BurnShift)
            .options(selectinload(BurnShift.clamp).selectinload(Clamp.site))
            .order_by(BurnShift.started_at.desc())
        )
        if clamp_id is not None:
            query = query.where(BurnShift.clamp_id == clamp_id)
        shifts = list((await db.execute(query)).scalars().all())
        today_valid = await _today_valid_slips(db)
        site_name = clamps[0].site.name if clamps else "乌石岗焖烧坞"
    return {
        "clamps": clamps,
        "shifts": shifts,
        "active_clamp_id": clamp_id,
        "status_labels": STATUS_LABELS,
        "site_name": site_name,
        "today_valid_by_clamp": today_valid,
    }


class AuthController(Controller):
    path = ""
    tags = ["auth"]

    @get("/login", media_type=MediaType.HTML)
    async def login_page(self, request: Request) -> Template:
        flash, flash_cat = _pop_flash(request)
        return Template(
            template_name="login.html",
            context={"flash": flash, "flash_cat": flash_cat},
        )

    @post("/login")
    async def login(
        self,
        request: Request,
        data: dict[str, Any] = Body(media_type=RequestEncodingType.URL_ENCODED),
    ) -> Redirect:
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        async with SessionLocal() as db:
            result = await db.execute(select(User).where(User.username == username))
            user = result.scalar_one_or_none()
            if not user or not verify_password(password, user.password_hash):
                request.set_session({"flash": "用户名或密码错误", "flash_cat": "error"})
                return Redirect("/login")
            request.set_session({"user_id": user.id})
        return Redirect("/")

    @get("/logout")
    async def logout(self, request: Request) -> Redirect:
        request.clear_session()
        return Redirect("/login")


class TimelineController(Controller):
    path = ""
    tags = ["timeline"]

    @get("/", media_type=MediaType.HTML)
    async def timeline(self, request: Request) -> Template | Redirect:
        if not request.user:
            return Redirect("/login")
        flash, flash_cat = _pop_flash(request)
        clamp_id = _parse_optional_int(request.query_params.get("clamp_id"))
        ctx = await _load_timeline_context(clamp_id)
        return Template(
            template_name="timeline.html",
            context={
                **ctx,
                "user": request.user,
                "active_nav": "timeline",
                "flash": flash,
                "flash_cat": flash_cat,
            },
        )

    @get("/timeline/partial", media_type=MediaType.HTML)
    async def timeline_partial(self, request: Request) -> Template | Redirect:
        if not request.user:
            return Redirect("/login")
        clamp_id = _parse_optional_int(request.query_params.get("clamp_id"))
        ctx = await _load_timeline_context(clamp_id)
        return Template(
            template_name="partials/board.html",
            context={
                **ctx,
                "user": request.user,
            },
        )

    @get("/drawer/shift-new", media_type=MediaType.HTML)
    async def drawer_shift_new(self, request: Request) -> Template | Redirect:
        if not request.user:
            return Redirect("/login")
        clamp_id = _parse_optional_int(request.query_params.get("clamp_id"))
        async with SessionLocal() as db:
            clamps = list((await db.execute(select(Clamp).order_by(Clamp.code))).scalars().all())
        return Template(
            template_name="partials/drawer_shift.html",
            context={
                "clamps": clamps,
                "preselect_clamp_id": clamp_id,
                "user": request.user,
            },
        )

    @get("/drawer/clamp/{clamp_id:int}", media_type=MediaType.HTML)
    async def drawer_clamp(self, request: Request, clamp_id: int) -> Template | Redirect:
        if not request.user:
            return Redirect("/login")
        async with SessionLocal() as db:
            result = await db.execute(
                select(Clamp)
                .where(Clamp.id == clamp_id)
                .options(selectinload(Clamp.shifts), selectinload(Clamp.site))
            )
            clamp = result.scalar_one_or_none()
            if not clamp:
                return Redirect("/")
            today_slip = (
                await db.execute(
                    select(DrawWeighSlip)
                    .where(
                        DrawWeighSlip.clamp_id == clamp_id,
                        DrawWeighSlip.weighed_on == _today(),
                        DrawWeighSlip.is_qualified.is_(True),
                        DrawWeighSlip.is_voided.is_(False),
                    )
                    .order_by(DrawWeighSlip.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        can_drawn, drawn_msg = can_mark_clamp_drawn(clamp, today_slip)
        return Template(
            template_name="partials/drawer_clamp.html",
            context={
                "clamp": clamp,
                "status_labels": STATUS_LABELS,
                "can_drawn": can_drawn,
                "drawn_msg": drawn_msg,
                "today_slip": today_slip,
                "today": _today(),
                "user": request.user,
            },
        )


class ShiftController(Controller):
    path = "/shifts"
    tags = ["shifts"]

    @post("/new")
    async def create_shift(
        self,
        request: Request,
        data: dict[str, Any] = Body(media_type=RequestEncodingType.URL_ENCODED),
    ) -> Redirect:
        if not request.user:
            return Redirect("/login")
        started_raw = data.get("started_at") or ""
        started_at = datetime.fromisoformat(started_raw) if started_raw else datetime.utcnow()
        peak_raw = (data.get("peak_temp_c") or "").strip()
        peak = float(peak_raw) if peak_raw else None
        clamp_id = int(data["clamp_id"])
        async with SessionLocal() as db:
            shift = BurnShift(
                clamp_id=clamp_id,
                started_at=started_at,
                peak_temp_c=peak,
                charcoal_grade=(data.get("charcoal_grade") or "B").strip(),
                notes=(data.get("notes") or "").strip(),
            )
            db.add(shift)
            clamp = (
                await db.execute(select(Clamp).where(Clamp.id == clamp_id))
            ).scalar_one_or_none()
            if clamp and clamp.status == Clamp.STATUS_STACKED:
                clamp.status = Clamp.STATUS_BURNING
            await db.commit()
        _set_flash(request, "焖烧班次已登记", "ok")
        return Redirect(f"/?clamp_id={clamp_id}")


class ClampController(Controller):
    path = "/clamps"
    tags = ["clamps"]

    @post("/{clamp_id:int}/status")
    async def set_status(
        self,
        request: Request,
        clamp_id: int,
        data: dict[str, Any] = Body(media_type=RequestEncodingType.URL_ENCODED),
    ) -> Redirect:
        if not request.user:
            return Redirect("/login")
        new_status = (data.get("status") or "").strip()
        async with SessionLocal() as db:
            result = await db.execute(
                select(Clamp)
                .where(Clamp.id == clamp_id)
                .options(selectinload(Clamp.shifts))
            )
            clamp = result.scalar_one_or_none()
            if not clamp:
                return Redirect("/")
            # 出炭前现场读取该窑当日最新合格且未作废联，与峰值门槛在同一函数内判定。
            today_slip = None
            if new_status == Clamp.STATUS_DRAWN:
                today_slip = (
                    await db.execute(
                        select(DrawWeighSlip)
                        .where(
                            DrawWeighSlip.clamp_id == clamp_id,
                            DrawWeighSlip.weighed_on == _today(),
                            DrawWeighSlip.is_qualified.is_(True),
                            DrawWeighSlip.is_voided.is_(False),
                        )
                        .order_by(DrawWeighSlip.created_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
            try:
                assert_can_set_clamp_status(clamp, new_status, today_slip)
                clamp.status = new_status
                await db.commit()
                _set_flash(request, f"窑 {clamp.code} 状态已更新", "ok")
            except RuleError as exc:
                _set_flash(request, str(exc), "error")
        return Redirect(f"/?clamp_id={clamp_id}")


class WeighSlipController(Controller):
    path = "/weigh-slips"
    tags = ["weigh-slips"]

    @get("", media_type=MediaType.HTML)
    async def list_page(self, request: Request) -> Template | Redirect:
        if not request.user:
            return Redirect("/login")
        flash, flash_cat = _pop_flash(request)
        async with SessionLocal() as db:
            slips = list(
                (
                    await db.execute(
                        select(DrawWeighSlip)
                        .options(
                            selectinload(DrawWeighSlip.clamp).selectinload(Clamp.site)
                        )
                        .order_by(
                            DrawWeighSlip.weighed_on.desc(),
                            DrawWeighSlip.created_at.desc(),
                        )
                    )
                )
                .scalars()
                .all()
            )
            clamps = list(
                (await db.execute(select(Clamp).order_by(Clamp.code))).scalars().all()
            )
            today_valid = await _today_valid_slips(db)
        valid_open_count = sum(1 for s in slips if s.is_qualified and not s.is_voided)
        return Template(
            template_name="weigh_slips.html",
            context={
                "slips": slips,
                "clamps": clamps,
                "today": _today(),
                "today_valid_by_clamp": today_valid,
                "today_valid_count": len(today_valid),
                "valid_open_count": valid_open_count,
                "user": request.user,
                "active_nav": "weigh",
                "flash": flash,
                "flash_cat": flash_cat,
            },
        )

    @post("/new")
    async def create_slip(
        self,
        request: Request,
        data: dict[str, Any] = Body(media_type=RequestEncodingType.URL_ENCODED),
    ) -> Redirect:
        if not request.user:
            return Redirect("/login")

        try:
            clamp_id = int(data.get("clamp_id"))
        except (TypeError, ValueError):
            _set_flash(request, "请选择炭窑", "error")
            return Redirect("/weigh-slips")

        weighed_raw = (data.get("weighed_on") or "").strip()
        try:
            weighed_on = date.fromisoformat(weighed_raw) if weighed_raw else _today()
        except ValueError:
            _set_flash(request, "过磅日格式无效，请按自然日（YYYY-MM-DD）填写", "error")
            return Redirect("/weigh-slips")

        try:
            gross_kg = float(data.get("gross_kg"))
            tare_kg = float(data.get("tare_kg"))
        except (TypeError, ValueError):
            _set_flash(request, "毛重与皮重须为数字（千克）", "error")
            return Redirect("/weigh-slips")

        weigher = (data.get("weigher") or "").strip()
        if not weigher:
            _set_flash(request, "须填写司秤人", "error")
            return Redirect("/weigh-slips")

        is_qualified = (data.get("is_qualified") or "1") == "1"

        try:
            validate_weigh_slip_values(gross_kg, tare_kg)
        except RuleError as exc:
            _set_flash(request, str(exc), "error")
            return Redirect("/weigh-slips")

        async with SessionLocal() as db:
            clamp = (
                await db.execute(select(Clamp).where(Clamp.id == clamp_id))
            ).scalar_one_or_none()
            if not clamp:
                _set_flash(request, "炭窑不存在", "error")
                return Redirect("/weigh-slips")

            # 应用层先挡一道，给出友好中文提示。
            if is_qualified:
                existing = (
                    await db.execute(
                        select(DrawWeighSlip).where(
                            DrawWeighSlip.clamp_id == clamp_id,
                            DrawWeighSlip.weighed_on == weighed_on,
                            DrawWeighSlip.is_qualified.is_(True),
                            DrawWeighSlip.is_voided.is_(False),
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    _set_flash(
                        request,
                        f"炭窑 {clamp.code} 在 {weighed_on.isoformat()} 已有一张未作废合格联"
                        f"（联号 {existing.id}，司秤 {existing.weigher}），同窑同一自然日只许落下一张",
                        "error",
                    )
                    return Redirect("/weigh-slips")

            slip = DrawWeighSlip(
                clamp_id=clamp_id,
                weighed_on=weighed_on,
                gross_kg=gross_kg,
                tare_kg=tare_kg,
                weigher=weigher,
                is_qualified=is_qualified,
                notes=(data.get("notes") or "").strip(),
            )
            db.add(slip)
            try:
                await db.commit()
            except IntegrityError:
                # 两名司秤并发抢落同窑同日合格联：数据库部分唯一索引兜底，只许落下一张。
                await db.rollback()
                _set_flash(
                    request,
                    f"炭窑 {clamp.code} 在 {weighed_on.isoformat()} 已有一张未作废合格联，"
                    "同窑同一自然日只许落下一张，本联未保存",
                    "error",
                )
                return Redirect("/weigh-slips")
        _set_flash(request, f"出炭过磅联（{clamp.code} · {weighed_on.isoformat()}）已落下", "ok")
        return Redirect("/weigh-slips")

    @post("/{slip_id:int}/void")
    async def void_slip(self, request: Request, slip_id: int) -> Redirect:
        if not request.user:
            return Redirect("/login")
        if request.user.role != "admin":
            _set_flash(request, "仅管理员可作废过磅联", "error")
            return Redirect("/weigh-slips")
        async with SessionLocal() as db:
            slip = (
                await db.execute(
                    select(DrawWeighSlip)
                    .where(DrawWeighSlip.id == slip_id)
                    .options(selectinload(DrawWeighSlip.clamp))
                )
            ).scalar_one_or_none()
            if slip is None:
                _set_flash(request, "过磅联不存在", "error")
            elif slip.is_voided:
                _set_flash(request, f"联号 {slip.id} 已作废，无需重复操作", "error")
            else:
                slip.is_voided = True
                slip.voided_at = utcnow()
                await db.commit()
                _set_flash(
                    request,
                    f"联号 {slip.id}（{slip.clamp.code} · {slip.weighed_on.isoformat()}）已作废，"
                    "不得再据此算出炭",
                    "ok",
                )
        return Redirect("/weigh-slips")
