import asyncio
import gzip
import io
import logging
from email.message import Message
from pathlib import Path
from urllib.parse import urlsplit, unquote
import scrapy
from scrapy.utils.defer import maybe_deferred_to_future
from .config import settings
from .db import transaction
from .models import Job, Task, Document
from .repository import (
    claim_tasks,
    locked_job,
    record_failure,
    record_document,
    enqueue,
    LostLease,
    finish_job,
)
from .storage import classify, store_original, sha
from .urls import Policy, public_address, PolicyError
from .adapters import html_links, xml_links
from sqlalchemy import select, func

log = logging.getLogger(__name__)

# Every status is handed back to fetch_task except the redirects, which must
# reach RedirectMiddleware. "handle_httpstatus_all" also disabled redirects:
# any http->https, www or download-link redirect then failed as an empty
# response. A followed redirect is a new request, so ScopeMiddleware rechecks
# host, public address and robots for every hop before it is fetched.
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
HANDLED_STATUSES = [status for status in range(100, 600) if status not in REDIRECT_STATUSES]


class ScopeMiddleware:
    def __init__(self, crawler):
        self.crawler = crawler

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)

    async def process_request(self, request, spider=None):
        spider = spider or self.crawler.spider
        delay = await asyncio.to_thread(spider.policy.check, request.url)
        if delay:
            await asyncio.sleep(delay)


class CollectorSpider(scrapy.Spider):
    name = "collector"

    def __init__(self, job_id, owner, **kwargs):
        super().__init__(**kwargs)
        self.job_id, self.owner = job_id, owner
        with transaction() as db:
            job = locked_job(db, job_id, owner)
            self.rules = job.policy
            self.source_id = job.source_id
        self.policy = Policy(self.rules["hosts"], self.rules["respect_robots"])

    async def abort_browser(self, request):
        try:
            await asyncio.to_thread(public_address, request.url)
            if request.is_navigation_request():
                await asyncio.to_thread(self.policy.check, request.url)
            return request.resource_type in ("image", "font", "media")
        except Exception:
            return True

    async def start(self):
        try:
            while True:
                tasks = claim_tasks(self.job_id, self.owner, settings.concurrency)
                if tasks:
                    await asyncio.gather(*(self.fetch_task(task) for task in tasks))
                    continue
                with transaction() as db:
                    job = locked_job(db, self.job_id, self.owner)
                    pending = db.scalar(
                        select(func.count())
                        .select_from(Task)
                        .where(Task.job_id == job.id, Task.status == "pending")
                    )
                    if job.status == "pausing" or not pending:
                        break
                await asyncio.sleep(1)
            finish_job(self.job_id, self.owner)
        except LostLease:
            log.warning("Collecte remplacée par un autre worker.")
        except Exception as exc:
            log.exception("Échec du moteur")
            try:
                finish_job(self.job_id, self.owner, str(exc))
            except LostLease:
                pass
        if False:
            yield scrapy.Request("https://example.com")

    async def download(self, url, browser=False, referer="", headers=None):
        meta = {
            "handle_httpstatus_list": HANDLED_STATUSES,
            "download_maxsize": settings.max_bytes,
            "playwright": browser,
        }
        if browser:
            meta.update(
                playwright_context=self.source_id,
                playwright_context_kwargs={
                    "user_agent": settings.user_agent,
                    "service_workers": "block",
                    "accept_downloads": True,
                },
                playwright_page_goto_kwargs={
                    "wait_until": "domcontentloaded",
                    "timeout": 45000,
                },
            )
            from scrapy_playwright.page import PageMethod

            meta["playwright_page_methods"] = [PageMethod("wait_for_timeout", 1500)]
        request = scrapy.Request(
            url,
            headers={**(headers or {}), **({"Referer": referer} if referer else {})},
            meta=meta,
            dont_filter=True,
        )
        engine = self.crawler.engine
        if hasattr(engine, "download_async"):
            return await engine.download_async(request)
        return await maybe_deferred_to_future(engine.download(request))

    async def fetch_task(self, task):
        browser = self.rules["mode"] == "browser"
        try:
            with transaction() as db:
                job = locked_job(db, self.job_id, self.owner)
                db.get(Task, task["id"]).method = "browser" if browser else "http"
                previous = db.scalar(
                    select(Document).where(
                        Document.source_id == self.source_id,
                        Document.url_hash == sha(task["url"].encode()),
                    )
                )
                headers = {}
                if previous:
                    if previous.etag:
                        headers["If-None-Match"] = previous.etag
                    if previous.last_modified:
                        headers["If-Modified-Since"] = previous.last_modified
            response = await self.download(
                task["url"], browser, task["parent_url"], headers if not browser else {}
            )
            if self.rules["mode"] == "auto" and response.status in (403, 406):
                browser = True
                response = await self.download(task["url"], True, task["parent_url"])
            if response.status == 304:
                with transaction() as db:
                    locked_job(db, self.job_id, self.owner)
                    doc = db.scalar(
                        select(Document).where(
                            Document.source_id == self.source_id,
                            Document.url_hash == sha(task["url"].encode()),
                        )
                    )
                    if not doc or not doc.current_version:
                        raise ValueError("Réponse 304 sans original connu.")
                    row = db.get(Task, task["id"])
                    row.status = "unchanged"
                    row.http_status = 304
                    row.document_id = doc.id
                    row.error = ""
                    # Re-parcourt les liens du HTML conservé pour ne pas perdre la couverture.
                    if doc.kind == "html":
                        from .models import Version
                        from .storage import resolve

                        version = db.get(Version, doc.current_version)
                        _, links = html_links(
                            resolve(version.blob_path).read_bytes(), doc.final_url
                        )
                        enqueue(
                            db,
                            db.get(Job, self.job_id),
                            links,
                            task["depth"] + 1,
                            doc.final_url,
                        )
                return
            if response.status == 404 and urlsplit(task["url"]).path in (
                "/sitemap.xml",
                "/sitemap_index.xml",
                "/rss.xml",
            ):
                with transaction() as db:
                    locked_job(db, self.job_id, self.owner)
                    row = db.get(Task, task["id"])
                    row.status = "skipped"
                    row.error = "Index facultatif absent"
                    row.http_status = 404
                return
            if response.status >= 400:
                record_failure(
                    self.job_id,
                    self.owner,
                    task["id"],
                    f"HTTP {response.status}",
                    response.status,
                    retry=response.status in (408, 425, 429, 500, 502, 503, 504),
                )
                return
            await asyncio.to_thread(self.policy.check, response.url)
            body = response.body
            disposition = Message()
            disposition["content-disposition"] = response.headers.get(
                b"Content-Disposition", b""
            ).decode("latin-1")
            suggested = (
                response.meta.get("playwright_suggested_filename", "")
                or disposition.get_filename()
                or ""
            )
            mime = response.headers.get(b"Content-Type", b"").decode("latin-1")
            kind, mime = classify(body, mime, response.url, suggested)
            title = (
                suggested
                or unquote(Path(urlsplit(response.url).path).name)
                or response.url
            )
            links = []
            if kind == "html":
                title, links = html_links(body, response.url)
                if self.rules["mode"] == "auto" and not browser:
                    from bs4 import BeautifulSoup

                    text = BeautifulSoup(body, "html.parser").get_text(" ", strip=True)
                    if len(text) < 150:
                        response = await self.download(
                            task["url"], True, task["parent_url"]
                        )
                        browser = True
                        if response.status >= 400:
                            raise ValueError(
                                f"Rendu navigateur : HTTP {response.status}"
                            )
                        await asyncio.to_thread(self.policy.check, response.url)
                        body = response.body
                        mime = response.headers.get(b"Content-Type", b"").decode(
                            "latin-1"
                        )
                        kind, mime = classify(
                            body,
                            mime,
                            response.url,
                            response.meta.get("playwright_suggested_filename", ""),
                        )
                        if kind == "html":
                            title, links = html_links(body, response.url)
            if kind == "index":
                if body[:2] == b"\x1f\x8b":
                    with gzip.GzipFile(fileobj=io.BytesIO(body)) as f:
                        body = f.read(settings.max_bytes + 1)
                    if len(body) > settings.max_bytes:
                        raise ValueError("Index décompressé trop volumineux.")
                links = xml_links(body, response.url)
                with transaction() as db:
                    job = locked_job(db, self.job_id, self.owner)
                    enqueue(db, job, links, task["depth"] + 1, response.url)
                    row = db.get(Task, task["id"])
                    row.status = "index"
                    row.http_status = response.status
                    row.error = ""
                return
            stored = await asyncio.to_thread(
                store_original, body, self.source_id, response.url, kind, suggested
            )
            response_headers = {
                k.decode("latin-1").lower(): response.headers.get(k, b"").decode(
                    "latin-1"
                )
                for k in response.headers
            }
            response_headers["_status"] = response.status
            record_document(
                self.job_id,
                self.owner,
                task["id"],
                response.url,
                title,
                kind,
                mime,
                body,
                stored,
                response_headers,
                links,
            )
            with transaction() as db:
                locked_job(db, self.job_id, self.owner)
                db.get(Task, task["id"]).method = "browser" if browser else "http"
        except LostLease:
            raise
        except Exception as exc:
            # Erreurs de politique / format : pas de nouvelles tentatives automatiques.
            retry = not isinstance(exc, (ValueError, PolicyError))
            record_failure(self.job_id, self.owner, task["id"], str(exc), retry=retry)


