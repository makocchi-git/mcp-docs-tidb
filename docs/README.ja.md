<h1 align="center">
  MCP server for storing/retrieving documents in TiDB
</h1>

<p align="center">
  <a href="/README.md">🇺🇸English</a> ·
  <strong>🇯🇵日本語</strong>
</p>

# mcp-docs-tidb

[TiDB](https://www.pingcap.com/tidb-cloud/) インスタンスをセマンティックメモリ層として公開する MCP (Model Context Protocol) サーバーです。[`mcp-server-qdrant`](https://github.com/qdrant/mcp-server-qdrant) に強くインスパイアされており、同じ `store` / `find` の形式を踏襲しながら、TiDB ネイティブの [`VECTOR`](https://docs.pingcap.com/tidb/stable/vector-search-overview) 型と `VEC_COSINE_DISTANCE` 関数をストレージおよび類似度検索のバックエンドとして使用します。

このサーバーは TiDB インスタンスがすでに起動・到達可能な状態であることを前提としています。TiDB のプロビジョニングや起動は行いません。

このリポジトリは 2 つの補完的な方法で利用できます：

- **MCP サーバーとして** — Claude Desktop / Cursor / Windsurf / Claude Code に組み込み、`docs-tidb-find` / `docs-tidb-list` / `docs-tidb-store` / `docs-tidb-ingest` ツールを呼び出す。この README の続きを参照してください。
- **Claude Code スキルとして** — リポジトリルートの `SKILL.md` が Claude に「このプロジェクトをうまく使う方法」（ingest と store の使い分け、次元の落とし穴、検索のエチケットなど）を教えます。[Claude Code スキルとして使う](#claude-code-スキルとして使う) を参照してください。スキルと MCP サーバーは独立しています。スキルが Claude の使い方を誘導し、MCP サーバーが実際にデータを提供します。両方を組み合わせると最も効果的です。

## 要件

- Python >= 3.10
- [uv](https://docs.astral.sh/uv/) パッケージマネージャー
- ベクター検索をサポートする到達可能な TiDB クラスター（TiDB v8.4+ セルフホスト、または TiDB Serverless）
- 対象データベースに対して `CREATE TABLE`、`INSERT`、`SELECT` の権限を持つユーザー

## インストール

`uvx`（[uv](https://docs.astral.sh/uv/) に同梱）を使うと、クローン不要で GitHub から直接ツールを実行できます：

```bash
uvx --from git+https://github.com/makocchi-git/mcp-docs-tidb mcp-docs-tidb
```

初回実行時に独立した環境が作成され、以降の呼び出しにはキャッシュが使用されます。特定のコミットやタグに固定するには、URL に `@<ref>` を付加してください（例：`@main`、`@v0.1.0`）。

開発目的（テスト実行・リント等）の場合は、リポジトリをクローンして `uv run` を使用してください：

```bash
git clone https://github.com/makocchi-git/mcp-docs-tidb.git
cd mcp-docs-tidb
uv sync
```

## 提供される MCP ツール

### `docs-tidb-store`

テキスト（任意のメタデータ付き）を TiDB テーブルに保存します。

| 引数 | 型 | 説明 |
| --- | --- | --- |
| `information` | string | 記憶させるテキスト。 |
| `collection_name` | string | 保存先の TiDB テーブル。`COLLECTION_NAME` がデフォルトとして設定されている場合は省略。 |
| `metadata` | JSON (省略可) | テキストと一緒に保存する任意の JSON メタデータ。 |
| `mtime` | float (省略可) | Unix エポック形式のソース更新日時。`metadata.mtime` に保存され、`metadata` 内に既存の `mtime` があれば上書きされます。呼び出し元がテキストの新鮮度を知っている場合（ファイルの mtime、上流の `Last-Modified` など）に使用し、後でフィルタリングや再インジェスト時のスキップに活用できます。 |

入力に関わらず、サーバーは常に現在の Unix エポックで `metadata.ingested_at` を記録します。

テーブルは最初の書き込み時に次のスキーマで自動作成されます：

```sql
CREATE TABLE <collection> (
  id        VARCHAR(36) PRIMARY KEY,
  content   TEXT          NOT NULL,
  metadata  JSON          NULL,
  embedding VECTOR(<dim>) NOT NULL
);
```

`TIDB_READ_ONLY=1` の場合、このツールは非表示になります。

### `docs-tidb-ingest`

ローカルファイルまたはディレクトリをコレクションに一括インジェストします。各ファイルをチャンクに分割し、`metadata.source` / `metadata.chunk` / `metadata.mtime` / `metadata.ingested_at` を付与し、（デフォルトでは）同じソースファイルの既存チャンクを置き換えます。以下の CLI と同じエンジンを使用し、LLM に公開されています。

| 引数 | 型 | 説明 |
| --- | --- | --- |
| `paths` | list[string] | サーバーホスト上のファイルまたはディレクトリ。 |
| `collection_name` | string | 対象の TiDB テーブル。`COLLECTION_NAME` が設定されている場合は省略。 |
| `recursive` | bool (デフォルト `false`) | ディレクトリを再帰的に処理する。 |
| `glob` | string (デフォルト `*.md`) | ディレクトリエントリに適用する glob パターン。 |
| `chunk_chars` | int (デフォルト `2000`) | チャンクあたりの最大文字数。 |
| `overlap` | int (デフォルト `200`) | チャンクのオーバーラップ文字数。 |
| `replace` | bool (デフォルト `true`) | 挿入前に同じ `source` タグを持つ既存チャンクを削除する。 |
| `only_modified` | bool (デフォルト `false`) | 同じ `source` に対してすでに保存されている `metadata.mtime` よりもディスク上の mtime が新しくないファイルはスキップする。既存レコードのないファイルは常に処理されます。インクリメンタルな更新に便利。 |
| `truncate_collection` | bool (デフォルト `false`) | インジェスト前に対象テーブルを `TRUNCATE` する。スキーマは保持され、すべての入力ファイルが再チャンク・再エンベッドされます。`only_modified=true` との組み合わせは許容されますが無意味です（トランケート後は比較対象の `mtime` が存在しないため）。 |

> `paths` は MCP クライアントではなく**サーバー**ホスト上で解決されます。stdio トランスポート（デフォルトの Claude Desktop 設定）ではファイルシステムを共有していますが、リモート/Docker デプロイメントでは共有されない場合があります。その場合は「TiDB へのドキュメント読み込み」で説明している同等の CLI の使用を推奨します。

`TIDB_READ_ONLY=1` の場合、このツールは非表示になります。

### `docs-tidb-find`

`VEC_COSINE_DISTANCE` を使用した類似度検索を実行します。

| 引数 | 型 | 説明 |
| --- | --- | --- |
| `query` | string | 検索するテキスト。 |
| `collection_name` | string | 検索対象の TiDB テーブル。`COLLECTION_NAME` がデフォルトとして設定されている場合は省略。 |

コサイン距離の昇順で上位 `TIDB_SEARCH_LIMIT`（デフォルト 10）件を返します。

### `docs-tidb-list`

コレクション内に登録されているドキュメントを `metadata.source` でグループ化して一覧表示します。インジェスト済みの内容を確認する（再インジェスト前など）、または新鮮度を確認するのに使用します。

| 引数 | 型 | 説明 |
| --- | --- | --- |
| `collection_name` | string | 検査対象の TiDB テーブル。`COLLECTION_NAME` がデフォルトとして設定されている場合は省略。 |

`metadata.source` の値ごとに 1 オブジェクトのリストを返します：

```json
[
  {
    "source": "/abs/path/to/file.md",
    "chunks": 12,
    "mtime": 1700000000.0,
    "ingested_at": 1700000050.5
  }
]
```

`mtime` と `ingested_at` は Unix エポック秒です（メタデータキーが存在しない場合は `null`）。`source` キーを持たないメタデータの行は無視されます。コレクションがまだ存在しない場合は空のリストを返します。`TIDB_READ_ONLY=1` の場合もこのツールは登録されたままです。

## 環境変数

### TiDB 接続

| 変数 | デフォルト | 説明 |
| --- | --- | --- |
| `TIDB_HOST` | `127.0.0.1` | TiDB ホスト。 |
| `TIDB_PORT` | `4000` | TiDB ポート。 |
| `TIDB_USER` | `root` | TiDB ユーザー。 |
| `TIDB_PASSWORD` | _（空）_ | TiDB パスワード。 |
| `TIDB_DATABASE` | `test` | データベース/スキーマ名。 |
| `TIDB_SSL_VERIFY_CERT` | `0` | TLS を有効にするには `1` に設定（TiDB Serverless では必須）。 |
| `TIDB_SSL_CA` | _（未設定）_ | オプションの CA バンドルパス（例：`/etc/ssl/cert.pem`）。 |

### 動作設定

| 変数 | デフォルト | 説明 |
| --- | --- | --- |
| `COLLECTION_NAME` | _（未設定）_ | デフォルトテーブル。設定すると、MCP ツールの `collection_name` 引数が省略されます。 |
| `TIDB_SEARCH_LIMIT` | `10` | `docs-tidb-find` が返す最大行数。 |
| `TIDB_READ_ONLY` | `0` | `1` の場合、`docs-tidb-store` ツールは登録されません。 |
| `TIDB_USE_VECTOR_INDEX` | `1` | `1` の場合、自動作成されるテーブルの embedding カラムにインライン `VECTOR INDEX ... USING HNSW` が含まれます。TiDB v8.4+ と TiFlash レプリカが必要 — [ベクターインデックス](#ベクターインデックス) を参照。 |
| `EMBEDDING_PROVIDER` | `fastembed` | エンベッディングプロバイダー（現在は `fastembed` のみサポート）。 |
| `EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | FastEmbed モデル名。 |
| `TOOL_STORE_DESCRIPTION` | _（ソース参照）_ | `docs-tidb-store` の LLM 向け説明文を上書き。 |
| `TOOL_FIND_DESCRIPTION` | _（ソース参照）_ | `docs-tidb-find` の LLM 向け説明文を上書き。 |
| `TOOL_INGEST_DESCRIPTION` | _（ソース参照）_ | `docs-tidb-ingest` の LLM 向け説明文を上書き。 |
| `TOOL_LIST_DESCRIPTION` | _（ソース参照）_ | `docs-tidb-list` の LLM 向け説明文を上書き。 |
| `TIDB_ALLOW_ARBITRARY_FILTER` | `0` | `1` の場合、`docs-tidb-find` に JSON フィルタースペックを受け付ける `query_filter` 引数を公開します。 |

## クイックスタート

### 1. TiDB をローカルで起動する

macOS / Linux では `tiup playground` が最も簡単な方法です：

```bash
tiup playground

# または、ベクター検索のために v8.4+ を明示的に指定：
tiup playground v8.5
```

デフォルトのエンドポイントは `127.0.0.1:4000`、ユーザー `root`、パスワードなし — このサーバーのデフォルト設定と一致します。

> ベクター検索には TiDB v8.4 以降が必要です。古いバージョンでは `VECTOR` 型が不明なため、`CREATE TABLE` に失敗します。

### 2. MCP サーバーを起動する

```bash
uvx --from git+https://github.com/makocchi-git/mcp-docs-tidb mcp-docs-tidb
```

stdio の代わりに HTTP クライアントを受け付けるには：

```bash
uvx --from git+https://github.com/makocchi-git/mcp-docs-tidb mcp-docs-tidb --transport streamable-http
# または、レガシー SSE
uvx --from git+https://github.com/makocchi-git/mcp-docs-tidb mcp-docs-tidb --transport sse
```

### 3. Claude Desktop に接続する

`~/Library/Application Support/Claude/claude_desktop_config.json`（macOS）を編集：

```json
{
  "mcpServers": {
    "mcp-docs-tidb": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/makocchi-git/mcp-docs-tidb",
        "mcp-docs-tidb"
      ],
      "env": {
        "TIDB_HOST": "127.0.0.1",
        "TIDB_PORT": "4000",
        "TIDB_USER": "root",
        "TIDB_PASSWORD": "",
        "TIDB_DATABASE": "test",
        "COLLECTION_NAME": "kb"
      }
    }
  }
}
```

### 4. Claude Code に接続する

**オプション A — CLI（ワンショット）**

```bash
claude mcp add mcp-docs-tidb uvx \
  --args "--from,git+https://github.com/makocchi-git/mcp-docs-tidb,mcp-docs-tidb" \
  -e TIDB_HOST=127.0.0.1 \
  -e TIDB_PORT=4000 \
  -e TIDB_USER=root \
  -e TIDB_DATABASE=test \
  -e COLLECTION_NAME=kb
```

ユーザーグローバルの `~/.claude/settings.json` にサーバーが追加されます。`--scope project` を付けると `.claude/settings.json`（プロジェクトローカル）に書き込まれます。

**オプション B — `settings.json` を直接編集する**

ユーザーグローバル（`~/.claude/settings.json`）またはプロジェクトローカル（`.claude/settings.json`）：

```json
{
  "mcpServers": {
    "mcp-docs-tidb": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/makocchi-git/mcp-docs-tidb",
        "mcp-docs-tidb"
      ],
      "env": {
        "TIDB_HOST": "127.0.0.1",
        "TIDB_PORT": "4000",
        "TIDB_USER": "root",
        "TIDB_PASSWORD": "",
        "TIDB_DATABASE": "test",
        "COLLECTION_NAME": "kb"
      }
    }
  }
}
```

編集後は Claude Code を再起動するか、`/mcp` を実行して再起動なしでリロードしてください。`/mcp` で `mcp-docs-tidb` が 4 つのツールとともに一覧に表示されていれば正常に動作しています。

### 5. ベクターインデックスを追加する（省略可）

詳細は [ベクターインデックス](#ベクターインデックス) を参照してください。要点：最初のインジェスト前に `TIDB_USE_VECTOR_INDEX=1` を設定するか、後で `ALTER TABLE` を実行します。いずれの場合もクラスターに TiFlash ノードが必要です。

## ベクターインデックス

`docs-tidb-find` は `SELECT ... ORDER BY VEC_COSINE_DISTANCE(embedding, ?) LIMIT N` を発行します。インデックスなしではフルテーブルスキャンになります — 〜10⁴ 行程度なら問題ありませんが、それ以上では遅くなります。TiDB は [HNSW ベクターインデックス](https://docs.pingcap.com/ai/vector-search-index/) をサポートしており、これを近似最近傍探索に変換できます。

### オプション A. `TIDB_USE_VECTOR_INDEX=1` — テーブルと同時に自動作成

最初の行が挿入される**前**に変数を設定してください。サーバーは次のように発行します：

```sql
CREATE TABLE <collection> (
  id        VARCHAR(36) PRIMARY KEY,
  content   TEXT          NOT NULL,
  metadata  JSON          NULL,
  embedding VECTOR(<dim>) NOT NULL,
  VECTOR INDEX `idx_embedding`
    ((VEC_COSINE_DISTANCE(`embedding`))) USING HNSW
);
```

テーブルがすでに存在する場合、このフラグは効果がありません — TiDB はテーブル作成時にのみインデックスを追加します。テーブルを削除するか、オプション B を使用してください。

### オプション B. 既存テーブルへの `ALTER TABLE`

```sql
ALTER TABLE kb
  ADD VECTOR INDEX idx_embedding
    ((VEC_COSINE_DISTANCE(embedding))) USING HNSW;
```

### 要件と注意事項

- **TiDB v8.4+**（v8.5+ 推奨）。古いバージョンは `VECTOR INDEX` 構文をサポートしていません。
- **TiFlash レプリカが必要です。** TiDB はインデックス作成時に自動的に割り当てますが、クラスターに実際に TiFlash ノードが存在している必要があります。存在しない場合、`CREATE TABLE` または `ALTER TABLE` が失敗します。`tiup playground` にはデフォルトで TiFlash が含まれています。一部のセルフホストデプロイメントや最小構成の Docker セットアップには含まれていない場合があります。`SELECT type FROM information_schema.cluster_info WHERE type='tiflash'` で確認してください。
- インデックスは**コサイン距離**（`VEC_COSINE_DISTANCE`）を使用します。`VEC_L2_DISTANCE` が必要な場合は、このフラグを使用せず、自動インデックスを削除して `ALTER TABLE` で独自に作成してください。
- インデックスは**読み取り側のみ**です：書き込みは TiKV 経由、読み取りは TiFlash 経由です。新鮮な挿入がインデックスを通じて検索可能になるまで短い遅延が発生する場合があります。

インデックスなしでも `docs-tidb-find` は動作します — フルスキャンになるだけです。

## エンベッディング次元

embedding カラムは `VECTOR(<dim>)` として宣言されます。`<dim>` は設定されたエンベッディングプロバイダーの `get_vector_size()` が返す値です。一般的な値：

| `EMBEDDING_MODEL` | `<dim>` |
| --- | --- |
| `sentence-transformers/all-MiniLM-L6-v2`（デフォルト） | `384` |
| `BAAI/bge-small-en-v1.5` | `384` |
| `BAAI/bge-base-en-v1.5` | `768` |
| `BAAI/bge-large-en-v1.5` | `1024` |

次元は最初の書き込み時にテーブルに固定（`CREATE TABLE ... VECTOR(<dim>)`）され、その後は変更できません — TiDB が型レベルで強制します。**異なる次元のエンベッディングモデルに切り替える場合は、次のどちらかが必要です：**

1. `COLLECTION_NAME` を新しいテーブルに向ける、または
2. `DROP TABLE <collection>` して次の書き込み時にサーバーが再作成するのを待つ。

既存テーブルに異なるサイズのベクターを書き込もうとすると、TiDB からの `VECTOR` 次元ミスマッチエラーで失敗します。

## Claude Code スキルとして使う

[Claude Code スキル](https://docs.claude.com/en/docs/claude-code/skills) は Claude のコンテキストにオンデマンドで読み込まれる小さな Markdown ドキュメントで、プロジェクト固有のガイダンスを提供します。このリポジトリには [`SKILL.md`](../SKILL.md)（`name: tidb-docs`）が含まれており、各 MCP ツールの使い分け、次元/インデックスの落とし穴、フィルターのセマンティクス、回復手順をカバーしています。

スキルは Claude Code がスキャンする `skills/<name>/SKILL.md` ディレクトリに配置する必要があります。次のいずれかの場所を選んでください：

### プロジェクトスコープ（推奨）

このチェックアウト内で Claude Code を実行する場合にのみ有効です。`SKILL.md` への編集は git で追跡されます。

```bash
mkdir -p .claude/skills/tidb-docs
ln -s "$(pwd)/SKILL.md" .claude/skills/tidb-docs/SKILL.md
```

### ユーザーグローバル

Claude Code で開くすべてのプロジェクトで有効になります。

```bash
mkdir -p ~/.claude/skills/tidb-docs
ln -s "$(pwd)/SKILL.md" ~/.claude/skills/tidb-docs/SKILL.md
```

（シンボリックリンクをサポートしない環境では、`cp` を使用してください。ただし編集後は再コピーが必要です。）

どちらの方法でも、Claude Code を再起動する（または `/skills` を実行する）と新しいスキルが読み込まれます。ロードされると、「これらのドキュメントを TiDB にインジェストして」や「TiDB ナレッジベースを検索して」といった指示でスキルがトリガーされます。

スキルはこのプロジェクトのインストール方法とは**独立しています**。リモートの `mcp-docs-tidb` に接続するプロジェクトに `SKILL.md` を組み込むことも、`tiup playground` インスタンスとローカルで使用することもできます。ガイダンスは同じです。

## TiDB へのドキュメント読み込み

既存のファイルからコレクションを作成するには 2 つの方法があります：

1. **MCP ツール `docs-tidb-ingest`** — LLM に「`~/docs` を `kb` コレクションに読み込んで」と指示する。サーバーが自らファイルを読み込んでチャンクを書き込みます。MCP サーバーとクライアントがファイルシステムを共有している場合の Claude Desktop / Cursor / Windsurf からのインタラクティブな使用に適しています。
2. **`mcp-docs-tidb-ingest` CLI** — LLM との会話外で実行する（cron や CI からコーパスを更新するなど）。MCP ツールと同じコードパスを使用するため、ワークフローに合わせて選択してください。

### CLI

```bash
# ./docs 以下のすべての Markdown ファイルを `kb` テーブルにインジェストする
TIDB_HOST=127.0.0.1 TIDB_PORT=4000 TIDB_USER=root TIDB_DATABASE=test \
  uvx --from git+https://github.com/makocchi-git/mcp-docs-tidb mcp-docs-tidb-ingest \
    --collection kb \
    --recursive --glob '*.md' \
    ./docs

# 編集後に再実行 — 同じファイルはソースファイルごとにアトミックに置き換えられます
uvx --from git+https://github.com/makocchi-git/mcp-docs-tidb mcp-docs-tidb-ingest \
  --collection kb --recursive --glob '*.md' ./docs

# インクリメンタル更新：ディスク上の mtime が TiDB に記録済みの値よりも
# 新しくないファイルをスキップ。変更が少ない大規模コーパスの cron 更新に最適。
uvx --from git+https://github.com/makocchi-git/mcp-docs-tidb mcp-docs-tidb-ingest \
  --collection kb --recursive --glob '*.md' --only-modified ./docs

# フル再構築：最初に全行を削除してから ./docs 以下をすべて再インジェストする。
# 大規模編集後や不整合状態からの回復に便利。
uvx --from git+https://github.com/makocchi-git/mcp-docs-tidb mcp-docs-tidb-ingest \
  --collection kb --recursive --glob '*.md' --truncate ./docs

# 各チャンクに追加メタデータをタグ付けする（フィルタブルフィールドと組み合わせて便利）
# 有効な JSON として解析できる値（数値、真偽値）は自動的にデコードされます
uvx --from git+https://github.com/makocchi-git/mcp-docs-tidb mcp-docs-tidb-ingest \
  --collection kb --recursive --glob '*.md' \
  --extra-metadata category=docs \
  --extra-metadata public=true \
  ./docs

# glob パターンに一致するファイルを除外する（ファイル名またはフルパス）
uvx --from git+https://github.com/makocchi-git/mcp-docs-tidb mcp-docs-tidb-ingest \
  --collection kb --recursive --glob '*.md' \
  --exclude-glob 'CHANGELOG.md' \
  --exclude-glob '*/drafts/*' \
  ./docs
```

フラグ一覧：

| フラグ | デフォルト | 意味 |
| --- | --- | --- |
| `--collection` | _（必須）_ | 対象の TiDB テーブル（最初の書き込み時に自動作成）。 |
| `--chunk-chars` | `2000` | チャンクあたりの文字数。 |
| `--overlap` | `200` | 隣接チャンク間のオーバーラップ文字数。 |
| `-r`, `--recursive` | off | ディレクトリを再帰的に処理する。 |
| `--glob` | `*.md` | ディレクトリ入力に適用する glob パターン。 |
| `--no-replace` | off | 同じソースファイルの既存チャンクを置き換えずに追加する。 |
| `--only-modified` | off | ディスク上の mtime が同じ `source` に記録された値より新しくないファイルをスキップ。既存レコードのないファイルは常に処理される。 |
| `--truncate` | off | インジェスト前にコレクションを `TRUNCATE TABLE` する。スキーマは保持され、全行が削除されてから入力が再チャンク・再エンベッドされる。 |
| `--extra-metadata` | _（未設定）_ | インジェストされる全チャンクに付与する追加の `KEY=VALUE` メタデータ。繰り返し指定可能。有効な JSON（数値、真偽値、配列、オブジェクト）は自動デコード。標準フィールド（`source`、`chunk`、`mtime`、`ingested_at`）は競合するキーより常に優先される。 |
| `--exclude-glob` | _（未設定）_ | スキップするファイルの glob パターン。繰り返し指定可能。ファイル名とフルパスの両方に対してマッチングされる（例：`--exclude-glob 'CHANGELOG.md'`、`--exclude-glob '*/drafts/*'`）。 |
| `-v`, `--verbose` | off | ファイルごとの進捗をログ出力（`--only-modified` でスキップされたファイルを含む）。 |

### 書き込まれる内容

各入力ファイルについて、CLI は次の処理を行います：

1. ファイルを UTF-8 として読み込む。
2. `--chunk-chars` の文字数で `--overlap` のオーバーラップを持つ文字ベースのチャンクに分割する。
3. （デフォルトでは）このファイルの絶対パスと一致する `metadata.source` を持つ既存行を削除する。
4. 各チャンクを `metadata = {"source": "<絶対パス>", "chunk": <0 始まりのインデックス>, "mtime": <ファイル mtime、エポック秒>, "ingested_at": <現在時刻、エポック秒>, ...}` で 1 行ずつ挿入する。`--extra-metadata` のペアが先にマージされ、4 つの標準フィールドはキーが競合した場合に常に優先される。

同じファイルを再インジェストしても、何回実行しても同じ行数が生成されます — cron 駆動の更新に便利です。単一インジェストの全チャンクは同じ `ingested_at` を共有します。`mtime` はインジェスト時点のファイルのディスク上の更新日時です。

### 再インジェストのセマンティクス

- **デフォルト（ソース単位で置き換え）**: 対象ファイルのチャンクのみが削除されます。同じコレクション内の他のファイルは変更されません。
- **`--no-replace`**: 以前にインジェストされたチャンクはそのままで、新しいチャンクが追加されます。バージョン管理された履歴が本当に必要な場合にのみ使用してください。
- **`--only-modified`（インクリメンタル）**: 各入力ファイルのディスク上の mtime が、同じ `source` に対して保存されている最大の `metadata.mtime` と比較されます。ディスク上の mtime が厳密に大きくないファイルはスキップされます（読み込み、エンベッド、書き込みは行われません）。既存レコードのないファイルは常に処理されます。`--no-replace` と組み合わせ可能ですが、よくある組み合わせはデフォルトの `replace=true` + `--only-modified` です。ソースファイルの mtime が信頼できることが前提です（ビルドステップや `git checkout` が mtime を書き換える場合があります）。
- **`--truncate`（フル再構築）**: 入力ファイルが読み込まれる**前**に `TRUNCATE TABLE` でコレクションの全行が削除されます。テーブルスキーマ（`VECTOR(<dim>)` カラムとインデックスを含む）は保持されるため、`DROP TABLE` + 最初のインジェストよりも効率的です。入力ディレクトリからファイルが削除されるなど、コーパスの形状が大きく変わった場合でインクリメンタルな再インジェストで古いチャンクが残る場合に使用してください。`--truncate` と `--no-replace` は共存できますが — トランケートですでにスレートがクリアされているため `--no-replace` は意味をなしません。
- **スキーマ変更**（例：異なる次元のエンベッディングモデルへの切り替え）: CLI はこの状況から回復できません — まず `DROP TABLE <collection>` してから再インジェストしてください。`--truncate` では不十分です（`VECTOR(<dim>)` カラム型が固定されているため）。

### Python API

`mcp-docs-tidb-ingest` は `mcp_docs_tidb.ingest.ingest_paths` のシンラッパーです。独自のパイプラインにインジェストを組み込む必要がある場合は直接呼び出せます：

```python
from pathlib import Path

from mcp_docs_tidb.embeddings.factory import create_embedding_provider
from mcp_docs_tidb.ingest import collect_paths, ingest_paths
from mcp_docs_tidb.settings import EmbeddingProviderSettings, TiDBSettings
from mcp_docs_tidb.tidb import TiDBConnector

connector = TiDBConnector(
    settings=TiDBSettings(),
    embedding_provider=create_embedding_provider(EmbeddingProviderSettings()),
)
try:
    files = collect_paths([Path("docs")], recursive=True, glob="*.md")
    n = ingest_paths(
        files,
        collection_name="kb",
        connector=connector,
        chunk_chars=1500,
        overlap=150,
        only_modified=True,  # mtime が進んでいないファイルはスキップ
        extra_metadata={"team": "platform"},
    )
    print(f"wrote {n} chunks")
finally:
    connector.close()
```

`extra_metadata` はすべてのチャンクのメタデータにマージされます（標準の `source` / `chunk` / `mtime` / `ingested_at` キーと一緒に）。[フィルタブルフィールド](#検索結果のフィルタリング) と組み合わせることで、例えば `team` でフィルタリングできます。

## 検索結果のフィルタリング

`docs-tidb-find` は `metadata` JSON カラム内の値によるフィルタリングをサポートしています。2 つのメカニズムが利用可能です — デプロイメントごとに最大 1 つを選んでください。

### オプション A. 宣言型フィルタブルフィールド（推奨）

フィルタリングしたいメタデータキー、その型、LLM に公開するオペレーターを定義します。宣言された各フィールドは自動作成テーブルの `VIRTUAL` 生成カラムとしてマテリアライズされ、高速なルックアップのためにインデックスが作成されます。

サーバーはこのリストを `TiDBSettings(filterable_fields=...)` コンストラクター引数から読み込むため、小さなラッパーモジュールを作成します：

```python
# my_tidb_server.py
from mcp_docs_tidb.mcp_server import TiDBMCPServer
from mcp_docs_tidb.settings import (
    EmbeddingProviderSettings,
    FilterableField,
    TiDBSettings,
    ToolSettings,
)

tidb_settings = TiDBSettings(
    filterable_fields=[
        FilterableField(
            name="category",
            description="メモリカテゴリ（例：'work'、'personal'）",
            field_type="keyword",
            condition="==",
        ),
        FilterableField(
            name="year",
            description="メモリが参照する年",
            field_type="integer",
            condition=">=",
        ),
        FilterableField(
            name="tags",
            description="これらのタグのいずれかに一致する",
            field_type="keyword",
            condition="any",
        ),
    ],
)

mcp = TiDBMCPServer(
    tool_settings=ToolSettings(),
    tidb_settings=tidb_settings,
    embedding_provider_settings=EmbeddingProviderSettings(),
)

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

`docs-tidb-find` は LLM に対して、型付き引数 `category: str | None`、`year: int | None`、`tags: list[str] | None` として公開されます。

サポートされる `field_type` × `condition` の組み合わせ：

| `field_type` | 使用可能な `condition` |
| --- | --- |
| `keyword` | `==`, `!=`, `any`, `except` |
| `integer` | `==`, `!=`, `>`, `>=`, `<`, `<=`, `any`, `except` |
| `float` | `>`, `>=`, `<`, `<=` |
| `boolean` | `==`, `!=` |

`condition` を省略した場合、フィールドはインデックスが作成されますが、LLM には引数として公開されません。

### オプション B. 任意 JSON フィルター

`TIDB_ALLOW_ARBITRARY_FILTER=1` を設定すると、`docs-tidb-find` に `query_filter` 引数が公開されます。値は JSON オブジェクトです：

```json
{
  "must":     [{"field": "category", "op": "==", "value": "work"}],
  "must_not": [{"field": "archived", "op": "==", "value": "true"}]
}
```

サポートされる `op` 値：`==`, `!=`, `>`, `>=`, `<`, `<=`, `in`, `not_in`。

このモードはフィールドを事前に宣言する必要がなく（インデックスも作成されないため）、アドホックな探索に便利ですが、大きなテーブルでは遅くなります。

## TiDB Serverless への接続

TiDB Serverless は TLS を必要とします。`TIDB_SSL_CA` をシステムの CA バンドルに向けてください：

```bash
TIDB_HOST=gateway01.us-west-2.prod.aws.tidbcloud.com \
TIDB_PORT=4000 \
TIDB_USER='xxxxx.root' \
TIDB_PASSWORD='your-password' \
TIDB_DATABASE='test' \
TIDB_SSL_VERIFY_CERT=1 \
TIDB_SSL_CA=/etc/ssl/cert.pem \
COLLECTION_NAME=kb \
uvx --from git+https://github.com/makocchi-git/mcp-docs-tidb mcp-docs-tidb
```

## 開発

```bash
uv sync
uv run pytest
uv run ruff check src
uv run mypy src
```

## ライセンス

Apache License 2.0.
