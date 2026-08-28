# HTTP transport contract

この文書は、Ollama と OpenAI-compatible backend が共有する HTTP 呼び出しの契約を定める。
目的は、複数 endpoint への並列 dispatch で client 側の connection setup を毎回発生させず、
JSON、Ollama NDJSON、OpenAI SSE の response lifecycle と timeout/redaction を一貫させること。

## 実装

- 抽象: `src/fugu_local/transport.py` の `HTTPTransport` / `TransportResponse`
- 既定: `PersistentHTTPTransport`
- テスト・特殊 handler 用: `UrllibHTTPTransport`
- backend wiring: `src/fugu_local/backends.py`
- テスト: `tests/test_transport.py`、`tests/test_backends.py`

`PersistentHTTPTransport` は `(scheme, host, port)` ごとに、呼び出し thread 専用の
`http.client.HTTPConnection` / `HTTPSConnection` を保持する。同じ connection object を
複数 thread が同時利用することはない。response を完全に読み終えて close した場合だけ
再利用し、`Connection: close`、read error、timeout、途中終了では entry を破棄する。
壊れた接続をその場で POST retry しないため、推論の二重実行を避け、次回 request で再接続する。

## timeout と timing

`timeout` は connect、request write、response read を含む monotonic な総予算として扱う。
socket timeout は残り予算へ更新され、期限切れ後の新しい read は開始しない。
`TransportTiming` hook は response close 時に次を通知する。

- `connect_ms`: 新規 connection の connect 時間（再利用時は 0）
- `ttfb_ms`: request 送信完了から response header 到着まで
- `read_ms`: response を開いてから close まで
- `total_ms`: request 開始から close まで
- `reused_connection`: connection reuse の有無

hook の例外は backend 結果へ影響させない。caller は必ず response を context manager
または `close()` で終了させる。

## response semantics と redaction

transport は status、headers、bytes read、line iterator のみを提供し、backend が protocol
固有の JSON/NDJSON/SSE 解釈を行う。HTTP error の body は read して socket を再利用可能に
できる場合でも、例外メッセージへ含めない。backend の `_safe_url()` は query/fragment を
削り、prompt、model output、credential を error に漏らさない。

## 比較と非採用

毎回 `urllib.request.urlopen` を呼ぶ方式は最小だが、connection reuse と connect/read の
境界を観測できない。外部 pool dependency は依存を増やし、local runtime の導入方針から
外れるため採用しない。標準ライブラリ `http.client` の thread-local connection reuse を
まず採用し、実機 benchmark で改善が測定誤差内なら pool を拡張しない判断を残す。

対象外は backend 固有 scheduler、public internet 向け TLS/auth termination、model server
自体の性能改善である。
