"""日本語AIニュース記事の収集・整形・重複排除ロジック。

日本語メディアのRSSフィードからAI関連記事を取得し、日本語以外の記事を
除外したうえで、URL/タイトルの類似度で重複を排除し、公開日時の新しい順に
整列して返す。

外部ネットワークアクセスを伴うフェッチ結果は ``TTLCache`` で一定時間
キャッシュし、過剰なリクエストを防ぐ。
"""

from __future__ import annotations

import calendar
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
# 日本語のAI/テック系メディア。ITmedia AI+ はAI専門、その他は総合テック
# フィードのため、日本語以外の記事は _is_japanese フィルタで除外される。
RSS_FEEDS: list[tuple[str, str]] = [
    ("ITmedia AI＋", "https://rss.itmedia.co.jp/rss/2.0/aiplus.xml"),
    ("ZDNet Japan", "https://feeds.japan.zdnet.com/rss/zdnet/all.rdf"),
    ("GIGAZINE", "https://gigazine.net/news/rss_2.0/"),
]

# 表示件数とキャッシュTTL（秒）
MAX_ARTICLES = 30
CACHE_TTL_SECONDS = 15 * 60  # 15分

# タイトル類似度で重複とみなす閾値
TITLE_SIMILARITY_THRESHOLD = 0.85

# 要約の最大文字数
SUMMARY_MAX_CHARS = 280

# href に展開して安全なURLスキーム（javascript: 等のXSSを防ぐ）
ALLOWED_URL_SCHEMES = {"http", "https"}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# ひらがな・カタカナ（半角カナ含む）。日本語記事の判定に用いる。
_KANA_RE = re.compile(r"[぀-ヿｦ-ﾟ]")


def _is_japanese(text: str) -> bool:
    """テキストが日本語かどうかを判定する。

    ひらがな・カタカナを1文字でも含めば日本語とみなす簡易判定。漢字のみの
    タイトル（中国語等との区別が難しい）は対象外だが、日本語の見出しは通常
    かなを含むため実用上は十分に機能する。外部ライブラリは使用しない。
    """
    if not text:
        return False
    return bool(_KANA_RE.search(text))


@dataclass
class Article:
    """1件のニュース記事。テンプレートからそのまま参照される。"""

    title: str
    url: str
    summary: str
    source: str
    published: datetime | None = None

    def __post_init__(self) -> None:
        # http(s) 以外のスキーム（javascript:, data: 等）は href に出さない。
        try:
            scheme = urlparse(self.url).scheme.lower()
        except ValueError:
            scheme = ""
        if scheme not in ALLOWED_URL_SCHEMES:
            self.url = "#"

    @property
    def published_display(self) -> str:
        """公開日時を人間に読みやすい文字列で返す。"""
        if not self.published:
            return "日時不明"
        return self.published.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _contains_ai_keyword(article: Article) -> bool:
    """タイトルまたは要約に「AI」が含まれるか判定する。"""
    return "AI" in article.title or "AI" in article.summary


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
            # feedparser の *_parsed は UTC の struct_time。time.mktime はローカル
            # タイム扱いになるため、UTC として解釈する calendar.timegm を使う。
            return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)
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
        """整形済みの記事リストを返す。キャッシュが有効ならそれを使う。

        失効判定〜フェッチ〜キャッシュ更新までロックを保持することで、
        複数スレッドが同時に失効を検出して重複フェッチする競合
        (TOCTOU / thundering herd) を防ぐ。
        """
        with self._lock:
            if not force_refresh and self._cache and self._cache.expires_at > self._now():
                return self._cache.articles
            articles = self._collect()
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

        # 総合テック系フィードが混ざるため、日本語タイトルの記事のみを残す。
        collected = [a for a in collected if _is_japanese(a.title)]

        # タイトルまたは要約に「AI」を含まない記事を除外する。
        collected = [a for a in collected if _contains_ai_keyword(a)]

        # 新しい順（公開日時不明は末尾）に整列
        collected.sort(
            key=lambda a: a.published or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

        deduped = _deduplicate(collected)
        return deduped[: self._max_articles]


# アプリ全体で共有するデフォルトのサービスインスタンス
news_service = NewsService()
