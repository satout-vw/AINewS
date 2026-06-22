# AI News Digest

日本語メディアのRSSフィードからAI関連ニュースを収集し、日本語以外の記事を
除外したうえで重複を排除した**最新30件**を、モダンなカードレイアウトで
一覧表示するWebアプリです。

## 特長

- 🗞 **複数の日本語ソースを集約** — ITmedia AI＋ / ZDNet Japan / GIGAZINE / ASCII.jp
- 🇯🇵 **日本語記事のみ表示** — タイトルにひらがな・カタカナを含む記事だけを抽出（総合テックフィードに混ざる英語記事を除外）
- 🧹 **重複排除** — URLの正規化に加え、タイトルの類似度（既定 0.85）で重複記事を除外
- ✂️ **自動要約** — RSSの `description` / `summary` を使用し、無ければ本文冒頭を切り出して整形
- 🔗 **情報元リンク** — 各記事にソース名・公開日時・クリッカブルな元記事URLを表示
- ⏱ **キャッシュ** — RSSフェッチ結果を15分キャッシュし、過剰なリクエストを抑制
- 🎨 **モダンUI** — Tailwind CSS（CDN）によるダークテーマのレスポンシブなカードデザイン

## 必要環境

- Python 3.9 以上

## セットアップと起動

### 1. 仮想環境の作成と有効化

```bash
# 仮想環境を作成（初回のみ）
python -m venv .venv

# 仮想環境を有効化
# macOS / Linux
source .venv/bin/activate

# Windows（コマンドプロンプト）
.venv\Scripts\activate.bat

# Windows（PowerShell）
.venv\Scripts\Activate.ps1
```

有効化すると、プロンプトの先頭に `(.venv)` と表示されます。

### 2. 環境変数ファイルの準備

`.env.example` をコピーして `.env` を作成します。

```bash
cp .env.example .env
```

必要に応じて `.env` を編集し、ポート番号やデバッグモードを設定してください。

```
# .env の設定例
PORT=5000
FLASK_DEBUG=0
```

> `.env` ファイルは `.gitignore` に含まれており、リポジトリにはコミットされません。

### 3. 依存ライブラリのインストール

```bash
pip install -r requirements.txt
```

### 4. アプリの起動

```bash
python app.py
```

起動後、ブラウザで <http://localhost:5000> を開いてください。

### 仮想環境の無効化

作業が終わったら `deactivate` コマンドで仮想環境を無効化できます。

```bash
deactivate
```

---

### ポートの変更（環境変数で上書き）

`.env` の `PORT` を変更するか、コマンド実行時に直接指定することもできます。

```bash
PORT=8080 python app.py
```

### デバッグモード

`.env` で `FLASK_DEBUG=1` に設定するか、コマンド実行時に指定します。

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

## 構成

```
.
├── app.py             # Flaskアプリ本体（ルーティング）
├── news.py            # RSS取得・整形・重複排除ロジック + TTLキャッシュ
├── templates/
│   └── index.html     # 記事一覧ページ（Tailwind CSS）
├── requirements.txt
├── .env.example       # 環境変数の設定例（リポジトリ管理）
├── .env               # 環境変数の実設定（.gitignore 対象、各自作成）
└── README.md
```

## ライセンス

社内利用を想定したサンプル実装です。