def scrapy_settings():
    return {
        "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
        "DOWNLOAD_HANDLERS": {
            "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
            "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        },
        "DOWNLOADER_MIDDLEWARES": {
            "app.spider.ScopeMiddleware": 100,
            "scrapy.downloadermiddlewares.retry.RetryMiddleware": None,
            "scrapy.downloadermiddlewares.robotstxt.RobotsTxtMiddleware": None,
        },
        "ROBOTSTXT_OBEY": False,  # Politique commune HTTP + navigateur dans ScopeMiddleware / abort_browser.
        "REDIRECT_ENABLED": True,
        "REDIRECT_MAX_TIMES": 5,
        "USER_AGENT": settings.user_agent,
        "DOWNLOAD_TIMEOUT": 60,
        "DOWNLOAD_MAXSIZE": settings.max_bytes,
        "CONCURRENT_REQUESTS": settings.concurrency,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "DOWNLOAD_DELAY": settings.delay,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_START_DELAY": settings.delay,
        "AUTOTHROTTLE_MAX_DELAY": 30,
        "PLAYWRIGHT_BROWSER_TYPE": "chromium",
        "PLAYWRIGHT_MAX_CONTEXTS": 2,
        "PLAYWRIGHT_MAX_PAGES_PER_CONTEXT": 2,
        "PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT": 45000,
        "PLAYWRIGHT_PROCESS_REQUEST_HEADERS": None,
        "LOG_LEVEL": "INFO",
        "TELNETCONSOLE_ENABLED": False,
    }
