"""AIニュース記事収集・要約リスト表示アプリ（Flask）。

複数のRSSフィードからAI関連記事を集約し、重複を排除した最新30件を
モダンなカードレイアウトで表示する。
"""

from __future__ import annotations

import logging
import os

from flask import Flask, render_template, request

from news import news_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = Flask(__name__)


@app.route("/")
def index():
    """記事一覧ページ。?refresh=1 でキャッシュを無視して再取得する。"""
    force_refresh = request.args.get("refresh") == "1"
    articles = news_service.get_articles(force_refresh=force_refresh)
    cache_age = news_service.cache_age_seconds()
    return render_template(
        "index.html",
        articles=articles,
        count=len(articles),
        cache_age_minutes=int(cache_age // 60) if cache_age is not None else None,
    )


@app.route("/healthz")
def healthz():
    """ヘルスチェック用エンドポイント。"""
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
