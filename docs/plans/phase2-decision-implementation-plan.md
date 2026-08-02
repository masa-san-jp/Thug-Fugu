# Phase 1 評価系是正 / 逐次推論DAG 実装計画

Status: planned（未着手）。親Issue: #106、Epic: #69。

本書は #106 のコメント「原因分析と改善設計案」を、コーディングエージェントが
**1 PR 単位で自律実装できる粒度**に分解した実装計画である。設計判断は本書で確定
させてあり、実装者（人間・エージェントを問わない）が新たに設計を起こす必要がない
ことを目標とする。

対象範囲は「Phase 2 着手可否を数値で判定できる状態にすること」までであり、
Phase 3 以降の分散基盤拡張は含まない。

---

## 0. 実行プロトコル（全 WP 共通・必読）

### 0.1 作業単位

- **1 Work Package (WP) = 1 branch = 1 PR**。WP をまたぐ変更を 1 PR に混ぜない。
- ブランチ名: `feat/wp<N>-<slug>`（例: `feat/wp1-evaluator-seed-and-stats`）
- PR タイトル: `WP<N>: <要約>`
- PR 本文に、本書の該当節へのリンクと「完了条件」チェックリストを転記する。
- 1 WP が 400 行を超える差分になる場合は、本書の「実装手順」の番号単位で PR を分割してよい。
  分割時も各 PR 単体でテストが通り、既存挙動を壊さないこと。

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

- **WP-2**: 新ベンチマークのゴールド解答の最終承認
- **WP-5**: コード実行 verifier の既定有効化（既定は無効のまま実装すること）
- **WP-9**: 最終的な Go / Pivot / No-Go の意思決定（スクリプトは判定値を出力してよいが、
  決定は人間が行う）

### 0.7 判断に迷ったときの既定方針

- 仕様の空白は「現行挙動を変えない・最小・opt-in」を選ぶ。
- 実験結果の**解釈**は行わない。数値の算出と記録に留める。
- 「品質が向上した」等の主張をドキュメントに書かない。数値と条件だけを書く。

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

| WP | 内容 | 主な成果物 | 自律度 | 想定 PR 数 |
|----|------|-----------|--------|-----------|
| WP-1 | 評価系の seed 伝播と統計妥当性 | `backends.py` / `orchestrator.py` / `evaluate_orchestration.py` | 完全自律 | 2–3 |
| WP-2 | hard benchmark v2 | `evals/phase2/tasks-v2.jsonl` / `validate_tasks.py` | 人間ゲート有 | 2 |
| WP-3 | 回答正規化と semantic voting | `src/fugu_local/answers.py` | 完全自律 | 1–2 |
| WP-4 | 逐次推論 DAG と stage 間スキーマ | `src/fugu_local/pipeline.py` / `stages.py` | 完全自律 | 3 |
| WP-5 | 実行・制約検証器 | `src/fugu_local/verifiers.py` | 完全自律（既定無効） | 1–2 |
| WP-6 | 誤答相関・補完性の計測 | `scripts/analyze_results.py` | 完全自律 | 1 |
| WP-7 | budget-matched / ablation 実験ハーネス | `evals/phase2/configs/` / `run_phase2_comparison.sh` | 完全自律 | 2 |
| WP-8 | multi-node 性能・縮退実験 | `scripts/benchmark_cluster.py` | 完全自律 | 1 |
| WP-9 | Go / Pivot / No-Go 判定 | `scripts/decide_phase2.py` / decision record | 人間ゲート有 | 1 |

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

- 新フラグ `--repeats N`（既定 1）を追加。`--seeds` は「実験 seed のリスト」として残すが、
  `--repeats` と併用した場合は `repeats × seeds` の直積ではなく、
  `seed_i = derive_seed(base_seed, f"repeat#{i}")` として **repeats 分の seed** を生成する。
- 結果行（`results.jsonl`）に以下を追加する。
  - `repeat_index`: 0 始まりの反復番号
  - `seed`: そのリクエストに渡した base seed
  - `seed_applied`: backend が seed を実際に payload に載せたか（bool）。
    `false` の場合、レポートでは "seed" ではなく **"stochastic repeat"** と表記する。
  - `worker_outputs`: `[{"role": str, "model": str, "ok": bool, "content": str,
    "usage": {...}}]`（WP-6 が依存するため必須）
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

