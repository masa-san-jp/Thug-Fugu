# Thug-Fugu Local LLM Orchestration

Thug AI の Fugu のように、複数ロールのローカル LLM を協調実行するための最小 Python 実装です。

**Status:** Local-first experimental. Built-in HTTP server はローカル開発 / private network 用であり、公開インターネット向けの hardened API server ではありません。外部公開する場合は reverse proxy 側で TLS、認証、rate limit、request size limit を設定してください。

標準ライブラリだけで動き、**Ollama** と **OpenAI 互換サーバー**（LM Studio、llama.cpp server、vLLM など）をバックエンドとして扱えます。planner / coder / reviewer などの複数ロールを並列実行し、synthesizer ロールが 1 つの回答に統合します。

- 設計仕様: [docs/design/local-llm-orchestration.md](docs/design/local-llm-orchestration.md)
- 分散構成（拡張）: [docs/design/distributed-inference.md](docs/design/distributed-inference.md)
- 運用セキュリティ: [docs/operations/security-profile.md](docs/operations/security-profile.md)
- OpenAI 互換範囲: [docs/reference/openai-compatibility.md](docs/reference/openai-compatibility.md)
- usage accounting 方針: [docs/reference/usage-accounting.md](docs/reference/usage-accounting.md)
- セキュリティポリシー: [SECURITY.md](SECURITY.md)

> 補足：「Fugu のように」は **発想（複数ロールの協調実行）のオマージュ**です。本実装は外部の proprietary Fugu API には依存せず、ローカル backend（Ollama / OpenAI 互換 / echo）だけで動く独立実装です。

---

## できること

- JSON 設定でローカル LLM とロールを宣言的に定義
- 複数ロール（planner / coder / reviewer / synthesizer など）の並列実行
- synthesizer ロールによる回答統合（無い場合は決定論的マージにフォールバック）
- お題に応じてロールを動的選抜（`keyword` ポリシー）
- OpenAI Chat Completions 互換のローカル HTTP API（`serve`）
- CLI からの単発実行（`run`）
- 実 LLM なしで動く `echo` backend によるテスト

---

## アーキテクチャ

```mermaid
flowchart TD
    U[ユーザー入力] --> O[Orchestrator]
    O -->|selection_policy で選抜| W1[worker: planner]
    O --> W2[worker: coder]
    O --> W3[worker: reviewer]
    W1 --> S[synthesizer ロール]
    W2 --> S
    W3 --> S
    S --> A[統合された回答]
    O -. synthesizer 無し/失敗時 .-> M[決定論的マージ] --> A
```

- worker ロール（`is_synthesizer: false`）は **並列実行**される（`ThreadPoolExecutor`、最大 `max_parallel_workers`）。各ロールは独立で、1 つが失敗しても他は続行する。
- `is_synthesizer: true` のロールが **統合**を担う。複数あれば先頭の 1 つが使われる。
- synthesizer が無い、または統合中に例外が出た場合は、worker 出力を**決定論的にマージ**して返す（`synthesis_error` に理由を記録）。
- worker が**全滅**した場合のみエラー（`OrchestrationError`）。

---

## クイックスタート

### 1. 設定確認

```bash
python3 -m fugu_local validate-config --config examples/fugu-local.echo.json
# => OK: 2 model(s), 4 role(s), selection_policy=keyword
```

### 2. 単発実行

```bash
PYTHONPATH=src python3 -m fugu_local run \
  --config examples/fugu-local.echo.json \
  "ローカルLLMのオーケストレーション設計をレビューして"
```

インストール後は `fugu-local run --config ... "質問"` でも実行できます。

### 3. HTTP サーバー（OpenAI Chat Completions 互換）

```bash
PYTHONPATH=src python3 -m fugu_local serve \
  --config examples/fugu-local.ollama.json --host 127.0.0.1 --port 8080 \
  --max-concurrent-requests 8   # 同時処理の上限。超過時は HTTP 429。/health に現在の上限を表示

curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"fugu-local","messages":[{"role":"user","content":"実装計画を作って"}],"temperature":0.2}'
```

