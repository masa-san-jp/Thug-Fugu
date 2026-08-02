# Phase 1 評価系是正 / 逐次推論DAG 実装計画

Status: planned（未着手）。親Issue: #106、Epic: #69。

本書は #106 のコメント「原因分析と改善設計案」を、コーディングエージェントが
**1 PR 単位で自律実装できる粒度**に分解した実装計画である。実装担当は
**Sonnet 5 クラスのコーディングエージェント**を想定し、設計判断・アルゴリズム・
プロンプト本文・作問手順まで本書で確定させてある。実装者が新たに設計を起こす
必要がないことを目標とし、判断余地が残る作業はレシピ（§3.10）または初版固定
（§5.4.1）で閉じている。

対象範囲は「Phase 2 着手可否を数値で判定できる状態にすること」までであり、
Phase 3 以降の分散基盤拡張は含まない。

---

## 0. 実行プロトコル（全 WP 共通・必読）

### 0.1 作業単位

- 作業単位は **1 サブ WP = 1 branch = 1 PR**。分割は §1.2 の「PR 分割（固定）」列で
  確定してあり、実装時に裁量で分割・統合しない。サブ WP を持たない WP は WP 全体で 1 PR。
- ブランチ名: `feat/wp<N><a-z>-<slug>`（例: `feat/wp1a-backend-seed`）
- PR タイトル: `WP<N><a-z>: <要約>`（サブ WP が無い場合は `WP<N>: <要約>`）
- PR 本文に、本書の該当節へのリンクと「完了条件」チェックリストを転記する。
- 各 PR は単体でテストが通り、既存挙動を壊さないこと。前のサブ WP に依存する場合は
  PR 本文に依存を明記し、マージ順を守る。

### 0.2 必須ゲート（`CONTRIBUTING.md` 準拠）

各 PR で以下がすべて成功すること。

```bash
python -m pip install '.[dev]'
python -m ruff check src tests scripts
python -m ruff format --check src tests scripts
PYTHONPATH=src python -m coverage run -m unittest discover -s tests -v
python -m coverage report --fail-under=85
python -m build
```

`scripts` を ruff の対象に加えるのは WP-1 の作業に含める（`pyproject.toml` の
`[tool.ruff] src` を更新）。WP-1 マージ前は `src tests` のみを対象にしてよい。

### 0.3 テストは LLM なしで完結させる

- 実 LLM・実ネットワークを必要とするテストを追加しない。
- オフライン実行には `EchoBackend`（`src/fugu_local/backends.py:436`）と
  `examples/fugu-local.echo.json` を使う。
- 決定的な応答が必要な場合は、テストファイル内に固定応答を返すスタブ backend を
  定義する（既存の流儀は `tests/test_orchestrator.py` を参照）。
- 実 LLM を要する検証は「手順書」として `docs/operations/` に書き、CI では回さない。

### 0.4 後方互換の原則

- 既存の設定 JSON・CLI フラグ・HTTP API の挙動を壊さない。
- 新機能は**すべて opt-in**（新しい `pattern` 値 / 新しい設定キー / 新しい CLI フラグ）で
  追加し、既定値は現行挙動と一致させる。
- 例外は WP-3（majority vote の正規化）。これは既存挙動がバグであるため既定値を
  変更する。逃げ道となる設定キーを用意し、`CHANGELOG.md` に破壊的変更として記載する。
- 未知の設定キーや未対応の組み合わせは黙って受理せず `ConfigError` で明示的に失敗
  させる（`CONTRIBUTING.md` の Safety 方針）。

### 0.5 ドキュメント同期義務

各 PR で該当するものを必ず更新する。更新漏れはレビューで差し戻す。

1. `docs/audit/feature-inventory.md`（SSOT。status ラベルは CONTRIBUTING の定義に従う）
2. `README.md` のロードマップ / 機能表
3. `docs/design/*` または `docs/operations/*` の該当仕様
4. `CHANGELOG.md`

### 0.6 HUMAN GATE（自律実行してはいけない箇所）

以下に到達したら PR を draft のままにし、#106 にコメントして停止する。

- **WP-2**: 新ベンチマークのゴールド解答の最終承認、および locked test セットの
  実行開始判断（§3.4）
- **WP-5**: コード実行検証の導入判断（本計画では**不採用**。§6.4 の前提条件が
  満たされない限り実装しない）
- **WP-9**: 最終的な Go / Pivot / No-Go の意思決定（スクリプトは判定値を出力してよいが、
  決定は人間が行う）

### 0.7 判断に迷ったときの既定方針

- 仕様の空白は「現行挙動を変えない・最小・opt-in」を選ぶ。
- 実験結果の**解釈**は行わない。数値の算出と記録に留める。
- 「品質が向上した」等の主張をドキュメントに書かない。数値と条件だけを書く。

### 0.8 実装エージェントの作業手順（Sonnet 5 クラスを想定）

各 PR は次の手順で進める。

1. §0 全体と、担当サブ WP の節**だけ**を読む。他 WP の節を読む必要はない
   （依存する成果物は、マージ済みのコード・テストとして参照する）
2. 節に列挙されたテスト名を先にテストファイルへ書き起こし、失敗することを確認する
   （テスト駆動。テスト名は本書のものを**改名せずそのまま**使う）
3. 実装し、§0.2 の必須ゲートをすべて通す
4. 完了条件のチェックリストを PR 本文へ転記し、各項目を自己検証してからチェックする

判断に迷った場合の優先順位: 担当節の記述 → §0.7 の既定方針 → それでも決まらない場合は
実装を止めず「最小・opt-in・現行挙動維持」の選択肢を採り、PR 本文の
「Open questions」欄に選択内容と根拠を 1〜3 行で記録する。本書と現行コードの記述が
実質的に矛盾する場合（行番号のずれ等の軽微なものを除く）は、PR を draft にして
#106 へコメントし停止する。

スコープ逸脱の禁止事項:

- 本書に無いリファクタリング・外部依存の追加・設定キーの追加
- 担当サブ WP 外のファイル変更（§0.5 のドキュメント同期を除く）
- 本書で定義したテスト名・スキーマのフィールド名・設定キー名・CLI フラグ名の改名

---

## 1. 全体像

### 1.1 原因と WP の対応

#106 コメントで挙げられた 7 つの原因に、以下の WP が対応する。

| # | 原因 | 対応 WP |
|---|------|---------|
| 1 | タスクが簡単すぎる（ceiling effect） | WP-2 |
| 2 | seed が LLM に渡っていない / 標本単位が不正 | WP-1 |
| 3 | role-specialized が逐次協調になっていない | WP-4 |
| 4 | majority vote が自由記述に対応していない | WP-3 |
| 5 | モデル多様性が能力補完性として設計されていない | WP-6 |
| 6 | 品質評価と分散基盤評価の混在 | WP-8 |
| 7 | 計算予算が揃っていない | WP-7 |

### 1.2 WP 一覧

| WP | 内容 | 主な成果物 | 自律度 | PR 分割（固定） |
|----|------|-----------|--------|----------------|
| WP-1 | 評価系の seed 伝播と統計妥当性 | `backends.py` / `orchestrator.py` / `evaluate_orchestration.py` | 完全自律 | WP-1a: seed 伝播（backend / orchestrator / config）<br>WP-1b: evaluator の反復・スキーマ・統計 |
| WP-2 | hard benchmark v2（決定的採点のみ・3 分割） | `evals/phase2/tasks-v2-*.jsonl` / `validate_tasks.py` | 人間ゲート有 | WP-2a: スキーマ・validator・手順書<br>WP-2b: タスク本体とキャリブレーション |
| WP-3 | 回答正規化と normalized majority / judge tiebreak | `src/fugu_local/answers.py` | 完全自律 | 1 PR |
| WP-4 | 逐次推論 DAG と stage 間スキーマ | `src/fugu_local/pipeline.py` / `stages.py` | 完全自律 | WP-4a: `stages.py`<br>WP-4b: `pipeline.py`<br>WP-4c: orchestrator / config 結線 |
| WP-5 | 制約・引用検証器（コード実行は不採用） | `src/fugu_local/verifiers.py` | 完全自律（既定無効） | 1 PR |
| WP-6 | 誤答相関・補完性の計測 | `scripts/analyze_results.py` | 完全自律 | 1 PR |
| WP-7 | budget-matched / ablation 実験ハーネス | `evals/phase2/configs/` / `make_budget_manifest.py` | 完全自律 | WP-7a: 予算 manifest と事前統制<br>WP-7b: 条件 config・ablation 生成・実行スクリプト |
| WP-8 | multi-node 性能・縮退実験 | `scripts/benchmark_cluster.py` | 完全自律 | 1 PR |
| WP-9 | Go / Pivot / No-Go 判定 | `scripts/decide_phase2.py` / decision record | 人間ゲート有 | 1 PR |

