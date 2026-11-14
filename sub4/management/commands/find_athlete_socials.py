import csv
import datetime
import json
import os
import re
import time as time_module
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand
from django.db.models import Count, Min, OuterRef, Subquery

from sub4.management.commands.scrape_world_athletics import (
    extract_wa_id,
    find_matching_athlete,
    normalize_text,
    parse_dob,
)
from sub4.models import Athlete, Performance
from sub4.templatetags.time_filters import format_mile_time

IG_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?instagram\.com/([A-Za-z0-9_.]{2,30})/?",
    re.IGNORECASE,
)
STRAVA_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?strava\.com/athletes/(\d+)/?",
    re.IGNORECASE,
)
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    re.DOTALL,
)

RESERVED_IG_HANDLES = {
    "p", "reel", "reels", "stories", "explore", "accounts", "about",
    "developer", "legal", "privacy", "terms", "help", "worldathletics",
    "milesplit", "flotrack", "citiusmag", "runnersworld", "strava",
    "ncaatrackfield", "ustfccca", "athleticsweekly", "britishathletics",
    "usatf", "athleticscanda", "athleticsaustralia", "share", "tv", "tags",
    " diamondleagueathletics", "diamondleagueathletics", "olympics",
}


def clean_ig_handle(raw):
    if not raw:
        return None
    val = str(raw).strip().lstrip("@")
    m = IG_URL_RE.search(val)
    if m:
        val = m.group(1)
    val = val.strip("/").split("?")[0].split("/")[0]
    if not val or val.isdigit() or val.lower() in RESERVED_IG_HANDLES:
        return None
    if re.match(r"^[A-Za-z0-9_.]{2,30}$", val):
        return val
    return None


def clean_strava_id(raw):
    if not raw:
        return None
    val = str(raw).strip()
    m = STRAVA_URL_RE.search(val)
    if m:
        return m.group(1)
    if val.isdigit():
        return val
    return None


def check_world_athletics_profile(session, wa_id):
    """
    Check World Athletics athlete profile JSON (__NEXT_DATA__) for social links and legacy iaafId.
    Returns (ig_handle, iaaf_id).
    """
    if not wa_id:
        return None, None
    url = f"https://worldathletics.org/athletes/_/{wa_id}"
    try:
        r = session.get(url, timeout=12)
        if r.status_code != 200:
            return None, None
        m = NEXT_DATA_RE.search(r.text)
        if not m:
            return None, None
        data = json.loads(m.group(1))
        basic = (
            data.get("props", {})
            .get("pageProps", {})
            .get("competitor", {})
            .get("basicData", {})
        ) or {}
        ig = clean_ig_handle(
            basic.get("instagramUsername") or basic.get("instagramLink")
        )
        iaaf_id = str(basic.get("iaafId")) if basic.get("iaafId") else None
        return ig, iaaf_id
    except Exception:
        return None, None


def check_wikidata_sparql(session, wa_id=None, athlete_name=None):
    """
    Query Wikidata SPARQL for Instagram (P2003), Strava (P5999), and WA ID (P1146).
    """
    headers = {
        "User-Agent": "Sub4MilersBot/1.0 (https://sub4milers.com; sub4milers@gmail.com)",
        "Accept": "application/sparql-results+json",
    }
    if wa_id:
        query = f"""
        SELECT ?ig ?strava WHERE {{
          ?item wdt:P1146 "{wa_id}" .
          OPTIONAL {{ ?item wdt:P2003 ?ig . }}
          OPTIONAL {{ ?item wdt:P5999 ?strava . }}
        }} LIMIT 5
        """
        try:
            r = session.get(
                "https://query.wikidata.org/sparql",
                params={"query": query, "format": "json"},
                headers=headers,
                timeout=12,
            )
            if r.status_code == 200:
                bindings = r.json().get("results", {}).get("bindings", [])
                for b in bindings:
                    ig = clean_ig_handle(b.get("ig", {}).get("value"))
                    st = clean_strava_id(b.get("strava", {}).get("value"))
                    if ig or st:
                        return ig, st, wa_id
        except Exception:
            pass

    if athlete_name:
        try:
            r = session.get(
                "https://www.wikidata.org/w/api.php",
                params={
                    "action": "wbsearchentities",
                    "search": athlete_name.title(),
                    "language": "en",
                    "format": "json",
                    "limit": 3,
                },
                headers=headers,
                timeout=10,
            )
            if r.status_code == 200:
                results = r.json().get("search", [])
                for item in results:
                    qid = item.get("id")
                    desc = (item.get("description") or "").lower()
                    if not qid:
                        continue
                    if desc and not any(
                        w in desc
                        for w in [
                            "runner",
                            "athlete",
                            "track",
                            "middle-distance",
                            "steeplechase",
                            "olympi",
                        ]
                    ):
                        continue
                    query = f"""
                    SELECT ?ig ?strava ?waId WHERE {{
                      OPTIONAL {{ wd:{qid} wdt:P2003 ?ig . }}
                      OPTIONAL {{ wd:{qid} wdt:P5999 ?strava . }}
                      OPTIONAL {{ wd:{qid} wdt:P1146 ?waId . }}
                    }} LIMIT 1
                    """
                    sr = session.get(
                        "https://query.wikidata.org/sparql",
                        params={"query": query, "format": "json"},
                        headers=headers,
                        timeout=10,
                    )
                    if sr.status_code == 200:
                        bindings = sr.json().get("results", {}).get("bindings", [])
                        for b in bindings:
                            ig = clean_ig_handle(b.get("ig", {}).get("value"))
                            st = clean_strava_id(b.get("strava", {}).get("value"))
                            found_wa = b.get("waId", {}).get("value")
                            if ig or st or found_wa:
                                return ig, st, found_wa
        except Exception:
            pass

    return None, None, None


def check_wikipedia_extlinks(session, athlete_name):
    """Check English Wikipedia external links for an athlete's Instagram or Strava."""
    headers = {
        "User-Agent": "Sub4MilersBot/1.0 (https://sub4milers.com; sub4milers@gmail.com)"
    }
    try:
        r = session.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "titles": athlete_name.title(),
                "prop": "extlinks|categories",
                "ellimit": "50",
                "format": "json",
                "redirects": 1,
            },
            headers=headers,
            timeout=10,
        )
        if r.status_code != 200:
            return None, None
        pages = r.json().get("query", {}).get("pages", {})
        for page_id, page in pages.items():
            if page_id == "-1":
                continue
            cats = " ".join(
                c.get("title", "").lower() for c in page.get("categories", [])
            )
            if cats and not any(
                w in cats for w in ["runner", "athlete", "births", "track", "mile"]
            ):
                continue
            ig = None
            st = None
            for link in page.get("extlinks", []):
                url = link.get("*", "") or link.get("url", "")
                if not ig and "instagram.com/" in url:
                    ig = clean_ig_handle(url)
                if not st and "strava.com/athletes/" in url:
                    st = clean_strava_id(url)
            if ig or st:
                return ig, st
    except Exception:
        pass
    return None, None