### 2.4 実装手順

1. `ChatRequest.seed` と 2 backend の payload 対応 ＋ テスト
2. `derive_seed` の実装（配置先: `src/fugu_local/backends.py` ではなく
   `src/fugu_local/orchestrator.py` のモジュール関数）と orchestrator 全経路への伝播 ＋ テスト
3. `config.orchestrator.seed` の追加 ＋ バリデーション ＋ テスト
4. evaluator の `--repeats` / 結果行スキーマ拡張（`worker_outputs` 含む）
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
- `test_accuracy_uses_task_level_mean`
- `test_paired_bootstrap_ci_is_deterministic`
- `test_seed_applied_flag_is_false_for_echo_backend`
- `test_worker_outputs_are_recorded`
- `test_legacy_manifest_schema_can_be_rerun`

### 2.6 完了条件

- 上記テストがすべて通り、必須ゲートが緑
- `evals/compare-echo.jsonl` を使った E2E 実行が新スキーマの `summary.json` を出力する
- `summary.json` に `sample_unit` / `n_tasks` / `repeats` / `paired` が存在する
- `docs/operations/evaluation-harness.md` に「標本単位は unique task」「seed_applied が
  false のときは stochastic repeat と表記する」旨が明記されている
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

- `evals/phase2/tasks-v2.jsonl`（新規）
- `scripts/validate_tasks.py`（新規）
- `docs/operations/benchmark-v2.md`（新規）

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
- `answer_type`: `single`（単一正解）/ `multi`（複数正解可）/ `verifiable`（実行検証可能）/
  `freeform`（自由記述）
- `freeform` の場合は `rubric` フィールド（採点観点の配列）が必須
- `verifiable` の場合は `grader.type = "exec"` を使い、WP-5 の実行検証器に接続する
- `review_status`: `pending` / `approved`。**`approved` への変更は人間のみが行う**

### 3.4 規模と構成

- unique task 合計 **150 問以上**（目標 300 問）
- 各 family 最低 20 問
- `easy` の割合は 20% 以下（direct routing の品質低下測定用に意図的に残す）
- 単一正解 / 複数正解 / 外部検証可能 / 長文生成を別集計できるよう `answer_type` を必ず埋める

### 3.5 難易度キャリブレーション手順（`docs/operations/benchmark-v2.md` に記載）

1. `best small single` 条件で `--repeats 3` を実行する
2. セット全体の正答率が 40〜70% に入っているか確認する
3. 全反復で正答したタスク（=天井）の割合が 20% を超える場合、そのタスクを
   より難しいものへ差し替える
4. 全反復で誤答したタスクの割合が 30% を超える場合、床効果として一部を差し替える
5. キャリブレーション結果を `evals/phase2/calibration.json` に保存する

### 3.6 `scripts/validate_tasks.py` の仕様

引数: タスク JSONL のパス。以下を検査し、違反時は非 0 で終了する。

- 各行が JSON として parse できる
- `id` が一意
- `family` が許可リストに含まれる
- `answer_type` が許可リストに含まれる
- `freeform` に `rubric` があり、要素が 1 つ以上
- `grader` が `_grade()` で扱える形式（既存の grader 実装と整合）
- 合計 150 行以上、各 family 20 行以上
- `easy` の割合が 20% 以下

### 3.7 テスト

`tests/test_validate_tasks.py`（新規）
- 正常なフィクスチャが通ること
- id 重複 / 未知の family / rubric 欠落 / 件数不足 のそれぞれで失敗すること

タスクファイル本体の検証は `tests/test_benchmark_v2.py` で
`validate_tasks.main([...])` を呼んで行う。

### 3.8 完了条件

- `PYTHONPATH=src python3 scripts/validate_tasks.py evals/phase2/tasks-v2.jsonl` が成功
- `docs/operations/benchmark-v2.md` に出典方針・キャリブレーション手順・
  ライセンス上の注意（既存データセットから引用する場合は出典とライセンスを
  `source` フィールドに明記）が書かれている

### 3.9 HUMAN GATE

