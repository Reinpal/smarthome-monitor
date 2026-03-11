"""SmartHome Monitor - ISG Heat Pump Scraper & Fronius Solar Collector.

Main entry point that runs two independent collection loops:
- ISG heat pump: scrape HTML pages every 5 minutes (configurable)
- Fronius solar: poll JSON API every 30 seconds (configurable)
"""

import logging
import signal
import sys
import time

from scraper.config import Config
from scraper.scrapers.isg_scraper import ISGScraper
from scraper.parsers.isg_parser import parse_isg_page
from scraper.collectors.fronius_collector import FroniusCollector
from scraper.exporters.otlp_exporter import OTLPExporter


def setup_logging(level: str) -> None:
    """Configure logging for the application."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


def isg_scrape_cycle(
    scraper: ISGScraper,
    exporter: OTLPExporter,
    pages: dict[str, str],
) -> None:
    """Execute a single ISG scrape -> parse -> export cycle.

    Args:
        scraper: ISG HTTP scraper instance
        exporter: OTLP metrics exporter instance
        pages: Dict mapping page_path -> page_name
    """
    logger = logging.getLogger(__name__)
    logger.info("Starting ISG scrape cycle...")

    total_metrics = 0
    total_pages = 0

    html_pages = scraper.fetch_all_pages(pages)

    for page_name, html in html_pages.items():
        try:
            values = parse_isg_page(html, page_name)
            count = exporter.export_values(page_name, values)
            total_metrics += count
            total_pages += 1
        except Exception:
            logger.exception("Error processing ISG page '%s'", page_name)

    logger.info(
        "ISG scrape cycle complete: %d metrics from %d pages",
        total_metrics,
        total_pages,
    )


def fronius_collect_cycle(
    collector: FroniusCollector,
    exporter: OTLPExporter,
    endpoints: dict[str, str],
) -> None:
    """Execute a single Fronius API collection cycle.

    Args:
        collector: Fronius API collector instance
        exporter: OTLP metrics exporter instance
        endpoints: Dict mapping endpoint name -> URL path
    """
    logger = logging.getLogger(__name__)
    logger.info("Starting Fronius collection cycle...")

    try:
        metrics = collector.collect_all(endpoints)
        count = exporter.export_fronius_values(metrics)
        logger.info(
            "Fronius collection cycle complete: %d metrics", count
        )
    except Exception:
        logger.exception("Error in Fronius collection cycle")


def main() -> None:
    """Main entry point with dual-interval collection loop."""
    config = Config()
    setup_logging(config.log_level)
    logger = logging.getLogger(__name__)

    logger.info("SmartHome Monitor starting...")
    logger.info("ISG Base URL: %s", config.isg_base_url)
    logger.info("ISG scrape interval: %ds", config.scrape_interval)
    logger.info("OTLP endpoint: %s", config.otlp_endpoint)
    logger.info("ISG pages to scrape: %s", list(config.isg_pages.values()))

    if config.fronius_enabled:
        logger.info("Fronius enabled: %s", config.fronius_base_url)
        logger.info("Fronius poll interval: %ds", config.fronius_poll_interval)
    else:
        logger.info("Fronius collector disabled")

    scraper = ISGScraper(config.isg_base_url)
    exporter = OTLPExporter(config.otlp_endpoint)

    fronius_collector = None
    if config.fronius_enabled:
        fronius_collector = FroniusCollector(config.fronius_base_url)

    # Graceful shutdown
    shutdown_event = False

    def signal_handler(signum, frame):
        nonlocal shutdown_event
        logger.info("Received signal %d, shutting down...", signum)
        shutdown_event = True

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Track when each collector last ran
    # Set to 0 so both run immediately on startup
    last_isg_scrape = 0.0
    last_fronius_poll = 0.0

    try:
        while not shutdown_event:
            now = time.monotonic()

            # Check if ISG scrape is due
            if now - last_isg_scrape >= config.scrape_interval:
                isg_scrape_cycle(scraper, exporter, config.isg_pages)
                last_isg_scrape = time.monotonic()

            # Check if Fronius poll is due
            if (
                fronius_collector is not None
                and now - last_fronius_poll >= config.fronius_poll_interval
            ):
                fronius_collect_cycle(
                    fronius_collector,
                    exporter,
                    config.fronius_endpoints,
                )
                last_fronius_poll = time.monotonic()

            # Sleep in small increments for responsive shutdown
            time.sleep(1)

    except Exception:
        logger.exception("Fatal error in main loop")
    finally:
        logger.info("Shutting down exporter...")
        exporter.shutdown()
        logger.info("SmartHome Monitor stopped.")


if __name__ == "__main__":
    main()
