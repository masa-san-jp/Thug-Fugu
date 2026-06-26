# Thug-Fugu 適応コーディネーター（Fugu 模倣）設計仕様書

Status: draft（設計のみ・実装はマサさんレビュー後）。作成 2026-06-26 aiko-dev。
位置づけ: 本書が SSOT。背景＝`local-llm-orchestration.md`（現行最小オーケストレータ）/`distributed-inference.md`（複数ノード）/`multi-terminal-orchestration.md`（単機マルチ端末・作業メモ）。
発端: マサさん 2026-06-26「単一マシン上で、役割分割と並列処理を、オーケストレーター自身がタスクを見て選ぶ。SakanaAI Fugu のオーケストレーション形態を模倣」。

---

## 1. 目的
現行 Thug-Fugu（設定で固定した役割を静的に呼ぶオーケストレータ）に、**タスクごとに「処理形態」と「役割割当」を自分で決める適応コーディネーター層**を載せ、単一マシン上で複数のローカル小モデル（別ターミナル＝別ポートのサーバ）を Fugu 風に協調させる。

## 2. スコープ
### In
- 単一マシン（GX10 128GB 想定。MBP も同型で可）。小モデルを複数、各ターミナルに OpenAI互換/Ollama サーバとして起動（別ポート）。
- 適応コーディネーター：直答 / 役割分割 / 同種並列 を**自動選択**＋役割（Thinker/Worker/Verifier）割当。
- 検証→再委譲ループ、モデルプール＋負荷分散＋health＋failover。
- 効果測定の評価ハーネス（make-or-break）。
### Out（本spec外）
- 複数マシン分散（`distributed-inference.md` で別途。本設計は localhost だが node を host:port に一般化すれば合流可）。
- コーディネーター自体の学習（Phase 4+ で別spec。本specは非学習＝ルール＋meta-call）。
- Thug-Fugu のサーバ/HTTP API 改変は最小（既存 `serve` は維持）。

## 3. 用語
- **endpoint**：1 つのモデルサーバ（例 `http://127.0.0.1:11435`）。1 ターミナル＝1 endpoint。
- **model**：config 上の論理名（backend/model/base_url を持つ）。既存。
- **pool**：同一モデルを載せた複数 endpoint の束（負荷分散の単位）。新規。
- **role**：Thinker / Worker / Verifier / Synthesizer。
- **pattern（処理形態）**：`direct` / `role_split` / `parallel_ensemble`。
- **coordinator**：タスクを見て pattern と役割割当（plan）を決める層。本specの中核。

## 4. 現状（出発点・既存コード）
- `models[]`（name/backend/model/base_url/timeout）、`roles[]`（model/system_prompt/keywords/always_include/is_synthesizer）、`orchestrator`（selection_policy=keyword|all / max_parallel_workers / temperature）。
- `FuguLocalOrchestrator.chat()`：worker を `ThreadPoolExecutor` 並列実行→synthesizer or 決定論マージ。role 失敗分離・run_id/latency ログあり。
- backends：ollama / openai-compatible / echo。`base_url` は model ごと独立＝**異種モデルを別ポートへ向けるのは既に可能**。
- → 足りないのは「**形態を選ぶ頭**」「同種プール/LB/failover」「検証ループ」「効果測定」。

## 5. 目標アーキテクチャ
```
            ┌──────────────────────────────────────────┐
 user task ─►│ Coordinator（triage）                      │
            │  入力=task → 出力=plan{pattern, assignments}│
            │  方式: rule first → 小モデルmeta-call(JSON) │
            └───────┬───────────────┬───────────────────┘
        plan=direct │   role_split  │  parallel_ensemble
                    ▼               ▼               ▼
              [1 model]   [Thinker→Worker→     [同一モデル×N endpoint]
                          Verifier(検証)]      （pool/LB/並列）
                              │  fail→再委譲          │
                              ▼                       ▼
                         ┌────────── Synthesizer（統合）──────────┐
                         └────────────────► final answer
   pool/LB/health/failover は role_split / parallel_ensemble の実行時に共通利用
```
- coordinator は推論を多く使わない軽い層（小モデル 1 手 or ルール）。
- 全 endpoint はローカル（127.0.0.1:別ポート）。プロセス供給は §10。

## 6. コンポーネント詳細

