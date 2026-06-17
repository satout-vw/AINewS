"""日本語以外のニュース記事を日本語へ翻訳するモジュール。

記事のタイトル・要約の言語を判定し、日本語でないものを Claude API で
日本語へ翻訳する。同一テキストの再翻訳を避けるためのスレッドセーフな
メモリキャッシュを備える。

APIキー未設定・``anthropic`` 未導入・API呼び出し失敗のいずれの場合も、
例外を送出せず原文をそのまま返すフェイルセーフ設計とする（翻訳できなくても
ニュース一覧の表示自体は止めない）。
"""

from __future__ import annotations

import logging
import os
import re
import threading

logger = logging.getLogger(__name__)

# 翻訳に用いる Claude モデル。環境変数 AINEWS_TRANSLATE_MODEL で上書き可能。
DEFAULT_TRANSLATE_MODEL = "claude-opus-4-8"
TRANSLATE_MODEL = os.environ.get("AINEWS_TRANSLATE_MODEL", DEFAULT_TRANSLATE_MODEL)

# 翻訳出力のトークン上限（タイトル + 要約に十分な余裕を持たせる）
TRANSLATE_MAX_TOKENS = 2048

# 翻訳結果の区切りに用いるマーカー
_TITLE_MARKER = "[TITLE]"
_SUMMARY_MARKER = "[SUMMARY]"

# ひらがな・カタカナ（日本語と判定する主シグナル）
_KANA_RE = re.compile(r"[぀-ヿｦ-ﾟ]")
# 日本語とみなしうる文字（かな + CJK統合漢字 + 半角カナ）
_JP_RE = re.compile(r"[぀-ヿ一-鿿ｦ-ﾟ]")
# 判定母数となる文字（空白・記号を除く）
_WORD_RE = re.compile(r"\w", re.UNICODE)

# 日本語判定の閾値（日本語文字数 / 総文字数）
_JP_RATIO_THRESHOLD = 0.5


def is_japanese(text: str) -> bool:
    """テキストが日本語とみなせるかを判定する。

    ひらがな・カタカナを含む場合は日本語とみなす。含まない場合は、日本語文字
    （かな・漢字）の比率が閾値以上であれば日本語とみなす。中国語など漢字のみ
    の言語との厳密な区別は行わない簡易判定。
    """
    if not text:
        return True  # 空文字は翻訳不要
    if _KANA_RE.search(text):
        return True
    total = len(_WORD_RE.findall(text))
    if total == 0:
        return True  # 記号・数字のみは翻訳不要
    jp = len(_JP_RE.findall(text))
    return jp / total >= _JP_RATIO_THRESHOLD


def _parse_translation(text: str, fallback_title: str, fallback_summary: str) -> tuple[str, str]:
    """マーカー形式の応答からタイトル・要約の訳文を抽出する。

    期待する形式を満たさない場合は、抽出できた範囲のみ採用し、残りは
    フォールバック（原文）を用いる。
    """
    if not text:
        return fallback_title, fallback_summary

    title = fallback_title
    summary = fallback_summary
    ti = text.find(_TITLE_MARKER)
    si = text.find(_SUMMARY_MARKER)
    if ti != -1 and si != -1 and si > ti:
        title_part = text[ti + len(_TITLE_MARKER) : si].strip()
        summary_part = text[si + len(_SUMMARY_MARKER) :].strip()
        if title_part:
            title = title_part
        if summary_part:
            summary = summary_part
    return title, summary


class TranslationService:
    """テキストを日本語へ翻訳するサービス。

    Claude API クライアントは初回利用時に遅延初期化する。APIキーが未設定、
    または ``anthropic`` が未導入の場合は自身を無効化し、以降は原文を返す。
    翻訳結果は (タイトル, 要約) 単位でメモリキャッシュする。
    """

    def __init__(
        self,
        model: str = TRANSLATE_MODEL,
        max_tokens: int = TRANSLATE_MAX_TOKENS,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[str, str]] = {}
        self._client = None
        self._client_ready = False

    def _get_client(self):
        """Claude API クライアントを返す。生成不能なら None（=無効化）。"""
        if self._client_ready:
            return self._client
        with self._lock:
            if self._client_ready:
                return self._client
            self._client_ready = True
            if not os.environ.get("ANTHROPIC_API_KEY"):
                logger.info("ANTHROPIC_API_KEY が未設定のため翻訳を無効化します。原文を表示します。")
                return None
            try:
                import anthropic

                self._client = anthropic.Anthropic()
            except Exception:  # noqa: BLE001 - 初期化失敗時は翻訳を諦めて原文表示に倒す
                logger.exception("anthropic クライアントの初期化に失敗したため翻訳を無効化します。")
                self._client = None
            return self._client

    def translate(self, title: str, summary: str) -> tuple[str, str]:
        """タイトルと要約を日本語へ翻訳して返す。

        いずれも日本語、またはサービス無効時・翻訳失敗時は原文をそのまま返す。
        既に日本語の項目は翻訳対象から除外し、原文を保持する。
        """
        title = title or ""
        summary = summary or ""
        title_is_ja = is_japanese(title)
        summary_is_ja = is_japanese(summary)
        if title_is_ja and summary_is_ja:
            return title, summary  # どちらも翻訳不要

        client = self._get_client()
        if client is None:
            return title, summary  # 無効化中は原文

        cache_key = f"{title}\n{summary}"
        with self._lock:
            cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            t_ja, s_ja = self._call_api(client, title, summary)
        except Exception:  # noqa: BLE001 - 呼び出し失敗は全体を止めず原文に倒す
            logger.exception("翻訳に失敗したため原文を表示します。")
            return title, summary

        # 元々日本語だった項目は翻訳結果を採用せず原文を保持する
        result = (
            title if title_is_ja else (t_ja or title),
            summary if summary_is_ja else (s_ja or summary),
        )
        with self._lock:
            self._cache[cache_key] = result
        return result

    def _call_api(self, client, title: str, summary: str) -> tuple[str, str]:
        """Claude API を呼び出し、タイトル・要約の訳文を取得する。"""
        system = (
            "あなたはプロの翻訳者です。与えられたニュース記事のタイトルと要約を、"
            "自然で読みやすい日本語に翻訳してください。固有名詞・製品名・社名は"
            "一般的な日本語表記に従い、無理に訳す必要はありません。"
            "余計な前置きや注釈は付けず、指定された形式でのみ出力してください。"
        )
        user = (
            "次のニュース記事を日本語に翻訳してください。\n\n"
            f"{_TITLE_MARKER}\n{title}\n\n"
            f"{_SUMMARY_MARKER}\n{summary}\n\n"
            "出力は必ず次の形式に従い、各マーカーの直後の行に訳文のみを記載してください。\n"
            f"{_TITLE_MARKER}\n<タイトルの日本語訳>\n{_SUMMARY_MARKER}\n<要約の日本語訳>"
        )
        response = client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        return _parse_translation(text, fallback_title=title, fallback_summary=summary)


# アプリ全体で共有するデフォルトの翻訳サービスインスタンス
translation_service = TranslationService()
