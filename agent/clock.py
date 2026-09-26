"""UK and New York time without a tz database (Windows Python ships none).

UK:  BST from last Sunday of March 01:00 UTC to last Sunday of October 01:00 UTC.
NY:  EDT from second Sunday of March 02:00 local to first Sunday of November 02:00 local.
The NY open lands at 14:30 UK most of the year and 13:30 UK in the weeks the
two countries' clock changes don't line up.
"""
import datetime as dt


def _last_sunday(y, m):
    d = (dt.date(y, m + 1, 1) if m < 12 else dt.date(y + 1, 1, 1)) - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() + 1) % 7)


def _nth_sunday(y, m, n):
    d = dt.date(y, m, 1)
    d += dt.timedelta(days=(6 - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def utcnow():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def uk_offset(utc):
    start = dt.datetime.combine(_last_sunday(utc.year, 3), dt.time(1))
    end = dt.datetime.combine(_last_sunday(utc.year, 10), dt.time(1))
    return 1 if start <= utc < end else 0


def ny_offset(utc):
    start = dt.datetime.combine(_nth_sunday(utc.year, 3, 2), dt.time(7))
    end = dt.datetime.combine(_nth_sunday(utc.year, 11, 1), dt.time(6))
    return -4 if start <= utc < end else -5


def uk_now():
    u = utcnow()
    return u + dt.timedelta(hours=uk_offset(u))


def uk_today():
    return uk_now().date()


def uk_iso_offset(uk_local):
    """'+01:00' or '+00:00' for a UK wall-clock time."""
    return "+01:00" if uk_offset(uk_local - dt.timedelta(hours=1)) else "+00:00"


def ny_open_uk(day):
    """UK wall-clock time of the 09:30 New York cash open on `day`."""
    approx_utc = dt.datetime.combine(day, dt.time(13, 30))
    utc = dt.datetime.combine(day, dt.time(9, 30)) - dt.timedelta(hours=ny_offset(approx_utc))
    return utc + dt.timedelta(hours=uk_offset(utc))


def stamp():
    n = uk_now()
    return n.strftime("%a %d %b %Y, %H:%M") + (" BST" if uk_offset(utcnow()) else " GMT")


def part_of_day():
    h = uk_now().hour
    return "morning" if h < 12 else "afternoon" if h < 18 else "evening"


def fmt_day(d):
    today = uk_today()
    if d == today:
        return "today"
    if d == today + dt.timedelta(days=1):
        return "tomorrow"
    return d.strftime("%a %d %b")
