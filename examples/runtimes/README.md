# Runtime conformance recipes

このディレクトリは、同じ Thug-Fugu の並列実行設定をどの推論サーバーへ
向けられるかを確認するための最小レシピです。各 JSON は通常の
`fugu-local` 設定としてそのまま読み込めます。`runtime_conformance` は
コア設定ローダーが無視する検証用メタデータです。

## 検証の実行

fixture だけで外部ランタイムへ接続せず、契約形状を確認できます。

```bash
python3 scripts/check_runtime_conformance.py \
  --manifest examples/runtimes/ollama.json \
  --fixture tests/fixtures/runtime-conformance/ollama.json \
  --output artifacts/runtime-conformance-ollama.json
```

実ランタイムを検証する場合は `--fixture` を外します。これはモデル生成を
発生させるため、利用可能なモデル名へ `models[0].model` を変更してから実行
してください。ランナーはサーバーを起動せず、インストールやモデル取得も
行いません。

5種類を1つの SSOT アーティファクトへまとめる例です。

```bash
python3 scripts/check_runtime_conformance.py \
  --manifest examples/runtimes/ollama.json \
  --manifest examples/runtimes/openai-compatible.json \
  --manifest examples/runtimes/llama-cpp.json \
  --manifest examples/runtimes/vllm.json \
  --manifest examples/runtimes/sglang.json \
  --output artifacts/runtime-conformance-matrix.json
```

出力は `supported` / `unsupported` / `unknown` のいずれかを全項目に記録
します。fixture の `unverified` は実ランタイムの証拠ではありません。
エンドポイント、API キー、ローカルパス、プロンプト、応答は共有用出力から
サニタイズまたは省略されます。

## レシピの使い分け

| ファイル | サーバー | Thug-Fugu backend | 起動例 | 実ハードウェア検証 |
| --- | --- | --- | --- | --- |
| `ollama.json` | Ollama native API | `ollama` | `ollama serve` | 未確認 |
| `openai-compatible.json` | 任意の OpenAI 互換 API | `openai-compatible` | 既存サーバーを指定 | 未確認 |
| `llama-cpp.json` | llama.cpp server | `openai-compatible` | `llama-server ...` | 未確認 |
| `vllm.json` | vLLM | `openai-compatible` | `vllm serve ...` | 未確認 |
| `sglang.json` | SGLang | `openai-compatible` | `python -m sglang.launch_server ...` | 未確認 |

Apple Silicon の個人利用では Ollama が導入・運用の簡潔さに優れます。一方、
GPU サーバーで同時実行数、キュー、prefix cache を評価する場合は llama.cpp、
vLLM、SGLang のサーバー固有設定を別途測定してください。このレシピだけで
性能勝者や機能対応を推測してはいけません。