### 1.3 依存関係

```text
WP-1 ──┬──> WP-6 ──┐
       ├──> WP-7 ──┼──> WP-9
WP-2 ──┘           │
WP-3 ──────────────┤
WP-4 ──┬──> WP-5 ──┘
WP-8 ──────────────> (WP-9 の Efficiency Pivot 判定にのみ寄与)
```

- 相互に独立で並列着手できる: **WP-1 / WP-2 / WP-3 / WP-4 / WP-8**
- WP-5 は WP-4 の stage 契約に依存
- WP-6 / WP-7 は WP-1（結果スキーマ）と WP-2（タスクセット）に依存
- WP-9 は WP-6 / WP-7 の出力に依存

---

## 2. WP-1: 評価系の seed 伝播と統計妥当性

### 2.1 目的

現在 `scripts/evaluate_orchestration.py:244` の `random.seed(f"{seed}:{condition}:{case}")` は
Python 標準 RNG を設定するだけで、backend へのリクエストには一切影響しない。
`ChatRequest`（`src/fugu_local/backends.py:29`）に seed フィールドが無く、Ollama の
`options`（`backends.py:350`, `backends.py:395`）にも OpenAI 互換 payload
（`backends.py:235`, `backends.py:276`）にも seed が載っていない。

結果として `2026-08-02-local-3seed` の 36 run は「36 標本」ではなく「12 問 × 3 反復」であり、
現在の集計は反復をタスクと同列に数えている。本 WP でこれを是正する。

### 2.2 変更対象

- `src/fugu_local/backends.py`
- `src/fugu_local/orchestrator.py`
- `src/fugu_local/config.py`
- `scripts/evaluate_orchestration.py`
- `pyproject.toml`（ruff の対象に `scripts` を追加）

### 2.3 仕様

**(a) `ChatRequest` に seed を追加**

```python
@dataclass(frozen=True)
class ChatRequest:
    model: str
    messages: List[ChatMessage]
    temperature: float = 0.2
    max_tokens: Optional[int] = None
    tools: Optional[List[dict]] = None
    tool_choice: Any = None
    seed: Optional[int] = None  # 追加
```

- `OllamaBackend`: `seed is not None` のとき `payload["options"]["seed"] = seed`
- `OpenAICompatibleBackend`: `seed is not None` のとき `payload["seed"] = seed`
- `EchoBackend`: seed を無視するが、`ChatResponse.raw` に `{"seed": seed}` を含める
- `seed is None` のとき、payload に seed キーを**追加しない**（既存挙動と完全一致）
- streaming 版（`stream_chat`）にも同じ規則を適用する

**(b) orchestrator への伝播と role 別 seed 導出**

`FuguLocalOrchestrator.chat()` に `seed: Optional[int] = None` を追加し、
すべての `ChatRequest` 生成箇所（`_run_workers` / `_synthesize` / `_run_verifier` /
`_run_direct` / `_run_parallel_ensemble` / coordinator の meta call）へ伝播する。

**同一 seed を全ロールに配ってはならない**。同一モデルを使う role が
決定論的に同一出力を返し、ensemble の多様性が消えるため。以下の導出規則を使う。

```python
def derive_seed(base_seed: Optional[int], stream_key: str) -> Optional[int]:
    """base_seed から stream ごとの決定的な seed を導出する。

    stream_key の例: "worker:planner", "worker:solver#2", "synthesizer",
    "verifier:attempt1", "coordinator".
    """
    if base_seed is None:
        return None
    digest = hashlib.sha256(f"{base_seed}:{stream_key}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")
```

- 同じ `(base_seed, stream_key)` は常に同じ値を返すこと
- role の追加・並び替えで他 role の seed が変わらないこと（index ではなく名前を使う理由）
- `parallel_ensemble` の反復は `stream_key` に `#<index>` を付けて区別する

**(c) 設定キー**

`orchestrator.seed`（既定 `null`）を追加。`chat(seed=...)` の明示指定が優先。

**(d) evaluator: 反復と seed の分離**

- 新フラグ `--repeats N`（既定 1）を追加する。
  - `--repeats` は単一の `--seed` とのみ併用できる。`--seeds`（複数 base seed）との併用は
    `SystemExit` で拒否する。複数 base seed × 反復の直積を許すと、どの base seed を
    反復導出の起点にするかが不定になり、標本の解釈が壊れるため。
  - 反復 i の seed は `seed_i = derive_seed(base_seed, f"repeat#{i}")` で導出する。
- 結果行（`results.jsonl`）に以下を追加する。
  - `repeat_index`: 0 始まりの反復番号
  - `seed`: そのリクエストに渡した base seed
  - `seed_sent`: seed を backend への payload に載せたか（bool）。これは「送信した」ことの
    記録であり、backend が実際に seed を適用した保証にはならない。`false` の場合、
    レポートでは "seed" ではなく **"stochastic repeat"** と表記する。`true` の場合も
    レポートには「送信のみ確認（適用は backend 依存）」と注記する。
  - `worker_outputs`: `[{"role": str, "model": str, "ok": bool, "content": str,
    "passed": bool, "usage": {...}}]`。`passed` は**そのタスクの grader を worker 出力にも
    適用した結果**。WP-6 の synthesizer 破壊率・修復率（「worker は正答、final は誤答」の
    判定）がこのフィールドに依存するため必須。
  - `stage_results`: WP-4 導入後に埋まる。WP-1 時点では空配列でよい。

**(e) evaluator: 集計の標本単位を unique task にする**

`summary.json` を以下の構造に変更する（`schema_version` を 3 に更新）。

```json
{
  "schema_version": 3,
  "sample_unit": "unique_task",
  "n_tasks": 150,
  "repeats": 3,
  "conditions": {
    "<label>": {
      "task_scores": {"<case_id>": 0.667},
      "accuracy": 0.612,
      "accuracy_stderr": 0.031,
      "by_family": {"math": 0.55, "coding": 0.70},
      "tokens_total": 123456,
      "wall_ms_p50": 4210.0,
      "wall_ms_p95": 9880.0
    }
  },
  "paired": [
    {
      "baseline": "01-single-e4b",
      "candidate": "05-sequential-dag",
      "n_tasks": 150,
      "mean_diff": 0.061,
      "ci_low": 0.018,
      "ci_high": 0.104,
      "method": "paired_bootstrap",
      "iterations": 10000,
      "rng_seed": 20260802
    }
  ]
}
```

- `task_scores[case_id]` は、そのタスクの repeats 平均正答率（0.0–1.0）
- `accuracy` は `task_scores` の**タスク単位平均**（run 単位平均ではない）
- paired bootstrap は unique task を再標本化する。RNG は `random.Random(20260802)` で固定し、
  同じ入力からは必ず同じ CI が出ること
- 旧 `schema_version` 1 / 2 の manifest による rerun は引き続き受理する（後方互換）

