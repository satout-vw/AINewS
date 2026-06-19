"""AIニュース記事収集・要約リスト表示アプリ（Flask）。

複数のRSSフィードからAI関連記事を集約し、重複を排除した最新30件を
モダンなカードレイアウトで表示する。
"""

from __future__ import annotations

import calendar
import logging
import os
from datetime import date, timezone

from flask import Flask, render_template, request

from news import news_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = Flask(__name__)

_MONTH_NAMES_JA = [
    "", "1月", "2月", "3月", "4月", "5月", "6月",
    "7月", "8月", "9月", "10月", "11月", "12月",
]


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


@app.route("/calendar")
@app.route("/calendar/<int:year>/<int:month>")
def calendar_view(year: int | None = None, month: int | None = None):
    """月別カレンダービュー。記事を日付ごとにカレンダー形式で表示する。

    不具合修正:
    - 毎月1日が常にSUN列に表示される不具合:
        calendar.Calendar(firstweekday=6).monthdayscalendar() を使い、
        各月の1日が正しい曜日列に配置されるよう修正。
    - 曜日の途中がブランクになる不具合:
        上記と同じ原因（先頭オフセット未計算）だったため、同時に解消。
    """
    today = date.today()
    if year is None:
        year = today.year
    if month is None:
        month = today.month

    # 範囲外のパラメータを正規化
    year = max(2000, min(year, 2100))
    month = max(1, min(month, 12))

    # キャッシュ済み記事を日付ごとに振り分け
    articles = news_service.get_articles()
    articles_by_day: dict[int, list] = {}
    for article in articles:
        if article.published:
            d = article.published.astimezone(timezone.utc).date()
            if d.year == year and d.month == month:
                articles_by_day.setdefault(d.day, []).append(article)

    # calendar.Calendar(firstweekday=6) で日曜始まりのカレンダーを生成する。
    # firstweekday=6 が日曜を週の先頭に指定するキーであり、
    # monthdayscalendar() は各週を [日, 月, 火, 水, 木, 金, 土] の順で返す。
    # 月外の日は 0 として返るため、テンプレート側で 0 を空白セルとして扱う。
    cal = calendar.Calendar(firstweekday=6)
    weeks = cal.monthdayscalendar(year, month)

    # 前月・翌月のナビゲーション用パラメータ
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1

    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1

    return render_template(
        "calendar.html",
        year=year,
        month=month,
        month_name=_MONTH_NAMES_JA[month],
        weeks=weeks,
        articles_by_day=articles_by_day,
        today=today,
        prev_year=prev_year,
        prev_month=prev_month,
        next_year=next_year,
        next_month=next_month,
    )


@app.route("/healthz")
def healthz():
    """ヘルスチェック用エンドポイント。"""
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
