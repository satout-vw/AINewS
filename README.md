# AI News Digest

複数のメディアのRSSフィードからAI関連ニュースを収集し、重複を排除した
**最新30件**をモダンなカードレイアウトで一覧表示するWebアプリです。

## 特長

- 🗞 **複数ソースを集約** — TechCrunch AI / MIT Technology Review / The Verge AI / Wired AI
- 🌐 **日本語へ自動翻訳** — 日本語以外の記事のタイトル・要約を Claude API で日本語に翻訳して表示（翻訳した記事には「🌐 翻訳」バッジを表示）
- 🧹 **重複排除** — URLの正規化に加え、タイトルの類似度（既定 0.85）で重複記事を除外
- ✂️ **自動要約** — RSSの `description` / `summary` を使用し、無ければ本文冒頭を切り出して整形
- 🔗 **情報元リンク** — 各記事にソース名・公開日時・クリッカブルな元記事URLを表示
- ⏱ **キャッシュ** — RSSフェッチ結果を15分キャッシュし、過剰なリクエストを抑制
- 🎨 **モダンUI** — Tailwind CSS（CDN）によるダークテーマのレスポンシブなカードデザイン

## 必要環境

- Python 3.9 以上

## セットアップと起動

```bash
# 1. 依存ライブラリのインストール
pip install -r requirements.txt

# 2. アプリの起動
python app.py
```

起動後、ブラウザで <http://localhost:5000> を開いてください。

### 日本語翻訳の有効化

日本語以外の記事の翻訳には Claude API を利用します。環境変数 `ANTHROPIC_API_KEY` を
設定すると翻訳が有効になります。

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python app.py
```

`ANTHROPIC_API_KEY` が未設定の場合や翻訳に失敗した場合は、**原文のまま表示**され、
一覧表示そのものは止まりません（フェイルセーフ）。翻訳に使うモデルは
`AINEWS_TRANSLATE_MODEL`（既定: `claude-opus-4-8`）で変更できます。

```bash
# 例: 低コスト・高速なモデルを使う
AINEWS_TRANSLATE_MODEL=claude-haiku-4-5 ANTHROPIC_API_KEY=sk-ant-... python app.py
```

### ポートの変更

```bash
PORT=8080 python app.py
```

### デバッグモード

```bash
FLASK_DEBUG=1 python app.py
```

## 使い方

- トップページ `/` で最新記事の一覧を表示します。
- ヘッダーの **「↻ 今すぐ更新」** ボタン（`/?refresh=1`）でキャッシュを無視して再取得します。
- `/healthz` はヘルスチェック用エンドポイントです。

## 設定

主な設定値は `news.py` の冒頭で変更できます。

| 定数 | 既定値 | 説明 |
|------|--------|------|
| `RSS_FEEDS` | 4ソース | 取得対象のRSSフィード一覧 `(ソース名, URL)` |
| `MAX_ARTICLES` | `30` | 一覧に表示する最大件数 |
| `CACHE_TTL_SECONDS` | `900`（15分） | RSSフェッチ結果のキャッシュ有効期間 |
| `TITLE_SIMILARITY_THRESHOLD` | `0.85` | この値以上に類似したタイトルを重複とみなす |
| `SUMMARY_MAX_CHARS` | `280` | 要約の最大文字数 |

翻訳関連の設定は環境変数で行います。

| 環境変数 | 既定値 | 説明 |
|----------|--------|------|
| `ANTHROPIC_API_KEY` | （未設定） | 設定すると日本語翻訳が有効になる。未設定時は原文表示 |
| `AINEWS_TRANSLATE_MODEL` | `claude-opus-4-8` | 翻訳に使用する Claude モデル |

## 構成

```
.
├── app.py             # Flaskアプリ本体（ルーティング）
├── news.py            # RSS取得・整形・重複排除ロジック + TTLキャッシュ
├── translator.py      # 言語判定 + Claude APIによる日本語翻訳（フェイルセーフ）
├── templates/
│   └── index.html     # 記事一覧ページ（Tailwind CSS）
├── requirements.txt
└── README.md
```

## ライセンス

社内利用を想定したサンプル実装です。
