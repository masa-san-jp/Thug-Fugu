# Fan-out executor contract

この文書は、複数 worker を同時に実行する fan-out の実装契約を定める。
対象は `role_split` と `parallel_ensemble` の request 内 worker fan-out であり、
routing、endpoint 容量制御、HTTP transport、sequential DAG の段階実行は対象外とする。

## SSOT

- 実装: `src/fugu_local/execution.py` の `FanoutExecutor`
- 呼び出し: `src/fugu_local/orchestrator.py` の `_run_workers`
- テスト: `tests/test_execution.py`、`tests/test_orchestrator.py`

## ライフサイクル

`FuguLocalOrchestrator` が設定された `max_parallel_workers` の固定サイズ pool を
1つ所有する。request ごとに executor や worker thread を作成しない。

- `_run_workers` は選択済み task を一括 submit する。
- 結果は submit 時の role 順で返す。完了順や backend の応答順に依存しない。
- worker の例外はその worker の `failed` 結果になり、同じ batch の他 worker を壊さない。
- `close()` は health monitor と fan-out pool を停止する。running callback は安全な
  中断ができないため完了を待ち、queue に残る task は破棄する。
- server の `server_close()` と CLI の `run` 終了時に `close()` を呼ぶ。

## deadline の状態

`deadline` は `time.perf_counter()` の絶対時刻である。deadline 到達時、
queue に残る task は新たに callback を開始せず `not_started` とする。
すでに callback に入った task は Python から任意の backend I/O を安全に中断できないため、
呼び出し元には `running_at_deadline` と `cancel_requested=true` を返す。
実体の future は pool が追跡し、callback 完了後に解放する。したがって、deadline は
レスポンスの待機を打ち切る境界であって、socket cancellation を意味しない。

`TaskTiming` hook は task の `started` と terminal state (`completed`、`failed`、
`not_started`、`running_at_deadline`) を通知し、submit 時刻、開始時刻、終了時刻、
queue 待ち時間を持つ。hook の例外は実行結果へ影響させない。

## 非目標

- endpoint の選択、queue policy、capacity planning
- socket / backend I/O の強制 cancellation
- sequential DAG の並列化
- backend batching
