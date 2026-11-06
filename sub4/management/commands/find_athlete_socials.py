import datetime
import json
import os
import re
import time as time_module
import urllib.parse
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand
from django.db.models import OuterRef, Subquery

from sub4.management.commands.scrape_world_athletics import normalize_text
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

RESERVED_IG_HANDLES = {
    "p", "reel", "reels", "stories", "explore", "accounts", "about",
    "developer", "legal", "privacy", "terms", "help", "worldathletics",
    "milesplit", "flotrack", "citiusmag", "runnersworld", "strava",
    "ncaatrackfield", "ustfccca", "athleticsweekly", "britishathletics",
    "usatf", "athleticscanda", "athleticsaustralia", "share", "tv", " tags",
}


def clean_ig_handle(raw):
    if not raw:
        return None
    val = str(raw).strip().lstrip("@")
    m = IG_URL_RE.search(val)
    if m:
        val = m.group(1)
    val = val.strip("/").split("?")[0].split("/")[0]
    if not val or val.lower() in RESERVED_IG_HANDLES:
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
    """Check World Athletics athlete profile JSON (__NEXT_DATA__) for social links."""
    if not wa_id:
        return None, None
    url = f"https://worldathletics.org/athletes/athlete={wa_id}"
    try:
        r = session.get(url, timeout=15)
        if r.status_code != 200:
            return None, None
        soup = BeautifulSoup(r.text, "html.parser")
        script = soup.find("script", id="__NEXT_DATA__")
        if not script or not script.string:
            return None, None
        data = json.loads(script.string)
        basic = (
            data.get("props", {})
            .get("pageProps", {})
            .get("competitor", {})
            .get("basicData", {})
        ) or {}
        ig = clean_ig_handle(basic.get("instagramUsername") or basic.get("instagramLink"))
        return ig, None
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
        # Search Wikidata entities by name
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
                        for w in ["runner", "athlete", "track", "middle-distance", "steeplechase", "olympi"]
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
            cats = " ".join(c.get("title", "").lower() for c in page.get("categories", []))
            if cats and not any(w in cats for w in ["runner", "athlete", "births", "track", "mile"]):
                continue
            ig = None
            st = None
            for link in page.get("extlinks", []):
                url = link.get("*", "")
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
    Returns list of {'title': ..., 'link': ..., 'snippet': ...}.
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
                headers={"X-Subscription-Token": brave_key, "Accept": "application/json"},
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

    if any(w in haystack for w in ["track", "mile", "runner", "running", "athletics", "xc"]):
        score += 0.20

    return min(score, 1.0)


def discover_socials_for_athlete(session, athlete):
    """
    Run multi-tier discovery for an Athlete and return:
    {
        'instagram': handle | None,
        'strava_id': id | None,
        'wa_id': wa_id | None,
        'source': str,
    }
    """
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
    today = datetime.date.today()
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
        "Discover Instagram and Strava accounts for newly inducted sub-4 milers "
        "and generate a ready-to-post Collab summary."
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
            "--markdown-output",
            type=str,
            default="",
            help="Optional path to write Markdown digest (e.g. for GitHub Issue creation)",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-run discovery even if instagram_handle and strava_id are already set",
        )

    def handle(self, *args, **options):
        from_file = options["from_file"]
        recent_n = options["recent"]
        athlete_query = options["athlete"]
        md_out = options["markdown_output"]
        force = options["force"]

        target_athletes = []

        if from_file:
            path = Path(from_file)
            if path.exists():
                ids = json.loads(path.read_text(encoding="utf-8"))
                if ids:
                    target_athletes = list(
                        Athlete.objects.filter(pk__in=ids).prefetch_related("performances")
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
            # Default to 10 most recent first-time inductees
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

        # Ensure WA IDs are populated from latest season toplist if missing
        missing_wa = [a for a in target_athletes if not a.world_athletics_id]
        if missing_wa:
            self.backfill_wa_ids(session, missing_wa)

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
            mark_str = format_mile_time(first_perf.time) if first_perf else "—"
            date_str = (
                first_perf.date.strftime("%d %b %Y").upper()
                if (first_perf and first_perf.date)
                else "UNRECORDED"
            )
            venue_str = (first_perf.location or "Unknown Venue") if first_perf else "—"
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
                "venue": venue_str,
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
            self.stdout.write(self.style.SUCCESS(f"Wrote Markdown collab digest to {out_path}"))

    def backfill_wa_ids(self, session, athletes):
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
            f"## OFFICIAL SUB-4:00.00 REGISTER — NEW INDUCTEE SOCIAL COLLAB QUEUE ({today_str})",
            "",
            f"**{len(report_rows)} new sub-4:00.00 mile inductee(s)** were added to the register.",
            "Use the handles, verification links, and pre-formatted captions below to send **Instagram Collab** invites.",
            "",
            "### 1. Inductee Social Lookup Table",
            "",
            "| Member # | Athlete | Nat. | First Sub-4 | Date | Instagram | Strava | Quick Search Fallbacks |",
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

            ig_q = urllib.parse.quote_plus(f'"{name_title}" {country} runner site:instagram.com')
            st_q = urllib.parse.quote_plus(f'"{name_title}" {country} runner site:strava.com/athletes')
            wa_link = (
                f" · [WA Profile](https://worldathletics.org/athletes/athlete={r['wa_id']})"
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
            "#### Option A: Combined Weekly Inductee Roundup Post",
            "```text",
            "OFFICIAL SUB-4:00.00 MILE REGISTER — NEW INDUCTEES",
            "",
        ])

        for r in report_rows:
            ath = r["athlete"]
            tag = f"@{r['instagram']}" if r["instagram"] else ath.name.title()
            lines.append(
                f"No. {r['member_num']} — {ath.name.title()} ({ath.countries or '—'}) // {r['mark']} ({r['date']}) {tag}"
            )

        lines.extend([
            "",
            "Full chronological register & all-time rankings at sub4milers.com",
            "#sub4mile #sub4milers #onemile #trackandfield #athletics",
            "```",
            "",
            "#### Option B: Individual Inductee Collab Posts",
            "",
        ])

        for r in report_rows:
            ath = r["athlete"]
            name_title = ath.name.title()
            tag_line = f"@{r['instagram']}" if r["instagram"] else f"{name_title} (tag @handle)"
            lines.extend([
                f"<details>",
                f"<summary><strong>#{r['member_num']} {name_title} ({r['mark']})</strong> — Click to copy caption</summary>",
                "",
                "```text",
                f"OFFICIAL REGISTER ENTRY // SUB-4:00.00 MILE CLUB",
                f"MEMBER NO. {r['member_num']}: {name_title.upper()} ({ath.countries or '—'})",
                f"MARK: {r['mark']}",
                f"DATE: {r['date']}",
                f"VENUE: {r['venue']}",
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
