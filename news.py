"""AIニュース記事の収集・整形・重複排除ロジック。

複数のRSSフィードからAI関連記事を取得し、URL/タイトルの類似度で
重複を排除した上で、公開日時の新しい順に整列して返す。

外部ネットワークアクセスを伴うフェッチ結果は ``TTLCache`` で一定時間
キャッシュし、過剰なリクエストを防ぐ。
"""

from __future__ import annotations

import html
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Iterable
from urllib.parse import urlparse, urlunparse

import feedparser
from dateutil import parser as date_parser

logger = logging.getLogger(__name__)

# 取得対象のRSSフィード一覧。(表示用ソース名, フィードURL)
RSS_FEEDS: list[tuple[str, str]] = [
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("MIT Technology Review", "https://www.technologyreview.com/topic/artificial-intelligence/feed/"),
    ("The Verge AI", "https://www.theverge.com/rss/ai/index.xml"),
    ("Wired AI", "https://www.wired.com/feed/tag/ai/latest/rss"),
]

# 表示件数とキャッシュTTL（秒）
MAX_ARTICLES = 30
CACHE_TTL_SECONDS = 15 * 60  # 15分

# タイトル類似度で重複とみなす閾値
TITLE_SIMILARITY_THRESHOLD = 0.85

# 要約の最大文字数
SUMMARY_MAX_CHARS = 280

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


@dataclass
class Article:
    """1件のニュース記事。テンプレートからそのまま参照される。"""

    title: str
    url: str
    summary: str
    source: str
    published: datetime | None = None

    @property
    def published_display(self) -> str:
        """公開日時を人間に読みやすい文字列で返す。"""
        if not self.published:
            return "日時不明"
        return self.published.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _strip_html(text: str) -> str:
    """HTMLタグを除去し、エンティティをデコードして空白を正規化する。"""
    if not text:
        return ""
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def _truncate(text: str, limit: int = SUMMARY_MAX_CHARS) -> str:
    """指定文字数を超える場合は語境界で切り詰めて省略記号を付与する。"""
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip()
    return f"{cut}…"


def _parse_published(entry) -> datetime | None:
    """RSSエントリから公開日時を取得する。複数フィールドをフォールバック。"""
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            return datetime.fromtimestamp(time.mktime(value), tz=timezone.utc)
    for key in ("published", "updated", "date"):
        raw = entry.get(key)
        if raw:
            try:
                dt = date_parser.parse(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except (ValueError, OverflowError):
                continue
    return None


def _extract_summary(entry) -> str:
    """description / summary を優先し、なければ本文冒頭を切り出す。"""
    for key in ("summary", "description"):
        raw = entry.get(key)
        if raw:
            cleaned = _strip_html(raw)
            if cleaned:
                return _truncate(cleaned)

    content = entry.get("content")
    if content and isinstance(content, list) and content:
        cleaned = _strip_html(content[0].get("value", ""))
        if cleaned:
            return _truncate(cleaned)

    return "（要約なし）"


def _normalize_url(url: str) -> str:
    """重複判定用にURLを正規化する（クエリ・フラグメント・末尾スラッシュを除去）。"""
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return url.strip().lower()
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parsed.path.rstrip("/")
    return urlunparse((parsed.scheme.lower(), netloc, path, "", "", "")).lower()


def _normalize_title(title: str) -> str:
    """タイトル類似度比較用に小文字化し記号・空白を除去する。"""
    title = title.lower()
    title = re.sub(r"[^\w\s]", "", title)
    return _WS_RE.sub(" ", title).strip()


def _fetch_feed(source: str, url: str) -> list[Article]:
    """単一フィードを取得してArticleのリストに変換する。失敗時は空リスト。"""
    articles: list[Article] = []
    try:
        parsed = feedparser.parse(url)
    except Exception:  # noqa: BLE001 - フィード単位の失敗は全体を止めない
        logger.exception("フィード取得に失敗しました: %s", url)
        return articles

    if getattr(parsed, "bozo", False) and not parsed.entries:
        logger.warning("フィードを解析できませんでした: %s (%s)", url, getattr(parsed, "bozo_exception", ""))
        return articles

    for entry in parsed.entries:
        link = (entry.get("link") or "").strip()
        title = _strip_html(entry.get("title") or "").strip()
        if not link or not title:
            continue
        articles.append(
            Article(
                title=title,
                url=link,
                summary=_extract_summary(entry),
                source=source,
                published=_parse_published(entry),
            )
        )
    return articles


def _deduplicate(articles: Iterable[Article]) -> list[Article]:
    """URLの一致およびタイトルの類似度で重複記事を除外する。

    先に出現した（=公開日時が新しい）記事を優先して残す。
    """
    seen_urls: set[str] = set()
    kept: list[Article] = []
    kept_titles: list[str] = []

    for article in articles:
        norm_url = _normalize_url(article.url)
        if norm_url and norm_url in seen_urls:
            continue

        norm_title = _normalize_title(article.title)
        is_dup = False
        for existing in kept_titles:
            if SequenceMatcher(None, norm_title, existing).ratio() >= TITLE_SIMILARITY_THRESHOLD:
                is_dup = True
                break
        if is_dup:
            continue

        if norm_url:
            seen_urls.add(norm_url)
        kept_titles.append(norm_title)
        kept.append(article)

    return kept


@dataclass
class _CacheEntry:
    articles: list[Article]
    expires_at: float


class NewsService:
    """記事取得のファサード。スレッドセーフなTTLキャッシュ付き。"""

    def __init__(
        self,
        feeds: list[tuple[str, str]] | None = None,
        ttl_seconds: int = CACHE_TTL_SECONDS,
        max_articles: int = MAX_ARTICLES,
    ) -> None:
        self._feeds = feeds if feeds is not None else RSS_FEEDS
        self._ttl = ttl_seconds
        self._max_articles = max_articles
        self._lock = threading.Lock()
        self._cache: _CacheEntry | None = None

    def _now(self) -> float:
        return time.monotonic()

    def get_articles(self, force_refresh: bool = False) -> list[Article]:
        """整形済みの記事リストを返す。キャッシュが有効ならそれを使う。"""
        with self._lock:
            if not force_refresh and self._cache and self._cache.expires_at > self._now():
                return self._cache.articles

        articles = self._collect()

        with self._lock:
            self._cache = _CacheEntry(articles=articles, expires_at=self._now() + self._ttl)
            return articles

    def cache_age_seconds(self) -> float | None:
        """現在のキャッシュの経過秒数。キャッシュ無しならNone。"""
        with self._lock:
            if not self._cache:
                return None
            return max(0.0, self._ttl - (self._cache.expires_at - self._now()))

    def _collect(self) -> list[Article]:
        collected: list[Article] = []
        for source, url in self._feeds:
            collected.extend(_fetch_feed(source, url))

        # 新しい順（公開日時不明は末尾）に整列
        collected.sort(
            key=lambda a: a.published or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

        deduped = _deduplicate(collected)
        return deduped[: self._max_articles]


# アプリ全体で共有するデフォルトのサービスインスタンス
news_service = NewsService()
