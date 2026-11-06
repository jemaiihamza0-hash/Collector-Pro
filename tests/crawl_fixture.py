"""Entrée de test uniquement : autorise un serveur HTTP de fixture sur loopback."""

import os
import app.repository
from tests.fixture_urls import fixture_normalizer
import app.urls
import app.spider

normalizer = fixture_normalizer(int(os.environ["FIXTURE_PORT"]))
app.urls.normalize = normalizer
app.repository.normalize = normalizer
app.urls.public_address = lambda url: None
app.spider.public_address = lambda url: None
from scrapy.crawler import CrawlerProcess
from app.spider import CollectorSpider, scrapy_settings
import sys

if __name__ == "__main__":
    process = CrawlerProcess(scrapy_settings())
    crawler = process.create_crawler(CollectorSpider)

    async def abort(request):
        return await crawler.spider.abort_browser(request)

    crawler.settings.set("PLAYWRIGHT_ABORT_REQUEST", abort, priority="cmdline")
    process.crawl(crawler, job_id=sys.argv[1], owner=sys.argv[2])
    process.start()