---

## 設定リファレンス

設定は 1 つの JSON で `models` / `roles` / `orchestrator` の 3 ブロックからなります。

### `models[]`

| フィールド | 型 | 必須 | 既定 | 説明 |
|---|---|---|---|---|
| `name` | string | ✅ | — | モデルの参照名（roles から参照。一意） |
| `backend` | string | ✅ | — | `ollama` / `openai-compatible` / `echo` |
| `model` | string | ✅ | — | バックエンド側のモデル名（例 `llama3.1`, `gpt-oss:120b`） |
| `base_url` | string | △ | `null` | エンドポイント URL。`ollama` / `openai-compatible` では **必須** |
| `api_key` | string | — | `null` | `openai-compatible` 用。`${ENV_VAR}` は環境変数に展開される |
| `timeout_seconds` | number | — | `120.0` | 1 リクエストのタイムアウト（> 0） |

### `roles[]`

| フィールド | 型 | 必須 | 既定 | 説明 |
|---|---|---|---|---|
| `name` | string | ✅ | — | ロール名（一意） |
| `model` | string | ✅ | — | 使う `models[].name`（存在しないとエラー） |
| `system_prompt` | string | — | `""` | そのロールの役割定義 |
| `keywords` | string[] | — | `[]` | `keyword` ポリシー時の選抜キーワード |
| `always_include` | bool | — | `false` | キーワードに関係なく常に選抜 |
| `is_synthesizer` | bool | — | `false` | 統合担当にする（worker からは除外される） |

### `orchestrator`

| フィールド | 型 | 既定 | 説明 |
|---|---|---|---|
| `selection_policy` | string | `"all"` | `all` または `keyword` |
| `max_parallel_workers` | int | `4` | 並列ワーカー数の上限（> 0） |
| `temperature` | number | `0.2` | 生成温度 |
| `max_tokens` | int? | `null` | 生成トークン上限（指定時は > 0） |
| `request_timeout_seconds` | number? | `null` | リクエスト全体のデッドライン秒（指定時は > 0）。各ロールの `timeout_seconds` とは別軸 |

### selection_policy の挙動

- **`all`**（既定）：synthesizer 以外の全 worker ロールを実行。
- **`keyword`**：`always_include` のロール＋**最新のユーザーメッセージ**に `keywords` のいずれかが（大文字小文字を無視して）含まれるロールを実行（過去の assistant / system メッセージは選抜に影響しない）。**1 つも一致しなければ先頭の worker ロールにフォールバック**する。

### request_timeout_seconds（リクエストデッドライン）

- `orchestrator.request_timeout_seconds` を指定すると、**リクエスト全体の上限時間**を設けます。各ロールの `timeout_seconds`（バックエンド単位）とは別軸で、user から見た総レイテンシを制御します。
- デッドライン到達時は未完了の worker を待たずに打ち切り、`WorkerResult.timed_out=true`・`error` 付きで結果に残します（バックグラウンドの呼び出しは各自の `timeout_seconds` で終了）。
- **1 つでも成功していれば**、その時点の出力で合成（または決定論マージ）して返します。デッドラインを過ぎている場合は synthesizer 呼び出しをスキップして即時にマージします。
- **全 worker がデッドラインに間に合わなければ** `OrchestrationError` を送出します。
- 未指定（既定）なら従来どおりデッドラインなしで全 worker を待ちます。

### バリデーション

models / roles が各 1 件以上、名前が一意、`backend` がサポート対象、`timeout_seconds` > 0、`ollama`/`openai-compatible` は `base_url` 必須、`roles[].model` が実在、`selection_policy` がサポート対象、`max_parallel_workers` > 0、`max_tokens`（指定時）> 0、`request_timeout_seconds`（指定時）> 0。違反は `ConfigError`。