paired bootstrap は以下の擬似コードのとおり実装する（scipy / numpy は導入しない。
これ以外の CI 算出方式を選ばない）。

```python
def paired_bootstrap_ci(diffs, iterations=10000, rng_seed=20260802):
    """diffs: 両条件に共通する unique task ごとの score 差
    (candidate - baseline) のリスト。task_scores から task_id 順に作る。"""
    rng = random.Random(rng_seed)
    n = len(diffs)
    means = []
    for _ in range(iterations):
        total = 0.0
        for _ in range(n):
            total += diffs[rng.randrange(n)]
        means.append(total / n)
    means.sort()
    mean_diff = sum(diffs) / n
    lo = means[int(0.025 * iterations)]
    hi = means[int(0.975 * iterations) - 1]
    return mean_diff, lo, hi
```

- `diffs` の並び順は `case_id` の辞書順で固定する（順序が変わると再標本化列が変わり
  CI が非決定になるため）
- 片方の条件にしか存在しないタスクは除外し、除外数を `paired[].n_excluded` に記録する

### 2.4 実装手順

**WP-1a（PR 1）**

1. `ChatRequest.seed` と 2 backend の payload 対応 ＋ テスト
2. `derive_seed` の実装（配置先: `src/fugu_local/backends.py` ではなく
   `src/fugu_local/orchestrator.py` のモジュール関数）と orchestrator 全経路への伝播 ＋ テスト
3. `config.orchestrator.seed` の追加 ＋ バリデーション ＋ テスト

**WP-1b（PR 2、WP-1a マージ後）**

4. evaluator の `--repeats` / 結果行スキーマ拡張（worker 単位の `passed` を含む
   `worker_outputs`）
5. evaluator の集計をタスク単位へ変更、paired bootstrap の追加、`schema_version` 3
6. `pyproject.toml` の ruff 対象に `scripts` を追加し、既存の指摘を解消
7. ドキュメント更新

### 2.5 テスト

`tests/test_backends.py`
- `test_ollama_payload_includes_seed_when_set`
- `test_openai_payload_includes_seed_when_set`
- `test_payload_omits_seed_when_none`
- `test_stream_payload_includes_seed_when_set`

`tests/test_orchestrator.py`
- `test_derive_seed_is_deterministic`
- `test_derive_seed_differs_per_role`
- `test_derive_seed_stable_when_role_order_changes`
- `test_seed_is_propagated_to_all_worker_requests`（スタブ backend で受信 seed を記録）
- `test_seed_none_leaves_requests_unseeded`

`tests/test_evaluate_orchestration.py`
- `test_repeats_are_aggregated_per_task`
- `test_repeats_with_multiple_seeds_is_rejected`
- `test_accuracy_uses_task_level_mean`
- `test_paired_bootstrap_ci_is_deterministic`
- `test_seed_sent_flag_is_false_for_echo_backend`
- `test_worker_outputs_include_per_worker_passed`
- `test_legacy_manifest_schema_can_be_rerun`

### 2.6 完了条件

- 上記テストがすべて通り、必須ゲートが緑
- `evals/compare-echo.jsonl` を使った E2E 実行が新スキーマの `summary.json` を出力する
- `summary.json` に `sample_unit` / `n_tasks` / `repeats` / `paired` が存在する
- `docs/operations/evaluation-harness.md` に「標本単位は unique task」「`seed_sent` が
  false のときは stochastic repeat と表記する。true でも送信の記録であり適用の保証ではない」
  旨が明記されている
- `docs/reports/phase1-local-3seed.md` に、旧結果が「12 問 × 3 反復であり 36 標本ではない」
  という注記が追記されている（結果自体は書き換えない）

### 2.7 非スコープ

新タスクセット（WP-2）、誤答相関分析（WP-6）、予算統制（WP-7）。

---

## 3. WP-2: hard benchmark v2

### 3.1 目的

現行 `evals/phase1/tasks.jsonl` は 12 問で、四則演算・首都・基本的な Python/SQL に
偏り、全条件が満点に到達している。品質差を観測可能にするため、単体ローカルモデルの
正答率が **40〜70%** に収まる難易度帯のタスクセットを作る。

### 3.2 変更対象

- `evals/phase2/tasks-v2-calibration.jsonl` / `tasks-v2-dev.jsonl` / `tasks-v2-test.jsonl`（新規）
- `scripts/validate_tasks.py`（新規）
- `docs/operations/benchmark-v2.md`（新規）

PR 分割: WP-2a（スキーマ定義・`validate_tasks.py`・`docs/operations/benchmark-v2.md`）、
WP-2b（タスク本体 3 ファイルとキャリブレーション結果）。

### 3.3 タスクスキーマ

既存スキーマを拡張する（既存フィールドは互換維持）。

```json
{
  "id": "math-v2-001",
  "family": "math",
  "difficulty": "hard",
  "answer_type": "single",
  "prompt": "...",
  "grader": {"type": "regex", "pattern": "...", "normalize": true},
  "source": "authored",
  "gold": "102",
  "gold_rationale": "...",
  "review_status": "pending"
}
```

- `family`: `math` / `coding` / `logic` / `planning` / `long_context` / `japanese` のいずれか
- `difficulty`: `easy` / `medium` / `hard`
- `answer_type`: `single`（単一正解）/ `multi`（複数正解可）の **2 種のみ**
- `grader.type`: 既存 `_grade()` が扱える `contains` / `regex` / `exact` の **3 種のみ**
  （`normalize` の併用可）
- **decision set は決定的採点のみで構成する。** `exec` 採点（実行検証）と
  `freeform` + rubric 採点は、grading framework が存在しないまま含めると採点不能になる
  （仕様の循環）ため decision set に**含めない**。rubric / exec 採点と長文生成の評価は
  Phase 2 判定後の拡張 WP として分離し、本計画の Go / No-Go には使わない
- `coding` / `long_context` family も、最終回答が決定的に採点できる形式で出題する
  （プログラムの出力値・戻り値・計算結果・長文からの抽出値などを短答で問う）
- `review_status`: `pending` / `approved`。**`approved` への変更は人間のみが行う**

### 3.4 規模と構成（3 分割と locked test）

評価セットへの過適合（難易度調整・モデル選択・最終判定に同じタスクを使い回すことによる
選択バイアス）を防ぐため、タスクを互いに素な 3 ファイルへ分割する。

| ファイル | 用途 | 規模 |
|---------|------|------|
| `tasks-v2-calibration.jsonl` | 難易度キャリブレーション（差し替え可） | 30 問以上 |
| `tasks-v2-dev.jsonl` | モデル・構成・プロンプトの選択と調整 | 60 問以上 |
| `tasks-v2-test.jsonl` | 最終 Go / Pivot / No-Go 判定専用（**locked**） | 60 問以上、各 family 10 問以上 |

- 3 ファイル横断で `id` は一意。全体で unique task **150 問以上**（目標 300 問）、
  各 family 最低 20 問
- `easy` の割合は各ファイルで 20% 以下（direct routing の品質低下測定用に意図的に残す）
- **locked test の実行禁止規則**: `tasks-v2-test.jsonl` は、比較条件の構成・予算 manifest
  （§8.4）・判定閾値（§10.3 の `decision-criteria.json`）がすべて確定・コミットされるまで
  実行しない。確定時に test ファイルの SHA-256 を `decision-criteria.json` に記録し、
  以後タスクを変更しない
- 難易度調整・構成調整の実験は calibration / dev のみで行う。test に対する実行は
  すべて最終判定の実行として扱う（やり直し不可）。実行開始の判断は人間が行う（§0.6）

### 3.5 難易度キャリブレーション手順（`docs/operations/benchmark-v2.md` に記載）

1. `best small single` 条件で **calibration セットに対して** `--repeats 3` を実行する
2. セット全体の正答率が 40〜70% に入っているか確認する
3. 全反復で正答したタスク（=天井）の割合が 20% を超える場合、そのタスクを
   より難しいものへ差し替える