ゴールド解答の正しさはエージェントの自己検証だけでは担保できない。以下を守ること。

- 可能な限り `answer_type: verifiable`（実行で検証できるコーディング / 計算課題）を優先し、
  自己検証可能な比率を上げる
- `review_status` は必ず `pending` で提出し、`approved` への変更は行わない
- 人間レビュー前の状態で実験を回してはならない旨を PR 本文に明記する

---

## 4. WP-3: 回答正規化と semantic voting

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
3. `Answer:` / `答え:` / `最終回答:` / `Final answer:` などの接頭辞除去（大文字小文字無視）
4. 前後の空白除去、連続空白を単一空白へ
5. casefold
6. 末尾の句読点（`.` `。` `,` `、`）除去
7. 数値の桁区切りカンマ除去、`42.0` → `42` の末尾ゼロ正規化

`extract_final_answer` は、複数行テキストから「最終回答」に相当する部分を取り出す。
接頭辞行があればその行を、無ければ最終非空行を返す。

`cluster_answers` は正規化文字列の一致でクラスタ化する。埋め込みモデル等の
追加依存は導入しない（本プロジェクトは依存ゼロを維持する）。

### 4.4 orchestrator への適用

- `_majority_vote` を `answers.majority_vote` の呼び出しに置き換える
- 設定 `coordinator.ensemble.normalize`（既定 **true**）を追加。`false` で従来の完全一致に戻せる
- `SUPPORTED_ENSEMBLE_VOTES`（`config.py:16`）に `"semantic"` を追加する
  - `"semantic"` は、正規化クラスタリングの結果すべてのクラスタが同数（＝多数決が決まらない）
    場合にのみ、judge role へ「どの候補が同一内容か」を問う 1 回の追加呼び出しを行う
  - judge role は `coordinator.ensemble.judge_role` で指定。未指定かつ `is_verifier` role が
    無ければ `ConfigError`
  - judge 呼び出しが失敗した場合は正規化多数決の結果へフォールバックし、警告を記録する
- 投票の内訳を `OrchestrationResult` に `vote_summary`（クラスタ数・得票数・
  正規化が効いたか）として記録する

### 4.5 テスト

`tests/test_answers.py`（新規、テーブル駆動）
- `42` / `42.0` / `**42**` / `The answer is 42.` / `答え: ４２` がすべて同一クラスタになる
- 日本語の表記ゆれ（全角・半角、句点有無）
- 異なる回答が別クラスタのままであること
- `cluster_answers` の決定性（同入力→同出力）

`tests/test_orchestrator.py`
- `test_majority_vote_normalizes_equivalent_answers`
- `test_majority_vote_exact_mode_when_normalize_false`
- `test_semantic_vote_calls_judge_only_on_tie`
- `test_semantic_vote_falls_back_when_judge_fails`

`tests/test_config.py`
- `test_semantic_vote_requires_judge_role`

### 4.6 完了条件

- `CHANGELOG.md` に「`ensemble.vote: majority` の既定挙動が正規化ありに変わった」ことを
  破壊的変更として記載
- `scripts/evaluate_orchestration.py` の `_normalize_answer` が `answers.normalize_answer` へ
  委譲され、実装の重複が無い
- `docs/reference/openai-compatibility.md` または該当設計文書に `semantic` vote が記載されている

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
- `src/fugu_local/orchestrator.py`（`sequential_dag` パターンの分岐）
- `src/fugu_local/config.py`（`SUPPORTED_PATTERNS` と `coordinator.dag`）
- `src/fugu_local/server.py`（streaming 非対応の明示）

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
プロンプト定型文は `stages.py` に定数として置き、テストで内容を固定する。

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
- `enabled: false` の stage はスキップし、下流は**直前の有効な stage の出力**を受け取る
  （WP-7 の ablation はこのフラグだけで実現できること）
- `fanout` は solver stage のみ有効。既定 1

### 5.6 制約

- **streaming 非対応**: `server.py:434` 付近の `allowed_patterns={"direct", "role_split"}` は
  変更しない。`sequential_dag` では `_prepare_stream` が `None` を返し、非 streaming 応答に
  フォールバックすること。この挙動をテストで固定する。
