#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import format_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin
from urllib.request import Request, urlopen

BASE_URL = "https://www.wwoz.org"
DEFAULT_URL = f"{BASE_URL}/calendar/livewire-music"
UA = "Mozilla/5.0 (OpenClaw RSS Builder)"


@dataclass
class EventItem:
    venue: str
    venue_url: str
    title: str
    event_url: str
    day_text: str
    time_text: str
    dt: datetime | None


class LivewireParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_calendar = False
        self.calendar_depth = 0
        self.in_heading_date = False
        self.heading_date_parts: list[str] = []

        self.in_panel_heading = False
        self.in_panel_title = False
        self.capture_venue = False
        self.current_venue = ""
        self.current_venue_href = ""

        self.in_calendar_info = False
        self.calendar_info_depth = 0
        self.capture_title = False
        self.current_title = ""
        self.current_event_href = ""
        self.capture_time_block = False
        self.time_parts: list[str] = []

        self.events: list[EventItem] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs = dict(attrs)
        class_attr = attrs.get("class", "")
        classes = set(class_attr.split())

        if tag == "div" and "livewire-calendar" in classes and not self.in_calendar:
            self.in_calendar = True
            self.calendar_depth = 1
            return
        if self.in_calendar and tag == "div":
            self.calendar_depth += 1

        if not self.in_calendar:
            return

        if tag == "span" and "heading-date" in classes:
            self.in_heading_date = True
            self.heading_date_parts = []

        if tag == "div" and "panel-heading" in classes:
            self.in_panel_heading = True
        elif self.in_panel_heading and tag == "h3" and "panel-title" in classes:
            self.in_panel_title = True
        elif self.in_panel_title and tag == "a":
            self.capture_venue = True
            self.current_venue_href = urljoin(BASE_URL, attrs.get("href", ""))
            self.current_venue = ""

        if tag == "div" and "calendar-info" in classes:
            self.in_calendar_info = True
            self.calendar_info_depth = 1
            self.current_title = ""
            self.current_event_href = ""
            self.time_parts = []
        elif self.in_calendar_info and tag == "div":
            self.calendar_info_depth += 1

        if self.in_calendar_info and tag == "p" and "truncate" in classes:
            self.capture_title = True
        elif self.capture_title and tag == "a":
            self.current_event_href = urljoin(BASE_URL, attrs.get("href", ""))

        if self.in_calendar_info and tag == "p" and "truncate" not in classes:
            self.capture_time_block = True

    def handle_endtag(self, tag: str) -> None:
        if self.in_heading_date and tag == "span":
            self.in_heading_date = False

        if self.capture_venue and tag == "a":
            self.capture_venue = False
        elif self.in_panel_title and tag == "h3":
            self.in_panel_title = False
        elif self.in_panel_heading and tag == "div":
            self.in_panel_heading = False

        if self.capture_title and tag == "p":
            self.capture_title = False
        if self.capture_time_block and tag == "p":
            self.capture_time_block = False

        if self.in_calendar_info and tag == "div":
            self.calendar_info_depth -= 1
            if self.calendar_info_depth == 0:
                self.in_calendar_info = False
                day_text, time_text, dt = parse_time_block(" ".join(self.time_parts))
                if self.current_title and self.current_event_href and self.current_venue:
                    self.events.append(
                        EventItem(
                            venue=clean(self.current_venue),
                            venue_url=self.current_venue_href,
                            title=clean(self.current_title),
                            event_url=self.current_event_href,
                            day_text=day_text,
                            time_text=time_text,
                            dt=dt,
                        )
                    )

        if self.in_calendar and tag == "div":
            self.calendar_depth -= 1
            if self.calendar_depth == 0:
                self.in_calendar = False

    def handle_data(self, data: str) -> None:
        if not self.in_calendar:
            return
        if self.in_heading_date:
            self.heading_date_parts.append(data)
        if self.capture_venue:
            self.current_venue += data
        if self.capture_title:
            self.current_title += data
        if self.capture_time_block:
            self.time_parts.append(data)


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_time_block(text: str):
    cleaned = clean(text)
    m = re.search(r"([A-Za-z]+,\s+[A-Za-z]+\s+\d{1,2})\s+at\s+([0-9:]+(?:am|pm))", cleaned, re.I)
    if not m:
        return "", cleaned, None
    day_text = m.group(1)
    time_text = m.group(2).lower()
    dt = None
    try:
        dt = datetime.strptime(f"{day_text}, 2026 {time_text}", "%A, %B %d, %Y %I:%M%p").replace(tzinfo=UTC)
    except ValueError:
        try:
            dt = datetime.strptime(f"{day_text}, 2026 {time_text}", "%A, %B %d, %Y %I%p").replace(tzinfo=UTC)
        except ValueError:
            dt = None
    return day_text, time_text, dt


def fetch(url: str) -> str:
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "ignore")


def rss_escape(text: str) -> str:
    return html.escape(text, quote=False)


def item_guid(event: EventItem) -> str:
    return hashlib.sha1(f"{event.event_url}|{event.venue}|{event.time_text}".encode()).hexdigest()


def build_rss(source_url: str, items: list[EventItem]) -> str:
    now = format_datetime(datetime.now(UTC))
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        '<channel>',
        '<title>WWOZ Livewire Music Calendar</title>',
        f'<link>{rss_escape(source_url)}</link>',
        '<description>Unofficial RSS feed generated from the WWOZ Livewire Music Calendar page.</description>',
        '<language>en-us</language>',
        f'<lastBuildDate>{now}</lastBuildDate>',
        '<generator>OpenClaw / wwoz_livewire_to_rss.py</generator>',
    ]
    for event in items:
        desc = f"Venue: {event.venue} | Date: {event.day_text} | Time: {event.time_text}"
        parts.extend([
            '<item>',
            f'<title>{rss_escape(event.title)} — {rss_escape(event.venue)}</title>',
            f'<link>{rss_escape(event.event_url)}</link>',
            f'<guid isPermaLink="false">{item_guid(event)}</guid>',
            f'<description>{rss_escape(desc)}</description>',
        ])
        if event.dt is not None:
            parts.append(f'<pubDate>{format_datetime(event.dt)}</pubDate>')
        parts.append('</item>')
    parts.extend(['</channel>', '</rss>'])
    return "\n".join(parts) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Convert WWOZ Livewire Music Calendar page to RSS")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--out", default="tmp/wwoz-livewire.xml")
    args = ap.parse_args()

    html_text = fetch(args.url)
    parser = LivewireParser()
    parser.feed(html_text)
    if not parser.events:
        raise SystemExit("No events found; page structure may have changed.")
    rss = build_rss(args.url, parser.events)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(rss)
    print(f"Wrote {len(parser.events)} items to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