4. 全反復で誤答したタスクの割合が 30% を超える場合、床効果として一部を差し替える
5. 差し替えは calibration / dev セットのみに行う。同じ作問プロセスと難易度基準で
   test セットを作成し、作成後は変更しない（§3.4 の locked 規則）
6. キャリブレーション結果を `evals/phase2/calibration.json` に保存する

### 3.6 `scripts/validate_tasks.py` の仕様

引数: タスク JSONL のパス（複数可）。以下を検査し、違反時は非 0 で終了する。

- 各行が JSON として parse できる
- `id` が**全ファイル横断で**一意（3 分割の互いに素性の検査）
- `family` / `difficulty` が許可リストに含まれる
- `answer_type` が `single` / `multi` のいずれか
- `grader.type` が `contains` / `regex` / `exact` のいずれか（`exec` や rubric 採点の
  混入は拒否する）
- `gold` が非空で、**grader が `gold` 自身に対して pass する**（自己整合チェック。
  作問ミスの機械検出）
- 3 ファイル合計 150 行以上、calibration 30 行以上、dev 60 行以上、test 60 行以上
- 全体で各 family 20 行以上、test で各 family 10 行以上
- `easy` の割合が各ファイルで 20% 以下

### 3.7 テスト

`tests/test_validate_tasks.py`（新規）
- 正常なフィクスチャが通ること
- ファイル横断の id 重複 / 未知の family / `exec` grader の混入 / `freeform` answer_type /
  gold 自己整合の失敗 / 件数不足 のそれぞれで失敗すること

タスクファイル本体の検証は `tests/test_benchmark_v2.py` で
`validate_tasks.main([...])` を呼んで行う。

### 3.8 完了条件

- `PYTHONPATH=src python3 scripts/validate_tasks.py evals/phase2/tasks-v2-*.jsonl` が成功
- `docs/operations/benchmark-v2.md` に出典方針・3 分割と locked test の運用規則・
  キャリブレーション手順・ライセンス上の注意（既存データセットから引用する場合は
  出典とライセンスを `source` フィールドに明記）が書かれている

### 3.9 HUMAN GATE

ゴールド解答の正しさはエージェントの自己検証だけでは担保できない。以下を守ること。

- ゴールド解答は、作問時に決定的に確かめられる形（計算結果・プログラムの出力値・
  長文からの抽出値など）を優先し、validator の gold 自己整合チェックを必ず通す
- `review_status` は必ず `pending` で提出し、`approved` への変更は行わない
- 人間レビュー前の状態で実験を回してはならない旨を PR 本文に明記する
- locked test セットの実行開始判断は人間が行う（§3.4）

### 3.10 作問レシピ（実装エージェント向け）

作問は本計画で唯一「正解の作り込み」という判断を含む作業のため、family ごとに
以下のレシピへ**限定**する。レシピ外の自由作問は行わない。gold を作問者の暗算・記憶で
決めることを禁止し、必ず機械的な導出を挟む。

- `math`: パラメータ化した問題型（多段の数量推論・整数論・組合せ・確率）で出題し、
  gold は使い捨ての計算スクリプトで算出する。導出過程（使った式と中間値）を
  `gold_rationale` に全文残す
- `logic`: 制約充足パズル（座席・スケジュール・真偽者など）。全解を列挙する
  スクリプトで**解の一意性を確認**してから gold を確定し、列挙結果の要約を
  `gold_rationale` に残す
- `coding`: 「このコードの出力は何か」「この入力での戻り値は何か」形式。gold は
  作問時にそのコードを実際に実行して得た値とし、実行出力を `gold_rationale` に貼る
- `planning`: 依存関係のある工程の最短手数・実行可能な順序数など、答えが一意に
  定まる形式。gold はスクリプトによる探索で確定する
- `long_context`: 2,000 字以上の資料を prompt に含め、資料内の**複数箇所を
  突き合わせないと答えられない**抽出・集計問題にする。gold は資料から機械的に
  再計算できること
- `japanese`: 上記いずれかの型を日本語で出題する（英語問題の翻訳ではなく、
  日本語として自然な問題文を書く）

作問用スクリプトはリポジトリにコミットしない（使い捨て）。ただしその出力は
`gold_rationale` に必ず残す。コミット前に validator の gold 自己整合チェック（§3.6）を
ローカルで実行する。

---

## 4. WP-3: 回答正規化と normalized majority / judge tiebreak

### 4.1 目的

`_majority_vote`（`src/fugu_local/orchestrator.py:1231`）は `result.content` の完全一致を
票として数えている。表現が異なれば全候補が 1 票となり、実質的に先頭候補を返す。
また `scripts/evaluate_orchestration.py:316` には既に `_normalize_answer` があるが、
orchestrator 側と共有されていない。

### 4.2 変更対象

- `src/fugu_local/answers.py`（新規）
- `src/fugu_local/orchestrator.py`
- `src/fugu_local/config.py`
- `scripts/evaluate_orchestration.py`（`_normalize_answer` を新モジュールへ委譲）

### 4.3 `src/fugu_local/answers.py` の公開 API

```python
def normalize_answer(text: str) -> str: ...
def extract_final_answer(text: str) -> str: ...
def cluster_answers(contents: Sequence[str]) -> List[List[int]]: ...
def majority_vote(contents: Sequence[str]) -> Tuple[str, int, int]:
    """(勝者本文, 得票数, クラスタ数) を返す。"""
```

**正規化規則**（この順に適用）:

