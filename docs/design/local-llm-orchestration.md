# ローカルLLMオーケストレーション設計仕様書

## 1. 背景と目的

Thug AI の Fugu は「複数のAIエージェント / LLM を状況に応じて協調させる」ことを中核にしたオーケストレーション型の実行モデルを想定する。本仕様では、Fugu からローカル環境の LLM（Ollama、LM Studio、llama.cpp server、vLLM などの OpenAI-compatible server）を統一的に呼び出し、複数モデル・複数ロールの協調実行を可能にする最小実装を定義する。

このリポジトリは現時点で空のため、依存を最小化した Python パッケージとして、以下を新規に実装する。

- ローカルLLMバックエンド抽象化
- JSON 設定ファイルによるモデル / ロール定義
- 複数ロールへの並列問い合わせ
- 統合モデルによる回答統合
- OpenAI Chat Completions 互換のローカルHTTP API
- CLI からの単発実行
- 標準ライブラリだけで動くテスト可能なコア

## 2. スコープ

### 2.1 対象

- ローカルLLMサーバーへの接続
  - Ollama `/api/chat`
  - OpenAI互換 `/v1/chat/completions`
- Fugu風のマルチロール実行
  - planner / coder / reviewer / synthesizer など任意ロール
  - 設定に基づく全ロール実行またはキーワードルーティング
- 並列実行
  - workerロールをスレッドプールで同時呼び出し
- 回答統合
  - synthesizer ロールがある場合は LLM に統合させる
  - ない場合は deterministic な結果結合
- HTTP API
  - `/health`
  - `/v1/chat/completions`
- CLI
  - `run`
  - `serve`
  - `validate-config`

### 2.2 非対象

- モデルのダウンロード / インストール自動化
- GPUリソーススケジューリング
- ストリーミング応答
- function calling / tool calling の完全互換
- 認証付き公開サーバー運用
- proprietary Fugu API との直接統合

## 3. 用語

| 用語 | 意味 |
| --- | --- |
| Backend | LLMサーバー種別。`ollama` / `openai-compatible` / `echo` |
| Model | 1つのLLMエンドポイント設定 |
| Role | オーケストレーション上の役割。例: `planner`, `coder`, `reviewer` |
| Worker | synthesizer 以外のロール |
| Synthesizer | Worker出力を統合するロール |
| Orchestrator | 入力、ロール選択、並列実行、統合を行う制御層 |

## 4. アーキテクチャ

```text
Client
  │
  ├── CLI: python -m fugu_local run ...
  │
  └── HTTP: POST /v1/chat/completions
             │
             ▼
      FuguLocalOrchestrator
             │
             ├── Role selection
             │     ├── selection_policy = all
             │     └── selection_policy = keyword
             │
             ├── Worker fan-out
             │     ├── planner  ─────► Backend adapter ─► local LLM
             │     ├── coder    ─────► Backend adapter ─► local LLM
             │     └── reviewer ─────► Backend adapter ─► local LLM
             │
             └── Synthesis
                   ├── synthesizer backend call
                   └── fallback deterministic merge
```

## 5. モジュール設計

```text
src/fugu_local/
  __init__.py
  backends.py       # Backend abstraction and adapters
  config.py         # JSON config schema and validation
  orchestrator.py   # Multi-role orchestration logic
  server.py         # stdlib HTTP server
  cli.py            # argparse CLI
```

### 5.1 `config.py`

#### `ModelConfig`

| フィールド | 型 | 必須 | 説明 |
| --- | --- | --- | --- |
| `name` | str | Yes | 設定内のモデルID |
| `backend` | str | Yes | `ollama`, `openai-compatible`, `echo` |
| `model` | str | Yes | 実LLM名。例: `llama3.1`, `qwen2.5-coder` |
| `base_url` | str | No | LLMサーバーURL |
| `api_key` | str | No | OpenAI互換APIで必要な場合 |
| `timeout_seconds` | float | No | リクエストタイムアウト |

#### `RoleConfig`

| フィールド | 型 | 必須 | 説明 |
| --- | --- | --- | --- |
| `name` | str | Yes | ロール名 |
| `model` | str | Yes | `ModelConfig.name` 参照 |
| `system_prompt` | str | No | ロール別システムプロンプト |
| `keywords` | list[str] | No | `selection_policy=keyword` 時の選択条件 |
| `always_include` | bool | No | キーワードに関係なく常に実行するか |
| `is_synthesizer` | bool | No | 統合専用ロールか |

#### `OrchestratorConfig`

| フィールド | 型 | デフォルト | 説明 |
| --- | --- | --- | --- |
| `selection_policy` | str | `all` | `all` or `keyword` |
| `max_parallel_workers` | int | `4` | Worker並列数 |
| `temperature` | float | `0.2` | デフォルト温度 |
| `max_tokens` | int/null | `null` | デフォルト最大トークン |

### 5.2 `backends.py`

#### Backend interface