### 6.1 Coordinator（triage）＝中核
- **入力**：会話（最新 user message ＋文脈）、利用可能なモデル/プール一覧（capability メタ）。
- **出力（plan）**：
  ```json
  {
    "pattern": "direct | role_split | parallel_ensemble",
    "reason": "短い根拠（ログ用）",
    "assignments": [
      {"role": "thinker",  "model": "<model_or_pool>"},
      {"role": "worker",   "model": "<...>"},
      {"role": "verifier", "model": "<...>"}
    ],
    "ensemble": {"pool": "<pool>", "n": 3, "vote": "synth|majority"}  // parallel時のみ
  }
  ```
- **決定方式（2段・非学習）**：
  1. **ルール**：キーワード/入力長/コード有無/明示指定で即決できるものは即決（例：短い事実質問→direct、"複数案/比較/ブレスト"→parallel_ensemble、"実装+レビュー"→role_split）。
  2. **meta-call**：ルールで決まらなければ、小モデルに「このタスクに最適な pattern と役割割当を上記 JSON で一手返答」させる（temperature 低・JSON 抽出は #200/ollama gotcha と同じ手当て＝format:json でなくプロンプト＋brace 抽出）。
- **フォールバック**：meta-call 失敗/不正 JSON → ルールの既定（role_split or direct）に落とす（fail-safe）。
- **可観測性**：plan と reason を run ログに必ず残す（評価ハーネス／将来の学習教師信号）。

### 6.2 役割（Thinker / Worker / Verifier / Synthesizer）
- 現行の planner/coder/reviewer/synthesizer を**役割タクソノミ**に一般化。役割→モデルは config 固定でなく **coordinator が plan で割当**（未指定時は config 既定にフォールバック＝後方互換）。
- 各役割は system_prompt テンプレートを持つ（汎用＋タスク文を差し込む）。

### 6.3 パターン実行器
- `direct`：assignments[0] の 1 モデルへ単発。
- `role_split`：Thinker（分解/方針）→ Worker（実行）→ Verifier（検証）を直列＋必要に応じ Worker 並列。異種モデル可。
- `parallel_ensemble`：pool の N endpoint へ同一/近似プロンプトを並列投入→ vote（多数決 or synthesizer 統合）。
- いずれも最後に Synthesizer（任意）で 1 つに統合。なければ決定論マージ（既存）。

### 6.4 モデルプール＋LB＋health＋failover（`distributed-inference.md` と共通部品）
- `model_pools[]`：論理名→複数 endpoint。`policy`=least_busy|round_robin。
- health：起動時＋定期に `/api/tags`(ollama)・`/v1/models`(OpenAI互換) を叩き、死/過負荷を選抜除外。
- failover：worker 失敗→同プールの別 endpoint で再試行（既存の role 失敗分離の上に追加）。

### 6.5 検証ループ
- Verifier 役が Worker 出力を採点/反証（"正しい？抜けは？"）。`pass/fail + 指摘`を返す。
- fail かつ retry 予算内なら、指摘を添えて Worker へ再委譲（最大 N 回）。予算切れは現状出力＋警告で着地（決して無限ループしない）。

### 6.6 自己再帰（任意・Phase 3）
- coordinator が大タスクを部分問題に割り、各部分に coordinator を再帰適用（深さ上限・予算上限つき）。Fugu の "自分を再帰的に呼ぶ" の最小版。

### 6.7 サーバ供給（プロセス管理・Thug-Fugu の外）
- 各ローカルモデルサーバを **systemd user service**（Issue #203 のパターン流用・`Restart=always`・linger）で常駐化。テンプレート `model-server@.service`（インスタンス＝ポート/モデル）。
- 軽くやるなら tmux で並べる（可視・手早い）。durable は systemd。
- config の endpoint 群と起動するサーバ群を**1 定義から生成**できると二重管理を防げる（将来）。

## 7. config 拡張（後方互換）
```json
{
  "models": [ /* 既存：name/backend/model/base_url/timeout */ ],
  "model_pools": [
    { "name": "fast", "model": "gpt-oss:20b",
      "endpoints": ["http://127.0.0.1:11434","http://127.0.0.1:11435"],
      "policy": "least_busy" }
  ],
  "roles": [ /* 既存。coordinator が上書き割当可。未割当はここを既定に */ ],
  "coordinator": {
    "enabled": true,
    "meta_model": "<small model for triage>",
    "rules": [ /* {match, pattern} の簡易ルール */ ],
    "default_pattern": "role_split",
    "verify": { "max_retries": 1 },
    "recursion": { "enabled": false, "max_depth": 1 }
  },
  "orchestrator": { /* 既存 */ }
}
```
- `coordinator.enabled=false` で**現行の静的挙動に完全フォールバック**（後方互換・既存 config はそのまま動く）。

