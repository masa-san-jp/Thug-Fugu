# 複数ターミナルの小モデルを Thug-Fugu で束ねる 設計

Status: draft（設計のみ・実装未）。作成 2026-06-26 aiko-dev。
親: `local-llm-orchestration.md`（単機最小オーケストレータ）/ `distributed-inference.md`（複数マシン分散）。
発端: マサさん 2026-06-26「Thug-Fugu で、複数ターミナルで立ち上げた小さなモデルをオーケストレーションできない？まず設計を」。

## 0. 一行結論
**できる。しかも"異種モデルを別ターミナルに置いて役割分担"は追加実装ゼロ（config の base_url を別ポートに振るだけ）。** マルチターミナル＝`distributed-inference.md` の **localhost 版**（node の host:port が localhost:別port になるだけ）。本書はその端末特化の設計と、単機ゆえの注意点をまとめる。

## 1. 「ターミナル」とは何か（定義）
各ターミナルで **モデルサーバを1つ起動**して HTTP で待受ける：
- `OLLAMA_HOST=127.0.0.1:11434 ollama serve`（別ターミナルは 11435, 11436…）
- or `llama-server -m model.gguf --port 8081`（別端末 8082…）
- or LM Studio / vLLM（OpenAI 互換ポート）

→ Thug-Fugu から見れば**ただの複数エンドポイント**。`models[].base_url` で各々を指す。Thug-Fugu はクライアント＝**サーバの"立ち上げ"自体は範囲外**（§5 で別途用意）。

## 2. 既にできること（土台・追加実装ゼロ）
- `models[].base_url` は model ごとに独立 → 「planner=:11434 の modelA / coder=:11435 の modelB / reviewer=:8081 の modelC」と**別ターミナルへ役割分担できる**。
- worker は `ThreadPoolExecutor` で並列投入 → 各 worker が別ポートのサーバを叩く＝プロセスとしては並列に飛ぶ。
- role 失敗分離＋synthesizer 失敗時の決定論マージ。
→ **異種モデル×役割分担（heterogeneous roles）は今日の config 編集だけで動く。**

## 3. 正直な注意：単機で「並列に速くなる」とは限らない
- 1 GPU しか無いマシンで N 端末を立てても、推論は GPU を**奪い合って実質直列**になりやすい（速度は伸びない／むしろ切替コストで遅くなることも）。
- 単機でマルチ端末が**効く条件**：(a) モデルが小さく **N 個同時に VRAM/RAM に載る**（GX10 のような大メモリ機なら小モデル数個は同時常駐可）、(b) **異種モデル**で多視点アンサンブル＝質を取りに行く、(c) 障害分離（1 端末が固まっても他は生きる）。
- 「速さ」が欲しいなら端末数より**マシン数**（`distributed-inference.md` の複数ノード）。単機マルチ端末の主目的は**質（アンサンブル）と容量と頑健性**、と割り切る。

## 4. オーケストレーションの2形態
### 4a. 異種ロール（heterogeneous）— 今すぐ可
各 role を別ターミナルの別モデルに割当（planner=A, coder=B, reviewer=C）。Thug-Fugu の素の設計そのもの。config だけ。
### 4b. 同種プール（homogeneous pool）— 要拡張
同じ小モデルを N 端末に立て、1 つの論理モデルとして束ね、負荷分散（least-busy/round-robin）して並列スループット or アンサンブル投票を取る。現状 role→model は 1:1 なので**プール概念が必要**。これは `distributed-inference.md` の `model_pools[]`＋health＋failover（Phase 2–3）と**同一の拡張**。localhost でも LAN でも同じ仕組みで効く。

## 5. "立ち上げ"と監視（Thug-Fugu の外・今日の systemd を流用）
端末サーバを手で起動すると、閉じ忘れ・落ちた時に気づけない。**Issue #203 で入れた systemd user service パターンを流用**して各モデルサーバを supervise するのが筋：
- `model-server@.service`（テンプレート）で `OLLAMA_HOST=127.0.0.1:1143X ollama serve` を `Restart=always`＋linger で常駐。インスタンス＝ポート/モデル別。
- or 軽量に tmux で並べる（可視・手早い）。durable にするなら systemd。
- Thug-Fugu config の base_url 群と、起動するサーバ群（ポート）を**1つの定義から生成**できると二重管理を防げる（将来）。

