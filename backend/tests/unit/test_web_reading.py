"""Reading a business's website (Feature 006, TASK-603): the fetcher's network
rules and the crawler, without leaving the process."""

from __future__ import annotations

import httpx
import pytest

from app.providers.web import (
    BlockedAddressError,
    HttpxWebFetcher,
    MockWebFetcher,
    WebFetchError,
)
from app.services.web_crawl import EMPTY_SITE, InvalidSiteUrlError, crawl, normalise_start

HTML = "text/html"


def page(title: str, body: str) -> str:
    return f"<html><head><title>{title}</title></head><body>{body}</body></html>"


class _Resolving(HttpxWebFetcher):
    """The real fetcher with DNS answered from a table."""

    def __init__(self, table: dict[str, list[str]], handler: object) -> None:
        super().__init__(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
        self.table = table

    async def _addresses(self, host: str) -> list[str]:
        return self.table.get(host, [])


class TestTheFetcherOnlyReachesThePublicInternet:
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/",
            "http://localhost/",
            "http://10.0.0.5/admin",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/",
            "http://interno.example/",  # resolves to a private address below
        ],
    )
    async def test_non_public_addresses_are_refused_before_connecting(self, url: str) -> None:
        sent: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(str(request.url))
            return httpx.Response(200, text="secreto")

        fetcher = _Resolving(
            {
                "interno.example": ["192.168.1.10"],
                "localhost": ["127.0.0.1"],
                "127.0.0.1": ["127.0.0.1"],
                "10.0.0.5": ["10.0.0.5"],
                "169.254.169.254": ["169.254.169.254"],
                "::1": ["::1"],
            },
            handler,
        )

        with pytest.raises(BlockedAddressError):
            await fetcher.fetch(url)
        assert sent == []

    async def test_a_redirect_inward_is_refused(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "http://10.0.0.5/"})

        fetcher = _Resolving({"tienda.pe": ["200.48.225.130"], "10.0.0.5": ["10.0.0.5"]}, handler)

        with pytest.raises(BlockedAddressError):
            await fetcher.fetch("https://tienda.pe/")

    @pytest.mark.parametrize("url", ["ftp://tienda.pe/", "https://tienda.pe:8443/"])
    async def test_other_schemes_and_ports_are_refused(self, url: str) -> None:
        fetcher = _Resolving({"tienda.pe": ["200.48.225.130"]}, lambda r: httpx.Response(200))
        with pytest.raises(BlockedAddressError):
            await fetcher.fetch(url)

    async def test_a_public_page_is_read_after_redirects(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/":
                return httpx.Response(301, headers={"location": "/inicio"})
            assert "LeoBot" in request.headers["user-agent"]
            return httpx.Response(
                200, headers={"content-type": "text/html; charset=utf-8"}, text="<p>Hola</p>"
            )

        fetcher = _Resolving({"tienda.pe": ["200.48.225.130"]}, handler)

        fetched = await fetcher.fetch("https://tienda.pe/")

        assert fetched.url == "https://tienda.pe/inicio"
        assert fetched.content_type == "text/html" and fetched.body == b"<p>Hola</p>"

    async def test_a_page_over_the_byte_cap_is_dropped(self) -> None:
        fetcher = _Resolving(
            {"tienda.pe": ["200.48.225.130"]}, lambda r: httpx.Response(200, content=b"x" * 5000)
        )
        fetcher._max_bytes = 1000

        with pytest.raises(WebFetchError):
            await fetcher.fetch("https://tienda.pe/")

    async def test_an_unknown_domain_is_an_error_not_a_crash(self) -> None:
        fetcher = _Resolving({}, lambda r: httpx.Response(200))
        with pytest.raises(WebFetchError):
            await fetcher.fetch("https://no-existe.pe/")


class TestTheStartAddress:
    @pytest.mark.parametrize(
        ("typed", "expected"),
        [
            ("tienda.pe", "https://tienda.pe/"),
            ("  https://WWW.Tienda.pe/carta#arriba ", "https://www.tienda.pe/carta"),
            ("http://tienda.pe/?p=1", "http://tienda.pe/?p=1"),
        ],
    )
    def test_it_is_normalised(self, typed: str, expected: str) -> None:
        assert normalise_start(typed) == expected

    @pytest.mark.parametrize(
        "typed", ["", "ftp://tienda.pe", "localhost", "http://10.0.0.5", "https://a:b@tienda.pe"]
    )
    def test_what_is_not_a_website_is_refused(self, typed: str) -> None:
        with pytest.raises(InvalidSiteUrlError):
            normalise_start(typed)


SITE = {
    "https://tienda.pe/robots.txt": (200, "text/plain", "User-agent: *\nDisallow: /privado\n"),
    "https://tienda.pe/": (
        200,
        HTML,
        page(
            "Tienda Leo",
            "<nav><a href='/envios'>Envíos</a> <a href='/privado'>Admin</a></nav>"
            "<script>var x = 'no es texto';</script><style>p{color:red}</style>"
            "<h1>Bienvenidos</h1><p>Vendemos mochilas escolares.</p>"
            "<a href='/envios#costos'>Ver envíos</a> <a href='/catalogo.pdf'>Catálogo</a>"
            "<a href='https://www.tienda.pe/contacto'>Contacto</a>"
            "<a href='https://otra-tienda.pe/'>Amigos</a>"
            "<footer><p>Av. Larco 123, Miraflores</p></footer>",
        ),
    ),
    "https://tienda.pe/envios": (
        200,
        HTML,
        page(
            "Envíos",
            "<h2>Envíos</h2><p>Enviamos a todo Lima en 24 horas.</p>"
            "<footer><p>Av. Larco 123, Miraflores</p></footer>",
        ),
    ),
    "https://www.tienda.pe/contacto": (
        200,
        HTML,
        page(
            "Contacto",
            "<p>Escríbenos al 999 888 777.</p><footer><p>Av. Larco 123, Miraflores</p></footer>",
        ),
    ),
    "https://tienda.pe/privado": (200, HTML, page("Admin", "<p>No debería leerse.</p>")),
}


class TestTheCrawler:
    async def test_it_reads_the_site_and_only_the_site(self) -> None:
        fetcher = MockWebFetcher(SITE)

        result = await crawl(fetcher, "tienda.pe", max_pages=20)

        text = result.text()
        assert [p.url for p in result.pages] == [
            "https://tienda.pe/",
            "https://tienda.pe/envios",
            "https://www.tienda.pe/contacto",
        ]
        assert "Vendemos mochilas escolares." in text and "24 horas" in text
        assert "no es texto" not in text and "color:red" not in text
        assert "No debería leerse" not in text
        assert {"url": "https://tienda.pe/privado", "motivo": "robots.txt"} in result.skipped
        assert "https://otra-tienda.pe/" not in fetcher.calls
        assert "https://tienda.pe/catalogo.pdf" not in fetcher.calls
        # The footer on every page is kept once.
        assert text.count("Av. Larco 123") == 1
        assert result.report()["web"]["robots_txt"] is True

    async def test_the_page_limit_holds(self) -> None:
        links = "".join(f"<a href='/p{n}'>p{n}</a>" for n in range(50))
        pages = {"https://tienda.pe/": (200, HTML, page("Inicio", f"<p>Inicio</p>{links}"))}
        pages |= {
            f"https://tienda.pe/p{n}": (200, HTML, page(f"P{n}", f"<p>Página {n}</p>"))
            for n in range(50)
        }
        fetcher = MockWebFetcher(pages)

        result = await crawl(fetcher, "https://tienda.pe/", max_pages=5)

        assert len(result.pages) == 5
        assert len([c for c in fetcher.calls if not c.endswith("robots.txt")]) == 5

    async def test_a_robots_file_that_cannot_be_read_means_no(self) -> None:
        fetcher = MockWebFetcher(
            {
                "https://tienda.pe/robots.txt": (503, "text/plain", ""),
                "https://tienda.pe/": SITE["https://tienda.pe/"],
            }
        )

        result = await crawl(fetcher, "https://tienda.pe/", max_pages=20)

        assert result.pages == [] and result.skipped[0]["motivo"] == "robots.txt"
        assert result.error is not None and "robots.txt" in result.error

    async def test_an_unreachable_site_says_so(self) -> None:
        fetcher = MockWebFetcher({"https://tienda.pe/robots.txt": (-1, "", "no existe el dominio")})

        result = await crawl(fetcher, "https://tienda.pe/", max_pages=20)

        assert result.error == "No pudimos abrir tu web: no existe el dominio. Revisa la dirección."

    async def test_a_start_page_that_answers_404_says_so(self) -> None:
        result = await crawl(MockWebFetcher({}), "https://tienda.pe/", max_pages=20)

        assert result.error == "No pudimos leer tu web: respondió 404."

    async def test_a_javascript_site_reads_as_empty(self) -> None:
        fetcher = MockWebFetcher(
            {
                "https://tienda.pe/": (
                    200,
                    HTML,
                    page("App", "<div id='root'></div><script>app()</script>"),
                )
            }
        )

        result = await crawl(fetcher, "https://tienda.pe/", max_pages=20)

        assert result.text() == "" and result.error is None
        assert EMPTY_SITE.startswith("No pudimos leer tu web")

    async def test_noindex_pages_and_non_html_are_skipped(self) -> None:
        fetcher = MockWebFetcher(
            {
                "https://tienda.pe/": (
                    200,
                    HTML,
                    page(
                        "Inicio", "<p>Hola a todos</p><a href='/oculta'>x</a><a href='/feed'>f</a>"
                    ),
                ),
                "https://tienda.pe/oculta": (
                    200,
                    HTML,
                    "<html><head><meta name='robots' content='noindex'></head>"
                    "<body><p>Oculta</p></body></html>",
                ),
                "https://tienda.pe/feed": (200, "application/json", "{}"),
            }
        )

        result = await crawl(fetcher, "https://tienda.pe/", max_pages=20)

        assert [p.url for p in result.pages] == ["https://tienda.pe/"]
        reasons = {s["url"]: s["motivo"] for s in result.skipped}
        assert reasons["https://tienda.pe/oculta"] == "la página pide no indexarse"
        assert reasons["https://tienda.pe/feed"] == "no es una página"