## 8. 評価ハーネス（make-or-break・最優先で作る）
- **目的**：コーディネーターの選択が本当に結果を良くしてるかを数字で出す。**これが無いと Fugu の"形"だけ真似て学習の旨み無しで迷子**になる（コーディネーターの目利きが全体の成否）。
- **構成**：問題セット（coding/reasoning/QA を各数十問・正解 or 採点器つき）× 実行条件〔A 単一良モデル / B 静的役割分割 / C 適応コーディネーター〕を回し、正答率・レイテンシ・コスト(token)・一貫性を比較表に。
- **採点**：自動（正解一致／ユニットテスト／LLM-judge をローカル小モデルで）。
- **出力**：CSV ＋サマリ。Phase ごとに回して退行を検知。Phase 4 の学習の教師信号にも流用。

## 9. 段階デリバリ＋受け入れ条件
- **Phase 0（コード 0）**：異種ロールを 2〜3 端末で実走・実測。受入＝並列が実際に飛ぶ／アンサンブルが単一を超えるかの数字が出る。
- **評価ハーネス**（Phase 0/1 で先行）：受入＝A/B/C を自動採点で比較表が出る。
- **Phase 1（中核）**：適応コーディネーター（rule+meta-call・非学習）＋パターン実行器 3 種。受入＝plan ログが出て 3 形態が切り替わり、C が B/A に対して評価ハーネスで**有意に劣らない**（できれば勝つ）。
- **Phase 2**：Verifier ループ＋pool/LB/health/failover。受入＝1 endpoint 落としても failover で完走／検証 fail→再委譲が効く。
- **Phase 3**：自己再帰＋systemd 常駐。受入＝サーバ kill で自動復活／再帰が深さ・予算内で止まる。
- **Phase 4+**：学習コーディネーター（別spec）。

## 10. リスク・正直な限界
- **コーディネーターの目利きが全て**：振り分けが下手だと単一良モデルに普通に負ける（複雑さだけ増えて損）。→ 評価ハーネスで常時監視、ダメな pattern は即無効化。
- **ローカル小モデルの天井**：Fugu の "members 超え" の創発は**プールがフロンティアだから**成立した面が大。小モデル同士は「小モデル群＞単一小モデル」は狙えても「フロンティア超え」は狙えない。盛らない。
- **meta-call の不安定さ**：小モデルの JSON 逸脱→fail-safe で既定 pattern に落とす。
- **単機の並列性（実機データで上方修正・2026-06-26）**：当初「1GPU だと奪い合って実質直列」と保守見積りしたが、**Google が単一 DGX Spark で Gemma 4 26B-A4B を 16並列（300 tok/s aggregate・最大32並列）**を実証（@googlegemma 2026-06-23・X API 実取得）。DGX Spark＝GX10 クラス（128GB unified）＋小 active の MoE なら**本物の並列が出る**。よって parallel_ensemble/プールは単機で現実的。ただし aggregate スループットは有限なので、役割を無制限に増やせばレイテンシは伸びる（並列度は HW 実測で決める）。
- **複雑さ**：`coordinator.enabled=false` で常に現行へ戻せる退路を残す。

## 11. 未確定（マサさん確認したい）
1. 初手のモデル構成。**第一候補＝Gemma 4 26B-A4B**（MoE・小 active・Apache2.0・tool-use＋推論モード・DGX Spark で 16〜32並列 実証済み）＝同種プール（parallel_ensemble）に最適。異種にするなら ＋gpt-oss:20b／phi4 等を混ぜる。実行機は DGX Spark（GX10）前提。
2. 評価ハーネスの問題ドメイン優先（coding / reasoning / QA のどれから）。
3. サーバ供給は systemd 常駐 / tmux 手動 どちらの体験で始めるか。
4. meta-call に使う小モデル（軽さ優先で 20b or phi4 等）。

## 12. 参考
- Fugu/TRINITY(0.6B,CMA-ES)/Conductor(RL)：sakana.ai/fugu-release・marktechpost・the-decoder・datacamp（2026-06-22）。
- 既存設計：local-llm-orchestration.md / distributed-inference.md / multi-terminal-orchestration.md。