def search_web_links(session, query):
    """
    Search web using optional API keys if present:
    - SERPER_API_KEY (https://serper.dev — 2,500 free Google queries)
    - BRAVE_API_KEY (https://api.search.brave.com)
    - GOOGLE_SEARCH_API_KEY + GOOGLE_SEARCH_CX
    """
    serper_key = os.environ.get("SERPER_API_KEY", "").strip()
    if serper_key:
        try:
            r = session.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": serper_key, "Content-Type": "application/json"},
                json={"q": query, "num": 5},
                timeout=12,
            )
            if r.status_code == 200:
                return [
                    {
                        "title": item.get("title", ""),
                        "link": item.get("link", ""),
                        "snippet": item.get("snippet", ""),
                    }
                    for item in r.json().get("organic", [])
                ]
        except Exception:
            pass

    brave_key = os.environ.get("BRAVE_API_KEY", "").strip()
    if brave_key:
        try:
            r = session.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={
                    "X-Subscription-Token": brave_key,
                    "Accept": "application/json",
                },
                params={"q": query, "count": 5},
                timeout=12,
            )
            if r.status_code == 200:
                return [
                    {
                        "title": item.get("title", ""),
                        "link": item.get("url", ""),
                        "snippet": item.get("description", ""),
                    }
                    for item in r.json().get("web", {}).get("results", [])
                ]
        except Exception:
            pass

    g_key = os.environ.get("GOOGLE_SEARCH_API_KEY", "").strip()
    g_cx = os.environ.get("GOOGLE_SEARCH_CX", "").strip()
    if g_key and g_cx:
        try:
            r = session.get(
                "https://www.googleapis.com/customsearch/v1",
                params={"key": g_key, "cx": g_cx, "q": query, "num": 5},
                timeout=12,
            )
            if r.status_code == 200:
                return [
                    {
                        "title": item.get("title", ""),
                        "link": item.get("link", ""),
                        "snippet": item.get("snippet", ""),
                    }
                    for item in r.json().get("items", [])
                ]
        except Exception:
            pass

    return []


def score_social_result(athlete_name, handle_or_id, title, snippet):
    """Score how likely a search result belongs to the target athlete."""
    tokens = [t for t in normalize_text(athlete_name).split() if len(t) > 1]
    if not tokens:
        return 0.0
    haystack = normalize_text(f"{handle_or_id} {title} {snippet}")
    handle_clean = re.sub(r"[^a-z0-9]", "", str(handle_or_id).lower())

    score = 0.0
    for tok in tokens:
        if tok in haystack:
            score += 0.35
        if tok in handle_clean:
            score += 0.25

    if any(
        w in haystack for w in ["track", "mile", "runner", "running", "athletics", "xc"]
    ):
        score += 0.20

    return min(score, 1.0)


def discover_socials_for_athlete(session, athlete):
    """Run single-athlete multi-tier discovery."""
    wa_id = athlete.world_athletics_id
    ig = clean_ig_handle(athlete.instagram_handle)
    st = clean_strava_id(athlete.strava_id)
    sources = []

    # Tier 1: World Athletics profile
    if wa_id and not ig:
        wa_ig, _ = check_world_athletics_profile(session, wa_id)
        if wa_ig:
            ig = wa_ig
            sources.append("World Athletics Profile")

    # Tier 2: Wikidata SPARQL / API
    if not ig or not st:
        wd_ig, wd_st, wd_wa = check_wikidata_sparql(
            session, wa_id=wa_id, athlete_name=athlete.name
        )
        if wd_ig and not ig:
            ig = wd_ig
            sources.append("Wikidata")
        if wd_st and not st:
            st = wd_st
            sources.append("Wikidata")
        if wd_wa and not wa_id:
            wa_id = wd_wa

    # Tier 3: Wikipedia external links
    if not ig or not st:
        wp_ig, wp_st = check_wikipedia_extlinks(session, athlete.name)
        if wp_ig and not ig:
            ig = wp_ig
            sources.append("Wikipedia")
        if wp_st and not st:
            st = wp_st
            sources.append("Wikipedia")

    # Tier 4: Search API (Serper / Brave / Google CSE) if configured
    country_hint = (athlete.countries or "").split(",")[0].strip()
    if not ig:
        ig_results = search_web_links(
            session,
            f'"{athlete.name.title()}" ({country_hint} OR track OR runner OR mile) site:instagram.com',
        )
        best_ig = None
        best_score = 0.0
        for res in ig_results:
            cand = clean_ig_handle(res.get("link"))
            if not cand:
                continue
            sc = score_social_result(
                athlete.name, cand, res.get("title", ""), res.get("snippet", "")
            )
            if sc > best_score and sc >= 0.45:
                best_score = sc
                best_ig = cand
        if best_ig:
            ig = best_ig
            sources.append("Web Search")

    if not st:
        st_results = search_web_links(
            session,
            f'"{athlete.name.title()}" ({country_hint} OR runner OR track) site:strava.com/athletes',
        )
        best_st = None
        best_score = 0.0
        for res in st_results:
            cand = clean_strava_id(res.get("link"))
            if not cand:
                continue
            sc = score_social_result(
                athlete.name, cand, res.get("title", ""), res.get("snippet", "")
            )
            if sc > best_score and sc >= 0.35:
                best_score = sc
                best_st = cand
        if best_st:
            st = best_st
            sources.append("Web Search")

    return {
        "instagram": ig,
        "strava_id": st,
        "wa_id": wa_id,
        "source": ", ".join(dict.fromkeys(sources)) if sources else "Search Links",
    }


def compute_chronological_ranks():
    """Return a dict mapping athlete.id -> 1-based chronological Sub-4 Club member number."""
    ordered_ids = (
        Athlete.objects.annotate(
            first_sub4_date=Subquery(
                Performance.objects.filter(athlete=OuterRef("pk"), is_first_sub4=True)
                .values("date")[:1]
            ),
            first_sub4_time=Subquery(
                Performance.objects.filter(athlete=OuterRef("pk"), is_first_sub4=True)
                .values("time")[:1]
            ),
        )
        .order_by("first_sub4_date", "first_sub4_time", "id")
        .values_list("id", flat=True)
    )
    return {ath_id: idx for idx, ath_id in enumerate(ordered_ids, start=1)}


