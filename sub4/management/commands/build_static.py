import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.test import Client

ROUTES = [
    ("/", "index.html", 0, ""),
    ("/fastest/", "fastest/index.html", 1, "fastest"),
    ("/mostrecent/", "mostrecent/index.html", 1, "mostrecent"),
    ("/mostSub4/", "mostSub4/index.html", 1, "mostSub4"),
    ("/analytics/", "analytics/index.html", 1, "analytics"),
]

SUBPAGES = ["fastest", "mostrecent", "mostSub4", "analytics"]


def rewrite_relative_links(html, depth, current_slug):
    """
    Convert root-relative Django URLs (/fastest/, /static/...) into relative URLs
    matching the existing GitHub Pages structure in docs/ and build/.
    """
    prefix = "../" * depth

    # Rewrite nav/page links
    html = html.replace('href="/"', f'href="{prefix}index.html"')
    for slug in SUBPAGES:
        if depth == 1 and slug == current_slug:
            html = html.replace(f'href="/{slug}/"', 'href="index.html"')
        else:
            html = html.replace(f'href="/{slug}/"', f'href="{prefix}{slug}/index.html"')

    # Rewrite static asset links in href/src/fetch attributes
    html = html.replace('href="/static/', f'href="{prefix}static/')
    html = html.replace('src="/static/', f'src="{prefix}static/')
    html = html.replace('fetch("/static/', f'fetch("{prefix}static/')

    return html


class Command(BaseCommand):
    help = "Render all site pages to static HTML in docs/ (and build/) without needing runserver or wget."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-dirs",
            nargs="+",
            default=["docs", "build"],
            help="Target directories to write rendered static HTML to (default: docs build)",
        )

    def handle(self, *args, **options):
        base_dir = Path(settings.BASE_DIR)
        output_dirs = [base_dir / d for d in options["output_dirs"]]

        client = Client()
        rendered_pages = {}

        for url_path, rel_dest, depth, slug in ROUTES:
            self.stdout.write(f"Rendering {url_path} -> {rel_dest}...")
            response = client.get(url_path, HTTP_HOST="localhost")
            if response.status_code != 200:
                raise CommandError(
                    f"Failed to render {url_path}: HTTP {response.status_code}"
                )
            html = response.content.decode("utf-8")
            html = rewrite_relative_links(html, depth=depth, current_slug=slug)
            rendered_pages[rel_dest] = html

        # Write rendered HTML files to each output directory
        for out_dir in output_dirs:
            out_dir.mkdir(parents=True, exist_ok=True)
            for rel_dest, html in rendered_pages.items():
                dest_file = out_dir / rel_dest
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                dest_file.write_text(html, encoding="utf-8")

        # Ensure countries.geojson is up to date in docs/static/data/
        src_static = base_dir / "static"
        docs_dir = base_dir / "docs"
        if docs_dir in output_dirs and src_static.exists():
            geojson_src = src_static / "data" / "countries.geojson"
            if geojson_src.exists():
                geojson_dst = docs_dir / "static" / "data" / "countries.geojson"
                geojson_dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(geojson_src, geojson_dst)

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully built {len(rendered_pages)} static pages into: "
                + ", ".join(str(d.relative_to(base_dir)) for d in output_dirs)
            )
        )