- **tool calling との併用は当面不可**: `tools.enabled` かつ `sequential_dag` の組み合わせは
  `ConfigError` で明示的に拒否する（黙って無視しない）。
- **deadline の尊重**: 既存の `_deadline_passed` を各 stage 境界で評価し、超過したら
  その時点の最良出力を返す。途中終了は `OrchestrationResult.warnings` に記録する。
- `OrchestrationResult` に `stage_results: List[StageOutput]` を追加する
  （WP-6 の stage 別寄与分析と WP-7 の ablation がこれに依存）。

### 5.7 実装手順

1. `stages.py`（データクラス、パーサ、プロンプト定型文）＋ テスト
2. `pipeline.py`（stage 実行、スキップ、fanout、deadline）＋ テスト
3. orchestrator / config / server の結線 ＋ テスト、ドキュメント

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
- `enabled: false` の stage がスキップされ、下流が直前の有効出力を受け取る
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

## 6. WP-5: 実行・制約検証器

### 6.1 目的

DAG の verifier stage を、LLM の自己申告ではなく機械的検証で支える。

### 6.2 変更対象

- `src/fugu_local/verifiers.py`（新規）
- `src/fugu_local/config.py`
- `src/fugu_local/pipeline.py`（verifier stage からの利用）

### 6.3 仕様

実装する検証器:

- `ConstraintVerifier`: 正規表現 / 数値範囲 / 文字数 / 出力形式（JSON parse 可否）の検査。
  外部プロセスを起動しない。
- `PythonExecVerifier`: 候補回答から抽出したコードを実行し、期待出力と比較する。
- `CitationVerifier`: **与えられた context 内**の引用一致のみを検査する。
  外部 URL の取得は行わない。

`PythonExecVerifier` の実行条件（すべて必須）:

- `subprocess` で `sys.executable -I -S` を起動（isolated / no site）
- `cwd` は `tempfile.TemporaryDirectory()`、実行後に必ず削除
- `timeout` 既定 10 秒、超過時は kill して `verification="failed"` を返す
- 環境変数は最小構成（`PATH` のみ）で渡す
- 標準出力・標準エラーは `max_output_bytes`（既定 65536）で切り詰める
- 例外・タイムアウト・非 0 終了はいずれも「失敗」として構造化して返し、
  呼び出し側に例外を伝播させない

### 6.4 安全性の制約（`CONTRIBUTING.md` の Safety 方針）

- **既定は無効**。`"verify": {"executable": {"enabled": false, ...}}`
- 既定の tool registry には追加しない。これは tool ではなく**評価専用の検証器**であり、
  `src/fugu_local/tools.py` には一切手を入れない。
- **HTTP サーバー経路から到達できないこと**。`server.py` から
  `executable.enabled=true` を有効化できないことをテストで固定する。
- サンドボックスはネットワーク遮断を保証しない。この制限を
  `docs/operations/security-profile.md` に明記する。

### 6.5 テスト

`tests/test_verifiers.py`（新規）
- 正常終了 / 非 0 終了 / タイムアウト / 巨大出力の切り詰め
- 一時ディレクトリが必ず削除される
- 既定設定では実行器が無効
- `ConstraintVerifier` の各検査
- `CitationVerifier` が外部取得を行わない（ネットワーク呼び出しが無いことをスタブで確認）

`tests/test_server.py`
- サーバー設定経由で実行検証器を有効化できない

### 6.6 完了条件

- 上記テストが通る
- `docs/operations/security-profile.md` に実行検証器の脅威モデルと制限が記載されている
- `docs/audit/feature-inventory.md` に `experimental`（既定無効）として追加

---

## 7. WP-6: 誤答相関・補完性の計測

### 7.1 目的

「異種モデルを並べる」ことに意味があるのは誤答が相関していない場合に限る。
また、改善が synthesizer 単体に由来するのか協調 stage に由来するのかを分離する必要がある。

### 7.2 変更対象

- `scripts/analyze_results.py`（新規）

### 7.3 仕様

入力: WP-1 で拡張された `results.jsonl`（`worker_outputs` と `stage_results` を含む）。
出力: `analysis.json` と `analysis.md`。

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