## 6. config 例（マルチ端末・localhost）
### 6a. 異種ロール（今すぐ）
```json
{
  "models": [
    { "name": "m-a", "backend": "ollama", "model": "gpt-oss:20b", "base_url": "http://127.0.0.1:11434" },
    { "name": "m-b", "backend": "ollama", "model": "phi4",        "base_url": "http://127.0.0.1:11435" },
    { "name": "m-c", "backend": "ollama", "model": "mistral",     "base_url": "http://127.0.0.1:11436" }
  ],
  "roles": [
    { "name": "planner",  "model": "m-a", "always_include": true, "system_prompt": "計画担当。分解しリスク。" },
    { "name": "coder",    "model": "m-b", "keywords": ["実装","code"], "system_prompt": "実装担当。手順を。" },
    { "name": "reviewer", "model": "m-c", "keywords": ["review","risk"], "system_prompt": "レビュー担当。" },
    { "name": "synth",    "model": "m-a", "is_synthesizer": true, "system_prompt": "統合担当。" }
  ],
  "orchestrator": { "selection_policy": "all", "max_parallel_workers": 3, "temperature": 0.2 }
}
```
### 6b. 同種プール（拡張後・distributed-inference.md の形）
```json
{
  "model_pools": [
    { "name": "fast", "model": "gpt-oss:20b",
      "endpoints": ["http://127.0.0.1:11434","http://127.0.0.1:11435","http://127.0.0.1:11436"],
      "policy": "least_busy" } ],
  "orchestrator": { "failover": true, "health_interval_s": 30 }
}
```

## 7. 足す実装（distributed-inference.md と完全に共通）
| 層 | 追加 | 形態 |
|---|---|---|
| プール | `model_pools[]`（論理名→複数 endpoint）。role はプール名も参照可 | 4b に必須 |
| ヘルス | 起動時＋定期に `/api/tags`・`/health`、死/過負荷を選抜除外 | 4b/頑健性 |
| LB/キュー | least-busy/round-robin、満杯はキュー | 4b スループット |
| フェイルオーバー | 失敗 worker を同プールの別 endpoint で再試行 | 頑健性 |
| サーバ supervise | `model-server@.service`（§5・systemd 流用） | "立ち上げ"の自動化 |

## 8. 段階デリバリ（Fugu 模倣・コーディネーター中心・小さく測る）
現状 Thug-Fugu＝"設定で固定した役割を呼ぶ静的オーケストレーター"。Fugu 化＝その上に **適応コーディネーター**（タスク毎に形態＋役割を決める）を載せる。本丸はそれ、他は支える部品。

- **Phase 0（コード 0）**: 6a の異種ロールを 2〜3 端末（localhost 別ポート）で実走・実測。並列が出るか／アンサンブルが単一モデルを超えるかを数字で見る。推測で作り込まない。
- **評価ハーネス（make-or-break・Phase 0/1 で先に作る）**: 同一問題群で「コーディネーター有り vs 単一良モデル」を**自動採点**。これが無いと "Fugu の形だけ・学習の旨み無し" で迷子。**コーディネーターの目利きが全て**（下手な振り分けは単一モデルに負ける）。Phase4 の学習コーディネーターの教師信号にも流用できる。
- **Phase 1（Fugu 型の心臓）**: ①適応コーディネーター層＝triage（(a)直答/(b)役割分割/(c)同種並列 を選ぶ）＋役割割当。初手は **ルール＋小モデルへの meta-call（"どの形態か"を JSON 一手返答）＝非学習**。⑤パターン実行器 3 種を切替。
- **Phase 2**: ③Verifier を一級化（検証→不合格で再委譲ループ）＋④`model_pools`/LB/health/failover（並列・差し替え・opt-out）。
- **Phase 3**: ⑥自己再帰呼び出し＋§5 の systemd で端末サーバ常駐化。
- **Phase 4+**: 学習コーディネーター。協調ログ（決定＋結果）を貯め、TRINITY 式に極小コーディネーターを CMA-ES/RL で進化＝本物に近づく。

### 着手推奨
Phase 0（実測）→ 評価ハーネス → Phase 1（適応コーディネーター）。

