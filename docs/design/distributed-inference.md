# 分散ローカル推論基盤 設計仕様（Thug-Fugu 拡張）

Status: partial。静的な複数マシンendpoint指定、model pool、active health、
least-busy/round-robin、endpoint failover、HTTP bounded queueは実装済み。
node登録・動的発見・hardware inventory・自動配置・coordinator冗長化は未実装。
作成 2026-06-23 aiko-dev、status更新 2026-07-30。
親: `docs/design/local-llm-orchestration.md`（単機の最小オーケストレータ）。
現状SSOT: [`docs/audit/feature-inventory.md`](../audit/feature-inventory.md)。
狙い: **メモリの小さい非力なマシンを複数束ね、各マシンに軽量モデルを分散配置して、合計で大きな処理能力を出す**ローカル分散推論基盤にする。

---

## 1. 背景と狙い

- 1 台では大モデル（120b 等）が載らない／遅い非力マシンが複数あるとき、各マシンに**軽量モデル**（gpt-oss:20b・phi4・mistral 等、量子化）を 1 つずつ載せて束ねる。
- 速度（並列で速くする）は GPU/帯域次第で限界があるが、**質（多視点アンサンブル）・容量（横に広げる）・頑健性（1 台落ちても続く）**を水平スケールで稼ぐ。
- クラウド非依存・LAN 内完結＝オフライン／無料／データが外に出ない（自己主権）。

## 2. 現状の Thug-Fugu で「既にできる」こと（土台）

- `models[].base_url` は **model ごとに独立**。→「model A はマシン1、model B はマシン2」と別エンドポイントへ向けられる。
- worker は `ThreadPoolExecutor` で**並列に投げる**。→各 worker が別マシンの ollama を叩けば、物理的に並列実行される（各マシンが別 GPU を持てば真の並列）。
- role 単位の失敗分離（1 worker 失敗でも他は続行）＋ synthesizer 失敗時の決定論的マージ。
- `model_pools[]`で複数endpointを1論理modelへ束ね、`round_robin` /
  `least_busy`とendpoint failoverを利用できる。
- Ollama `/api/tags` / OpenAI-compatible `/v1/models` active probe、
  passive cooldown、health-aware routingを利用できる。
- HTTPサーバーにはoptional bounded queue / backpressureがある。

> つまり **静的な複数マシン分散は追加実装ゼロで可能**（config の base_url を LAN 内の各ノードに振るだけ）。本設計は、その上に「動的・耐障害・スケジューリング」の層を足す。

## 3. アーキテクチャ（拡張後）

```
[Thug-Fugu Orchestrator (1台 = コーディネータ)]
   │  role→node 割当 / ヘルス / ロードバランス
   ├─HTTP→ Node1: ollama (gpt-oss:20b)   base_url=http://node1:11434
   ├─HTTP→ Node2: ollama (phi4)           base_url=http://node2:11434
   ├─HTTP→ Node3: ollama (mistral)        base_url=http://node3:11434
   └─HTTP→ NodeN: ...
```

- コーディネータ＝Thug-Fugu を動かす 1 台（軽い。推論はしない）。
- ノード＝各非力マシン。ollama を立て LAN で公開（`OLLAMA_HOST=0.0.0.0`）。1 ノード 1 軽量モデルが基本（メモリ制約）。

## 4. 足すべき層（拡張ポイント）

| 層 | 役割 | 現状 | 追加内容 |
|---|---|---|---|
| ① ノード登録/発見 | どのマシンが居て何を載せてるか | 静的endpoint configのみ | `nodes[]`（host/model/capacity）をconfig化。将来はmDNS等で動的発見 |
| ② ヘルスチェック | 生存・負荷の監視 | endpoint active probe/passive cooldownは実装済み。node hardware/load inventoryは無し | node単位のCPU/GPU/RAM/VRAM/load/model状態を収集 |
| ③ ロードバランス/キュー | 同種モデルを複数ノードに置き、空きへ流す | model poolのleast-busy/round-robinとHTTP bounded queueは実装済み | 登録nodeのcapacity-aware routingとper-node backpressure |
| ④ 障害フェイルオーバー | ノード障害時の再ルーティング | 静的pool内endpoint failoverは実装済み | node registryから代替nodeを選ぶ動的reroute |
| ⑤ モデル配置戦略 | メモリに載る範囲で何をどこに | 手動 | ノードの RAM/VRAM 申告に基づき、載るモデルだけ割当。大タスクは分割→複数ノードのアンサンブルで質を補完 |

