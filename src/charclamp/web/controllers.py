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

from charclamp.domain.models import BurnShift, Clamp, User, WeighSlip, utcnow
from charclamp.domain.rules import (
    RuleError,
    assert_can_set_clamp_status,
    can_mark_clamp_drawn,
    find_valid_slip_for_day,
    validate_new_slip,
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


def local_today() -> date:
    """过磅联「自然日」以服务器当地日历日为准。"""
    return date.today()


def _parse_weigh_date(raw: str | None) -> date:
    raw = (raw or "").strip()
    if raw:
        return date.fromisoformat(raw)
    return local_today()


def _valid_slip_map(clamps: list[Clamp], on_date: date) -> dict[int, Any]:
    """clamp_id -> 该自然日最新一张合格未作废联（无则不含键）。"""
    mapping: dict[int, Any] = {}
    for clamp in clamps:
        slip = find_valid_slip_for_day(list(clamp.weigh_slips), on_date)
        if slip is not None:
            mapping[clamp.id] = slip
    return mapping


async def _load_timeline_context(clamp_id: int | None = None) -> dict[str, Any]:
    today = local_today()
    async with SessionLocal() as db:
        clamps = list(
            (
                await db.execute(
                    select(Clamp)
                    .options(
                        selectinload(Clamp.site),
                        selectinload(Clamp.shifts),
                        selectinload(Clamp.weigh_slips),
                    )
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
        site_name = clamps[0].site.name if clamps else "乌石岗焖烧坞"

        valid_today = _valid_slip_map(clamps, today)
        # 专页口径：今日未作废合格联张数（唯一约束保证每窑至多一张，与有联窑数恒等，差为 0）。
        page_valid_today = (
            await db.execute(
                select(WeighSlip.id).where(
                    WeighSlip.weighed_on == today,
                    WeighSlip.is_qualified.is_(True),
                    WeighSlip.voided.is_(False),
                )
            )
        ).all()
        silhouette_count = len(valid_today)
        page_count = len(page_valid_today)
    return {
        "clamps": clamps,
        "shifts": shifts,
        "active_clamp_id": clamp_id,
        "status_labels": STATUS_LABELS,
        "site_name": site_name,
        "today": today,
        "valid_today_map": valid_today,
        "slip_silhouette_count": silhouette_count,
        "slip_page_count": page_count,
        "slip_reconcile_diff": silhouette_count - page_count,
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
        today = local_today()
        async with SessionLocal() as db:
            result = await db.execute(
                select(Clamp)
                .where(Clamp.id == clamp_id)
                .options(
                    selectinload(Clamp.shifts),
                    selectinload(Clamp.site),
                    selectinload(Clamp.weigh_slips),
                )
            )
            clamp = result.scalar_one_or_none()
            if not clamp:
                return Redirect("/")
            today_slip = find_valid_slip_for_day(list(clamp.weigh_slips), today)
            can_drawn, drawn_msg = can_mark_clamp_drawn(clamp, today_slip)
        return Template(
            template_name="partials/drawer_clamp.html",
            context={
                "clamp": clamp,
                "status_labels": STATUS_LABELS,
                "can_drawn": can_drawn,
                "drawn_msg": drawn_msg,
                "today": today,
                "today_slip": today_slip,
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
        today = local_today()
        async with SessionLocal() as db:
            result = await db.execute(
                select(Clamp)
                .where(Clamp.id == clamp_id)
                .options(selectinload(Clamp.shifts), selectinload(Clamp.weigh_slips))
            )
            clamp = result.scalar_one_or_none()
            if not clamp:
                return Redirect("/")
            try:
                # 唯一出炭入口：峰值 ≥400℃ 旧门槛 + 当日合格未作废过磅联，均在此函数内校验，禁止旁路。
                slip = (
                    find_valid_slip_for_day(list(clamp.weigh_slips), today)
                    if new_status == Clamp.STATUS_DRAWN
                    else None
                )
                assert_can_set_clamp_status(clamp, new_status, slip)
                clamp.status = new_status
                await db.commit()
                _set_flash(request, f"窑 {clamp.code} 状态已更新", "ok")
            except RuleError as exc:
                _set_flash(request, str(exc), "error")
        return Redirect(f"/?clamp_id={clamp_id}")


async def _load_weighing_context() -> dict[str, Any]:
    today = local_today()
    async with SessionLocal() as db:
        clamps = list(
            (
                await db.execute(
                    select(Clamp)
                    .options(selectinload(Clamp.site), selectinload(Clamp.weigh_slips))
                    .order_by(Clamp.code)
                )
            )
            .scalars()
            .all()
        )
        slips = list(
            (
                await db.execute(
                    select(WeighSlip)
                    .options(selectinload(WeighSlip.clamp).selectinload(Clamp.site))
                    .order_by(WeighSlip.weighed_on.desc(), WeighSlip.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        valid_today = _valid_slip_map(clamps, today)
        page_rows = (
            await db.execute(
                select(WeighSlip.id).where(
                    WeighSlip.weighed_on == today,
                    WeighSlip.is_qualified.is_(True),
                    WeighSlip.voided.is_(False),
                )
            )
        ).all()
        # 唯一约束保证每窑当日至多一张有效合格联，故「有效联张数」恒等于「有联窑数」，与剪影标记差为 0。
        silhouette_count = len(valid_today)
        page_count = len(page_rows)
    return {
        "clamps": clamps,
        "slips": slips,
        "today": today,
        "valid_today_map": valid_today,
        "slip_silhouette_count": silhouette_count,
        "slip_page_count": page_count,
        "slip_reconcile_diff": silhouette_count - page_count,
    }


class WeighSlipController(Controller):
    path = ""
    tags = ["weighing"]

    @get("/weighing", media_type=MediaType.HTML)
    async def weighing_page(self, request: Request) -> Template | Redirect:
        if not request.user:
            return Redirect("/login")
        flash, flash_cat = _pop_flash(request)
        ctx = await _load_weighing_context()
        return Template(
            template_name="weighing.html",
            context={
                **ctx,
                "user": request.user,
                "active_nav": "weighing",
                "flash": flash,
                "flash_cat": flash_cat,
            },
        )

    @post("/weigh-slips/new")
    async def create_slip(
        self,
        request: Request,
        data: dict[str, Any] = Body(media_type=RequestEncodingType.URL_ENCODED),
    ) -> Redirect:
        if not request.user:
            return Redirect("/login")
        try:
            clamp_id = int(data["clamp_id"])
        except (KeyError, TypeError, ValueError):
            _set_flash(request, "未选择有效的炭窑", "error")
            return Redirect("/weighing")
        try:
            weighed_on = _parse_weigh_date(data.get("weighed_on"))
            gross_kg = float((data.get("gross_kg") or "").strip())
            tare_kg = float((data.get("tare_kg") or "").strip())
        except (TypeError, ValueError):
            _set_flash(request, "过磅日或重量填写有误：日期用 YYYY-MM-DD，重量为数字", "error")
            return Redirect("/weighing")
        weigher = (data.get("weigher") or "").strip()
        # 复选框未勾选时字段不会提交，故以显式 == "1" 判定。
        is_qualified = data.get("is_qualified") == "1"

        async with SessionLocal() as db:
            clamp = (
                (
                    await db.execute(
                        select(Clamp)
                        .where(Clamp.id == clamp_id)
                        .options(selectinload(Clamp.weigh_slips))
                    )
                )
                .scalars()
                .one_or_none()
            )
            if not clamp:
                _set_flash(request, "所选炭窑不存在", "error")
                return Redirect("/weighing")
            # rollback() 会使 ORM 对象属性过期，异常分支不能再访问 clamp.code，
            # 故先把纯字符串取到局部变量里。
            clamp_code = clamp.code
            day_iso = weighed_on.isoformat()
            try:
                validate_new_slip(clamp, weighed_on, gross_kg, tare_kg, weigher, is_qualified)
                slip = WeighSlip(
                    clamp_id=clamp.id,
                    weighed_on=weighed_on,
                    gross_kg=gross_kg,
                    tare_kg=tare_kg,
                    weigher=weigher,
                    is_qualified=is_qualified,
                    created_by_id=request.user.id,
                )
                db.add(slip)
                await db.commit()
                _set_flash(
                    request,
                    f"已为窑 {clamp_code} 落下 {day_iso} 过磅联，净重 {slip.net_kg:g} 千克",
                    "ok",
                )
            except RuleError as exc:
                await db.rollback()
                _set_flash(request, str(exc), "error")
            except IntegrityError:
                # 两名司秤并发热点：部分唯一索引兜底，只允许落下一张。
                await db.rollback()
                _set_flash(
                    request,
                    f"炭窑 {clamp_code} 在 {day_iso} 已有一张未作废的合格过磅联，"
                    "本张未保存（可能是并发同时提交）",
                    "error",
                )
        return Redirect("/weighing")

    @post("/weigh-slips/{slip_id:int}/void")
    async def void_slip(
        self,
        request: Request,
        slip_id: int,
        data: dict[str, Any] = Body(media_type=RequestEncodingType.URL_ENCODED),
    ) -> Redirect:
        if not request.user:
            return Redirect("/login")
        if request.user.role != "admin":
            _set_flash(request, "只有管理员可以作废过磅联", "error")
            return Redirect("/weighing")
        async with SessionLocal() as db:
            slip = (
                (await db.execute(select(WeighSlip).where(WeighSlip.id == slip_id)))
                .scalars()
                .one_or_none()
            )
            if not slip:
                _set_flash(request, "过磅联不存在", "error")
                return Redirect("/weighing")
            if slip.voided:
                _set_flash(request, "该过磅联已是作废状态", "error")
                return Redirect("/weighing")
            slip.voided = True
            slip.voided_by_id = request.user.id
            slip.voided_at = utcnow()
            await db.commit()
            _set_flash(request, f"过磅联 #{slip.id} 已作废，此后不再据此算出炭", "ok")
        return Redirect("/weighing")
