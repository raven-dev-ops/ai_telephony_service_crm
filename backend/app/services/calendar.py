from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
from pathlib import Path
from typing import List, Optional
import httpx
import json

try:  # Optional Google Calendar dependencies.
    from google.auth.transport.requests import Request
    from google.oauth2.service_account import Credentials as ServiceAccountCredentials
    from google.oauth2.credentials import Credentials as UserCredentials
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except Exception:  # pragma: no cover - fallback when google libs are absent.
    Request = None  # type: ignore[assignment]
    ServiceAccountCredentials = None  # type: ignore[assignment]
    UserCredentials = None  # type: ignore[assignment]
    build = None  # type: ignore[assignment]

    class HttpError(Exception):
        """Lightweight stand-in for googleapiclient.errors.HttpError."""

        def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
            # Accept any signature shape the callers provide.
            super().__init__(*args)
            self.resp = kwargs.get("resp")
            self.content = kwargs.get("content")


from ..config import get_settings
from ..db import SQLALCHEMY_AVAILABLE, SessionLocal
from ..db_models import BusinessDB
from ..services.oauth_tokens import oauth_store, OAuthToken

logger = logging.getLogger(__name__)


@dataclass
class TimeSlot:
    start: datetime
    end: datetime


def _parse_closed_days(raw: str | None) -> set[int]:
    """Parse a comma-separated list of closed days into weekday indices.

    Accepts short/long day names (e.g. "Sun", "Sunday") or integers 0-6
    where Monday=0.
    """
    if not raw:
        return set()
    tokens = [t.strip() for t in str(raw).split(",") if t.strip()]
    if not tokens:
        return set()
    mapping = {
        "mon": 0,
        "monday": 0,
        "tue": 1,
        "tues": 1,
        "tuesday": 1,
        "wed": 2,
        "wednesday": 2,
        "thu": 3,
        "thur": 3,
        "thurs": 3,
        "thursday": 3,
        "fri": 4,
        "friday": 4,
        "sat": 5,
        "saturday": 5,
        "sun": 6,
        "sunday": 6,
    }
    closed: set[int] = set()
    for token in tokens:
        key = token.lower()
        if key in mapping:
            closed.add(mapping[key])
            continue
        try:
            idx = int(token)
        except ValueError:
            continue
        if 0 <= idx <= 6:
            closed.add(idx)
    return closed


def _get_business_hours(business_id: str | None) -> tuple[int, int, set[int]]:
    """Return (open_hour, close_hour, closed_days) for a tenant.

    Defaults are taken from calendar settings and may be overridden on
    a per-tenant basis via the Business row when database support is
    available.
    """
    settings = get_settings().calendar
    open_hour = getattr(settings, "default_open_hour", 8)
    close_hour = getattr(settings, "default_close_hour", 17)
    closed_days = _parse_closed_days(getattr(settings, "default_closed_days", ""))

    if business_id and SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session_db = SessionLocal()
        try:
            row = session_db.get(BusinessDB, business_id)
        finally:
            session_db.close()
        if row is not None:
            if getattr(row, "open_hour", None) is not None:
                open_hour = int(row.open_hour)  # type: ignore[arg-type]
            if getattr(row, "close_hour", None) is not None:
                close_hour = int(row.close_hour)  # type: ignore[arg-type]
            if getattr(row, "closed_days", None):
                closed_days = _parse_closed_days(row.closed_days)

    # Guard against misconfiguration where close_hour <= open_hour; treat as always open.
    if close_hour <= open_hour:
        return open_hour, close_hour, set()
    return open_hour, close_hour, closed_days