- 統計量はすべて `random.Random(20260802)` 固定でブートストラップし、決定的に再現できること
- 欠損（`worker_outputs` が空、`stage_results` が無い）は例外にせず、
  該当指標を `null` として出力し、`analysis.json.warnings` に理由を記録する

### 7.4 テスト

`tests/test_analyze_results.py`（新規）
- 手計算できる小さな合成 `results.jsonl` を用意し、各指標の期待値を厳密に検証
- phi 係数の既知ケース（完全相関 1.0 / 無相関 0.0 / 完全逆相関 -1.0）
- 破壊率・修復率の境界ケース
- `worker_outputs` 欠損時に例外を出さず warning を出す

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

### 8.4 予算統制

新フラグ:

- `--budget-tokens N`: 1 タスクあたりの総トークン上限
- `--budget-wall-ms N`: 1 タスクあたりの実時間上限
- `--match-budget-to <label>`: 指定条件の実測中央値に他条件の予算を合わせる

予算超過時の扱い（**この定義をドキュメントに固定すること**）:

- 予算を超過した run は `budget_exceeded: true` を記録し、**不正解として集計する**。
  「予算内で返せた回答」を評価対象とするため、除外ではなく不正解扱いとする。
- 予算内で完了した run のみを対象にした補助集計も `summary.json` に併記する。

実験は 2 系統で回す。

- **budget-matched**: total tokens / wall-clock の上限を揃える
- **natural configuration**: 各方式の通常設定で Pareto frontier を見る

### 8.5 `scripts/make_ablation_configs.py`

入力: DAG 設定 JSON。出力: stage を 1 つずつ `enabled: false` にした設定群。
- `writer` stage は除去対象外（最終回答生成が消えるため）
- 生成されたファイル名は `06-ablation-no-<stage>.json`
- 生成した設定がすべて `load_config()` を通ることを確認してから書き出す

### 8.6 テスト

`tests/test_evaluate_orchestration.py`
- `test_budget_exceeded_runs_count_as_incorrect`
- `test_match_budget_to_uses_median_of_reference_condition`
- `test_summary_includes_budget_filtered_view`

`tests/test_make_ablation_configs.py`（新規）
- 生成数が「除去可能 stage 数」と一致する
- `writer` が除去されない
- 生成物がすべて `load_config()` を通る

### 8.7 完了条件

- `scripts/run_phase2_comparison.sh` が echo backend で end-to-end に完走する
- `docs/operations/phase2-comparison.md` に条件表・予算定義・実行手順が記載されている

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
| `require_graceful_degradation` | true（障害時に品質を維持して縮退運転できる） |

**No-Go**: 上記いずれも満たさない場合。

### 10.4 `scripts/decide_phase2.py` の仕様

入力: WP-1 の `summary.json`、WP-6 の `analysis.json`、WP-8 の
`cluster-benchmark.json`、および `decision-criteria.json`。

出力: `decision.json` と `decision.md`。

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

- 実機での実験実行（人間 or 実 LLM 環境）
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
| WP-2 のゴールド解答に誤りが混入する | 実験結果が無効化する | `verifiable` タスク比率を上げる。人間レビューを必須ゲートにする |
| ローカル小型モデルが stage スキーマを守らない | DAG が機能しない | 寛容なパーサを正常系として実装。`parse_error` 率を測定指標として記録する |
| DAG の stage 数だけレイテンシが線形に増える | 実用性が失われる | deadline を全 stage で尊重。WP-7 の budget-matched で必ず定量化する |
| 実行検証器が安全側の制約を破る | セキュリティ問題 | 既定無効、tool registry 非登録、server 経路から到達不可をテストで固定 |
| 改善が synthesizer 単体に由来する | 仮説が実証されない | WP-7 の ablation と WP-6 の帰属分析を Go 条件に含める |

### 非スコープ

- Phase 3 以降（動的推論ルータ、ノード登録クラスタリング、Web UI）の実装
- 埋め込みモデル・外部依存の導入（本プロジェクトは依存ゼロを維持する）
- クラウドモデルを本番経路に組み込むこと（参照値としての比較のみ）
- 実 LLM を必要とする CI テストの追加