---

## 手元のモデルに差し替える

`examples/fugu-local.ollama.json` は `llama3.1` / `qwen2.5-coder` を例にしています。手元の Ollama にあるモデル名へ `models[].model` を変えるだけで動きます。

例：`gpt-oss:120b`（高精度ロール）と `gpt-oss:20b`（高速ロール）を使う（`examples/fugu-local.gpt-oss.json`）。

```json
{
  "models": [
    { "name": "oss-120b", "backend": "ollama", "model": "gpt-oss:120b", "base_url": "http://localhost:11434", "timeout_seconds": 300 },
    { "name": "oss-20b",  "backend": "ollama", "model": "gpt-oss:20b",  "base_url": "http://localhost:11434", "timeout_seconds": 300 }
  ],
  "roles": [
    { "name": "planner",     "model": "oss-120b", "system_prompt": "計画担当。タスクを分解しリスクを挙げる。", "keywords": ["設計","plan"], "always_include": true },
    { "name": "coder",       "model": "oss-20b",  "system_prompt": "実装担当。具体的な手順を出す。", "keywords": ["実装","code"] },
    { "name": "reviewer",    "model": "oss-120b", "system_prompt": "レビュアー。正しさ・安全・運用リスクを点検。", "keywords": ["レビュー","risk"] },
    { "name": "synthesizer", "model": "oss-120b", "system_prompt": "worker 出力を 1 つの簡潔な回答に統合。", "is_synthesizer": true }
  ],
  "orchestrator": { "selection_policy": "keyword", "max_parallel_workers": 4, "temperature": 0.2 }
}
```

OpenAI 互換サーバー（LM Studio / vLLM 等）を使う場合は `backend: "openai-compatible"`、`base_url`、必要なら `api_key: "${OPENAI_API_KEY}"`（環境変数展開）を指定します。

---

## バックエンド

| backend | 用途 | 必須 |
|---|---|---|
| `ollama` | ローカル Ollama サーバー | `base_url`（例 `http://localhost:11434`） |
| `openai-compatible` | LM Studio / llama.cpp server / vLLM 等の OpenAI 互換 API | `base_url`、必要に応じ `api_key` |
| `echo` | 実 LLM を呼ばず入力をそのまま返す。テスト・配線確認用 | なし |

---

## パフォーマンス特性

- worker は並列に投げられますが、**1 GPU の Ollama がバックエンドだと実際は直列**に処理されます（GPU が 1 つなら同時実行の旨味は出ない）。`max_parallel_workers` は「同時に投げる上限」であって、GPU が増えない限り総時間は概ね各ロールの直列和になります。
- 参考実測：`gpt-oss:120b` を 3 ロール＋`gpt-oss:20b` を 1 ロール、単一 GPU で約 **2 分 38 秒 / 1 回**。
- 速くしたい場合：ロール数を絞る、軽いモデルを混ぜる、`max_tokens` を抑える、`temperature` を下げる。単一GPU(GX10/MBP)での並列ロールや複数GPUの静的割当は [role/model assignment](docs/operations/multi-gpu-role-assignment.md)、複数ノードへ水平分散する場合は [distributed-inference.md](docs/design/distributed-inference.md) を参照。
- 1 つの config から必要なローカルサーバ群（ポート/モデル）を導出して起動コマンドを出すには `scripts/serve_local_models.py --config <config>` を使う（既定は表示のみ）。
- MBP（Apple M4 Max）+ `qwen2.5:0.5b` の実測では `OLLAMA_NUM_PARALLEL=2` が最良平均（1.54x vs `=1`）でした。まず `=2` から試し、モデル/プロンプトごとに測ってください。

---

## モデルプール / フェイルオーバー / ロードバランス

`model_pools[]` を使うと、1つの論理モデル名を複数エンドポイント（別ポート/別ノード）に束ね、role から参照できます。既存 `models[]` のみの設定はそのまま動きます（後方互換）。

