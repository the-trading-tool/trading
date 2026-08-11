"""Turning scraped cell text into values that are safe to store.

Everything here is pure: no browser, no network, no clock beyond `now`.
"""

import datetime as dt
import math
import re

_NUMBER_RE = re.compile(r"[-+]?\d[\d.,]*")
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?")


def parse_number(raw, locale='de'):
    """Parse a localised price string into a float.

    Handles German ("24.004,02"), plain ("1.1383") and US ("24,004.02") formats
    and strips unit suffixes such as " PKT" / " EUR" / " USD".
    Returns None when the text holds no usable number.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if math.isfinite(float(raw)) else None

    # Drop every kind of spacing first — the site uses non-breaking and thin
    # spaces both as padding and as a thousands separator.
    text = re.sub(r"[\s   ']", "", str(raw)).replace('−', '-')
    match = _NUMBER_RE.search(text)
    if not match:
        return None

    token = match.group(0).rstrip('.,')
    has_dot, has_comma = '.' in token, ',' in token

    if has_dot and has_comma:
        # The separator that appears last is the decimal one.
        if token.rfind(',') > token.rfind('.'):
            token = token.replace('.', '').replace(',', '.')
        else:
            token = token.replace(',', '')
    elif has_comma:
        # Several commas can only be thousands separators.
        token = token.replace(',', '') if token.count(',') > 1 else token.replace(',', '.')
    elif has_dot:
        tail = token.rsplit('.', 1)[1]
        # "1.234" is ambiguous — on a German page it is a thousands separator.
        if token.count('.') > 1 or (len(tail) == 3 and locale == 'de'):
            token = token.replace('.', '')

    try:
        value = float(token)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def parse_time(raw):
    """Extract a HH:MM:SS clock time from a cell such as "26.05.25 21:57:52".

    Returns None when no plausible time is present.
    """
    if raw is None:
        return None
    match = _TIME_RE.search(str(raw))
    if not match:
        return None
    hour, minute, second = int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)
    if hour > 23 or minute > 59 or second > 59:
        return None
    return f"{hour:02d}:{minute:02d}:{second:02d}"


def to_timestamp(time_str, source_tz='', now=None):
    """Turn a scraped clock time into a full local timestamp string.

    The page prints a wall-clock time without a date and without a zone. When
    the collector's machine does not run in the source's zone (e.g. a Pi on
    Canary time reading MESZ quotes), every quote looks like it lies in the
    future — and a receiver that resolves "future" to "yesterday" then files a
    whole day of ticks one day early.

    `source_tz` (an IANA name such as "Europe/Berlin") makes the offset
    explicit. Without it the time is taken as already local. The date is chosen
    as the occurrence closest to `now`, so a quote just after midnight still
    lands on the right day.

    Returns "YYYY-MM-DD HH:MM:SS", or None when `time_str` holds no time.
    """
    clock = parse_time(time_str)
    if clock is None:
        return None
    now = now or dt.datetime.now()
    parsed = dt.datetime.strptime(clock, "%H:%M:%S").time()

    if source_tz:
        shifted = _to_local(parsed, source_tz, now)
        if shifted is not None:
            return shifted.strftime("%Y-%m-%d %H:%M:%S")

    # No zone configured: pick the candidate date closest to now.
    candidates = [dt.datetime.combine(now.date() + dt.timedelta(days=offset), parsed)
                  for offset in (-1, 0, 1)]
    best = min(candidates, key=lambda stamp: abs((stamp - now).total_seconds()))
    return best.strftime("%Y-%m-%d %H:%M:%S")


def _to_local(parsed_time, source_tz, now):
    """Interpret `parsed_time` as wall clock in `source_tz` and convert to local."""
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(source_tz)
    except Exception:
        return None

    local_zone = now.astimezone().tzinfo
    best = None
    for offset in (-1, 0, 1):
        # The source's calendar day, not ours — they can differ around midnight.
        day = (now.astimezone(zone) + dt.timedelta(days=offset)).date()
        aware = dt.datetime.combine(day, parsed_time, tzinfo=zone)
        local = aware.astimezone(local_zone).replace(tzinfo=None)
        if best is None or abs((local - now).total_seconds()) < abs((best - now).total_seconds()):
            best = local
    return best


def quote_age_minutes(time_str, now=None):
    """Return how many minutes ago a quote was published.

    Accepts a full "YYYY-MM-DD HH:MM:SS" (what the collector sends) as well as a
    bare "HH:MM:SS"; for the latter the day is assumed to be today, wrapping
    over midnight.
    """
    now = now or dt.datetime.now()
    try:
        stamp = dt.datetime.strptime(str(time_str), "%Y-%m-%d %H:%M:%S")
        return (now - stamp).total_seconds() / 60.0
    except (TypeError, ValueError):
        pass
    try:
        stamp = dt.datetime.combine(now.date(), dt.datetime.strptime(time_str, "%H:%M:%S").time())
    except (TypeError, ValueError):
        return None
    delta = (now - stamp).total_seconds() / 60.0
    if delta < -5:                      # quote "in the future" → it belongs to yesterday
        delta += 24 * 60
    return delta