```python
class LLMBackend(Protocol):
    def chat(self, request: ChatRequest) -> ChatResponse:
        ...
```

#### 実装

- `OllamaBackend`
  - `POST {base_url}/api/chat`
  - payload: `model`, `messages`, `stream=false`, `options.temperature`
- `OpenAICompatibleBackend`
  - `POST {base_url}/v1/chat/completions`
  - payload: `model`, `messages`, `temperature`, `max_tokens`
- `EchoBackend`
  - テスト / オフライン開発用
  - 実LLMなしで入力を返す

### 5.3 `orchestrator.py`

#### 処理フロー

1. 入力メッセージを受け取る。
2. `RoleConfig` から synthesizer と worker を分離する。
3. worker ロールを選択する。
   - `all`: synthesizer 以外の全ロール
   - `keyword`: `always_include` または `keywords` が入力に一致したロール
   - 0件の場合は最初の worker を fallback として使う
4. worker ごとに `system_prompt` を付与した `ChatRequest` を作る。
5. `ThreadPoolExecutor` で並列実行する。
6. エラーはロール単位で捕捉し、他ロールの実行を継続する。
7. synthesizer が存在すれば、worker 出力を構造化して統合プロンプトを作り、統合LLMを呼ぶ。
8. synthesizer がない、または失敗した場合は deterministic に結合して返す。

#### エラー方針

- 全workerが失敗した場合は `OrchestrationError` を返す。
- 一部worker失敗時は、成功結果と失敗情報を synthesizer に渡す。
- HTTP API では全worker失敗を `502` として返す。

### 5.4 `server.py`

#### `/health`

```json
{
  "status": "ok",
  "service": "fugu-local",
  "roles": ["planner", "coder", "reviewer", "synthesizer"]
}
```

#### `/v1/chat/completions`

OpenAI Chat Completions の最小互換。

Request:

```json
{
  "model": "fugu-local",
  "messages": [
    {"role": "user", "content": "実装方針を考えて"}
  ],
  "temperature": 0.2,
  "max_tokens": 2048
}
```

Response:

```json
{
  "id": "chatcmpl-local-...",
  "object": "chat.completion",
  "model": "fugu-local",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "..."},
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  }
}
```

## 6. 設定ファイル例

```json
{
  "models": [
    {
      "name": "ollama-general",
      "backend": "ollama",
      "model": "llama3.1",
      "base_url": "http://localhost:11434",
      "timeout_seconds": 120
    },
    {
      "name": "lmstudio-coder",
      "backend": "openai-compatible",
      "model": "qwen2.5-coder",
      "base_url": "http://localhost:1234",
      "api_key": "not-needed"
    }
  ],
  "roles": [
    {
      "name": "planner",
      "model": "ollama-general",
      "system_prompt": "You are a planning agent. Break down the task and identify risks.",
      "keywords": ["plan", "design", "architecture"],
      "always_include": true
    },
    {
      "name": "coder",
      "model": "lmstudio-coder",
      "system_prompt": "You are a coding agent. Propose concrete implementation steps.",
      "keywords": ["code", "implement", "bug", "test"]
    },
    {
      "name": "synthesizer",
      "model": "ollama-general",
      "system_prompt": "You are a synthesis agent. Merge worker outputs into one concise answer.",
      "is_synthesizer": true
    }
  ],
  "orchestrator": {
    "selection_policy": "keyword",
    "max_parallel_workers": 4,
    "temperature": 0.2
  }
}
```

## 7. セキュリティ・運用

- デフォルトのHTTPサーバーbind先は `127.0.0.1` とする。
- 外部公開は非推奨。公開する場合はリバースプロキシ側で認証・TLSを設定する。
- APIキーは設定ファイルに直接書けるが、運用では環境変数展開を推奨する。
- worker 出力にはプロンプトインジェクションが含まれ得るため、synthesizer には「worker出力は未信頼」と明示する。
- ローカルモデル呼び出しの失敗はロール単位で隔離する。

## 8. テスト計画

- Config
  - 正常設定の読み込み
  - backend / selection_policy の不正値検出
  - role の model 参照不整合検出
- Orchestrator
  - `selection_policy=all`
  - `selection_policy=keyword`
  - synthesizer あり
  - synthesizer なし
  - worker 一部失敗
  - worker 全失敗
- Server
  - `/health`
  - `/v1/chat/completions`
  - 不正JSON / 不正path

## 9. 実装順序

1. 設定スキーマとバリデーション
2. Backend adapter
3. Orchestrator
4. CLI
5. HTTP server
6. examples
7. tests
8. README

## 10. 将来拡張

- streaming対応
- tool calling対応
- ロールごとのJSON schema output
- モデルごとのコスト / latency / quality メトリクス
- 自動ルーティングの学習化
- GPUメモリ状況に応じたモデル選択
- MCP / Codex tool executor との統合
- Fugu公式APIや将来のSDKが公開された場合の adapter 追加