```json
{
  "model_pools": [
    {
      "name": "fast-pool",
      "backend": "ollama",
      "model": "gpt-oss:20b",
      "endpoints": ["http://127.0.0.1:11434", "http://127.0.0.1:11435"],
      "policy": "least_busy"
    }
  ],
  "roles": [
    {"name": "thinker", "model": "fast-pool", "always_include": true}
  ]
}
```

- `policy`: `round_robin`（呼び出しごとに先頭メンバーをローテーション）または `least_busy`（同時実行中の最も少ないメンバーを優先）。
- **フェイルオーバー**: あるメンバーが失敗したら同プールの次メンバーへ再試行。全メンバー失敗で初めてそのロールが失敗扱いになる。
- role は `models[].name` でも `model_pools[].name` でも参照可能（名前空間は一意）。
- サンプル: `examples/fugu-local.model-pool.json`。
- 現状は最小縦切りで、定期 health check は未実装（失敗時フェイルオーバーで代替）。動的発見やキューは今後。

例:

```bash
PYTHONPATH=src python3 -m fugu_local run \
  --config examples/fugu-local.model-pool.json \
  "設計案を作り、別視点でレビューして"
```

## 適応コーディネーター（Fugu-style）

`coordinator.enabled=true` を設定すると、固定ロール実行の前段に軽量な triage 層が入り、最新 user message を見て処理形態を選びます。既存設定では `enabled=false` が既定なので後方互換です。

対応済みの最小縦切り:

- `direct`: 1 worker へ単発で投げる
- `role_split`: 既存の worker 並列 + synthesizer 統合
- `parallel_ensemble`: 同一 role を N 並列で走らせ、`synth` または `majority` で統合
- ルール/ヒューリスティック/meta-call(JSON抽出)の順で plan を決定
- `OrchestrationResult.pattern` / `plan_reason` / `plan_source` と構造化ログで plan を確認可能

例:

```bash
PYTHONPATH=src python3 -m fugu_local run \
  --config examples/fugu-local.coordinator.json \
  "複数案を比較して"
```

設計の全体像は [Fugu-style coordinator spec](docs/design/fugu-style-coordinator-spec.md) を参照してください。現時点では Phase 1 の最小実装で、model pool / health / failover / verifier retry loop / recursive coordination は後続フェーズです。

## ログ / オブザーバビリティ

- 各オーケストレーション実行に `run_id` が付与され、`fugu_local.orchestrator` ロガーが INFO で 1 行の構造化サマリ（run_id・総レイテンシ・選抜ロール・synthesizer・各ロールの model / ok / latency_ms / エラー要約）を出力します。
- **プロンプト本文・生成結果はログに出しません**（既定で非機微）。同じレコードの詳細が要るときは当ロガーを `DEBUG` に上げてください。
- 全ロール失敗時は `WARNING` にエラー要約を出します。
- プログラムからは `OrchestrationResult.run_id` / `.latency_ms` と各 `WorkerResult.latency_ms` で計測値を取得できます。

```python
import logging
logging.getLogger("fugu_local.orchestrator").setLevel(logging.INFO)  # 既定の構造化ログ
# logging.getLogger("fugu_local.orchestrator").setLevel(logging.DEBUG)  # 詳細レコード
```

---

## トラブルシュート

| 症状 | 原因 / 対処 |
|---|---|
| `base_url is required for backend 'ollama'` | `models[].base_url` を指定（例 `http://localhost:11434`） |
| 接続エラー / タイムアウト | Ollama が起動しているか（`ollama serve`）、`model` が pull 済みか（`ollama pull <model>`）、`timeout_seconds` を延長 |
| 推論モデル（gpt-oss 等）で**回答が空**になる | 推論モデルは答えの前に大量の「思考」トークンを使う。`max_tokens` が小さいと思考の途中で打ち切られ空になることがある → `max_tokens` を十分大きく取る（または未指定にしてモデル既定に委ねる） |
| 別 PC の Ollama に繋がらない | Ollama は既定で `127.0.0.1` のみ待受。`OLLAMA_HOST=0.0.0.0` で起動し、ポート 11434 を許可。LAN/Tailscale 内に限定し公開しない |
| `Role '...' references unknown model '...'` | `roles[].model` が `models[].name` と一致しているか確認 |
| `All worker roles failed` | 各 worker のエラーが連結表示される。モデル名・base_url・サーバー稼働を確認 |