class Command(BaseCommand):
    help = (
        "Discover Instagram and Strava accounts for sub-4 milers "
        "(either recent inductees or the full all-time database) and export Markdown/CSV."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--from-file",
            type=str,
            default="",
            help="Path to JSON file containing a list of newly created Athlete IDs",
        )
        parser.add_argument(
            "--recent",
            type=int,
            default=0,
            help="Find socials for the N most recent first-time sub-4 inductees",
        )
        parser.add_argument(
            "--athlete",
            type=str,
            default="",
            help="Find socials for athletes matching this name substring",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Run fast bulk discovery across ALL sub-4 milers in the database",
        )
        parser.add_argument(
            "--markdown-output",
            type=str,
            default="",
            help="Optional path to write Markdown digest (e.g. for GitHub Issue creation)",
        )
        parser.add_argument(
            "--csv-output",
            type=str,
            default="",
            help="Optional path to write CSV of discovered Instagram/Strava handles",
        )
        parser.add_argument(
            "--full-csv-output",
            type=str,
            default="",
            help="Optional path to write full CSV of all athletes (matched + search links)",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-run discovery even if instagram_handle and strava_id are already set",
        )
        parser.add_argument(
            "--search-since",
            type=int,
            default=0,
            help="Run Tier 4 web search (Serper/Brave/Google) for athletes without IG who debuted in or after this year (e.g. 2000)",
        )
        parser.add_argument(
            "--search-limit",
            type=int,
            default=0,
            help="Optional max number of athletes to query during --search-since (0 = all)",
        )

    def handle(self, *args, **options):
        if options["all"]:
            self.handle_bulk_all(options)
            return
        if options["search_since"]:
            self.handle_search_since(options)
            return

        from_file = options["from_file"]
        recent_n = options["recent"]
        athlete_query = options["athlete"]
        md_out = options["markdown_output"]
        csv_out = options["csv_output"]
        force = options["force"]

        target_athletes = []

        if from_file:
            path = Path(from_file)
            if path.exists():
                ids = json.loads(path.read_text(encoding="utf-8"))
                if ids:
                    target_athletes = list(
                        Athlete.objects.filter(pk__in=ids).prefetch_related(
                            "performances"
                        )
                    )
        elif athlete_query:
            target_athletes = list(
                Athlete.objects.filter(name__icontains=athlete_query).prefetch_related(
                    "performances"
                )
            )
        elif recent_n > 0:
            recent_perfs = (
                Performance.objects.filter(is_first_sub4=True, date__isnull=False)
                .select_related("athlete")
                .order_by("-date", "time")[:recent_n]
            )
            target_athletes = [p.athlete for p in recent_perfs]
        else:
            recent_perfs = (
                Performance.objects.filter(is_first_sub4=True, date__isnull=False)
                .select_related("athlete")
                .order_by("-date", "time")[:10]
            )
            target_athletes = [p.athlete for p in recent_perfs]

        if not target_athletes:
            self.stdout.write("No target athletes to process.")
            return

        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
        })

        missing_wa = [a for a in target_athletes if not a.world_athletics_id]
        if missing_wa:
            self.backfill_wa_ids_current_year(session, missing_wa)

        chrono_ranks = compute_chronological_ranks()
        report_rows = []

        for athlete in target_athletes:
            if force:
                athlete.instagram_handle = None
                athlete.strava_id = None

            res = discover_socials_for_athlete(session, athlete)
            updated = False
            if res["instagram"] and res["instagram"] != athlete.instagram_handle:
                athlete.instagram_handle = res["instagram"]
                updated = True
            if res["strava_id"] and res["strava_id"] != athlete.strava_id:
                athlete.strava_id = res["strava_id"]
                updated = True
            if res["wa_id"] and res["wa_id"] != athlete.world_athletics_id:
                athlete.world_athletics_id = res["wa_id"]
                updated = True
            if updated and athlete.pk:
                athlete.save()

            first_perf = (
                athlete.performances.filter(is_first_sub4=True).first()
                or athlete.performances.order_by("date", "time").first()
            )
            best_perf = athlete.performances.order_by("time").first()
            perf_count = athlete.performances.count()

            mark_str = format_mile_time(first_perf.time) if first_perf else "—"
            date_str = (
                first_perf.date.strftime("%d %b %Y").upper()
                if (first_perf and first_perf.date)
                else "UNRECORDED"
            )
            venue_str = (
                (first_perf.location or "Unknown Venue") if first_perf else "—"
            )
            member_num = chrono_ranks.get(athlete.pk, "—")

            ig_handle = athlete.instagram_handle
            strava_id = athlete.strava_id

            self.stdout.write(
                f"  • #{member_num} {athlete.name.title()} ({athlete.countries or '—'}) — "
                f"{mark_str} ({date_str}) | "
                f"IG: {'@' + ig_handle if ig_handle else 'not found'} | "
                f"Strava: {strava_id or 'not found'}"
            )

            report_rows.append({
                "athlete": athlete,
                "member_num": member_num,
                "mark": mark_str,
                "date": date_str,
                "raw_date": first_perf.date if first_perf else None,
                "venue": venue_str,
                "pb": format_mile_time(best_perf.time) if best_perf else mark_str,
                "perf_count": perf_count,
                "instagram": ig_handle,
                "strava_id": strava_id,
                "wa_id": athlete.world_athletics_id,
                "source": res["source"],
            })
            time_module.sleep(0.3)

        if md_out:
            md_text = self.build_markdown_report(report_rows)
            out_path = Path(md_out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(md_text, encoding="utf-8")
            self.stdout.write(
                self.style.SUCCESS(f"Wrote Markdown collab digest to {out_path}")
            )

        if csv_out:
            self.write_csv_export(report_rows, Path(csv_out), only_matched=False)

    # ── BULK WEB SEARCH FOR POST-YEAR ATHLETES MISSING INSTAGRAM ────────────
    def handle_search_since(self, options):
        since_year = options["search_since"]
        limit = options["search_limit"]
        csv_out = options["csv_output"] or "static/data/sub4_instagram_handles.csv"
        full_csv_out = options["full_csv_output"] or "static/data/sub4_all_athletes_socials.csv"

        if not any(
            os.environ.get(k, "").strip()
            for k in ("SERPER_API_KEY", "BRAVE_API_KEY", "GOOGLE_SEARCH_API_KEY")
        ):
            self.stderr.write(
                self.style.ERROR(
                    "No search API key found in environment (SERPER_API_KEY, BRAVE_API_KEY, or GOOGLE_SEARCH_API_KEY)."
                )
            )
            return

        athletes = list(
            Athlete.objects.annotate(first_date=Min("performances__date"))
            .filter(first_date__year__gte=since_year)
            .prefetch_related("performances")
            .order_by("-first_date", "name")
        )
        missing = [a for a in athletes if not clean_ig_handle(a.instagram_handle)]
        if limit > 0:
            missing = missing[:limit]

        self.stdout.write(
            f"Found {len(athletes)} athletes since {since_year}; querying web search for {len(missing)} missing Instagram handles..."
        )

        def search_one(ath):
            s = requests.Session()
            country_hint = (ath.countries or "").split(",")[0].strip()
            q = f'"{ath.name.title()}" ({country_hint} OR track OR runner OR mile) site:instagram.com'
            results = search_web_links(s, q)
            best_ig = None
            best_score = 0.0
            for res in results:
                cand = clean_ig_handle(res.get("link"))
                if not cand:
                    continue
                sc = score_social_result(
                    ath.name, cand, res.get("title", ""), res.get("snippet", "")
                )
                if sc > best_score and sc >= 0.45:
                    best_score = sc
                    best_ig = cand
            return ath, best_ig

        matched_athletes = []
        source_by_id = {}
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(search_one, a) for a in missing]
            done = 0
            for fut in as_completed(futures):
                done += 1
                ath, ig = fut.result()
                if ig:
                    ath.instagram_handle = ig
                    matched_athletes.append(ath)
                    source_by_id[ath.pk] = "Web Search"
                if done % 100 == 0 or done == len(missing):
                    self.stdout.write(
                        f"  ...queried {done}/{len(missing)} ({len(matched_athletes)} matched)"
                    )

        if matched_athletes:
            Athlete.objects.bulk_update(
                matched_athletes, ["instagram_handle"], batch_size=500
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Saved {len(matched_athletes)} new Instagram handles to db.sqlite3."
                )
            )
        else:
            self.stdout.write("No new Instagram handles matched.")

        # Export updated CSVs across all athletes
        all_athletes = list(Athlete.objects.all().prefetch_related("performances"))
        chrono_ranks = compute_chronological_ranks()
        report_rows = []
        for a in all_athletes:
            perfs = list(a.performances.all())
            first_perf = next((p for p in perfs if p.is_first_sub4), None)
            if not first_perf and perfs:
                dated = [p for p in perfs if p.date]
                first_perf = (
                    min(dated, key=lambda p: (p.date, p.time))
                    if dated
                    else min(perfs, key=lambda p: p.time)
                )
            best_perf = min(perfs, key=lambda p: p.time) if perfs else None
            mark_str = format_mile_time(first_perf.time) if first_perf else "—"
            date_str = (
                first_perf.date.strftime("%Y-%m-%d")
                if (first_perf and first_perf.date)
                else ""
            )
            venue_str = (first_perf.location or "") if first_perf else ""
            pb_str = format_mile_time(best_perf.time) if best_perf else mark_str
            report_rows.append({
                "athlete": a,
                "member_num": chrono_ranks.get(a.pk, 99999),
                "mark": mark_str,
                "date": date_str,
                "raw_date": first_perf.date if first_perf else None,
                "venue": venue_str,
                "pb": pb_str,
                "perf_count": len(perfs),
                "instagram": a.instagram_handle,
                "strava_id": a.strava_id,
                "wa_id": a.world_athletics_id,
                "source": source_by_id.get(
                    a.pk,
                    "Verified" if (a.instagram_handle or a.strava_id) else "Search Link",
                ),
            })

        report_rows.sort(
            key=lambda r: (
                r["raw_date"] or datetime.date(1900, 1, 1),
                -(r["member_num"] if isinstance(r["member_num"], int) else 0),
            ),
            reverse=True,
        )
        matched_count = self.write_csv_export(report_rows, Path(csv_out), only_matched=True)
        total_count = self.write_csv_export(report_rows, Path(full_csv_out), only_matched=False)
        self.stdout.write(
            self.style.SUCCESS(
                f"Exported {matched_count} matched handles to {csv_out} and {total_count} total rows to {full_csv_out}."
            )
        )

    # ── FAST BULK DISCOVERY ACROSS ALL ~2,389 ATHLETES ─────────────────────
    def handle_bulk_all(self, options):
        t_start = time_module.time()
        force = options["force"]
        csv_out = options["csv_output"] or "sub4_instagram_handles.csv"
        full_csv_out = options["full_csv_output"] or "sub4_all_athletes_socials.csv"

        athletes = list(Athlete.objects.all().prefetch_related("performances"))
        self.stdout.write(
            f"Starting bulk social discovery for {len(athletes)} sub-4 milers..."
        )

        if force:
            for a in athletes:
                a.instagram_handle = None
                a.strava_id = None

        source_by_id = {}

        # ── STAGE 1: Backfill WA IDs from World Athletics All-Time Toplist ──
        existing_wa_count = sum(1 for a in athletes if a.world_athletics_id)
        athletes_to_update = set()
        if existing_wa_count < 2100 or force:
            self.stdout.write(
                "\n[Stage 1/4] Fetching World Athletics All-Time Men's One Mile Toplist (pages 1–23)..."
            )
            alltime_rows = self.fetch_alltime_wa_toplist()
            self.stdout.write(
                f"  Fetched {len(alltime_rows)} sub-4 performers from World Athletics."
            )

            by_norm_name = {}
            for a in athletes:
                by_norm_name.setdefault(normalize_text(a.name), []).append(a)

            wa_matched = 0
            for r in alltime_rows:
                wa_id = r["wa_id"]
                if not wa_id:
                    continue
                dob, birth_year = parse_dob(r["dob"])
                norm = normalize_text(r["name"])
                cands = by_norm_name.get(norm, [])
                matched_ath = None
                if len(cands) == 1:
                    cand = cands[0]
                    if not (
                        birth_year and cand.DOB and abs(birth_year - cand.DOB.year) > 2
                    ):
                        matched_ath = cand
                elif len(cands) > 1:
                    for cand in cands:
                        if (
                            birth_year
                            and cand.DOB
                            and abs(birth_year - cand.DOB.year) <= 1
                        ):
                            matched_ath = cand
                            break
                if not matched_ath:
                    matched_ath, _ = find_matching_athlete(
                        r["name"],
                        r["country"],
                        dob,
                        birth_year,
                        athletes,
                        wa_id=wa_id,
                        threshold=0.88,
                    )
                if matched_ath:
                    changed = False
                    if not matched_ath.world_athletics_id:
                        matched_ath.world_athletics_id = wa_id
                        changed = True
                        wa_matched += 1
                    if dob and not matched_ath.DOB:
                        matched_ath.DOB = dob
                        changed = True
                    if r["country"] and (
                        not matched_ath.countries
                        or matched_ath.countries.strip().lower() in ("nan", "none", "")
                    ):
                        matched_ath.countries = r["country"]
                        changed = True
                    if changed:
                        athletes_to_update.add(matched_ath)

            self.stdout.write(
                f"  Matched {wa_matched} World Athletics IDs to database athletes."
            )
        else:
            self.stdout.write(
                f"\n[Stage 1/4] {existing_wa_count} athletes already have World Athletics IDs; skipping toplist backfill."
            )

        # ── STAGE 2: Bulk Wikidata SPARQL Queries ───────────────────────────
        self.stdout.write(
            "\n[Stage 2/4] Running bulk Wikidata SPARQL queries for Instagram (P2003) & Strava (P5999)..."
        )
        wd_by_wa, wd_by_name = self.fetch_bulk_wikidata_socials()
        self.stdout.write(
            f"  Loaded {len(wd_by_wa)} Wikidata athlete ID records and {len(wd_by_name)} name records."
        )

        wd_ig_hits = 0
        wd_strava_hits = 0
        for a in athletes:
            rec = None
            if a.world_athletics_id and a.world_athletics_id in wd_by_wa:
                rec = wd_by_wa[a.world_athletics_id]
            else:
                norm = normalize_text(a.name)
                if norm in wd_by_name:
                    cand_recs = wd_by_name[norm]
                    for cr in cand_recs:
                        if cr["birth_year"] and a.DOB and abs(cr["birth_year"] - a.DOB.year) > 2:
                            continue
                        rec = cr
                        break
            if rec:
                changed = False
                if rec.get("ig") and not a.instagram_handle:
                    a.instagram_handle = rec["ig"]
                    source_by_id[a.pk] = "Wikidata"
                    wd_ig_hits += 1
                    changed = True
                if rec.get("strava") and not a.strava_id:
                    a.strava_id = rec["strava"]
                    source_by_id.setdefault(a.pk, "Wikidata")
                    wd_strava_hits += 1
                    changed = True
                if rec.get("wa_id") and not a.world_athletics_id:
                    a.world_athletics_id = rec["wa_id"]
                    changed = True
                if changed:
                    athletes_to_update.add(a)

        self.stdout.write(
            f"  Wikidata matched {wd_ig_hits} Instagram handles and {wd_strava_hits} Strava accounts."
        )

        # ── STAGE 3: Concurrent World Athletics Profile (__NEXT_DATA__) Check ──
        # Check athletes with a WA ID who still need an Instagram handle
        candidates_for_wa_profile = []
        for a in athletes:
            if not a.world_athletics_id or a.instagram_handle:
                continue
            # Prioritize athletes born >= 1965 or whose first sub-4 was >= 1990
            if a.DOB and a.DOB.year < 1960:
                continue
            candidates_for_wa_profile.append(a)

        self.stdout.write(
            f"\n[Stage 3/4] Checking {len(candidates_for_wa_profile)} World Athletics athlete profiles concurrently..."
        )
        wa_profile_ig_hits = 0
        wa_iaaf_wd_hits = 0

        def fetch_wa_for_athlete(ath):
            s = requests.Session()
            s.headers.update({"User-Agent": "Mozilla/5.0"})
            ig, iaaf_id = check_world_athletics_profile(s, ath.world_athletics_id)
            return ath, ig, iaaf_id

        with ThreadPoolExecutor(max_workers=24) as executor:
            futures = [
                executor.submit(fetch_wa_for_athlete, ath)
                for ath in candidates_for_wa_profile
            ]
            done_count = 0
            for fut in as_completed(futures):
                done_count += 1
                if done_count % 250 == 0:
                    self.stdout.write(
                        f"  ...checked {done_count}/{len(candidates_for_wa_profile)} WA profiles"
                    )
                ath, ig, iaaf_id = fut.result()
                changed = False
                if ig and not ath.instagram_handle:
                    ath.instagram_handle = ig
                    source_by_id[ath.pk] = "World Athletics Profile"
                    wa_profile_ig_hits += 1
                    changed = True
                # Also check if legacy 6-digit iaafId matches Wikidata P1146!
                if iaaf_id and iaaf_id in wd_by_wa:
                    rec = wd_by_wa[iaaf_id]
                    if rec.get("ig") and not ath.instagram_handle:
                        ath.instagram_handle = rec["ig"]
                        source_by_id[ath.pk] = "Wikidata (via IAAF ID)"
                        wa_iaaf_wd_hits += 1
                        changed = True
                    if rec.get("strava") and not ath.strava_id:
                        ath.strava_id = rec["strava"]
                        source_by_id.setdefault(ath.pk, "Wikidata (via IAAF ID)")
                        changed = True
                if changed:
                    athletes_to_update.add(ath)

        self.stdout.write(
            f"  World Athletics profiles matched {wa_profile_ig_hits} Instagram handles "
            f"(+{wa_iaaf_wd_hits} via legacy IAAF ID -> Wikidata)."
        )

        # ── STAGE 4: Batch English Wikipedia External Links Lookup ──────────
        missing_ig_athletes = [a for a in athletes if not a.instagram_handle]
        self.stdout.write(
            f"\n[Stage 4/4] Batch-checking English Wikipedia external links for {len(missing_ig_athletes)} remaining athletes..."
        )
        wp_hits = self.fetch_batch_wikipedia_socials(
            missing_ig_athletes, source_by_id, athletes_to_update
        )
        self.stdout.write(f"  Wikipedia external links matched {wp_hits} additional handles.")

        # Persist all updates to SQLite in one bulk_update
        if athletes_to_update:
            Athlete.objects.bulk_update(
                list(athletes_to_update),
                ["world_athletics_id", "instagram_handle", "strava_id", "DOB", "countries"],
                batch_size=500,
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"\nSaved updates for {len(athletes_to_update)} athletes to db.sqlite3."
                )
            )

        # Build rows for CSV export
        chrono_ranks = compute_chronological_ranks()
        report_rows = []
        for a in athletes:
            perfs = list(a.performances.all())
            first_perf = next((p for p in perfs if p.is_first_sub4), None)
            if not first_perf and perfs:
                dated = [p for p in perfs if p.date]
                first_perf = (
                    min(dated, key=lambda p: (p.date, p.time))
                    if dated
                    else min(perfs, key=lambda p: p.time)
                )
            best_perf = min(perfs, key=lambda p: p.time) if perfs else None

            mark_str = format_mile_time(first_perf.time) if first_perf else "—"
            date_str = (
                first_perf.date.strftime("%Y-%m-%d")
                if (first_perf and first_perf.date)
                else ""
            )
            venue_str = (first_perf.location or "") if first_perf else ""
            pb_str = format_mile_time(best_perf.time) if best_perf else mark_str

            report_rows.append({
                "athlete": a,
                "member_num": chrono_ranks.get(a.pk, 99999),
                "mark": mark_str,
                "date": date_str,
                "raw_date": first_perf.date if first_perf else None,
                "venue": venue_str,
                "pb": pb_str,
                "perf_count": len(perfs),
                "instagram": a.instagram_handle,
                "strava_id": a.strava_id,
                "wa_id": a.world_athletics_id,
                "source": source_by_id.get(
                    a.pk,
                    "Verified" if (a.instagram_handle or a.strava_id) else "Search Link",
                ),
            })

        # Sort matched CSV by most recent first sub-4 date (so active/current runners are first)
        report_rows.sort(
            key=lambda r: (
                r["raw_date"] or datetime.date(1900, 1, 1),
                -(r["member_num"] if isinstance(r["member_num"], int) else 0),
            ),
            reverse=True,
        )

        matched_count = self.write_csv_export(
            report_rows, Path(csv_out), only_matched=True
        )
        total_count = self.write_csv_export(
            report_rows, Path(full_csv_out), only_matched=False
        )

        elapsed = round(time_module.time() - t_start, 1)
        ig_total = sum(1 for r in report_rows if r["instagram"])
        st_total = sum(1 for r in report_rows if r["strava_id"])
        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone in {elapsed}s! "
                f"Total verified Instagram handles: {ig_total} | "
                f"Total verified Strava accounts: {st_total} | "
                f"Total unique athletes with IG/Strava: {matched_count} (written to {csv_out}) | "
                f"Full {total_count}-athlete master sheet written to {full_csv_out}."
            )
        )

    def fetch_alltime_wa_toplist(self):
        """Fetch pages 1..23 of World Athletics All-Time Men's One Mile Toplist concurrently."""
        def fetch_page(p):
            url = (
                "https://worldathletics.org/records/all-time-toplists/middlelong/one-mile/all/men/senior"
                f"?regionType=world&page={p}&bestResultsOnly=true&oversizedTrack=all&windReading=all&ageCategory=senior"
            )
            for _ in range(2):
                try:
                    r = requests.get(
                        url, timeout=20, headers={"User-Agent": "Mozilla/5.0"}
                    )
                    if r.status_code == 200:
                        soup = BeautifulSoup(r.text, "html.parser")
                        out = []
                        for tr in soup.select("table.records-table tbody tr"):
                            td_comp = tr.find("td", {"data-th": "Competitor"})
                            td_dob = tr.find("td", {"data-th": "DOB"})
                            td_nat = tr.find("td", {"data-th": "Nat"})
                            td_mark = tr.find("td", {"data-th": "Mark"})
                            if not td_comp or not td_mark:
                                continue
                            mark = " ".join(td_mark.get_text().split())
                            if mark.startswith("4:"):
                                continue
                            out.append({
                                "name": " ".join(td_comp.get_text().split()),
                                "wa_id": extract_wa_id(td_comp),
                                "dob": (
                                    " ".join(td_dob.get_text().split())
                                    if td_dob
                                    else ""
                                ),
                                "country": (
                                    " ".join(td_nat.get_text().split())
                                    if td_nat
                                    else ""
                                ),
                            })
                        return out
                except Exception:
                    time_module.sleep(1)
            return []

        with ThreadPoolExecutor(max_workers=8) as ex:
            pages = list(ex.map(fetch_page, range(1, 24)))
        return [row for page in pages for row in page]

    def fetch_bulk_wikidata_socials(self):
        """
        Fetch all track/athletics Wikidata records with World Athletics ID (P1146),
        Instagram (P2003), or Strava (P5999) in two fast bulk SPARQL queries.
        """
        headers = {
            "User-Agent": "Sub4MilersBot/1.0 (https://sub4milers.com; sub4milers@gmail.com)",
            "Accept": "application/sparql-results+json",
        }
        by_wa = {}
        by_name = {}

        # Query 1: All items with P1146 + (P2003 or P5999)
        q1 = """
        SELECT ?waId ?ig ?strava WHERE {
          ?item wdt:P1146 ?waId .
          { ?item wdt:P2003 ?ig . } UNION { ?item wdt:P5999 ?strava . }
        }
        """
        try:
            r = requests.get(
                "https://query.wikidata.org/sparql",
                params={"query": q1, "format": "json"},
                headers=headers,
                timeout=25,
            )
            if r.status_code == 200:
                for b in r.json().get("results", {}).get("bindings", []):
                    wa_id = b.get("waId", {}).get("value", "").strip()
                    ig = clean_ig_handle(b.get("ig", {}).get("value"))
                    st = clean_strava_id(b.get("strava", {}).get("value"))
                    if wa_id and (ig or st):
                        entry = by_wa.setdefault(wa_id, {"ig": None, "strava": None})
                        if ig and not entry["ig"]:
                            entry["ig"] = ig
                        if st and not entry["strava"]:
                            entry["strava"] = st
        except Exception as e:
            self.stdout.write(f"  Warning: Wikidata Query 1 error: {e}")

        # Query 2: All items with P1146 + English label + birth year + optional IG/Strava
        # so we can also match by English label even when WA ID differs (e.g. 6-digit IAAF ID)
        q2 = """
        SELECT ?waId ?ig ?strava ?label ?dob WHERE {
          ?item wdt:P1146 ?waId .
          { ?item wdt:P2003 ?ig . } UNION { ?item wdt:P5999 ?strava . }
          ?item rdfs:label ?label .
          FILTER(LANG(?label) = "en")
          OPTIONAL { ?item wdt:P569 ?dob . }
        }
        """
        try:
            r = requests.get(
                "https://query.wikidata.org/sparql",
                params={"query": q2, "format": "json"},
                headers=headers,
                timeout=30,
            )
            if r.status_code == 200:
                for b in r.json().get("results", {}).get("bindings", []):
                    label = b.get("label", {}).get("value", "")
                    wa_id = b.get("waId", {}).get("value", "").strip()
                    ig = clean_ig_handle(b.get("ig", {}).get("value"))
                    st = clean_strava_id(b.get("strava", {}).get("value"))
                    dob_raw = b.get("dob", {}).get("value", "")
                    byear = (
                        int(dob_raw[:4])
                        if dob_raw and len(dob_raw) >= 4 and dob_raw[:4].isdigit()
                        else None
                    )
                    if label and (ig or st):
                        norm = normalize_text(label)
                        by_name.setdefault(norm, []).append({
                            "wa_id": wa_id,
                            "ig": ig,
                            "strava": st,
                            "birth_year": byear,
                        })
        except Exception as e:
            self.stdout.write(f"  Warning: Wikidata Query 2 error: {e}")

        # Query 3: Athletes with sport=athletics (wd:Q542) + IG/Strava who may not have P1146 set
        q3 = """
        SELECT ?ig ?strava ?label ?dob WHERE {
          ?item wdt:P641 wd:Q542 .
          { ?item wdt:P2003 ?ig . } UNION { ?item wdt:P5999 ?strava . }
          ?item rdfs:label ?label .
          FILTER(LANG(?label) = "en")
          OPTIONAL { ?item wdt:P569 ?dob . }
        }
        """
        try:
            r = requests.get(
                "https://query.wikidata.org/sparql",
                params={"query": q3, "format": "json"},
                headers=headers,
                timeout=30,
            )
            if r.status_code == 200:
                for b in r.json().get("results", {}).get("bindings", []):
                    label = b.get("label", {}).get("value", "")
                    ig = clean_ig_handle(b.get("ig", {}).get("value"))
                    st = clean_strava_id(b.get("strava", {}).get("value"))
                    dob_raw = b.get("dob", {}).get("value", "")
                    byear = (
                        int(dob_raw[:4])
                        if dob_raw and len(dob_raw) >= 4 and dob_raw[:4].isdigit()
                        else None
                    )
                    if label and (ig or st):
                        norm = normalize_text(label)
                        by_name.setdefault(norm, []).append({
                            "wa_id": "",
                            "ig": ig,
                            "strava": st,
                            "birth_year": byear,
                        })
        except Exception as e:
            self.stdout.write(f"  Warning: Wikidata Query 3 error: {e}")

        return by_wa, by_name

    def fetch_batch_wikipedia_socials(self, athletes, source_by_id, athletes_to_update):
        """
        Check English Wikipedia external links (filtered by elquery=www.instagram.com)
        and Wikidata QIDs in batches of 50 athlete names per API call.
        """
        headers = {
            "User-Agent": "Sub4MilersBot/1.0 (https://sub4milers.com; sub4milers@gmail.com)"
        }
        title_to_athletes = {}
        all_titles = []
        for a in athletes:
            t = a.name.title().strip()
            if t:
                norm_t = normalize_text(t)
                title_to_athletes.setdefault(norm_t, []).append(a)
                title_to_athletes.setdefault(normalize_text(f"{t} (runner)"), []).append(a)
                all_titles.append(t)
                all_titles.append(f"{t} (runner)")

        unique_titles = list(dict.fromkeys(all_titles))
        batches = [unique_titles[i : i + 50] for i in range(0, len(unique_titles), 50)]

        hits = 0

        def check_batch(batch):
            try:
                r = requests.get(
                    "https://en.wikipedia.org/w/api.php",
                    params={
                        "action": "query",
                        "titles": "|".join(batch),
                        "prop": "extlinks|categories",
                        "elquery": "www.instagram.com",
                        "ellimit": "500",
                        "cllimit": "500",
                        "format": "json",
                        "redirects": 1,
                    },
                    headers=headers,
                    timeout=15,
                )
                if r.status_code != 200:
                    return []
                q = r.json().get("query", {})
                redirect_map = {}
                for item in q.get("normalized", []) + q.get("redirects", []):
                    redirect_map[item.get("to", "")] = item.get("from", "")

                found = []
                for page_id, page in q.get("pages", {}).items():
                    if str(page_id).startswith("-"):
                        continue
                    title = page.get("title", "")
                    orig_title = redirect_map.get(title, title)
                    cats = " ".join(
                        c.get("title", "").lower() for c in page.get("categories", [])
                    )
                    if cats and not any(
                        w in cats
                        for w in [
                            "runner",
                            "athlete",
                            "track",
                            "mile",
                            "olympic",
                            "steeplechase",
                            "distance",
                            "cross-country",
                        ]
                    ):
                        continue
                    ig = None
                    for link in page.get("extlinks", []):
                        url = link.get("*", "") or link.get("url", "")
                        if "instagram.com/" in url:
                            cand = clean_ig_handle(url)
                            if cand:
                                ig = cand
                                break
                    if ig:
                        found.append((orig_title, title, ig, None))
                return found
            except Exception:
                return []

        with ThreadPoolExecutor(max_workers=10) as ex:
            batch_results = list(ex.map(check_batch, batches))

        for res_list in batch_results:
            for orig_title, page_title, ig, st in res_list:
                cands = title_to_athletes.get(
                    normalize_text(orig_title), []
                ) or title_to_athletes.get(normalize_text(page_title), [])
                if len(cands) == 1:
                    ath = cands[0]
                    changed = False
                    if ig and not ath.instagram_handle:
                        ath.instagram_handle = ig
                        source_by_id[ath.pk] = "Wikipedia"
                        hits += 1
                        changed = True
                    if st and not ath.strava_id:
                        ath.strava_id = st
                        source_by_id.setdefault(ath.pk, "Wikipedia")
                        changed = True
                    if changed:
                        athletes_to_update.add(ath)

        return hits

    def write_csv_export(self, report_rows, out_path, only_matched=True):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "club_number",
            "name",
            "country",
            "instagram_handle",
            "instagram_url",
            "strava_id",
            "strava_url",
            "first_sub4_time",
            "first_sub4_date",
            "first_sub4_venue",
            "personal_best",
            "total_sub4_runs",
            "dob",
            "world_athletics_url",
            "source",
            "google_instagram_search",
        ]
        written = 0
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in report_rows:
                ig = r["instagram"]
                st = r["strava_id"]
                if only_matched and not (ig or st):
                    continue
                ath = r["athlete"]
                name_title = ath.name.title()
                country = ath.countries or ""
                ig_q = urllib.parse.quote_plus(
                    f'"{name_title}" {country} runner site:instagram.com'
                )
                writer.writerow({
                    "club_number": r["member_num"],
                    "name": name_title,
                    "country": country,
                    "instagram_handle": f"@{ig}" if ig else "",
                    "instagram_url": f"https://www.instagram.com/{ig}/" if ig else "",
                    "strava_id": st or "",
                    "strava_url": f"https://www.strava.com/athletes/{st}" if st else "",
                    "first_sub4_time": r["mark"],
                    "first_sub4_date": r["date"],
                    "first_sub4_venue": r["venue"],
                    "personal_best": r["pb"],
                    "total_sub4_runs": r["perf_count"],
                    "dob": ath.DOB.strftime("%Y-%m-%d") if ath.DOB else "",
                    "world_athletics_url": (
                        f"https://worldathletics.org/athletes/_/{r['wa_id']}"
                        if r["wa_id"]
                        else ""
                    ),
                    "source": r["source"],
                    "google_instagram_search": (
                        f"https://www.google.com/search?q={ig_q}"
                    ),
                })
                written += 1
        return written

    def backfill_wa_ids_current_year(self, session, athletes):
        """Backfill world_athletics_id from current year World Athletics toplist if missing."""
        from sub4.management.commands.scrape_world_athletics import Command as ScrapeCmd

        scraper = ScrapeCmd()
        scraper.stdout = self.stdout
        year = datetime.date.today().year
        try:
            rows = scraper.fetch_year_rows(session, year, best_only=True, max_pages=6)
        except Exception:
            return

        wa_by_norm_name = {}
        for r in rows:
            if r.get("WA_ID") and r.get("Name"):
                wa_by_norm_name[normalize_text(r["Name"])] = r["WA_ID"]

        for ath in athletes:
            found_id = wa_by_norm_name.get(normalize_text(ath.name))
            if found_id:
                ath.world_athletics_id = found_id
                if ath.pk:
                    ath.save(update_fields=["world_athletics_id"])

    def build_markdown_report(self, report_rows):
        today_str = datetime.date.today().strftime("%d %b %Y").upper()
        lines = [
            f"## SUB 4 REGISTER — NEW SUB-4 SOCIAL COLLAB QUEUE ({today_str})",
            "",
            f"**{len(report_rows)} new sub-4:00.00 mile runner(s)** were added to the register.",
            "Use the handles, verification links, and pre-formatted captions below to send **Instagram Collab** invites.",
            "",
            "### 1. Social Lookup Table",
            "",
            "| No. | Athlete | Country | First Sub-4 | Date | Instagram | Strava | Quick Search Fallbacks |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
        ]

        for r in report_rows:
            ath = r["athlete"]
            name_title = ath.name.title()
            country = ath.countries or "—"
            ig = r["instagram"]
            st = r["strava_id"]

            ig_cell = (
                f"[**@{ig}**](https://www.instagram.com/{ig}/)"
                if ig
                else "_Not auto-matched_"
            )
            st_cell = (
                f"[Strava #{st}](https://www.strava.com/athletes/{st})"
                if st
                else "_Not auto-matched_"
            )

            ig_q = urllib.parse.quote_plus(
                f'"{name_title}" {country} runner site:instagram.com'
            )
            st_q = urllib.parse.quote_plus(
                f'"{name_title}" {country} runner site:strava.com/athletes'
            )
            wa_link = (
                f" · [WA Profile](https://worldathletics.org/athletes/_/{r['wa_id']})"
                if r["wa_id"]
                else ""
            )
            search_cell = (
                f"[Find IG](https://www.google.com/search?q={ig_q}) · "
                f"[Find Strava](https://www.google.com/search?q={st_q}){wa_link}"
            )

            lines.append(
                f"| **#{r['member_num']}** | **{name_title}** | {country} | `{r['mark']}` | {r['date']} | "
                f"{ig_cell} | {st_cell} | {search_cell} |"
            )

        lines.extend([
            "",
            "---",
            "",
            "### 2. Ready-to-Copy Instagram Collab Captions",
            "",
            "#### Option A: Combined Weekly Roundup Post",
            "```text",
            "NEW SUB 4 MILERS",
            "",
        ])

        for r in report_rows:
            ath = r["athlete"]
            tag = f"@{r['instagram']}" if r["instagram"] else ath.name.title()
            lines.append(
                f"No. {r['member_num']} · {ath.name.title()} ({ath.countries or '—'}) — {r['mark']} ({r['date']}) {tag}"
            )

        lines.extend([
            "",
            "Full register at sub4milers.com",
            "#sub4mile #sub4milers #onemile #trackandfield #athletics",
            "```",
            "",
            "#### Option B: Individual Collab Posts",
            "",
        ])

        for r in report_rows:
            ath = r["athlete"]
            name_title = ath.name.title()
            tag_line = (
                f"@{r['instagram']}"
                if r["instagram"]
                else f"{name_title} (tag @handle)"
            )
            lines.extend([
                f"<details>",
                f"<summary><strong>#{r['member_num']} {name_title} ({r['mark']})</strong> — Click to copy caption</summary>",
                "",
                "```text",
                f"SUB 4:00.00 MILE CLUB — NO. {r['member_num']}",
                f"{name_title.upper()} ({ath.countries or '—'})",
                f"Time: {r['mark']}",
                f"Date: {r['date']}",
                f"Venue: {r['venue']}",
                "",
                f"Congratulations to {tag_line} on breaking the 4-minute mile barrier.",
                f"Full register at sub4milers.com",
                f"#sub4mile #sub4milers #onemile #trackandfield",
                "```",
                "</details>",
                "",
            ])

        if not any(
            os.environ.get(k)
            for k in ("SERPER_API_KEY", "BRAVE_API_KEY", "GOOGLE_SEARCH_API_KEY")
        ):
            lines.extend([
                "---",
                "> **Tip for 100% Automatic NCAA/Club Handle Matching:**",
                "> Built-in lookup uses World Athletics profiles, Wikidata, and Wikipedia. To also auto-match collegiate/club runners via Google Search, add a free `SERPER_API_KEY` (2,500 free searches at [serper.dev](https://serper.dev)) in **Settings → Secrets and variables → Actions**.",
            ])

        return "\n".join(lines)