## 8.5 SakanaAI Fugu の実形態（2026-06-26 裏取り）と模倣方針
マサさん要件「Fugu のオーケストレーション形態を模倣／オーケストレーターがタスクを見て適した形態を選ぶ」。Fugu は 2026-06-22 ローンチ（学習データ外のため一次情報で確認）。

**Fugu の形態**：それ自体が小さな**コーディネーターモデル**。1 OpenAI 互換エンドポイントの裏で、タスクを見て ①直接答える vs 専門モデルのチームを組む を判断 → ②役割（Thinker / Worker / Verifier）を割当 → ③swappable なモデルプールへ委譲（**自分自身も再帰的に呼ぶ**）→ ④検証 → ⑤統合。土台は ICLR2026 の 2 論文：
- **TRINITY**＝約 **0.6B** の極小コーディネーターを **CMA-ES（進化計算）** で進化させ、遥かに大きいモデル群に Thinker/Worker/Verifier を適応割当。
- **Conductor**＝**強化学習**で「どう指示し・どんな通信構造で・各エージェントに過去のどの部分を見せるか」を学習。

**普通のオーケストレーターとの違い（6点）**：①ルール/固定ワークフローでなく"学習した方策"で配る ②MoE（トークン単位・重み混合）と違いタスク単位でモデル丸ごとの推論を協調 ③役割をタスク毎に動的割当（固定の役割→モデル対応でない）④自己再帰で階層分解 ⑤プール差し替え自由＝провайдер障害を迂回 ⑥検証(Verifier)が組み込み。

**優位性**：創発＝**オーケストレーターが束ねた個々のモデルより強い**（11ベンチ中10首位・SWE-Bench Pro 73.7 vs Opus4.8 69.2 等）。長い多段タスクで顕著。

**俺らの自作への正直な含意（2点）**：
- Fugu の肝は**コーディネーターが学習済み**(CMA-ES/RL)。本設計の初手はルール＋meta-call の**非学習**コーディネーター＝"形"は模倣できるが方策は無い。将来、協調ログを貯めて TRINITY 式に極小コーディネーターを進化させると本物に近づく（Phase 4+）。
- "members 超え"の創発は**プールがフロンティアモデルだから**成立した面が大。ローカル小モデル同士は天井が低く、「小モデル群＞単一小モデル」は狙えても「フロンティア超え」は狙えない。盛らない。

出典: sakana.ai/fugu-release / marktechpost / the-decoder / datacamp（2026-06-22 launch・TRINITY 0.6B+CMA-ES・Conductor RL）。

**Thug-Fugu での模倣方針（単機・ローカル小モデル）**：現状は静的 keyword routing。これに**適応コーディネーター層**を足す＝
1. **トリアージ**：タスクを見て (a) 単一モデルで直答 (b) 役割分割チーム (4a) (c) 同種並列/アンサンブル (4b) を選ぶ。選択器は (i) ルールベース（キーワード/長さ/コード有無）→ (ii) 小モデル自身に「どの形態が適切か」を一手 JSON で答えさせる meta-call、の2段（軽量・ローカル完結）。
2. **役割割当**：Thinker/Worker/Verifier をプールのどのローカルモデルに振るか決める（容量・特性で）。
3. **委譲＆検証**：既存 worker 並列実行＋Verifier 役で検証（自己採点/反証）。
4. **統合**：synthesizer（既存）で統合。
5. **swappable プール＋opt-out**：§7 のプール定義に「有効/無効」フラグ。
※ Fugu 本体は「コーディネーター"モデル"を学習」だが、こちらはまず**学習なしのルール＋meta-call コーディネーター**で形態を模倣（Phase 0–2）。将来、選択ログを貯めて小モデルを軽くチューニング＝Fugu の "学習したコーディネーター" に近づける（Phase 4+）。

## 9. 未確定（マサさん確認したい）
1. 主目的は？ (a) 異種アンサンブルで**質** (b) 同種プールで**スループット** (c) 単に**容量**（大きい処理を小モデル群で）。→ a なら 6a で即着手、b なら pool 実装が要る。
2. 走らせるマシンは GX10 単機？ それとも将来 MBP 等も足す（その瞬間 `distributed-inference.md` の複数ノードに合流）。
3. サーバ起動は systemd 常駐 / tmux 手動 どちらの体験が良いか。
4. モデル構成の初手（例：20b×1＋phi4×1＋mistral×1 の異種3、など）。
```
```