def _get_business_capacity(
    business_id: str | None,
) -> tuple[Optional[int], bool, int, dict[str, int]]:
    """Return (max_jobs_per_day, reserve_mornings_for_emergencies, travel_buffer_minutes, service_durations).

    Values are pulled from the Business row when database support is available.
    Missing values fall back to sensible defaults:
    - max_jobs_per_day: None (no explicit per-day cap)
    - reserve_mornings_for_emergencies: False
    - travel_buffer_minutes: 0
    - service_durations: {} (no per-service overrides)
    """
    max_jobs_per_day: Optional[int] = None
    reserve_mornings_for_emergencies = False
    travel_buffer_minutes = 0
    service_durations: dict[str, int] = {}

    if business_id and SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session_db = SessionLocal()
        try:
            row = session_db.get(BusinessDB, business_id)
            raw_duration_config = getattr(row, "service_duration_config", None) or ""
            try:
                parsed = json.loads(raw_duration_config) if raw_duration_config else {}
                if isinstance(parsed, dict):
                    service_durations = {
                        str(k): int(v)
                        for k, v in parsed.items()
                        if isinstance(v, (int, float))
                    }
            except Exception:
                service_durations = {}
        finally:
            session_db.close()
        if row is not None:
            if getattr(row, "max_jobs_per_day", None) is not None:
                try:
                    max_jobs_per_day = int(row.max_jobs_per_day)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    max_jobs_per_day = None
            if getattr(row, "reserve_mornings_for_emergencies", None) is not None:
                reserve_mornings_for_emergencies = bool(
                    row.reserve_mornings_for_emergencies  # type: ignore[attr-defined]
                )
            if getattr(row, "travel_buffer_minutes", None) is not None:
                try:
                    travel_buffer_minutes = max(
                        0, int(row.travel_buffer_minutes)  # type: ignore[arg-type]
                    )
                except (TypeError, ValueError):
                    travel_buffer_minutes = 0

    return (
        max_jobs_per_day,
        reserve_mornings_for_emergencies,
        travel_buffer_minutes,
        service_durations,
    )


def _load_gcal_tokens(business_id: str) -> OAuthToken | None:
    if not (SQLALCHEMY_AVAILABLE and SessionLocal is not None):
        return None
    session_db = SessionLocal()
    try:
        row = session_db.get(BusinessDB, business_id)
        if not row:
            return None
        if getattr(row, "gcalendar_access_token", None) and getattr(
            row, "gcalendar_refresh_token", None
        ):
            expires_at = (
                row.gcalendar_token_expires_at.timestamp()
                if getattr(row, "gcalendar_token_expires_at", None)
                else datetime.now(UTC).timestamp() + 3600
            )
            return OAuthToken(
                access_token=row.gcalendar_access_token,
                refresh_token=row.gcalendar_refresh_token,
                expires_at=expires_at,
            )
    finally:
        session_db.close()
    return None


def _save_gcal_tokens(
    business_id: str, access_token: str, refresh_token: str, expires_in: int
) -> OAuthToken:
    tok = oauth_store.save_tokens(
        "gcalendar",
        business_id,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
    )
    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        session_db = SessionLocal()
        try:
            row = session_db.get(BusinessDB, business_id)
            if row:
                row.gcalendar_access_token = access_token
                row.gcalendar_refresh_token = refresh_token
                row.gcalendar_token_expires_at = datetime.now(UTC) + timedelta(
                    seconds=expires_in
                )
                session_db.add(row)
                session_db.commit()
        finally:
            session_db.close()
    return tok


