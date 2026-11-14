import csv
import math
import subprocess
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Count, Min
from PIL import Image, ImageDraw, ImageFont

from sub4.models import Athlete, Performance


# Vintage Timesheet Palette
BG_CREAM = (246, 241, 230)       # #f6f1e6
BG_CARD = (239, 232, 216)        # #efe8d8
INK_BLACK = (24, 23, 21)         # #181715
INK_FADED = (82, 78, 72)         # #524e48
INK_MUTED = (133, 127, 118)      # #857f76
ACCENT_RED = (166, 36, 27)       # #a6241b
RULE_DARK = (56, 53, 48)         # #383530
RULE_LIGHT = (205, 196, 178)     # #cdc4b2


def get_fonts(base_dir: Path):
    font_dir = base_dir / "static" / "fonts"
    reg_path = str(font_dir / "CourierPrime-Regular.ttf")
    bold_path = str(font_dir / "CourierPrime-Bold.ttf")
    title_path = str(font_dir / "SpecialElite-Regular.ttf")

    def load(path, size):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            return ImageFont.load_default()

    return {
        "title_xl": load(title_path, 66),
        "title_lg": load(title_path, 52),
        "title_md": load(title_path, 40),
        "title_sm": load(title_path, 30),
        "mono_xxl": load(bold_path, 86),
        "mono_xl": load(bold_path, 58),
        "mono_lg": load(bold_path, 40),
        "mono_md": load(bold_path, 30),
        "mono_sm": load(reg_path, 25),
        "mono_xs": load(reg_path, 20),
        "mono_xxs": load(reg_path, 17),
        "bold_sm": load(bold_path, 25),
        "bold_xs": load(bold_path, 20),
    }


def make_paper_canvas(width: int, height: int) -> Image.Image:
    """Create a cream archival ledger canvas with subtle ruled lines and double border."""
    img = Image.new("RGB", (width, height), BG_CREAM)
    draw = ImageDraw.Draw(img)

    for y in range(60, height - 60, 36):
        draw.line([(48, y), (width - 48, y)], fill=(236, 229, 214), width=1)

    m = 36
    draw.rectangle([m, m, width - m, height - m], outline=INK_BLACK, width=3)
    draw.rectangle([m + 8, m + 8, width - m - 8, height - m - 8], outline=RULE_DARK, width=1)
    return img


def draw_header(draw: ImageDraw.ImageDraw, width: int, fonts: dict, kicker: str, title: str, subtitle: str = "") -> int:
    """Draw standard top archival masthead and return next Y coordinate."""
    x_left = 76
    x_right = width - 76
    y = 74

    draw.text((x_left, y), kicker.upper(), font=fonts["bold_xs"], fill=ACCENT_RED)
    draw.text((x_right, y), "SUB 4 REGISTER · 1609.34M", font=fonts["mono_xs"], fill=INK_MUTED, anchor="ra")
    y += 34
    draw.line([(x_left, y), (x_right, y)], fill=INK_BLACK, width=2)
    y += 22

    draw.text((x_left, y), title, font=fonts["title_lg"], fill=INK_BLACK)
    y += 62

    if subtitle:
        sub_font = fonts["mono_xs"] if len(subtitle) > 54 else fonts["mono_sm"]
        draw.text((x_left, y), subtitle, font=sub_font, fill=INK_FADED)
        y += 38

    draw.line([(x_left, y), (x_right, y)], fill=INK_BLACK, width=2)
    draw.line([(x_left, y + 6), (x_right, y + 6)], fill=RULE_DARK, width=1)
    return y + 28


def draw_footer(draw: ImageDraw.ImageDraw, width: int, height: int, fonts: dict, left_note: str, right_note: str = "@sub4milers"):
    x_left = 76
    x_right = width - 76
    y_line = height - 96
    draw.line([(x_left, y_line), (x_right, y_line)], fill=RULE_DARK, width=2)
    draw.text((x_left, y_line + 16), left_note, font=fonts["mono_xs"], fill=INK_FADED)
    draw.text((x_right, y_line + 16), right_note, font=fonts["bold_xs"], fill=ACCENT_RED, anchor="ra")


