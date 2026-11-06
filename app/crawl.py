"""Un processus Scrapy par collecte ; jamais de reactor dans FastAPI."""

import sys
from scrapy.crawler import CrawlerProcess
from .spider import CollectorSpider, scrapy_settings

if __name__ == "__main__":
    job_id, owner = sys.argv[1:3]
    process = CrawlerProcess(scrapy_settings())
    crawler = process.create_crawler(CollectorSpider)

    # Le prédicat a besoin de la politique de la collecte avant les requêtes navigateur.
    async def abort(request):
        return await crawler.spider.abort_browser(request)

    crawler.settings.set("PLAYWRIGHT_ABORT_REQUEST", abort, priority="cmdline")
    process.crawl(crawler, job_id=job_id, owner=owner)
    process.start()