1. Unicode NFKC 正規化（全角英数字・記号を半角へ）
2. コードフェンス（```...```）と Markdown 強調（`**`, `*`, `_`, `` ` ``）の除去
3. 接頭辞の除去（大文字小文字無視）: `Answer:` / `答え:` / `最終回答:` / `Final answer:` /
   `The answer is` / `The final answer is`。この一覧は `answers.py` に定数として置き、
   テストは**この定数に含まれる接頭辞のみ**を検証する（規則に無い表現を同一視することを
   テストで要求しない）
4. 前後の空白除去、連続空白を単一空白へ
5. casefold
6. 末尾の句読点（`.` `。` `,` `、`）除去
7. 数値の桁区切りカンマ除去、`42.0` → `42` の末尾ゼロ正規化。この規則は正規化後の
   文字列全体が数値（`^[+-]?\d[\d,]*(\.\d+)?$` に一致）の場合にのみ適用する
   （文中の数値には触らない。バージョン番号等の誤変換を防ぐため）

`extract_final_answer` は、複数行テキストから「最終回答」に相当する部分を取り出す。
接頭辞行があればその行を、無ければ最終非空行を返す。

`cluster_answers` は正規化文字列の一致でクラスタ化する。埋め込みモデル等の
追加依存は導入しない（本プロジェクトは依存ゼロを維持する）。

### 4.4 orchestrator への適用

- `_majority_vote` を `answers.majority_vote` の呼び出しに置き換える
- 設定 `coordinator.ensemble.normalize`（既定 **true**）を追加。`false` で従来の完全一致に戻せる
  - `majority` + `normalize: true` の組は **normalized majority** と呼ぶ。これは文字列正規化に
    よるクラスタリングであり意味的同値判定ではないため、コード・ドキュメントとも
    "semantic" という呼称は使わない
- `SUPPORTED_ENSEMBLE_VOTES`（`config.py:16`）に `"judge_tiebreak"` を追加する
  - `"judge_tiebreak"` は normalized majority を先に行い、最大クラスタが同数で並んだ
    （＝多数決が決まらない）場合にのみ、judge role へ「どの候補を採用するか」を問う
    1 回の追加呼び出しを行う（normalized majority と judge selection の分離）
  - judge role は `coordinator.ensemble.judge_role` で指定。未指定かつ `is_verifier` role が
    無ければ `ConfigError`
  - judge 呼び出しが失敗した場合は normalized majority の結果へフォールバックし、
    警告を記録する
- 投票の内訳を `OrchestrationResult` に `vote_summary`（クラスタ数・得票数・
  正規化が効いたか・judge を呼んだか）として記録する

### 4.5 テスト

`tests/test_answers.py`（新規、テーブル駆動）
- `42` / `42.0` / `**42**` / `The answer is 42.` / `答え: ４２` がすべて同一クラスタになる
- 日本語の表記ゆれ（全角・半角、句点有無）
- 異なる回答が別クラスタのままであること
- `cluster_answers` の決定性（同入力→同出力）

`tests/test_orchestrator.py`
- `test_majority_vote_normalizes_equivalent_answers`
- `test_majority_vote_exact_mode_when_normalize_false`
- `test_judge_tiebreak_called_only_on_tie`
- `test_judge_tiebreak_falls_back_when_judge_fails`

`tests/test_config.py`
- `test_judge_tiebreak_requires_judge_role`

### 4.6 完了条件

- `CHANGELOG.md` に「`ensemble.vote: majority` の既定挙動が正規化ありに変わった」ことを
  破壊的変更として記載
- `scripts/evaluate_orchestration.py` の `_normalize_answer` が `answers.normalize_answer` へ
  委譲され、実装の重複が無い
- `docs/reference/openai-compatibility.md` または該当設計文書に normalized majority と
  `judge_tiebreak` vote の定義が記載されている

---

## 5. WP-4: 逐次推論 DAG と stage 間スキーマ

### 5.1 目的

`_run_role_split`（`src/fugu_local/orchestrator.py:551`）は、全 worker role に同一の
`messages` を渡して並列実行し、その出力を synthesizer が統合する。planner の分解結果を
solver が入力として使う経路も、critic が solver の候補を読む経路も存在しない。
実質「fan-out + synthesis」である。

本 WP で、**前工程の成果物を次工程が実際に入力として利用する**推論グラフを実装する。
これが本計画の中核であり、「複数モデル協調＝品質向上」という仮説を検証可能にする唯一の WP。

### 5.2 変更対象

- `src/fugu_local/stages.py`（新規: stage 間データ契約）
- `src/fugu_local/pipeline.py`（新規: DAG 実行器）
- `src/fugu_local/orchestrator.py`（`sequential_dag` パターンの分岐と streaming 除外）
- `src/fugu_local/config.py`（`SUPPORTED_PATTERNS` と `coordinator.dag`）
- `src/fugu_local/server.py` は変更しない想定（streaming フォールバックの挙動テストのみ追加）

### 5.3 stage 間データ契約（`stages.py`）

```python
@dataclass(frozen=True)
class Claim:
    text: str
    evidence: str = ""
    confidence: float = 0.0
    verification: str = "required"  # required|passed|failed|unavailable

@dataclass(frozen=True)
class StageOutput:
    stage: str
    role: str
    answer: str
    claims: List[Claim] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    uncertainties: List[str] = field(default_factory=list)
    requested_checks: List[str] = field(default_factory=list)
    subproblems: List[str] = field(default_factory=list)  # planner 用
    raw_text: str = ""
    parse_error: Optional[str] = None
    usage: Optional[TokenUsage] = None
```

**寛容なパーサ** `parse_stage_output(stage, role, text) -> StageOutput`:

1. テキスト中の最初の JSON オブジェクト（コードフェンス内を優先）を抽出して parse
2. 成功したら既知フィールドをマッピング。未知フィールドは無視
3. 失敗したら `answer=text`, `claims=[]`, `parse_error="<理由>"` として返す
   （**例外を投げてパイプラインを止めてはならない**。ローカル小型モデルは頻繁に
   スキーマを外すため、フォールバックが正常系の一部である）

各 stage のシステムプロンプトには、この JSON スキーマを明示的に埋め込む。
プロンプト定型文は §5.4.1 の初版を `stages.py` に定数として置く（本文を発明しない）。

### 5.4 DAG の構成

```text
planner    -> 問題・制約・部分問題を構造化（subproblems を出力）
solvers    -> planner の subproblems を割り当てられて独立に解く
verifiers  -> code execution / constraint / citation / calculation（WP-5）
critic     -> 候補と検証結果を読んで誤りを特定
reviser    -> critic の指摘を受けて回答を修正
claim_judge-> 主張単位で採用・棄却・不明を判定
writer     -> 採用された主張だけから最終回答を生成
```

**必須の実装要件**: 各 stage のプロンプトには、前段の `StageOutput` を
自由文の連結ではなく**構造化されたセクション**として埋め込むこと。

```text
## Subproblems from planner
1. ...
2. ...