def load_socials_lookup(base_dir: Path) -> dict[str, dict]:
    """Load Instagram/Strava handles from static/data/sub4_all_athletes_socials.csv."""
    csv_path = base_dir / "static" / "data" / "sub4_all_athletes_socials.csv"
    lookup = {}
    if csv_path.exists():
        with csv_path.open("r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                name = (row.get("name") or "").strip().lower()
                if name:
                    lookup[name] = row
    return lookup


def fmt_time_obj(t) -> str:
    if not t:
        return "3:59.--"
    total_cs = int(round((t.second + t.microsecond / 1_000_000) * 100))
    sec, cs = divmod(total_cs, 100)
    return f"{t.minute}:{sec:02d}.{cs:02d}"


def time_obj_seconds(t) -> float:
    if not t:
        return 240.0
    return t.minute * 60 + t.second + t.microsecond / 1_000_000


def format_seconds(sec: float) -> str:
    m = int(sec // 60)
    rem = sec - m * 60
    return f"{m}:{rem:05.2f}"


class Command(BaseCommand):
    help = "Generates Instagram cards (1080x1350), carousels, On-This-Day digest, and 9:16 Data Animation Reels (1080x1920 MP4)."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Generate inductee cards, On-This-Day card, Age Carousel, and all 4 Reels.")
        parser.add_argument("--inductees", type=int, default=6, help="Number of most recent sub-4 inductee cards to render.")
        parser.add_argument("--skip-reels", action="store_true", help="Skip rendering MP4 reels (faster for weekly cron if desired).")
        parser.add_argument("--transparent-reels", action="store_true", help="Render text-free transparent-background reels (.mov + .webm) for Adobe Express.")

    def handle(self, *args, **options):
        base_dir = Path(settings.BASE_DIR)
        fonts = get_fonts(base_dir)
        socials = load_socials_lookup(base_dir)

        out_root = base_dir / "static" / "instagram"
        inductees_dir = out_root / "inductees"
        otd_dir = out_root / "on_this_day"
        carousel_dir = out_root / "carousels" / "age_series"
        reels_dir = out_root / "reels"
        ae_reels_dir = out_root / "adobe_express_ready" / "reels"

        for d in (inductees_dir, otd_dir, carousel_dir, reels_dir, ae_reels_dir):
            d.mkdir(parents=True, exist_ok=True)

        self.stdout.write("Building chronological athlete ledger from database...")
        chron_athletes = self.build_chronological_ledger(socials)
        age_records = [r for r in chron_athletes if r["age_days"] is not None and 14 * 365 <= r["age_days"] <= 45 * 365]

        if options["transparent_reels"] and not options["all"]:
            self.stdout.write("Rendering 4 transparent text-free Reels (.mov + .webm) for Adobe Express...")
            for ext in (".mov", ".webm"):
                self.render_reel_1_age_histogram(ae_reels_dir / f"reel_1_age_at_first_sub4_transparent{ext}", fonts, age_records, transparent=True)
                self.render_reel_2_country_race(ae_reels_dir / f"reel_2_country_bar_race_transparent{ext}", fonts, chron_athletes, transparent=True)
                self.render_reel_3_annual_surge(ae_reels_dir / f"reel_3_annual_sub4_surge_transparent{ext}", fonts, transparent=True)
                self.render_reel_4_first_sub4_stopwatch(ae_reels_dir / f"reel_4_first_sub4_stopwatch_transparent{ext}", fonts, chron_athletes, transparent=True)
            self.stdout.write(self.style.SUCCESS(f"Transparent reels generated in {ae_reels_dir}"))
            return

        self.stdout.write("1. Generating recent Sub-4 Runner Cards...")
        self.generate_inductee_cards(inductees_dir, fonts, chron_athletes, count=options["inductees"])

        self.stdout.write("2. Generating 'This Week in Sub-4 History' card & digest...")
        self.generate_on_this_day(otd_dir, fonts, chron_athletes)

        self.stdout.write("3. Generating 4-slide 'Age at First Sub-4' Data Carousel...")
        self.generate_age_carousel(carousel_dir, fonts, age_records)

        if not options["skip_reels"]:
            self.stdout.write("4. Rendering 4 Instagram Data Animation Reels (1080x1920 @ 30fps)...")
            self.render_reel_1_age_histogram(reels_dir / "reel_1_age_at_first_sub4.mp4", fonts, age_records)
            self.render_reel_2_country_race(reels_dir / "reel_2_country_bar_race.mp4", fonts, chron_athletes)
            self.render_reel_3_annual_surge(reels_dir / "reel_3_annual_sub4_surge.mp4", fonts)
            self.render_reel_4_first_sub4_stopwatch(reels_dir / "reel_4_first_sub4_stopwatch.mp4", fonts, chron_athletes)

        self.stdout.write(self.style.SUCCESS(f"All Instagram assets generated in {out_root}"))

    def build_chronological_ledger(self, socials: dict) -> list[dict]:
        """Build ordered list of all athletes with their first sub-4 run, rank, PB, total runs, and age."""
        athletes = (
            Athlete.objects.annotate(
                first_date=Min("performances__date"),
                best_time_val=Min("performances__time"),
                total_runs=Count("performances"),
            )
            .filter(first_date__isnull=False)
            .prefetch_related("performances")
            .order_by("first_date", "best_time_val", "name")
        )

        ledger = []
        for idx, ath in enumerate(athletes, start=1):
            perfs = sorted(
                [p for p in ath.performances.all() if p.date is not None],
                key=lambda p: (p.date, p.time),
            )
            first_perf = perfs[0] if perfs else ath.performances.order_by("time").first()
            best_perf = ath.performances.order_by("time").first()
            first_date = first_perf.date if first_perf else ath.first_date
            first_mark = fmt_time_obj(first_perf.time) if first_perf else "3:59.--"
            first_sec = time_obj_seconds(first_perf.time) if first_perf else 239.9
            pb_mark = fmt_time_obj(best_perf.time) if best_perf else first_mark
            venue = (first_perf.location or "").strip() if first_perf else ""
            country = ((ath.countries or "").split(",")[0].strip().upper()) or "---"
            ig = (ath.instagram_handle or socials.get(ath.name.lower(), {}).get("instagram_handle", "") or "").strip().lstrip("@")

            age_days = None
            age_years = None
            age_rem_days = None
            age_exact = None
            if ath.DOB and first_date:
                d = (first_date - ath.DOB).days
                if 14 * 365 <= d <= 45 * 365:
                    age_days = d
                    age_years = int(d // 365.2425)
                    age_rem_days = max(0, int(d - age_years * 365.2425))
                    age_exact = d / 365.2425

            ledger.append({
                "athlete": ath,
                "rank": idx,
                "name": ath.name,
                "country": country,
                "dob": ath.DOB,
                "date": first_date,
                "mark": first_mark,
                "sec": first_sec,
                "pb": pb_mark,
                "venue": venue,
                "total_runs": ath.total_runs,
                "ig": ig,
                "age_days": age_days,
                "age_years": age_years,
                "age_rem_days": age_rem_days,
                "age_exact": age_exact,
            })
        return ledger

    # ------------------------------------------------------------------
    # 1. RECENT SUB-4 RUNNER CARDS (1080x1350)
    # ------------------------------------------------------------------
    def generate_inductee_cards(self, out_dir: Path, fonts: dict, chron_athletes: list[dict], count: int = 6):
        recent = sorted(chron_athletes, key=lambda r: (r["date"], r["rank"]), reverse=True)[:count]
        for rec in recent:
            W, H = 1080, 1350
            img = make_paper_canvas(W, H)
            draw = ImageDraw.Draw(img)

            y = draw_header(
                draw, W, fonts,
                kicker="Most Recent Sub 4 // Official Time Record",
                title=f"CHRONOLOGICAL #{rec['rank']}",
                subtitle="Sub-Four-Minute Mile Register (Since 6 May 1954)",
            )

            # Main Athlete Box
            draw.rectangle([76, y, W - 76, y + 270], fill=BG_CARD, outline=INK_BLACK, width=2)
            draw.text((106, y + 28), "ATHLETE", font=fonts["bold_xs"], fill=INK_MUTED)
            draw.text((W - 106, y + 28), f"COUNTRY: {rec['country']}", font=fonts["bold_xs"], fill=ACCENT_RED, anchor="ra")

            name_font = fonts["title_xl"] if len(rec["name"]) <= 20 else fonts["title_lg"]
            draw.text((106, y + 72), rec["name"].upper(), font=name_font, fill=INK_BLACK)

            ig = rec["ig"]
            ig_line = f"INSTAGRAM: @{ig}" if ig else "SUB 4 MILE REGISTER"
            draw.text((106, y + 168), ig_line, font=fonts["mono_md"], fill=ACCENT_RED if ig else INK_FADED)

            if rec["age_years"] is not None and rec["dob"]:
                age_str = f"AGE AT FIRST SUB 4: {rec['age_years']}y {rec['age_rem_days']}d (DOB {rec['dob'].strftime('%d %b %Y').upper()})"
            else:
                age_str = "DISTANCE: ONE MILE (1,609.34 METRES)"
            draw.text((106, y + 216), age_str, font=fonts["mono_xs"], fill=INK_FADED)

            y += 305

            # Giant Official Time Stamp Box
            draw.rectangle([76, y, W - 76, y + 240], fill=BG_CREAM, outline=ACCENT_RED, width=3)
            draw.text((106, y + 24), "FIRST SUB-4:00 MILE MARK", font=fonts["bold_xs"], fill=ACCENT_RED)
            draw.text((106, y + 66), rec["mark"], font=fonts["mono_xxl"], fill=INK_BLACK)

            date_str = rec["date"].strftime("%d %B %Y").upper() if rec["date"] else "---"
            draw.text((106, y + 182), f"RECORDED ON {date_str}", font=fonts["mono_sm"], fill=INK_FADED)

            draw.rectangle([W - 360, y + 64, W - 110, y + 164], outline=ACCENT_RED, width=3)
            draw.text((W - 235, y + 92), "VERIFIED", font=fonts["bold_sm"], fill=ACCENT_RED, anchor="ma")
            draw.text((W - 235, y + 124), "SUB-4:00", font=fonts["mono_sm"], fill=ACCENT_RED, anchor="ma")

            y += 275

            rows = [
                ("VENUE / LOCATION", (rec["venue"] or "Official Track Meet")[:38]),
                ("CHRONOLOGICAL NUMBER", f"#{rec['rank']} IN WORLD HISTORY"),
                ("PERSONAL BEST", rec["pb"]),
                ("CAREER SUB-4 MILES", f"{rec['total_runs']} RECORDED SUB-4 PERFORMANCE{'S' if rec['total_runs'] != 1 else ''}"),
            ]
            for label, val in rows:
                draw.text((86, y + 12), label, font=fonts["bold_xs"], fill=INK_MUTED)
                draw.text((W - 86, y + 10), str(val), font=fonts["mono_sm"], fill=INK_BLACK, anchor="ra")
                y += 58
                draw.line([(76, y), (W - 76, y)], fill=RULE_LIGHT, width=1)

            draw_footer(draw, W, H, fonts, "SOURCE: SUB 4 REGISTER · WORLD ATHLETICS & TILASTOPAJA")
            slug = "".join(c if c.isalnum() else "_" for c in rec["name"].lower()).strip("_")
            out_path = out_dir / f"runner_{rec['rank']}_{slug}.png"
            img.save(out_path, "PNG")

    # ------------------------------------------------------------------
    # 2. ON THIS DAY / THIS WEEK IN SUB-4 HISTORY
    # ------------------------------------------------------------------
    def generate_on_this_day(self, out_dir: Path, fonts: dict, chron_athletes: list[dict]):
        today = date.today()
        target_mds = {((today + timedelta(days=offset)).month, (today + timedelta(days=offset)).day) for offset in range(-1, 6)}

        anniversaries = []
        for rec in chron_athletes:
            d = rec["date"]
            if d and (d.month, d.day) in target_mds:
                priority = (
                    (50 if rec["rank"] <= 100 else 0)
                    + (30 if rec["sec"] < 233.0 else 0)
                    + (25 if rec["ig"] else 0)
                    + (20 if d.year < 1980 else 0)
                )
                anniversaries.append({
                    **rec,
                    "years_ago": today.year - d.year,
                    "priority": priority,
                })

        anniversaries.sort(key=lambda x: (-x["priority"], x["date"].year))
        top_items = anniversaries[:7]

        W, H = 1080, 1350
        img = make_paper_canvas(W, H)
        draw = ImageDraw.Draw(img)

        window_end = today + timedelta(days=5)
        window_str = f"{today.strftime('%d %b').upper()} – {window_end.strftime('%d %b').upper()}"
        y = draw_header(
            draw, W, fonts,
            kicker=f"Historical Dispatch · {window_str}",
            title="THIS WEEK IN SUB-4 HISTORY",
            subtitle="Notable First Sub-4:00 Mile Breakthroughs Recorded During This Week",
        )

        for idx, item in enumerate(top_items):
            d = item["date"]
            card_h = 114
            fill_c = BG_CARD if idx % 2 == 0 else BG_CREAM
            draw.rectangle([76, y, W - 76, y + card_h], fill=fill_c, outline=RULE_DARK, width=1)

            draw.rectangle([76, y, 226, y + card_h], fill=INK_BLACK)
            draw.text((151, y + 24), str(d.year), font=fonts["mono_lg"], fill=BG_CREAM, anchor="ma")
            draw.text((151, y + 72), d.strftime("%d %b").upper(), font=fonts["bold_xs"], fill=BG_CARD, anchor="ma")

            name_line = f"{item['name'].upper()} ({item['country']})"
            draw.text((250, y + 18), name_line[:30], font=fonts["mono_md"], fill=INK_BLACK)
            meta_parts = [f"Sub-4 #{item['rank']}"]
            if item["venue"]:
                meta_parts.append(item["venue"][:26])
            if item["ig"]:
                meta_parts.append(f"@{item['ig']}")
            draw.text((250, y + 64), " · ".join(meta_parts), font=fonts["mono_xs"], fill=INK_FADED)

            draw.text((W - 96, y + 34), item["mark"], font=fonts["mono_lg"], fill=ACCENT_RED, anchor="ra")
            y += card_h + 14

        draw_footer(draw, W, H, fonts, f"TOTAL THIS WEEK IN ARCHIVE: {len(anniversaries)} FIRST-TIME SUB-4 MILERS")
        card_path = out_dir / "this_week_in_sub4_history.png"
        img.save(card_path, "PNG")

        md_lines = [
            f"# This Week in Sub-4 History ({window_str})",
            "",
            f"**{len(anniversaries)} runners** in history broke the 4-minute mile for the very first time during this calendar week.",
            "",
            "## Featured Anniversaries",
            "",
            "| Date | Years Ago | Runner | Country | Register # | First Sub-4 | Venue | Instagram |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
        ]
        for item in top_items:
            ig_cell = f"[@{item['ig']}](https://www.instagram.com/{item['ig']}/)" if item["ig"] else "—"
            disp_name = item["name"].title() if item["name"].islower() else item["name"]
            md_lines.append(
                f"| {item['date'].strftime('%d %b %Y')} | {item['years_ago']}y | **{disp_name}** | {item['country']} | #{item['rank']} | `{item['mark']}` | {item['venue'] or '—'} | {ig_cell} |"
            )

        ig_tags = " ".join(f"@{item['ig']}" for item in top_items if item["ig"])
        md_lines.extend([
            "",
            "## Ready-to-Post Instagram Caption",
            "",
            "```text",
            f"THIS WEEK IN SUB-4 HISTORY ({window_str})",
            "",
            f"{len(anniversaries)} milers entered the sub-4:00 register during this week across track history:",
        ])
        for item in top_items[:5]:
            tag_sfx = f" (@{item['ig']})" if item["ig"] else ""
            disp_name = item["name"].title() if item["name"].islower() else item["name"]
            md_lines.append(
                f"• {item['date'].year} ({item['date'].strftime('%d %b')}): {disp_name} ({item['country']}) — {item['mark']} [Sub-4 #{item['rank']}]{tag_sfx}"
            )
        md_lines.extend([
            "",
            "Full chronological register at the link in bio.",
            f"{ig_tags}".strip(),
            "#sub4mile #onemile #trackandfield #athletics #miler #sub4register",
            "```",
            "",
        ])
        (out_dir / "this_week_digest.md").write_text("\n".join(md_lines), encoding="utf-8")

    # ------------------------------------------------------------------
    # 3. 4-SLIDE AGE AT FIRST SUB-4 CAROUSEL (1080x1350)
    # ------------------------------------------------------------------
    def generate_age_carousel(self, out_dir: Path, fonts: dict, records: list[dict]):
        W, H = 1080, 1350
        counts = Counter(r["age_years"] for r in records)
        ages = list(range(15, 35))
        max_c = max(counts.get(a, 0) for a in ages) or 1
        total_n = len(records)
        median_days = sorted(r["age_days"] for r in records)[total_n // 2]
        med_y = int(median_days // 365.2425)
        med_d = int(median_days - med_y * 365.2425)
        teen_count = sum(1 for r in records if r["age_years"] <= 19)
        thirty_count = sum(1 for r in records if r["age_years"] >= 30)

        # --- SLIDE 1: Overall Age Distribution ---
        img1 = make_paper_canvas(W, H)
        d1 = ImageDraw.Draw(img1)
        y = draw_header(
            d1, W, fonts,
            kicker="Sub 4 Data Story · Slide 1 of 4",
            title="AT WHAT AGE DO RUNNERS",
            subtitle=f"FIRST BREAK 4:00?  ({total_n:,} Verified Debut Ages · 1954–2026)",
        )

        box_w = (W - 152 - 32) // 3
        stats = [
            ("MEDIAN DEBUT AGE", f"{med_y}y {med_d}d", "PEAK WINDOW: 21–23 YRS"),
            ("TEEN PRODIGIES (≤19)", f"{teen_count}", f"{teen_count / total_n * 100:.1f}% OF ALL SUB-4 MILERS"),
            ("30+ LATE BLOOMERS", f"{thirty_count}", "OLDEST DEBUT: 34Y 227D"),
        ]
        for i, (lbl, big, sub) in enumerate(stats):
            bx = 76 + i * (box_w + 16)
            d1.rectangle([bx, y, bx + box_w, y + 148], fill=BG_CARD, outline=INK_BLACK, width=2)
            d1.text((bx + 18, y + 16), lbl, font=fonts["bold_xs"], fill=INK_MUTED)
            d1.text((bx + 18, y + 50), big, font=fonts["mono_lg"], fill=ACCENT_RED if i == 1 else INK_BLACK)
            d1.text((bx + 18, y + 112), sub, font=fonts["mono_xxs"], fill=INK_FADED)

        y += 184

        chart_top = y + 20
        chart_bottom = H - 175
        chart_h = chart_bottom - chart_top
        bar_slot = (W - 168) / len(ages)

        d1.text((76, y - 8), "NUMBER OF RUNNERS BY AGE AT FIRST SUB-4:00 MILE (TEEN SUB-4S IN RED)", font=fonts["bold_xs"], fill=INK_FADED)

        for idx, age in enumerate(ages):
            c = counts.get(age, 0)
            bh = int((c / max_c) * (chart_h - 45))
            bx0 = int(84 + idx * bar_slot + 4)
            bx1 = int(84 + (idx + 1) * bar_slot - 4)
            by1 = chart_bottom
            by0 = by1 - max(bh, 3 if c > 0 else 0)
            bar_color = ACCENT_RED if age <= 19 else INK_BLACK
            if c > 0:
                d1.rectangle([bx0, by0, bx1, by1], fill=bar_color)
                d1.text(((bx0 + bx1) // 2, by0 - 22), str(c), font=fonts["mono_xxs"], fill=bar_color, anchor="ma")
            d1.text(((bx0 + bx1) // 2, by1 + 12), str(age), font=fonts["bold_xs"], fill=INK_BLACK, anchor="ma")

        d1.line([(76, chart_bottom), (W - 76, chart_bottom)], fill=INK_BLACK, width=2)
        d1.text((W // 2, chart_bottom + 44), "AGE IN YEARS ON DAY OF FIRST SUB-4:00 MILE", font=fonts["mono_xs"], fill=INK_MUTED, anchor="ma")
        draw_footer(d1, W, H, fonts, "SWIPE FOR TEEN PRODIGIES & 30+ LATE BLOOMERS ->")
        img1.save(out_dir / "slide_1_age_distribution.png", "PNG")

        # --- SLIDE 2: Youngest Ever (Teen Prodigies) ---
        img2 = make_paper_canvas(W, H)
        d2 = ImageDraw.Draw(img2)
        y = draw_header(
            d2, W, fonts,
            kicker="Sub 4 Data Story · Slide 2 of 4",
            title="THE TEEN PRODIGIES",
            subtitle="The 8 Youngest Runners in History to Break the 4-Minute Mile",
        )
        youngest = sorted(records, key=lambda r: r["age_days"])[:8]
        for idx, r in enumerate(youngest, start=1):
            row_h = 104
            fill_c = BG_CARD if idx % 2 == 1 else BG_CREAM
            d2.rectangle([76, y, W - 76, y + row_h], fill=fill_c, outline=RULE_DARK, width=1)
            d2.rectangle([76, y, 168, y + row_h], fill=ACCENT_RED if idx <= 4 else INK_BLACK)
            d2.text((122, y + 32), f"#{idx}", font=fonts["mono_lg"], fill=BG_CREAM, anchor="ma")

            ig_tag = f" · @{r['ig']}" if r["ig"] else ""
            d2.text((192, y + 16), f"{r['name'].upper()} ({r['country']})", font=fonts["mono_md"], fill=INK_BLACK)
            d2.text(
                (192, y + 60),
                f"{r['date'].strftime('%d %b %Y').upper()} · MARK: {r['mark']}{ig_tag}",
                font=fonts["mono_xs"],
                fill=INK_FADED,
            )
            age_badge = f"{r['age_years']}y {r['age_rem_days']}d"
            d2.text((W - 96, y + 34), age_badge, font=fonts["mono_lg"], fill=ACCENT_RED, anchor="ra")
            y += row_h + 12

        draw_footer(d2, W, H, fonts, "1 RUNNER AT AGE 15 · ONLY 4 BEFORE TURNING 17 ->")
        img2.save(out_dir / "slide_2_teen_prodigies.png", "PNG")

        # --- SLIDE 3: Oldest First-Time Sub-4 Milers (Late Bloomers) ---
        img3 = make_paper_canvas(W, H)
        d3 = ImageDraw.Draw(img3)
        y = draw_header(
            d3, W, fonts,
            kicker="Sub 4 Data Story · Slide 3 of 4",
            title="THE 30+ LATE BLOOMERS",
            subtitle="Oldest Runners in History to Record Their Very First Sub-4:00 Mile",
        )
        oldest = sorted(records, key=lambda r: -r["age_days"])[:8]
        for idx, r in enumerate(oldest, start=1):
            row_h = 104
            fill_c = BG_CARD if idx % 2 == 1 else BG_CREAM
            d3.rectangle([76, y, W - 76, y + row_h], fill=fill_c, outline=RULE_DARK, width=1)
            d3.rectangle([76, y, 168, y + row_h], fill=INK_BLACK)
            d3.text((122, y + 32), f"#{idx}", font=fonts["mono_lg"], fill=BG_CREAM, anchor="ma")

            ig_tag = f" · @{r['ig']}" if r["ig"] else ""
            d3.text((192, y + 16), f"{r['name'].upper()} ({r['country']})", font=fonts["mono_md"], fill=INK_BLACK)
            d3.text(
                (192, y + 60),
                f"{r['date'].strftime('%d %b %Y').upper()} · MARK: {r['mark']}{ig_tag}",
                font=fonts["mono_xs"],
                fill=INK_FADED,
            )
            age_badge = f"{r['age_years']}y {r['age_rem_days']}d"
            d3.text((W - 96, y + 34), age_badge, font=fonts["mono_lg"], fill=ACCENT_RED, anchor="ra")
            y += row_h + 12

        draw_footer(d3, W, H, fonts, f"ONLY {thirty_count} RUNNERS HAVE DEBUTED UNDER 4:00 AT AGE 30 OR OLDER ->")
        img3.save(out_dir / "slide_3_late_bloomers.png", "PNG")

        # --- SLIDE 4: Debut Age by Decade (1950s to 2020s) ---
        img4 = make_paper_canvas(W, H)
        d4 = ImageDraw.Draw(img4)
        y = draw_header(
            d4, W, fonts,
            kicker="Sub 4 Data Story · Slide 4 of 4",
            title="DEBUT AGE BY DECADE",
            subtitle="How First-Time Sub-4 Milers Have Shifted Across 8 Decades (1950s–2020s)",
        )

        decades = [1950, 1960, 1970, 1980, 1990, 2000, 2010, 2020]
        for idx, dec in enumerate(decades):
            dec_recs = [r for r in records if (r["date"].year // 10) * 10 == dec]
            if not dec_recs:
                continue
            avg_age = sum(r["age_exact"] for r in dec_recs) / len(dec_recs)
            teens = sum(1 for r in dec_recs if r["age_years"] <= 19)
            youngest_dec = min(dec_recs, key=lambda r: r["age_days"])

            row_h = 104
            fill_c = BG_CARD if idx % 2 == 0 else BG_CREAM
            d4.rectangle([76, y, W - 76, y + row_h], fill=fill_c, outline=RULE_DARK, width=1)
            d4.rectangle([76, y, 226, y + row_h], fill=INK_BLACK if dec < 2020 else ACCENT_RED)
            d4.text((151, y + 32), f"{dec}s", font=fonts["mono_lg"], fill=BG_CREAM, anchor="ma")

            d4.text(
                (250, y + 16),
                f"{len(dec_recs):,} NEW MILERS · MEAN AGE: {avg_age:.1f} YRS",
                font=fonts["mono_md"],
                fill=INK_BLACK,
            )
            d4.text(
                (250, y + 60),
                f"TEENS (≤19): {teens}  ·  YOUNGEST: {youngest_dec['name'].upper()} ({youngest_dec['age_years']}y {youngest_dec['age_rem_days']}d)",
                font=fonts["mono_xs"],
                fill=INK_FADED,
            )
            y += row_h + 12

        draw_footer(d4, W, H, fonts, "EXPLORE THE FULL INTERACTIVE REGISTER AT SUB4MILERS")
        img4.save(out_dir / "slide_4_age_by_decade.png", "PNG")

    # ------------------------------------------------------------------
    # HELPER: PIPE PIL FRAMES DIRECTLY TO FFMPEG
    # ------------------------------------------------------------------
    def _open_ffmpeg_writer(self, out_path: Path, width: int = 1080, height: int = 1920, fps: int = 30, alpha: bool = False):
        if alpha:
            if out_path.suffix.lower() == ".webm":
                encode_args = [
                    "-c:v", "libvpx-vp9",
                    "-pix_fmt", "yuva420p",
                    "-b:v", "0",
                    "-crf", "30",
                    "-deadline", "realtime",
                    "-cpu-used", "4",
                ]
            else:
                encode_args = [
                    "-c:v", "qtrle",
                    "-pix_fmt", "argb",
                ]
            in_pix = "rgba"
        else:
            encode_args = [
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-crf", "20",
                "-preset", "fast",
                "-movflags", "+faststart",
            ]
            in_pix = "rgb24"

        cmd = [
            "ffmpeg",
            "-y",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-pix_fmt", in_pix,
            "-s", f"{width}x{height}",
            "-r", str(fps),
            "-i", "-",
            *encode_args,
            str(out_path),
        ]
        return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # ------------------------------------------------------------------
    # REEL 1: AGE AT FIRST SUB-4 ANIMATED HISTOGRAM (1954 -> 2026)
    # ------------------------------------------------------------------
    def render_reel_1_age_histogram(self, out_path: Path, fonts: dict, records: list[dict], transparent: bool = False):
        W, H, FPS = 1080, 1920, 30
        if transparent:
            base_canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            y_top = 310
            card_bg = (239, 233, 220, 235)
            track_bg = (24, 23, 21, 24)
        else:
            base_canvas = make_paper_canvas(W, H)
            base_draw = ImageDraw.Draw(base_canvas)
            y_top = draw_header(
                base_draw, W, fonts,
                kicker="Sub 4 Data Animation · Reel 01",
                title="AGE AT FIRST SUB-4:00",
                subtitle="Every Runner's Debut Age Added Chronologically (1954–2026)",
            )
            draw_footer(base_draw, W, H, fonts, f"DATA: {len(records):,} VERIFIED BIRTHDATES · WORLD ATHLETICS")
            card_bg = BG_CARD
            track_bg = BG_CARD

        ages = list(range(15, 35))
        final_counts = Counter(r["age_years"] for r in records)
        global_max = max(final_counts.get(a, 0) for a in ages) or 1

        milestones = [
            (1964, "1964: JIM RYUN (USA) — 17y 37d (3:59.0) · First High School Sub-4"),
            (1966, "1966: TIM DANIELSON & MARTY LIQUORI Join High School Sub-4 List"),
            (2001, "2001: ALAN WEBB (USA) — 18y 13d (3:53.43) · Prefontaine Classic"),
            (2015, "2015: GRANT FISHER & MATTHEW MATON Continue High School Wave"),
            (2017, "2017: JAKOB INGEBRIGTSEN (NOR) — 16y 250d (3:58.07) · Youngest Ever"),
            (2023, "2023: CAMERON MYERS (AUS) — 16y 259d (3:55.44) · #2 Youngest Ever"),
            (2025, "2025: SAM RUTHE (NZL) 15y 341d (3:58.35) Youngest Ever"),
        ]

        total_frames = 12 * FPS
        hold_frames = 3 * FPS
        proc = self._open_ffmpeg_writer(out_path, W, H, FPS, alpha=transparent)

        for f_idx in range(total_frames + hold_frames):
            progress = min(1.0, f_idx / max(1, total_frames - 1))
            n_show = max(1, int(round(progress * len(records))))
            subset = records[:n_show]
            cur_year = subset[-1]["date"].year if subset else 1954
            cur_counts = Counter(r["age_years"] for r in subset)
            teens_so_far = sum(1 for r in subset if r["age_years"] <= 19)

            frame = base_canvas.copy()
            draw = ImageDraw.Draw(frame)

            y = y_top
            draw.rectangle([76, y, W - 76, y + 185], fill=card_bg, outline=INK_BLACK, width=2)
            draw.text((108, y + 22), "YEAR", font=fonts["bold_xs"], fill=INK_MUTED)
            draw.text((108, y + 54), str(cur_year), font=fonts["mono_xxl"], fill=ACCENT_RED)

            draw.text((430, y + 22), "RUNNERS RECORDED", font=fonts["bold_xs"], fill=INK_MUTED)
            draw.text((430, y + 60), f"{len(subset):,}", font=fonts["mono_xl"], fill=INK_BLACK)

            draw.text((760, y + 22), "TEENS (≤19)", font=fonts["bold_xs"], fill=INK_MUTED)
            draw.text((760, y + 60), f"{teens_so_far}", font=fonts["mono_xl"], fill=ACCENT_RED)

            y += 220

            row_h = 52
            label_x = 150
            bar_x0 = 175
            bar_max_w = W - bar_x0 - 150

            draw.text((76, y - 6), "AGE", font=fonts["bold_xs"], fill=INK_MUTED)
            draw.text((bar_x0, y - 6), "FIRST-TIME SUB-4 MILERS BY EXACT AGE IN YEARS", font=fonts["bold_xs"], fill=INK_MUTED)
            y += 30

            for age in ages:
                c = cur_counts.get(age, 0)
                bw = int((c / global_max) * bar_max_w)
                is_teen = age <= 19
                bar_col = ACCENT_RED if is_teen else INK_BLACK

                draw.text((label_x, y + 10), f"{age} YRS", font=fonts["bold_xs"], fill=bar_col, anchor="ra")
                draw.rectangle([bar_x0, y + 8, bar_x0 + bar_max_w, y + row_h - 12], fill=track_bg)
                if bw > 0:
                    draw.rectangle([bar_x0, y + 8, bar_x0 + max(bw, 6), y + row_h - 12], fill=bar_col)
                if c > 0:
                    draw.text((bar_x0 + max(bw, 6) + 14, y + 10), str(c), font=fonts["bold_xs"], fill=bar_col)
                y += row_h

            active_m = "1954: ROGER BANNISTER (GBR) — 25y 44d (3:59.4) · First Sub-4 Mile"
            for m_yr, m_txt in milestones:
                if cur_year >= m_yr:
                    active_m = m_txt

            box_y = H - 255
            draw.rectangle([76, box_y, W - 76, box_y + 130], fill=card_bg, outline=ACCENT_RED, width=3)
            draw.text((104, box_y + 20), "CHRONICLE MILESTONE DISPATCH", font=fonts["bold_xs"], fill=ACCENT_RED)
            draw.text((104, box_y + 62), active_m[:54], font=fonts["mono_sm"], fill=INK_BLACK)

            proc.stdin.write(frame.tobytes())

        proc.stdin.close()
        proc.wait()

    # ------------------------------------------------------------------
    # REEL 2: SUB-4 MILERS BY COUNTRY BAR CHART RACE (1954 -> 2026)
    # ------------------------------------------------------------------
    def render_reel_2_country_race(self, out_path: Path, fonts: dict, chron_athletes: list[dict], transparent: bool = False):
        W, H, FPS = 1080, 1920, 30
        if transparent:
            base_canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            y_top = 310
            card_bg = (239, 233, 220, 235)
        else:
            base_canvas = make_paper_canvas(W, H)
            base_draw = ImageDraw.Draw(base_canvas)
            y_top = draw_header(
                base_draw, W, fonts,
                kicker="Sub 4 Data Animation · Reel 02",
                title="SUB-4 MILERS BY NATION",
                subtitle="Cumulative Sub-4:00 Mile Runners by Country (1954–2026)",
            )
            draw_footer(base_draw, W, H, fonts, f"SOURCE: SUB 4 REGISTER · {len(chron_athletes):,} ATHLETES WORLDWIDE")
            card_bg = BG_CARD

        years = list(range(1954, 2027))
        yearly_counts = {}
        yearly_total = {}
        running = defaultdict(int)
        idx = 0
        n_ath = len(chron_athletes)
        for yr in years:
            while idx < n_ath and chron_athletes[idx]["date"].year <= yr:
                c = chron_athletes[idx]["country"]
                if c and c != "---":
                    running[c] += 1
                idx += 1
            yearly_counts[yr] = dict(running)
            yearly_total[yr] = idx

        total_frames = 20 * FPS
        hold_frames = 3 * FPS
        proc = self._open_ffmpeg_writer(out_path, W, H, FPS, alpha=transparent)

        smooth_rank = {}

        for f_idx in range(total_frames + hold_frames):
            progress = min(1.0, f_idx / max(1, total_frames - 1))
            yr_float = 1954 + progress * (2026 - 1954)
            yr_lo = int(math.floor(yr_float))
            yr_hi = min(2026, yr_lo + 1)
            alpha = yr_float - yr_lo

            c_lo = yearly_counts[yr_lo]
            c_hi = yearly_counts[yr_hi]
            all_countries = set(c_lo.keys()) | set(c_hi.keys())
            interp = {
                c: (1.0 - alpha) * c_lo.get(c, 0) + alpha * c_hi.get(c, 0)
                for c in all_countries
            }
            ranked_all = sorted(interp.items(), key=lambda kv: (-kv[1], kv[0]))
            target_rank_map = {code: r_idx for r_idx, (code, _) in enumerate(ranked_all)}

            for code, r_idx in target_rank_map.items():
                if code not in smooth_rank:
                    smooth_rank[code] = float(min(r_idx, 12))
                else:
                    smooth_rank[code] += (r_idx - smooth_rank[code]) * 0.18

            top12 = ranked_all[:12]
            max_val = max((v for _, v in top12), default=1.0) or 1.0
            tot_runners = int(round((1.0 - alpha) * yearly_total[yr_lo] + alpha * yearly_total[yr_hi]))

            frame = base_canvas.copy()
            draw = ImageDraw.Draw(frame)

            y = y_top
            draw.rectangle([76, y, W - 76, y + 185], fill=card_bg, outline=INK_BLACK, width=2)
            draw.text((108, y + 22), "SEASON", font=fonts["bold_xs"], fill=INK_MUTED)
            draw.text((108, y + 54), str(yr_lo), font=fonts["mono_xxl"], fill=ACCENT_RED)
            draw.text((450, y + 22), "TOTAL SUB-4 MILERS WORLDWIDE", font=fonts["bold_xs"], fill=INK_MUTED)
            draw.text((450, y + 60), f"{tot_runners:,}", font=fonts["mono_xl"], fill=INK_BLACK)
            draw.text((450, y + 132), f"NATIONS REPRESENTED: {len(all_countries)}", font=fonts["mono_xs"], fill=INK_FADED)

            y += 230
            row_h = 88
            bar_x0 = 215
            bar_max_w = W - bar_x0 - 165

            # Fixed rank slot labels on the left
            for slot_idx in range(1, 13):
                slot_y = y + (slot_idx - 1) * row_h
                draw.text((92, slot_y + 18), f"#{slot_idx:02d}", font=fonts["mono_xs"], fill=INK_MUTED)

            # Draw visible countries smoothly gliding to their target rank slot
            visible = [
                (code, interp[code], smooth_rank[code], target_rank_map[code])
                for code in interp
                if smooth_rank.get(code, 99.0) <= 11.6
            ]
            # Draw lower ranks first so #1 leader glides cleanly on top during overtakes
            visible.sort(key=lambda item: -item[2])

            for code, val, s_rank, t_rank in visible:
                ry = int(round(y + s_rank * row_h))
                bw = int((val / max_val) * bar_max_w)
                bar_col = ACCENT_RED if t_rank == 0 else INK_BLACK

                draw.text((148, ry + 14), code, font=fonts["mono_md"], fill=INK_BLACK)
                draw.rectangle([bar_x0, ry + 12, bar_x0 + max(bw, 8), ry + row_h - 18], fill=bar_col)
                draw.text((bar_x0 + max(bw, 8) + 16, ry + 16), f"{int(round(val)):,}", font=fonts["mono_md"], fill=bar_col)

            proc.stdin.write(frame.tobytes())

        proc.stdin.close()
        proc.wait()

    # ------------------------------------------------------------------
    # REEL 3: THE SUB-4 EXPLOSION — RUNS PER YEAR (1954 -> 2025)
    # ------------------------------------------------------------------
    def render_reel_3_annual_surge(self, out_path: Path, fonts: dict, transparent: bool = False):
        W, H, FPS = 1080, 1920, 30
        if transparent:
            base_canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            y_top = 310
            card_bg = (239, 233, 220, 235)
        else:
            base_canvas = make_paper_canvas(W, H)
            base_draw = ImageDraw.Draw(base_canvas)
            y_top = draw_header(
                base_draw, W, fonts,
                kicker="Sub 4 Data Animation · Reel 03",
                title="THE SUB-4 EXPLOSION",
                subtitle="Total Sub-4:00 Mile Performances Recorded Each Year (1954–2025)",
            )
            draw_footer(base_draw, W, H, fonts, "7,774 TOTAL SUB-4:00 MILES LOGGED IN THE ARCHIVE")
            card_bg = BG_CARD

        runs_dates = list(Performance.objects.exclude(date__isnull=True).values_list("date", flat=True))
        by_year = Counter(d.year for d in runs_dates if 1954 <= d.year <= 2025)
        years = list(range(1954, 2026))
        max_yr_count = max((by_year.get(yr, 0) for yr in years), default=1) or 1

        era_notes = [
            (1954, "1954: Bannister & Landy break 4:00 on cinder tracks (2 total runs)"),
            (1967, "1967: Jim Ryun lowers the World Record to 3:51.1 in Bakersfield"),
            (1979, "1979: Sebastian Coe & Steve Ovett ignite the Golden British Mile Era"),
            (1999, "1999: Hicham El Guerrouj runs 3:43.13 in Rome — Still the World Record"),
            (2019, "2019: NCAA indoor & track revolution pushes annual sub-4s past 300/yr"),
            (2023, "2023–2025: Super-spikes + fast indoor tracks trigger 600+ sub-4s/year"),
        ]

        total_frames = 11 * FPS
        hold_frames = 3 * FPS
        proc = self._open_ffmpeg_writer(out_path, W, H, FPS, alpha=transparent)

        for f_idx in range(total_frames + hold_frames):
            progress = min(1.0, f_idx / max(1, total_frames - 1))
            n_years = max(1, int(round(progress * len(years))))
            active_years = years[:n_years]
            cur_yr = active_years[-1]
            cur_val = by_year.get(cur_yr, 0)
            cum_runs = sum(by_year.get(y, 0) for y in active_years)

            frame = base_canvas.copy()
            draw = ImageDraw.Draw(frame)

            y = y_top
            draw.rectangle([76, y, W - 76, y + 185], fill=card_bg, outline=INK_BLACK, width=2)
            draw.text((108, y + 22), "YEAR", font=fonts["bold_xs"], fill=INK_MUTED)
            draw.text((108, y + 54), str(cur_yr), font=fonts["mono_xxl"], fill=ACCENT_RED)

            draw.text((410, y + 22), "SUB-4 RUNS THAT YEAR", font=fonts["bold_xs"], fill=INK_MUTED)
            draw.text((410, y + 60), f"{cur_val:,}", font=fonts["mono_xl"], fill=INK_BLACK)

            draw.text((770, y + 22), "ALL-TIME TOTAL", font=fonts["bold_xs"], fill=INK_MUTED)
            draw.text((770, y + 60), f"{cum_runs:,}", font=fonts["mono_xl"], fill=ACCENT_RED)

            chart_top = y + 245
            chart_bottom = H - 310
            chart_h = chart_bottom - chart_top
            slot_w = (W - 168) / len(years)

            for idx, yr in enumerate(years):
                bx0 = int(84 + idx * slot_w)
                bx1 = max(bx0 + 4, int(84 + (idx + 1) * slot_w - 2))
                if yr <= cur_yr:
                    c = by_year.get(yr, 0)
                    bh = int((c / max_yr_count) * chart_h)
                    col = ACCENT_RED if yr >= 2022 or yr == cur_yr else INK_BLACK
                    draw.rectangle([bx0, chart_bottom - max(bh, 3), bx1, chart_bottom], fill=col)

                if yr % 10 == 0 or yr == 1954:
                    draw.text((bx0, chart_bottom + 14), str(yr), font=fonts["mono_xxs"], fill=INK_FADED)

            draw.line([(76, chart_bottom), (W - 76, chart_bottom)], fill=INK_BLACK, width=3)

            note_txt = era_notes[0][1]
            for e_yr, e_txt in era_notes:
                if cur_yr >= e_yr:
                    note_txt = e_txt

            box_y = H - 245
            draw.rectangle([76, box_y, W - 76, box_y + 125], fill=card_bg, outline=ACCENT_RED, width=3)
            draw.text((104, box_y + 18), "HISTORICAL CONTEXT", font=fonts["bold_xs"], fill=ACCENT_RED)
            draw.text((104, box_y + 58), note_txt[:55], font=fonts["mono_sm"], fill=INK_BLACK)

            proc.stdin.write(frame.tobytes())

        proc.stdin.close()
        proc.wait()

    # ------------------------------------------------------------------
    # REEL 4: HOW FAST IS A RUNNER'S FIRST SUB-4? (3:59.99 -> 3:49.00)
    # ------------------------------------------------------------------
    def render_reel_4_first_sub4_stopwatch(self, out_path: Path, fonts: dict, chron_athletes: list[dict], transparent: bool = False):
        W, H, FPS = 1080, 1920, 30
        if transparent:
            base_canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            y_top = 310
            card_bg = (239, 233, 220, 235)
            track_bg = (24, 23, 21, 24)
        else:
            base_canvas = make_paper_canvas(W, H)
            base_draw = ImageDraw.Draw(base_canvas)
            y_top = draw_header(
                base_draw, W, fonts,
                kicker="Sub 4 Data Animation · Reel 04",
                title="HOW FAST IS A FIRST SUB-4?",
                subtitle="Stopwatch Distribution of Every Runner's First Sub-4:00 Mile",
            )
            draw_footer(base_draw, W, H, fonts, "OVER HALF OF ALL MILERS BREAK 4:00 BY LESS THAN 1 SECOND")
            card_bg = BG_CARD
            track_bg = BG_CARD

        debut_marks = [
            (r["sec"], r["name"], r["country"], r["mark"], r["date"].year)
            for r in chron_athletes
            if 225.0 <= r["sec"] < 240.0
        ]

        buckets = [
            ("3:59.50 – 3:59.99", 239.50, 240.00),
            ("3:59.00 – 3:59.49", 239.00, 239.50),
            ("3:58.50 – 3:58.99", 238.50, 239.00),
            ("3:58.00 – 3:58.49", 238.00, 238.50),
            ("3:57.00 – 3:57.99", 237.00, 238.00),
            ("3:56.00 – 3:56.99", 236.00, 237.00),
            ("3:55.00 – 3:55.99", 235.00, 236.00),
            ("3:54.00 – 3:54.99", 234.00, 235.00),
            ("3:52.00 – 3:53.99", 232.00, 234.00),
            ("SUB-3:52.00 DEBUT", 220.00, 232.00),
        ]
        max_b = max(
            sum(1 for s, *_ in debut_marks if lo <= s < hi)
            for _, lo, hi in buckets
        ) or 1
        total_debuts = len(debut_marks) or 1

        total_frames = 10 * FPS
        hold_frames = 3 * FPS
        proc = self._open_ffmpeg_writer(out_path, W, H, FPS, alpha=transparent)

        for f_idx in range(total_frames + hold_frames):
            progress = min(1.0, f_idx / max(1, total_frames - 1))
            cur_sec = 239.99 - progress * (239.99 - 229.00)
            revealed = [m for m in debut_marks if m[0] >= cur_sec]
            in_359 = sum(1 for m in revealed if m[0] >= 239.00)

            frame = base_canvas.copy()
            draw = ImageDraw.Draw(frame)

            y = y_top
            draw.rectangle([76, y, W - 76, y + 195], fill=card_bg, outline=ACCENT_RED, width=3)
            draw.text((108, y + 22), "STOPWATCH SWEEP", font=fonts["bold_xs"], fill=ACCENT_RED)
            draw.text((108, y + 56), format_seconds(cur_sec), font=fonts["mono_xxl"], fill=INK_BLACK)

            pct = len(revealed) / total_debuts * 100
            draw.text((540, y + 22), "DEBUTS SLOWER THAN CLOCK", font=fonts["bold_xs"], fill=INK_MUTED)
            draw.text((540, y + 62), f"{len(revealed):,} ({pct:.0f}%)", font=fonts["mono_xl"], fill=ACCENT_RED)
            draw.text((540, y + 138), f"3:59.00–3:59.99 ZONE: {in_359:,} RUNNERS", font=fonts["mono_xs"], fill=INK_FADED)

            y += 245
            row_h = 98
            bar_x0 = 385
            bar_max_w = W - bar_x0 - 155

            for label, lo, hi in buckets:
                c = sum(1 for s, *_ in revealed if lo <= s < hi)
                bw = int((c / max_b) * bar_max_w)
                is_359 = lo >= 239.0
                col = ACCENT_RED if is_359 else INK_BLACK

                draw.text((84, y + 22), label, font=fonts["mono_sm"], fill=col)
                draw.rectangle([bar_x0, y + 14, bar_x0 + bar_max_w, y + row_h - 24], fill=track_bg)
                if bw > 0:
                    draw.rectangle([bar_x0, y + 14, bar_x0 + max(bw, 6), y + row_h - 24], fill=col)
                if c > 0:
                    draw.text((bar_x0 + max(bw, 6) + 14, y + 22), f"{c:,}", font=fonts["mono_sm"], fill=col)
                y += row_h

            proc.stdin.write(frame.tobytes())

        proc.stdin.close()
        proc.wait()