---

## ユースケース例

- **設計レビュー**：planner で分解 → reviewer で正しさ/安全/リスク点検 → synthesizer で統合。
- **多視点の意思決定支援**：複数の reviewer に異なる観点（正確性 / セキュリティ / 運用）を持たせて立体化。
- **コードレビュー / 実装計画**：coder（実装手順）＋ reviewer（リスク）の二役。
- 既存ツールから「1 つのローカル LLM」として使いたい場合は `serve` して `/v1/chat/completions` を叩く。

---

## 拡張ポイント

- **新しい backend**：`src/fugu_local/backends.py` にバックエンドを追加し、`config.SUPPORTED_BACKENDS` に登録。
- **新しい selection_policy**：`orchestrator._select_worker_roles` に分岐を追加し、`config.SUPPORTED_SELECTION_POLICIES` に登録。
- **ロール追加**：config の `roles[]` に足すだけ（コード変更不要）。
- **複数GPU/複数マシン分散**：単一ホストの複数GPUは [docs/operations/multi-gpu-role-assignment.md](docs/operations/multi-gpu-role-assignment.md)、複数ノードは [docs/design/distributed-inference.md](docs/design/distributed-inference.md) を参照（`models[].base_url` を各 endpoint へ向ける静的分散は追加実装なしで可能）。

---

## テスト / 品質チェック

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

開発用ツールを入れる場合:

```bash
python3 -m pip install -e '.[dev]'
```

CI と同等の品質チェック:

```bash
python3 -m ruff check src tests
python3 -m ruff format --check src tests
PYTHONPATH=src python3 -m coverage run -m unittest discover -s tests -v
python3 -m coverage report --fail-under=80
```

`echo` backend を使えば実 LLM なしでオーケストレーションの配線をテストできます。

---

## 制限事項

- tool calling、GPU スケジューリングはこの最小実装の対象外です。tool calling の設計方針は [tool-calling-support.md](docs/design/tool-calling-support.md) を参照してください。
- ロール選抜は静的（`all` / `keyword`）で、ラウンド間の動的な指示更新や反復ループは持ちません（必要なら呼び出し側で実装します）。
- `/v1/chat/completions` は最小対応です。`stream: true` は buffered SSE（生成後に chunk 化）で対応します。対応範囲は [OpenAI 互換範囲](docs/reference/openai-compatibility.md) を参照してください。
- `usage` は現時点では互換用プレースホルダーです。方針は [usage accounting 方針](docs/reference/usage-accounting.md) を参照してください。

## セキュリティ注意

- デフォルトの HTTP bind は `127.0.0.1` を推奨します。
- `0.0.0.0`、`::`、LAN IP、ホスト名など非 loopback に bind する場合は、明示的に `--allow-unsafe-bind` が必要です。
- 外部公開する場合は、リバースプロキシ側で認証、TLS、リクエストサイズ制限、レート制限を設定してください。Ollama 自体は認証を持たないため、LAN / Tailscale 内に限定してください。
- Backend HTTP error body は、prompt / completion / credential の漏えいを避けるため user-visible error から redaction されます。
- `api_key` は設定に直接書かず、`${ENV_VAR}` 展開で環境変数から渡せます。
- 脆弱性を見つけた場合は、公開 issue に exploit details を書かず [SECURITY.md](SECURITY.md) に従って報告してください。
- 詳細は [運用セキュリティ](docs/operations/security-profile.md) を参照してください。
