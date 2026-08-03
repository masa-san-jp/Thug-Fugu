# 逐次推論 DAG（`sequential_dag`）設計仕様書

Status: `experimental`（v0.1.0 以降の追加機能）。実装状況の SSOT は
[`docs/audit/feature-inventory.md`](../audit/feature-inventory.md)。

親計画: [`docs/plans/phase2-decision-implementation-plan.md`](../plans/phase2-decision-implementation-plan.md)
の WP-4。親 Issue: [#106](https://github.com/masa-san-jp/Thug-Fugu/issues/106)。

## 1. 背景と目的

既存の `role_split` パターン（`_run_role_split`、`src/fugu_local/orchestrator.py`）は、
全 worker role に同一の `messages` を渡して**並列実行**し、その出力を synthesizer が
統合する「fan-out + synthesis」である。planner の分解結果を solver が入力として使う
経路も、critic が solver の候補を読む経路も存在しない。

`sequential_dag` パターンは、**前工程の成果物を次工程が実際に入力として利用する**
推論グラフを実装する。これは「複数モデル協調＝品質向上」という仮説を検証可能にする
ための機能であり、Phase 2 の Go / Pivot / No-Go 判定（#106）の中核である。

## 2. 全体構成

```text
planner    -> 問題・制約・部分問題を構造化（subproblems を出力）
solvers    -> planner の subproblems を割り当てられて独立に解く（fanout 可）
verifiers  -> 各 claim を検証（LLM ベース。実行検証・制約検証は別 WP-5）
critic     -> 候補と検証結果を読んで誤りを特定
reviser    -> critic の指摘を受けて回答を修正
claim_judge-> 主張単位で採用・棄却・不明を判定
writer     -> 採用された主張だけから最終回答を生成
```

7 stage は固定である。stage の追加・削除・並び替えはサポートしない
（`src/fugu_local/stages.py` の `STAGE_NAMES`）。

## 3. stage 間データ契約

各 stage の応答は寛容な JSON パーサ（`stages.parse_stage_output`）で
`StageOutput` にパースされる。

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
    subproblems: List[str] = field(default_factory=list)
    raw_text: str = ""
    parse_error: Optional[str] = None
    usage: Optional[TokenUsage] = None
```

**パース失敗は正常系の一部である**。ローカル小型モデルは頻繁に JSON スキーマを
外すため、パース失敗時は例外を投げず `answer=生テキスト全体`, `parse_error="<理由>"`
として扱い、パイプラインを止めない。未知の JSON フィールドは無視する。

## 4. プロンプト構造

各 stage のシステムプロンプトは `stages.stage_system_prompt(stage)` が返す
**固定文言**（本文言い回しの調整は本 WP のスコープ外。実験結果を見て別 PR で行う）
に、全 stage 共通の JSON 応答指示（`STAGE_JSON_INSTRUCTION`）を連結したものである。

ユーザーメッセージは、元タスクと、**それまでに完了した stage の出力を構造化した
セクション**（`## Header` 形式）を累積したものである。例えば critic の呼び出し時点では:

```text
## Task
<元タスク>

## Subproblems from planner
1. ...
2. ...

## Constraints from planner
- ...

## Candidate answers from solvers
- [solver#1] ...
- [solver#2] ...

## Verification results
- claim "..." -> failed (reason: ...)
```

というセクション群が渡る。これにより「前段の出力が後段の入力に実際に使われている」
ことをテスト（`tests/test_pipeline.py`）で直接検証できる。

## 5. 設定

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

- `stages[].name` は 7 種のみ許可。未知の名前・重複は `ConfigError`
- `stages[].role` は `roles[]` に存在すること。存在しなければ `ConfigError`
- `fanout` は `solver` stage のみ有効。他 stage で 1 以外を指定すると `ConfigError`
- `solver` / `writer` は無効化（`enabled: false`）または省略できない。`ConfigError`
- `coordinator.dag.stages` が非空の場合、`solver` と `writer` は必ず含まれていなければ
  ならない（省略は無効化と同じ扱いのため）

サンプル設定は
[`examples/fugu-local.sequential-dag.json`](../../examples/fugu-local.sequential-dag.json)
を参照（`fugu-local run "..." --config examples/fugu-local.sequential-dag.json` で
EchoBackend により実行できる）。

## 6. stage 別 bypass 規則

`enabled: false` の stage は、「直前の有効 stage の出力を下流へ渡す」という
汎用規則ではなく、**stage ごとに固有の bypass 規則**でスキップする。stage ごとに
入力契約が異なるため、単純な素通しでは ablation（実験での比較）が成立しないためである。

| 無効化した stage | bypass 規則 |
|-----------------|------------|
| `planner` | 原問題全体を単一の subproblem として solver に渡す |
| `solver` | 無効化不可（`ConfigError`） |
| `verifier` | 全 claim の `verification` を `"unavailable"` にし、下流プロンプトに「機械検証は実行されていない」ことを明示する |
| `critic` | critique を空にし、**`reviser` も連動してスキップする**（reviser の入力契約は critique を前提とするため）。solver 候補を直接 claim_judge へ渡す |
| `reviser` | critic の指摘を適用せず、solver 候補と critique をそのまま claim_judge へ渡す |
| `claim_judge` | 全 claim を「未審査（unreviewed）」として writer へ渡し、writer プロンプトにその旨を明示する |
| `writer` | 無効化不可（`ConfigError`） |

この `enabled` フラグと bypass 規則だけで、将来の ablation 実験（WP-7）が実現できる。

## 7. fanout と seed

`solver` stage の `fanout: N` は、solver を N 回呼び出す。各呼び出しには
`derive_seed(base_seed, f"dag:solver#{i}")`（`src/fugu_local/seeding.py`）で導出した
異なる seed が渡る。N 回の呼び出しは、planner の subproblems へラウンドロビンで
1 件ずつ割り当てられる（`subproblems[i % len(subproblems)]`）。

## 8. 制約

- **streaming 非対応**: `prepare_streaming_response()`
  （`src/fugu_local/orchestrator.py` の `allowed_patterns={"direct", "role_split"}`）は
  `sequential_dag` を含まない。HTTP サーバーはこの場合 `None` を受け取り、
  既存の非 streaming（buffered SSE）応答へ自動的にフォールバックする
  （`server.py` 側の変更は不要）。
- **tool calling との併用不可**: `tool_calling.enabled=true` かつ、coordinator の
  `default_pattern` または `rules[]` のいずれかが `sequential_dag` を選択しうる場合、
  `ConfigError` で明示的に拒否する（黙って無視しない）。
- **deadline の尊重**: `coordinator.request_timeout_seconds`
  などから計算される deadline を、各 stage 境界で評価する。超過した場合、
  それ以降の stage 呼び出しを行わず、その時点で得られている最良の回答
  （reviser の修正回答があればそれ、なければ最初の solver 回答）を返す。
  途中終了は `OrchestrationResult.warnings` に記録される。
- **実行検証・制約検証は未実装**: `verifier` stage は現時点で LLM 呼び出しのみで、
  コード実行・制約検証・引用検証（WP-5、`docs/plans/phase2-decision-implementation-plan.md`
  §6）は別途実装される。

## 9. `OrchestrationResult` への追加

- `stage_results: List[StageOutput]` — 実行された全 stage 呼び出しの結果
  （`fanout` により同一 stage 名が複数回出現しうる）
- `warnings: List[str]` — deadline 超過などの途中終了理由

`worker_results` には各 stage 呼び出しが `role="dag:<stage名>"` の
`WorkerResult` として記録され、既存の usage 集計・失敗検知ロジックと統合される。

## 10. 非スコープ

- stage プロンプト文言のチューニング（実験結果を見て別 PR）
- 実行検証器・制約検証器（WP-5）
- budget-matched 実験・ablation 実験ハーネス（WP-7）
- stage 構成の動的変更（現在は config で固定の 7 stage のみ）
