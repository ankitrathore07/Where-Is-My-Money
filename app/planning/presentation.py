"""Formatting helpers for server-rendered planning pages."""

from datetime import date

from app.dashboard.presentation import format_money


def format_month(value: date) -> str:
    return value.strftime("%B %Y")


def format_date(value: date) -> str:
    return f"{value.strftime('%B')} {value.day}, {value.year}"


def format_period(start: date, end: date) -> str:
    if start.year == end.year:
        return f"{start.strftime('%B')} {start.day}–{end.strftime('%B')} {end.day}, {end.year}"
    return f"{format_date(start)}–{format_date(end)}"


def format_money_input(cents: int) -> str:
    dollars, remainder = divmod(cents, 100)
    return f"{dollars}.{remainder:02d}"


__all__ = [
    "format_date",
    "format_money",
    "format_money_input",
    "format_month",
    "format_period",
]
