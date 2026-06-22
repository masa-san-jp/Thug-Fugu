# Thug-Fugu Local LLM Orchestration

Thug AI の Fugu のように、複数ロールのローカル LLM を協調実行するための最小 Python 実装です。

この実装は標準ライブラリだけで動き、Ollama と OpenAI 互換サーバー（LM Studio、llama.cpp server、vLLM など）をバックエンドとして扱えます。

## できること

- JSON 設定でローカル LLM とロールを定義
- planner / coder / reviewer / synthesizer などの複数ロールを並列実行
- synthesizer ロールによる回答統合
- OpenAI Chat Completions 互換のローカル API
- CLI からの単発実行
- 実 LLM なしで動く `echo` backend によるテスト

## クイックスタート

### 1. 設定確認

```bash
python3 -m fugu_local validate-config --config examples/fugu-local.echo.json
```

### 2. 単発実行

```bash
PYTHONPATH=src python3 -m fugu_local run \
  --config examples/fugu-local.echo.json \
  "ローカルLLMのオーケストレーション設計をレビューして"
```

インストール後は以下でも実行できます。

```bash
fugu-local run --config examples/fugu-local.echo.json "質問内容"
```

### 3. HTTP サーバー起動

```bash
PYTHONPATH=src python3 -m fugu_local serve \
  --config examples/fugu-local.echo.json \
  --host 127.0.0.1 \
  --port 8080
```

### 4. OpenAI Chat Completions 互換リクエスト

```bash
curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{
    "model": "fugu-local",
    "messages": [{"role": "user", "content": "実装計画を作って"}],
    "temperature": 0.2
  }'
```

## Ollama 例

Ollama が `http://localhost:11434` で起動済みで、`llama3.1` が利用可能な場合:

```bash
PYTHONPATH=src python3 -m fugu_local run \
  --config examples/fugu-local.ollama.json \
  "Python API のエラーハンドリング方針を考えて"
```

## 設計仕様

開発チーム向けの設計仕様書は以下を参照してください。

- `docs/design/local-llm-orchestration.md`

## テスト

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## 注意

- デフォルトの HTTP bind は `127.0.0.1` を推奨します。
- 外部公開する場合は、リバースプロキシ側で認証と TLS を設定してください。
- ストリーミング、tool calling、GPU スケジューリングはこの最小実装の対象外です。