## 5. config 拡張案（後方互換）

既存実装は`models[]`（model→単一base_url）と
`model_pools[]`（論理model→複数endpoint）を併用できる。例:

```json
{
  "model_pools": [
    {
      "name": "fast",
      "backend": "ollama",
      "model": "gpt-oss:20b",
      "endpoints": ["http://node1:11434", "http://node3:11434"],
      "policy": "least_busy",
      "cooldown_seconds": 30,
      "health": {"enabled": true, "interval_seconds": 30}
    }
  ]
}
```

将来のcluster layerでは、endpoint URLの直接列挙に加えてnode metadataを
参照できるようにする:

```json
{
  "nodes": [
    {"name": "node1", "base_url": "http://node1:11434", "ram_gb": 16, "vram_gb": 8},
    {"name": "node2", "base_url": "http://node2:11434", "ram_gb": 8, "vram_gb": 0}
  ]
}
```

- 既存の`models[]`/`model_pools[]`は維持する（後方互換）。
- `nodes[]`は登録・hardware inventory・動的配置のmetadata layerとして追加する。

## 6. 段階的デリバリ

- **既存実装**: static endpoint distribution、model pools、health、
  least-busy/round-robin、endpoint failover、HTTP queue。
- **評価基盤（#72、実装済み）**: single-vs-multi条件、manifest、raw output、
  latency/tokenを記録し、同じmanifestから再実行できる。
- **Phase 1（#73–#75）**: 2台以上の実機比較とpower/cost測定を実施し、
  複数model構成の価値仮説を検証。
- **Phase 2**: node登録＋hardware/load inventory。
- **Phase 3**: capability-aware dynamic routing / node failover / per-node queue。
- **Phase 4**: node動的発見・モデル自動配置・coordinator冗長化。

## 7. 非力マシン前提の工夫

- 1 ノード 1 軽量モデル（量子化・7B 以下目安）、メモリに載る範囲だけ。
- 速度は GPU/帯域で頭打ち→**速さでなく「横に広げて質と容量」**を取る設計に割り切る（アンサンブル＝直列でも効く）。
- ネットワークは LAN 前提（WAN 越えはレイテンシで割に合わない）。タイムアウトは余裕を持つ。
- コーディネータは推論を持たず軽量に保つ（落ちると全体が止まるため、将来は冗長化）。

## 8. 非対象（この設計でやらないこと）

- モデル並列（1 モデルを複数 GPU に分割する tensor/pipeline parallel）＝別領域。ここは「モデル単位で水平分散」に限定。
- 学習・ファインチューニングの分散。
- 認証付き公開運用（LAN 内前提。外部公開はリバースプロキシ＋TLS 別途）。

## 9. 受け入れ条件（Phase 1）

- [ ] 2 ノード（別マシン）に別モデルを載せ、Thug-Fugu から 1 回のオーケストレーションで両方が使われる
- [ ] 1 ノードを落としても、残りで（失敗分離＋merge で）回答が返る
- [ ] 静的分散で、同一マシン集約より総時間が短縮することを実測（2 GPU ある場合）

## 10. 未確定（要相談）

1. ノード数とハード（手元にある非力マシンの台数・RAM）。
2. コーディネータをどの 1 台にするか（GB10 か、別の常時起動マシンか）。
3. `model_pools` の割当ポリシー初期値（least_busy 推奨）。