## Candidate answers from solvers
- [solver#1] ...
- [solver#2] ...

## Verification results
- claim "..." -> failed (reason: ...)

## Open uncertainties
- ...
```

この「前段出力が後段プロンプトに入っていること」はテストで必ずアサートする。
入っていなければ本 WP の目的を達成していない。

### 5.4.1 stage システムプロンプト初版（このまま `stages.py` の定数に採用する）

プロンプトの言い回しの調整は実装スコープ外とする（改善は Phase B 以降、実験結果を
見て別 PR で行う）。初版として以下を固定し、テストは「定数が存在すること」と
「前段出力セクションが埋め込まれること」のみを検証する。

全 stage 共通で、system prompt の末尾に次の定数 `STAGE_JSON_INSTRUCTION` を連結する。

> Respond with a single JSON object:
> `{"answer": str, "claims": [{"text": str, "evidence": str, "confidence": float,
> "verification": "required"}], "assumptions": [str], "uncertainties": [str],
> "requested_checks": [str], "subproblems": [str]}`.
> Omit fields that do not apply. Do not add any text outside the JSON object.

| stage | system prompt 本文（初版・英語で固定） |
|-------|--------------------------------------|
| `planner` | You are the planner. Decompose the task into 2-5 self-contained subproblems and list the explicit constraints. Put the subproblems in "subproblems" and the constraints in "assumptions". |
| `solver` | You are a solver. Solve ONLY the subproblem assigned to you, respecting the planner's constraints. Put your result in "answer" and each factual step in "claims". |
| `verifier` | You are the verifier. For each claim listed below, check it against the task and the constraints. Set each claim's "verification" to "passed" or "failed" and put the reason in "evidence". |
| `critic` | You are the critic. Read the candidate answers and the verification results below and identify concrete errors. Describe each error as a claim in "claims". |
| `reviser` | You are the reviser. Fix the candidate answer using ONLY the critic's findings below. Put the corrected answer in "answer". |
| `claim_judge` | You are the claim judge. For each claim below, decide adopt, reject, or unknown, and record the decision with its reason in "evidence". |
| `writer` | You are the writer. Compose the final answer using ONLY the adopted claims below. Put the final answer in "answer". |

### 5.5 設定

```json
"coordinator": {
  "default_pattern": "sequential_dag",
  "dag": {
    "stages": [
      {"name": "planner",     "role": "planner",     "enabled": true},
      {"name": "solver",      "role": "solver",      "enabled": true, "fanout": 3},
      {"name": "verifier",    "role": "judge",       "enabled": true},
      {"name": "critic",      "role": "critic",      "enabled": true},
      {"name": "reviser",     "role": "solver",      "enabled": true},
      {"name": "claim_judge", "role": "judge",       "enabled": true},
      {"name": "writer",      "role": "synthesizer", "enabled": true}
    ],
    "max_stage_tokens": 1024
  }
}
```

- `SUPPORTED_PATTERNS`（`config.py:15`）に `"sequential_dag"` を追加
- `stages[].name` は既知の 7 種のみ許可。未知の名前は `ConfigError`
- `stages[].role` は `roles[]` に存在すること。存在しなければ `ConfigError`
- `fanout` は solver stage のみ有効。既定 1
- `enabled: false` の stage は、以下の **stage 別 bypass 規則**に従ってスキップする。
  「直前の有効 stage の出力を下流へ渡す」という汎用規則は使わない。stage ごとに
  入力契約が異なるため、単純な素通しでは ablation が有効な比較にならない。

| 無効化した stage | bypass 規則 |
|-----------------|------------|
| `planner` | 原問題全体を単一の subproblem として solver に渡す |
| `solver` | 無効化不可（候補生成が消える）。`ConfigError` |
| `verifier` | 全 claim の `verification` を `"unavailable"` にし、下流プロンプトに「機械検証は実行されていない」と明示する |
| `critic` | critique を空にし、**`reviser` も連動してスキップする**（reviser の入力契約は critique を前提とするため）。solver 候補を直接 claim_judge へ渡す |
| `reviser` | critic の指摘を適用せず、solver 候補と critique をそのまま claim_judge へ渡す |
| `claim_judge` | 全 claim を「未審査（unreviewed）」として writer へ渡し、writer プロンプトにその旨を明示する |
| `writer` | 無効化不可（最終回答生成が消える）。`ConfigError` |

WP-7 の ablation は、この `enabled` フラグと bypass 規則だけで実現できること。

### 5.6 制約

- **streaming 非対応**: `src/fugu_local/orchestrator.py:434`
  （`prepare_streaming_response()` → `_prepare_stream()` の
  `allowed_patterns={"direct", "role_split"}`）は変更しない。`sequential_dag` では
  `prepare_streaming_response()` が `None` を返し、`server.py` 側は既存の
  非 streaming（buffered SSE）応答にフォールバックすること。この挙動をテストで固定する。
- **tool calling との併用は当面不可**: `tools.enabled` かつ `sequential_dag` の組み合わせは
  `ConfigError` で明示的に拒否する（黙って無視しない）。
- **deadline の尊重**: 既存の `_deadline_passed` を各 stage 境界で評価し、超過したら
  その時点の最良出力を返す。途中終了は `OrchestrationResult.warnings` に記録する。
- `OrchestrationResult` に `stage_results: List[StageOutput]` を追加する
  （WP-6 の stage 別寄与分析と WP-7 の ablation がこれに依存）。

### 5.7 実装手順

1. **WP-4a**: `stages.py`（データクラス、パーサ、プロンプト定型文）＋ テスト
2. **WP-4b**: `pipeline.py`（stage 実行、bypass 規則、fanout、deadline）＋ テスト
3. **WP-4c**: orchestrator / config の結線と streaming フォールバックのテスト、ドキュメント

### 5.8 テスト

`tests/test_stages.py`（新規）
- 正常な JSON / コードフェンス入り JSON / 壊れた JSON / JSON 以外 の 4 ケースで
  `parse_stage_output` が期待通り（例外を出さない）
- 未知フィールドを無視する

`tests/test_pipeline.py`（新規、EchoBackend + スタブ backend）
- stage が定義順に実行される
- **planner の `subproblems` が solver プロンプトに含まれる**
- **solver の候補が critic プロンプトに含まれる**
- **verifier の結果が claim_judge プロンプトに含まれる**
- 各 stage の bypass 規則が仕様どおり動く: planner 無効→原問題が単一 subproblem として
  solver に渡る / critic 無効→reviser も連動スキップし solver 候補が claim_judge へ渡る /
  verifier 無効→`verification="unavailable"` が下流プロンプトに明示される /
  claim_judge 無効→writer プロンプトに「未審査」が明示される
- `solver` / `writer` の無効化が `ConfigError` になる
- `fanout: 3` で solver が 3 回呼ばれ、それぞれ異なる seed を受け取る（WP-1 と結合）
- deadline 超過で途中終了し、warning が記録される
- parse 失敗時も最終回答が返る

`tests/test_config.py`
- 未知 stage 名 / 未知 role 参照 / tools 併用 が `ConfigError`

`tests/test_server.py`
- `sequential_dag` では streaming にならず非 streaming 応答になる

### 5.9 完了条件

- 上記テストが通り、必須ゲートが緑
- `examples/fugu-local.sequential-dag.json` を追加し、`EchoBackend` で
  `fugu-local` CLI から実行できる
- `docs/design/sequential-inference-dag.md` を新規作成し、stage 契約・プロンプト構造・
  制約（streaming / tool 非対応）を記載
- `docs/audit/feature-inventory.md` に `sequential_dag` 行を `experimental` で追加

---

## 6. WP-5: 制約・引用検証器（コード実行は不採用）

### 6.1 目的

DAG の verifier stage を、LLM の自己申告ではなく機械的検証で支える。

**スコープ判断（レビュー反映）**: 当初案にあった `PythonExecVerifier`（subprocess による
モデル生成コードの実行）は**実装しない**。`python -I -S` ＋一時ディレクトリ＋環境変数制限
では、モデル生成コードによるファイル読み書き・削除、ネットワーク通信、他プロセスの起動、
CPU / メモリ / プロセス数の枯渇のいずれも防げない。「HTTP から到達不能」にしても、
CLI から実行した時点でユーザー権限をそのまま持つ。加えて WP-2 の decision set を
決定的採点のみに限定した（§3.3）ため、Phase 2 判定にコード実行検証は不要である。

### 6.2 変更対象

- `src/fugu_local/verifiers.py`（新規）
- `src/fugu_local/config.py`
- `src/fugu_local/pipeline.py`（verifier stage からの利用）

### 6.3 仕様

実装する検証器（いずれも**プロセス内で完結し、subprocess・ネットワーク・
ファイル書き込みを行わない**）:

- `ConstraintVerifier`: 正規表現 / 数値範囲 / 文字数 / 出力形式（JSON parse 可否）の検査
- `CitationVerifier`: **与えられた context 内**の引用一致のみを検査する。
  外部 URL の取得は行わない

設定は `"verify": {"checks": {...}}` 配下に置き、既定は無効。検証結果は
`Claim.verification`（`passed` / `failed` / `unavailable`）へ反映する。

### 6.4 コード実行検証の扱い(将来拡張として前提条件のみ規定し、実装しない)

以下がすべて満たせる場合に限り、別 WP として提案できる。

- OS レベルのサンドボックス（コンテナ等）で、ネットワーク遮断・read-only filesystem・
  非特権ユーザー・CPU / メモリ / PID 数の制限がすべて構成されること
- 実行はユーザーが明示的に用意した外部サンドボックスランナー経由に限り、
  本パッケージが直接 subprocess を起動しないこと
- 既定の tool registry には追加しないこと（`src/fugu_local/tools.py` に手を入れない）
- HTTP サーバー経路から有効化できないこと
- 導入可否の判断は HUMAN GATE（§0.6）

この前提が満たせない間、コード実行検証を実装してはならない。

### 6.5 テスト

`tests/test_verifiers.py`（新規）
- `ConstraintVerifier` の各検査（正規表現 / 数値範囲 / 文字数 / JSON parse）
- `CitationVerifier` が context 内一致のみを検査し、ネットワーク呼び出しを行わない
  （スタブで確認）
- 既定設定では全検証器が無効
- `verifiers.py` が `subprocess` を import していない（モジュール検査。
  コード実行が紛れ込まないことの機械的な歯止め）

`tests/test_server.py`
- サーバー API 経由で検証器の設定を変更できない

### 6.6 完了条件

- 上記テストが通る
- `docs/operations/security-profile.md` に「コード実行検証を不採用とした理由と、
  将来導入する場合の前提条件（§6.4）」が記載されている
- `docs/audit/feature-inventory.md` に `experimental`（既定無効）として追加

---

## 7. WP-6: 誤答相関・補完性の計測

### 7.1 目的

「異種モデルを並べる」ことに意味があるのは誤答が相関していない場合に限る。
また、改善が synthesizer 単体に由来するのか協調 stage に由来するのかを分離する必要がある。

### 7.2 変更対象

- `scripts/analyze_results.py`（新規）

### 7.3 仕様

入力: WP-1 で拡張された `results.jsonl`（worker ごとの `passed` を含む `worker_outputs` と
`stage_results`）。
出力: `analysis.json` と `analysis.md`。

synthesizer 破壊率・修復率の判定（「worker は正答、final は誤答」等）には、WP-1 が
評価時に記録する **worker 単位の `passed`**（タスク grader を各 worker 出力へ適用した結果）
を使う。analyzer 自身は grader を再適用しない（grader 定義とタスク snapshot への依存を
持ち込まないため）。`passed` が無い旧形式の入力では該当指標を `null` とし、warning を出す。

算出する指標:

| 指標 | 定義 |
|------|------|
| 正誤行列 | task × condition の 0/1（repeats がある場合は平均） |
| ペア誤答相関 | 条件間・worker 間の phi 係数と一致率 |
| oracle upper bound | いずれかの worker が正答したタスクの割合 |
| synthesizer 破壊率 | 「ある worker が正答 かつ 最終が誤答」の割合 |
| synthesizer 修復率 | 「全 worker が誤答 かつ 最終が正答」の割合 |
| stage 別寄与 | ablation 条件との差分（WP-7 の出力と結合） |
| quality / 1k tokens | 正答数 ÷ (総トークン ÷ 1000) |
| cost per correct | 総 wall_ms・トークン ÷ 正答数 |
| family 別内訳 | 上記を `family` ごとに分解 |

- phi 係数は 2×2 分割表（n11=両方正答, n10=A のみ正答, n01=B のみ正答, n00=両方誤答。
  repeats がある場合はタスク単位の多数決で 0/1 化する）から次式で算出する。

  `phi = (n11*n00 - n10*n01) / sqrt((n11+n10)*(n01+n00)*(n11+n01)*(n10+n00))`

  分母が 0 のときは `null` を出力する。`math.sqrt` のみを使い、scipy 等は導入しない
- 統計量はすべて `random.Random(20260802)` 固定でブートストラップし、決定的に再現できること
- 欠損（`worker_outputs` が空、`stage_results` が無い）は例外にせず、
  該当指標を `null` として出力し、`analysis.json.warnings` に理由を記録する

### 7.4 テスト

`tests/test_analyze_results.py`（新規）
- 手計算できる小さな合成 `results.jsonl` を用意し、各指標の期待値を厳密に検証
- phi 係数の既知ケース（完全相関 1.0 / 無相関 0.0 / 完全逆相関 -1.0）
- 破壊率・修復率の境界ケース
- `worker_outputs` 欠損時に例外を出さず warning を出す
- `worker_outputs` に `passed` が無い旧形式で、破壊率・修復率が `null` になり warning が出る

### 7.5 完了条件

- `PYTHONPATH=src python3 scripts/analyze_results.py <results.jsonl> --output-dir <dir>` が動く
- `docs/operations/evaluation-harness.md` に各指標の定義が記載されている

---

## 8. WP-7: budget-matched / ablation 実験ハーネス

### 8.1 目的

現在の比較は multi-model 側が最大 7.2 倍のトークンを使っており、
「追加 compute の効果」と「オーケストレーション固有の効果」を分離できない。

### 8.2 変更対象

- `evals/phase2/configs/`（新規、条件 1〜7）
- `scripts/evaluate_orchestration.py`（予算統制フラグ）
- `scripts/make_budget_manifest.py`（新規）
- `scripts/make_ablation_configs.py`（新規）
- `scripts/run_phase2_comparison.sh`（新規）

### 8.3 比較条件

| # | label | 内容 |
|---|-------|------|
| 1 | `01-best-small-single` | 小型単体 |
| 2 | `02-best-large-single` | 大型単体 |
| 3 | `03-same-model-repeat` | 同一モデル反復サンプリング + 投票 |
| 4 | `04-heterogeneous-ensemble` | 異種モデル並列 + 統合 |
| 5 | `05-sequential-dag` | WP-4 の逐次 DAG |
| 6 | `06-ablation-*` | DAG から stage を 1 つずつ除去（自動生成） |
| 7 | `07-cloud-reference.template` | クラウド参照値（テンプレートのみ、鍵は含めない） |

### 8.4 予算統制（二段階・事前統制）

予算は**事後ペナルティではなく事前統制**とする。token 上限を超えてから不正解扱いに
するだけでは計算は既に消費されており、予算統制にならない。また、予算を同一実験中の
実測中央値から決めると実行順序依存になる。以下の二段階で実行する。

**Phase A: baseline 計測 → 予算確定**

1. baseline 条件（`01` / `02`）を natural configuration で calibration / dev セットに
   対して実行する
2. `scripts/make_budget_manifest.py`（新規）が結果から family 別の token / wall-clock
   中央値を算出し、`evals/phase2/budget-manifest.json` を書き出す
   （予算 = baseline 中央値 × 係数。係数は manifest に明記し、既定 1.0。出力は決定的）
3. budget manifest をコミットして**凍結**する。以後の比較実験はこのファイルだけを参照し、
   同一実験内の実測から予算を決めない

**Phase B: 比較実験（事前統制の実装）**

- 新フラグ `--budget-manifest <path>` で凍結済み manifest を読み込む
- token 予算は stage / worker 数で事前に按分し、各 `ChatRequest.max_tokens` に配分する
  （DAG は `max_stage_tokens` を予算から導出する）
- wall-clock 予算は orchestrator の request deadline（既存の
  `request_timeout_seconds` / `_deadline_passed` 機構）として渡し、到達時点で
  後続 stage を打ち切ってその時点の最良出力を返す
- **事後チェックの併用**: 事前配分は近似（prompt tokens は事後にしか分からない）のため、
  実測が予算を超過した run は `budget_exceeded: true` を記録し、**不正解として集計する**。
  「予算内で返せた回答」を評価対象とするため、除外ではなく不正解扱いとする。
  予算内で完了した run のみを対象にした補助集計も `summary.json` に併記する

実験は 2 系統で回す。

- **budget-matched**: 凍結済み manifest の上限を全条件に適用する
- **natural configuration**: 各方式の通常設定で Pareto frontier を見る

### 8.5 `scripts/make_ablation_configs.py`

入力: DAG 設定 JSON。出力: stage を 1 つずつ `enabled: false` にした設定群。
- `solver` / `writer` stage は除去対象外（§5.5 の bypass 規則で無効化不可のため）
- 各 ablation の意味は §5.5 の bypass 規則で定義される（例: `no-critic` は
  reviser も連動スキップ）。生成物の命名は `06-ablation-no-<stage>.json`
- 生成した設定がすべて `load_config()` を通ることを確認してから書き出す

### 8.6 テスト

`tests/test_evaluate_orchestration.py`
- `test_budget_manifest_allocates_max_tokens_before_execution`
- `test_budget_wall_clock_is_enforced_via_deadline`
- `test_budget_exceeded_runs_count_as_incorrect`
- `test_summary_includes_budget_filtered_view`

`tests/test_make_budget_manifest.py`（新規）
- 合成 results から family 別中央値が正しく算出される
- 係数が manifest に記録される
- 出力が決定的（同入力→同出力）

`tests/test_make_ablation_configs.py`（新規）
- 生成数が「除去可能 stage 数」と一致する
- `solver` / `writer` が除去されない
- 生成物がすべて `load_config()` を通る

### 8.7 完了条件

- `scripts/run_phase2_comparison.sh` が echo backend で end-to-end に完走する
  （対象タスクファイルは引数で受け取り、既定は dev セット。test セットの実行は
  §3.4 の locked 規則に従い凍結完了後のみ）
- `docs/operations/phase2-comparison.md` に条件表・Phase A / B の二段階手順・
  予算定義・実行手順が記載されている

---

## 9. WP-8: multi-node 性能・縮退実験

### 9.1 目的

「複数の安価なマシンを束ねる」性能・費用仮説の検証は、品質実験とは別系統で行う。
現在の Phase 1 実験は 1 台の Mac・1 つの Ollama endpoint 上であり、
この仮説の検証にはなっていない。

### 9.2 変更対象

- `scripts/benchmark_cluster.py`（新規）
- `docs/operations/multi-node-benchmark.md`（新規）

### 9.3 仕様

既存の `model_pool` / `routing.py` / `health.py` を使い、以下を測る。

- throughput（req/s）を並列度ごとに
- latency p50 / p95 / p99
- member 停止を注入したときの成功率・latency の縮退カーブ
- 電力・ハードウェア構成は測定器がないため**手入力メタデータ**として
  `hardware.json` に記録する（自動測定は行わない）

**品質指標は一切出力しない。** 品質実験と混同しないことを doc に明記する。

### 9.4 テスト

`tests/test_benchmark_cluster.py`（新規）
- スタブ backend で p50 / p95 / p99 の計算が正しい
- member 停止注入時に成功率が期待通り低下する
- 実ネットワークを使わない

### 9.5 完了条件

- スタブ backend での CI テストが通る
- `docs/operations/multi-node-benchmark.md` に実機手順と、
  「これは品質仮説の検証ではない」旨の明記がある

---

## 10. WP-9: Go / Pivot / No-Go 判定

### 10.1 目的

#106 の完了条件である「数値化された Go / No-Go 基準と意思決定の記録」を満たす。

### 10.2 変更対象

- `evals/phase2/decision-criteria.json`（新規、閾値の外出し）
- `scripts/decide_phase2.py`（新規）
- `docs/decisions/0001-phase2-go-pivot-no-go.md`（新規）

### 10.3 判定基準（`decision-criteria.json` の初期値）

**Intelligence Go**（すべて満たす）

| キー | 閾値 |
|------|------|
| `min_accuracy_gain_pt` | 5.0（budget-matched で sequential DAG − best single） |
| `require_paired_ci_lower_above_zero` | true |
| `min_families_improved` | 2 |
| `max_easy_task_regression_pt` | 2.0 |
| `require_ablation_attribution` | true（協調 stage を 1 つ以上除去すると改善が有意に減る） |

**Efficiency Pivot**（Intelligence Go を満たさず、いずれか 1 つを満たす）

| キー | 閾値 |
|------|------|
| `min_cost_reduction_pct` | 30.0 |
| `min_p95_latency_reduction_pct` | 30.0 |
| `degradation_min_success_rate_pct` | 95.0（WP-8 の単一 member 停止注入時のリクエスト成功率） |
| `degradation_max_p95_increase_pct` | 50.0（同、p95 latency の増加率上限。成功率基準と**両方**満たしたとき graceful degradation 成立） |

graceful degradation は WP-8 が出力する**成功率と p95 latency のみ**で定義する。
WP-8 は品質指標を出力しない（§9.3）ため、品質（正答率）はこの基準に含めない。
上 2 行（cost / p95 削減）は WP-7 の budget-matched 結果から、下 2 行は WP-8 の
障害注入結果から判定する。

**No-Go**: 上記いずれも満たさない場合。

### 10.4 `scripts/decide_phase2.py` の仕様

入力: WP-1 の `summary.json`、WP-6 の `analysis.json`、WP-8 の
`cluster-benchmark.json`、および `decision-criteria.json`。

出力: `decision.json` と `decision.md`。

`decision-criteria.json` には locked test セットの SHA-256（§3.4）を記録する欄を設ける。
`decide_phase2.py` は、入力 `summary.json` の manifest が参照するタスクファイルの SHA-256 が
この値と一致することを検査し、不一致（＝locked test 以外での実行結果）の場合は
`verdict: "insufficient_data"` として理由を出力する。

- 各基準について `{"criterion": ..., "value": ..., "threshold": ..., "met": bool}` を出力
- 総合判定 `verdict` は `"intelligence_go"` / `"efficiency_pivot"` / `"no_go"` /
  `"insufficient_data"` のいずれか
- 入力が欠けている基準は `met: null` とし、1 つでも `null` があれば
  `verdict` は `"insufficient_data"` にする（**推測で判定しない**）
- スクリプトは判定値を出力するのみで、Issue のクローズや状態変更を行わない

### 10.5 テスト

`tests/test_decide_phase2.py`（新規）
- 各 verdict に到達する合成入力を用意して分岐を網羅
- 欠損入力で `insufficient_data` になる
- locked test の SHA-256 不一致で `insufficient_data` になる
- 閾値が `decision-criteria.json` から読まれている（ハードコードされていない）

### 10.6 完了条件

- `docs/decisions/0001-phase2-go-pivot-no-go.md` が作成され、
  背景・基準・（実験後に）実測値・決定・決定者・日付の欄がある
- No-Go 時の代替方針（評価ハーネスまたはローカル LLM ルータとして整理）が
  decision record に明記されている

### 10.7 HUMAN GATE

最終的な Go / Pivot / No-Go の意思決定は人間が行う。エージェントは
`decision.md` を生成し、decision record の「決定」欄を空欄のまま PR を出して停止する。

---

## 11. 進行順序

### スプリント 1（並列着手可能）

- WP-1 評価系の是正
- WP-3 回答正規化
- WP-4 逐次 DAG
- WP-2 ベンチマーク v2（HUMAN GATE で人間レビュー待ちに入る）

### スプリント 2

- WP-5 検証器（WP-4 マージ後）
- WP-6 分析スクリプト（WP-1 マージ後）
- WP-7 実験ハーネス（WP-1 / WP-2 マージ後）
- WP-8 multi-node ベンチ（独立、いつでも可）

### スプリント 3

- Phase A: baseline 計測と予算 manifest の凍結（§8.4）
- 比較条件の構成・判定閾値の確定と locked test の SHA-256 記録（§3.4 / §10.4）
- Phase B: 比較実験（budget-matched / natural。dev セット → 凍結後に locked test の順）
- WP-9 判定と decision record
- #106 のクローズ

### Issue 整理（#106 の「やること」より）

Phase 3〜5 の細粒度 Issue（#82〜#97）は Epic #69 配下へ移し、
アクティブに見せるのは Phase 1 / 2 のみとする。この作業は本計画の WP には含めず、
リポジトリ所有者が行う。

---

## 12. リスクと非スコープ

### リスク

| リスク | 影響 | 緩和 |
|--------|------|------|
| WP-2 のゴールド解答に誤りが混入する | 実験結果が無効化する | 決定的採点への限定と validator の gold 自己整合チェック（§3.6）。人間レビューを必須ゲートにする |
| ローカル小型モデルが stage スキーマを守らない | DAG が機能しない | 寛容なパーサを正常系として実装。`parse_error` 率を測定指標として記録する |
| DAG の stage 数だけレイテンシが線形に増える | 実用性が失われる | deadline を全 stage で尊重。WP-7 の budget-matched で必ず定量化する |
| 検証器が安全側の制約を破る | セキュリティ問題 | コード実行検証は不採用（§6.4）。検証器はプロセス内完結とし、`subprocess` 非依存をテストで固定 |
| 評価セットへの過適合（選択バイアス） | 最終判定が無効化する | calibration / dev / locked test の 3 分割と、test の実行禁止・SHA-256 固定規則（§3.4） |
| 改善が synthesizer 単体に由来する | 仮説が実証されない | WP-7 の ablation と WP-6 の帰属分析を Go 条件に含める |

### 非スコープ

- Phase 3 以降（動的推論ルータ、ノード登録クラスタリング、Web UI）の実装
- 埋め込みモデル・外部依存の導入（本プロジェクトは依存ゼロを維持する）
- クラウドモデルを本番経路に組み込むこと（参照値としての比較のみ)
- 実 LLM を必要とする CI テストの追加
- モデル生成コードの実行（コード実行検証・`exec` grader・rubric / freeform 採点。
  いずれも Phase 2 判定後の拡張として分離。§3.3 / §6.4）