def _refresh_gcal_tokens(business_id: str, settings) -> OAuthToken | None:
    tok = oauth_store.get_tokens("gcalendar", business_id) or _load_gcal_tokens(
        business_id
    )
    if not tok:
        return None
    client_id = settings.oauth.google_client_id
    client_secret = settings.oauth.google_client_secret
    if tok.refresh_token and client_id and client_secret:
        data = {
            "grant_type": "refresh_token",
            "refresh_token": tok.refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        try:
            with httpx.Client(timeout=8.0) as client:
                resp = client.post("https://oauth2.googleapis.com/token", data=data)
            if resp.status_code == 200:
                payload = resp.json()
                access_token = payload.get("access_token", tok.access_token)
                refresh_token = payload.get("refresh_token") or tok.refresh_token
                expires_in = int(payload.get("expires_in") or 3600)
                return _save_gcal_tokens(
                    business_id, access_token, refresh_token, expires_in
                )
        except Exception:
            logger.warning(
                "gcal_token_refresh_failed",
                exc_info=True,
                extra={"business_id": business_id},
            )
    return _save_gcal_tokens(
        business_id,
        access_token=f"gcalendar_access_{int(datetime.now(UTC).timestamp())}",
        refresh_token=tok.refresh_token,
        expires_in=3600,
    )


def _align_to_business_hours(
    start: datetime,
    duration: timedelta,
    open_hour: int,
    close_hour: int,
    closed_days: set[int],
) -> datetime:
    """Align a candidate start time to fall within business hours.

    - Moves the start forward to the next open day/hour when needed.
    - Ensures the entire duration fits before close_hour; otherwise moves
      to the next open day.
    """
    candidate = start
    # Safety guard against pathological configurations.
    max_iterations = 14  # roughly two weeks of search
    for _ in range(max_iterations):
        weekday = candidate.weekday()  # Monday=0
        if weekday in closed_days:
            # Move to next day at opening time.
            candidate = (candidate + timedelta(days=1)).replace(
                hour=open_hour, minute=0, second=0, microsecond=0
            )
            continue

        day_open = candidate.replace(hour=open_hour, minute=0, second=0, microsecond=0)
        day_close = candidate.replace(
            hour=close_hour, minute=0, second=0, microsecond=0
        )

        if candidate < day_open:
            candidate = day_open

        if candidate + duration <= day_close:
            return candidate

        # Move to next day at opening time.
        candidate = (candidate + timedelta(days=1)).replace(
            hour=open_hour, minute=0, second=0, microsecond=0
        )

    # Fallback: return the original start if alignment fails repeatedly.
    return start


def _build_busy_ranges(
    appointments: List,
    travel_buffer_minutes: int,
    target_address: str | None = None,
) -> List[tuple[datetime, datetime]]:
    """Return busy ranges for appointments with optional travel buffers."""

    ranges: List[tuple[datetime, datetime]] = []
    for appt in appointments:
        appt_start = getattr(appt, "start_time", None)
        appt_end = getattr(appt, "end_time", None)
        if not appt_start or not appt_end:
            continue
        buffer_minutes = travel_buffer_minutes
        if target_address and getattr(appt, "address", None):
            try:
                from ..services.geo_utils import geocode_address, haversine_km

                prev_coords = geocode_address(getattr(appt, "address"))
                next_coords = geocode_address(target_address)
                if prev_coords and next_coords:
                    km = haversine_km(prev_coords, next_coords)
                    buffer_minutes = max(buffer_minutes, int((km / 40) * 60) + 10)
            except Exception:
                buffer_minutes = travel_buffer_minutes

        if buffer_minutes > 0:
            appt_start = appt_start - timedelta(minutes=buffer_minutes)
            appt_end = appt_end + timedelta(minutes=buffer_minutes)
        ranges.append((appt_start, appt_end))
    ranges.sort(key=lambda r: r[0])
    return ranges


def _parse_datetime_utc(raw: str | None) -> datetime | None:
    """Parse an ISO8601/RFC3339 datetime and normalize to UTC.

    - Accepts a trailing "Z" and converts to +00:00.
    - If the timestamp is naive, assumes UTC to preserve internal invariants.
    """
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class CalendarService:
    """Encapsulates calendar operations.

    By default, this service operates in stub mode (no external calls). If
    the environment is configured with a service account credentials file and
    `CALENDAR_USE_STUB=false`, it will attempt to use Google Calendar.
    """

    def resolve_duration_minutes(
        self,
        business_id: str | None,
        service_type: str | None,
        default_minutes: int,
    ) -> int:
        """Resolve duration using per-service overrides when configured."""

        _, _, _, service_durations = _get_business_capacity(business_id)
        if service_type and service_type in service_durations:
            return int(service_durations[service_type])
        return default_minutes

    def __init__(self) -> None:
        self._settings = get_settings().calendar
        self._client = self._build_client() if not self._settings.use_stub else None

    def _build_client(self):
        creds_path = self._settings.credentials_file
        if not creds_path:
            return None

        path = Path(creds_path)
        if not path.exists():
            return None

        scopes = ["https://www.googleapis.com/auth/calendar"]
        try:
            creds = ServiceAccountCredentials.from_service_account_file(
                str(path), scopes=scopes
            )
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
            service = build("calendar", "v3", credentials=creds, cache_discovery=False)
            return service
        except Exception:
            # Fallback to stub behaviour if credentials are invalid/misconfigured.
            return None

    def _build_user_client(self, business_id: str | None):
        """Build a Google Calendar client using per-tenant OAuth tokens when available."""
        if not business_id:
            return None
        if not (UserCredentials and Request and build):
            return None
        settings = get_settings()
        tok = oauth_store.get_tokens("gcalendar", business_id) or _load_gcal_tokens(
            business_id
        )
        if not tok:
            return None
        client_id = settings.oauth.google_client_id
        client_secret = settings.oauth.google_client_secret
        creds = UserCredentials(
            token=tok.access_token,
            refresh_token=tok.refresh_token,
            token_uri="https://oauth2.googleapis.com/token",  # nosec B106 - OAuth endpoint
            client_id=client_id,
            client_secret=client_secret,
        )
        try:
            if creds and getattr(creds, "expired", False):
                creds.refresh(Request())
                # Persist refreshed tokens so subsequent requests stay valid.
                expiry = getattr(creds, "expiry", None)
                expires_in = 3600
                if expiry:
                    try:
                        expires_in = max(
                            60, int((expiry - datetime.now(UTC)).total_seconds())
                        )
                    except Exception:
                        expires_in = 3600
                _save_gcal_tokens(
                    business_id,
                    access_token=creds.token,
                    refresh_token=creds.refresh_token or tok.refresh_token,
                    expires_in=expires_in,
                )
            return build("calendar", "v3", credentials=creds, cache_discovery=False)
        except Exception:
            refreshed = _refresh_gcal_tokens(business_id, settings)
            if refreshed:
                return self._build_user_client(business_id)
            return None

    def _resolve_calendar_id(self, business_id: str | None, calendar_id: str | None):
        if calendar_id:
            return calendar_id
        if business_id and SQLALCHEMY_AVAILABLE and SessionLocal is not None:
            session = SessionLocal()
            try:
                row = session.get(BusinessDB, business_id)
                if row is not None and getattr(row, "calendar_id", None):
                    return row.calendar_id  # type: ignore[return-value]
            finally:
                session.close()
        return self._settings.calendar_id

    async def find_slots(
        self,
        duration_minutes: int,
        calendar_id: str | None = None,
        business_id: str | None = None,
        is_emergency: bool | None = None,
        technician_id: str | None = None,
        address: str | None = None,
        service_type: str | None = None,
    ) -> List[TimeSlot]:
        duration_minutes = self.resolve_duration_minutes(
            business_id, service_type, duration_minutes
        )
        # Choose client: tenant OAuth > service account > stub.
        client = (
            None
            if self._settings.use_stub
            else self._build_user_client(business_id) or self._client
        )
        if not client:
            # Stub implementation with basic capacity and routing constraints.
            now = datetime.now(UTC)
            open_hour, close_hour, closed_days = _get_business_hours(business_id)
            duration = timedelta(minutes=duration_minutes)

            # If we don't have a business context, fall back to simple alignment.
            if not business_id:
                candidate = _align_to_business_hours(
                    now + timedelta(hours=1),
                    duration,
                    open_hour,
                    close_hour,
                    closed_days,
                )
                end = candidate + duration
                return [TimeSlot(start=candidate, end=end)]

            from ..repositories import (
                appointments_repo,
            )  # Local import to avoid cycles.

            (
                max_jobs_per_day,
                reserve_mornings_for_emergencies,
                travel_buffer_minutes,
                _service_durations,
            ) = _get_business_capacity(business_id)

            # Search starting roughly an hour from now, up to two weeks out.
            search_start = now + timedelta(hours=1)
            max_days = 14
            appointments = appointments_repo.list_for_business(business_id)
            if technician_id is not None:
                appointments = [
                    a
                    for a in appointments
                    if getattr(a, "technician_id", None) == technician_id
                ]

            for day_offset in range(max_days):
                day_base = search_start + timedelta(days=day_offset)
                weekday = day_base.weekday()
                if weekday in closed_days:
                    continue

                day = day_base.date()
                day_open = datetime(
                    day.year,
                    day.month,
                    day.day,
                    open_hour,
                    0,
                    0,
                    tzinfo=UTC,
                )
                day_close = datetime(
                    day.year,
                    day.month,
                    day.day,
                    close_hour,
                    0,
                    0,
                    tzinfo=UTC,
                )

                # Skip days where the requested duration cannot fit at all.
                if day_open + duration > day_close:
                    continue

                # Start no earlier than search_start on the first day; otherwise at open.
                candidate_start = day_open
                if day_offset == 0 and search_start > candidate_start:
                    candidate_start = search_start

                # Optionally reserve mornings for emergencies only.
                if reserve_mornings_for_emergencies and not is_emergency:
                    morning_end_hour = max(open_hour, 12)
                    morning_end = datetime(
                        day.year,
                        day.month,
                        day.day,
                        morning_end_hour,
                        0,
                        0,
                        tzinfo=UTC,
                    )
                    if candidate_start < morning_end:
                        candidate_start = morning_end

                if candidate_start + duration > day_close:
                    continue

                # Collect existing appointments for this business on the day.
                day_appts = []
                for appt in appointments:
                    appt_start = getattr(appt, "start_time", None)
                    appt_end = getattr(appt, "end_time", None)
                    if not appt_start or not appt_end:
                        continue
                    if appt_start.date() != day:
                        continue
                    status = getattr(appt, "status", "SCHEDULED").upper()
                    if status not in {"SCHEDULED", "CONFIRMED"}:
                        continue
                    day_appts.append(appt)

                # Enforce per-day capacity if configured.
                if max_jobs_per_day is not None and len(day_appts) >= max_jobs_per_day:
                    continue

                # Build busy ranges including travel buffers.
                busy_ranges = _build_busy_ranges(
                    day_appts, travel_buffer_minutes, address
                )

                # Scan for the first free gap that can hold the duration.
                candidate = candidate_start
                for busy_start, busy_end in busy_ranges:
                    # If the candidate window fits before the next busy range, use it.
                    if candidate + duration <= busy_start:
                        break
                    # Otherwise, move candidate to the end of this busy range and continue.
                    if candidate < busy_end:
                        candidate = busy_end
                    if candidate + duration > day_close:
                        break
                else:
                    # No busy ranges or candidate is after all busy ranges.
                    if candidate + duration <= day_close:
                        return [TimeSlot(start=candidate, end=candidate + duration)]
                    continue

                # After scanning busy ranges, ensure the candidate still fits in the day.
                if candidate + duration <= day_close:
                    return [TimeSlot(start=candidate, end=candidate + duration)]

            # Fallback: if no constrained slot found, align next hour within business hours.
            fallback = _align_to_business_hours(
                now + timedelta(hours=1),
                duration,
                open_hour,
                close_hour,
                closed_days,
            )
            return [TimeSlot(start=fallback, end=fallback + duration)]

        # Minimal Google Calendar implementation: look for the next free window
        # after "now" of the requested duration, using the primary calendar.
        now = datetime.now(UTC)
        open_hour, close_hour, closed_days = _get_business_hours(business_id)
        (
            max_jobs_per_day,
            reserve_mornings_for_emergencies,
            travel_buffer_minutes,
            service_durations,
        ) = _get_business_capacity(business_id)
        duration = timedelta(
            minutes=service_durations.get(service_type or "", duration_minutes)
        )
        candidate_start = _align_to_business_hours(
            now + timedelta(hours=1),
            duration,
            open_hour,
            close_hour,
            closed_days,
        )
        time_min = now.isoformat().replace("+00:00", "Z")
        time_max = (now + timedelta(days=7)).isoformat().replace("+00:00", "Z")
        cal_id = self._resolve_calendar_id(business_id, calendar_id)

        busy_ranges: List[tuple[datetime, datetime]] = []
        try:
            body = {
                "timeMin": time_min,
                "timeMax": time_max,
                "items": [{"id": cal_id}],
            }
            freebusy = client.freebusy().query(body=body).execute()
            cal_busy = freebusy["calendars"][cal_id]["busy"]
            for item in cal_busy:
                start = datetime.fromisoformat(item["start"].replace("Z", "+00:00"))
                end = datetime.fromisoformat(item["end"].replace("Z", "+00:00"))
                busy_ranges.append((start, end))
        except HttpError:
            # On API failure, fall back to stub behaviour.
            now = datetime.now(UTC)
            open_hour, close_hour, closed_days = _get_business_hours(business_id)
            candidate = _align_to_business_hours(
                now + timedelta(hours=1),
                duration,
                open_hour,
                close_hour,
                closed_days,
            )
            end = candidate + duration
            return [TimeSlot(start=candidate, end=end)]

        # Apply a simple per-day capacity check when we have a business
        # context by counting busy blocks that fall on the candidate day.
        if max_jobs_per_day is not None and busy_ranges:
            day = candidate_start.date()
            day_blocks = [(s, e) for s, e in busy_ranges if s.date() == day]
            if len(day_blocks) >= max_jobs_per_day:
                # Fall back to a stub-style aligned slot if capacity is exceeded.
                candidate = _align_to_business_hours(
                    now + timedelta(hours=1),
                    duration,
                    open_hour,
                    close_hour,
                    closed_days,
                )
                return [TimeSlot(start=candidate, end=candidate + duration)]

        # Very simple search: look for first gap that fits the duration,
        # optionally padding existing events with a travel buffer.
        padded_busy: List[tuple[datetime, datetime]] = []
        for start, end in busy_ranges:
            if travel_buffer_minutes > 0:
                start = start - timedelta(minutes=travel_buffer_minutes)
                end = end + timedelta(minutes=travel_buffer_minutes)
            padded_busy.append((start, end))

        padded_busy.sort(key=lambda r: r[0])

        # Optionally avoid mornings for non-emergency work.
        if reserve_mornings_for_emergencies and not is_emergency:
            morning_end_hour = max(open_hour, 12)
            morning_end = candidate_start.replace(
                hour=morning_end_hour, minute=0, second=0, microsecond=0
            )
            if candidate_start < morning_end:
                candidate_start = morning_end

        for busy_start, busy_end in padded_busy:
            if candidate_start + duration <= busy_start:
                return [TimeSlot(start=candidate_start, end=candidate_start + duration)]
            candidate_start = max(candidate_start, busy_end)

        # If we find nothing in the next week, fall back to stub-like behaviour.
        return [TimeSlot(start=candidate_start, end=candidate_start + duration)]

    async def create_event(
        self,
        summary: str,
        slot: TimeSlot,
        description: str = "",
        calendar_id: str | None = None,
        business_id: str | None = None,
    ) -> str:
        client = (
            None
            if self._settings.use_stub
            else self._build_user_client(business_id) or self._client
        )
        if not client:
            # Stub behaviour.
            return f"event_placeholder_{slot.start.isoformat()}"

        event = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": slot.start.isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": slot.end.isoformat(), "timeZone": "UTC"},
        }
        try:
            cal_id = self._resolve_calendar_id(business_id, calendar_id)
            created = client.events().insert(calendarId=cal_id, body=event).execute()
            return created.get("id", f"event_placeholder_{slot.start.isoformat()}")
        except HttpError:
            return f"event_placeholder_{slot.start.isoformat()}"

    async def update_event(
        self,
        event_id: str,
        slot: TimeSlot,
        summary: str | None = None,
        description: str | None = None,
        calendar_id: str | None = None,
        business_id: str | None = None,
    ) -> bool:
        client = (
            None
            if self._settings.use_stub
            else self._build_user_client(business_id) or self._client
        )
        if not client:
            return False

        cal_id = self._resolve_calendar_id(business_id, calendar_id)
        body: dict = {
            "start": {"dateTime": slot.start.isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": slot.end.isoformat(), "timeZone": "UTC"},
        }
        if summary is not None:
            body["summary"] = summary
        if description is not None:
            body["description"] = description

        try:
            (
                client.events()
                .patch(calendarId=cal_id, eventId=event_id, body=body)
                .execute()
            )
            return True
        except HttpError:
            return False

    async def handle_inbound_update(
        self,
        *,
        business_id: str | None,
        event_id: str,
        status: str | None = None,
        start: str | None = None,
        end: str | None = None,
        summary: str | None = None,
        description: str | None = None,
    ) -> bool:
        """Handle an inbound calendar update/cancellation notification.

        This is a best-effort sync path that updates the stored appointment
        matching the given calendar_event_id; when no appointment is found we
        return False but do not error.
        """
        from ..repositories import appointments_repo  # local import

        finder = getattr(appointments_repo, "find_by_calendar_event", None)
        appt = finder(event_id, business_id=business_id) if callable(finder) else None
        if not appt:
            return False

        start_dt = _parse_datetime_utc(start)
        end_dt = _parse_datetime_utc(end)
        if start_dt and end_dt and start_dt >= end_dt:
            start_dt = None
            end_dt = None

        updates = {}
        current_start = getattr(appt, "start_time", None)
        current_end = getattr(appt, "end_time", None)
        if current_start is not None and getattr(current_start, "tzinfo", None) is None:
            current_start = current_start.replace(tzinfo=UTC)
        if current_end is not None and getattr(current_end, "tzinfo", None) is None:
            current_end = current_end.replace(tzinfo=UTC)
        if start_dt:
            updates["start_time"] = start_dt
        if end_dt:
            updates["end_time"] = end_dt
        if description is not None:
            updates["description"] = description
        if summary is not None:
            updates["service_type"] = summary

        cancelled = status and status.lower() in {"cancelled", "canceled", "declined"}
        if cancelled:
            updates["status"] = "CANCELLED"
            updates["job_stage"] = "Cancelled"
        else:
            times_changed = False
            if start_dt and current_start and start_dt != current_start.astimezone(UTC):
                times_changed = True
            if end_dt and current_end and end_dt != current_end.astimezone(UTC):
                times_changed = True
            if times_changed:
                updates.setdefault("status", "SCHEDULED")
                updates.setdefault("job_stage", "Rescheduled")

        if updates:
            appointments_repo.update(appt.id, **updates)
        return True

    def has_conflict(
        self,
        *,
        business_id: str,
        start: datetime,
        end: datetime,
        technician_id: str | None = None,
        address: str | None = None,
        is_emergency: bool = False,
    ) -> bool:
        """Return True when the proposed slot conflicts with business rules."""

        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        if end.tzinfo is None:
            end = end.replace(tzinfo=UTC)

        if start >= end:
            return True

        open_hour, close_hour, closed_days = _get_business_hours(business_id)
        if start.weekday() in closed_days:
            return True

        day_open = start.replace(hour=open_hour, minute=0, second=0, microsecond=0)
        day_close = start.replace(hour=close_hour, minute=0, second=0, microsecond=0)
        if start < day_open or end > day_close:
            return True

        (
            max_jobs_per_day,
            reserve_mornings_for_emergencies,
            travel_buffer_minutes,
            _,
        ) = _get_business_capacity(business_id)

        if reserve_mornings_for_emergencies and not is_emergency:
            morning_end = start.replace(
                hour=max(open_hour, 12), minute=0, second=0, microsecond=0
            )
            if start < morning_end:
                return True

        from ..repositories import appointments_repo  # local import to avoid cycles

        appointments = appointments_repo.list_for_business(business_id)
        if technician_id is not None:
            appointments = [
                a
                for a in appointments
                if getattr(a, "technician_id", None) == technician_id
            ]
        day_appts = []
        for appt in appointments:
            appt_start = getattr(appt, "start_time", None)
            appt_end = getattr(appt, "end_time", None)
            if not appt_start or not appt_end:
                continue
            if appt_start.date() != start.date():
                continue
            status = getattr(appt, "status", "SCHEDULED").upper()
            if status not in {"SCHEDULED", "CONFIRMED"}:
                continue
            day_appts.append(appt)

        if max_jobs_per_day is not None and len(day_appts) >= max_jobs_per_day:
            return True

        busy_ranges = _build_busy_ranges(day_appts, travel_buffer_minutes, address)
        for busy_start, busy_end in busy_ranges:
            if start < busy_end and end > busy_start:
                return True

        return False

    async def delete_event(
        self,
        event_id: str,
        calendar_id: str | None = None,
        business_id: str | None = None,
    ) -> bool:
        client = (
            None
            if self._settings.use_stub
            else self._build_user_client(business_id) or self._client
        )
        if not client:
            return False

        cal_id = self._resolve_calendar_id(business_id, calendar_id)
        try:
            client.events().delete(calendarId=cal_id, eventId=event_id).execute()
            return True
        except HttpError:
            return False


calendar_service = CalendarService()
