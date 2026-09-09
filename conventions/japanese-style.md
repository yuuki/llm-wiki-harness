---
type: meta
title: "日本語整文ガイド(用語ポリシー)"
date: 2026-06-03 22:30
created: 2026-06-03
updated: 2026-06-03
aliases:
  - "日本語整文ガイド"
  - "Japanese Style Guide"
tags:
  - 2026/06/03
  - meta
  - conventions
  - style
status: evergreen
related:
  - "[[conventions]]"
  - "[[index]]"
---

# 日本語整文ガイド(用語ポリシー)

Navigation: [[conventions]] | [[index]]

> [!important] vault 全体の方針。ノート(`wiki/`・`research/`・`notes/`・`papers/` ほか)を**新規作成・更新するたびに、最初からこの方針で書く**。後追いの整文を不要にするのが目的。`wiki/` 操作時は [[conventions]] §1 と本ガイドの双方に従う。

## 原則

和文の技術文書として自然に読めることを最優先する。和文中に英単語が素のまま混ざる**コードスイッチング**(例:「multi-modal な observability データ」「不要な action でステップを浪費」)を避け、英語表現は (1) 原語のまま残す、(2) カタカナにする、(3) 漢語に訳す、のいずれかに必ず寄せる。常体(だ・である調)で書く。箇条書きの体言止めは可。

## 1. 原語のまま残す(英語・略語)

- **略語**:LLM, SRE, GPU, AI, RCA, API, MOC, MFU, TNR, SOTA, TSFM, RDMA, NIC, CISO, CPU, FinOps, PDF, RoCEv2, CCL, NCCL, PCIe, F1, CUDA, IT, MTTM, TTR, HPC, MoE, SER, POMDP など定着した頭字語。
- **固有名詞**:システム・製品・組織・人物・データセット名(Stratus, AIOpsLab, MegaScale, Datadog, NVIDIA, SONiC, Kubernetes, NVLink, InfiniBand ……)。
- **会議・刊行物**:NSDI, MLSys, NeurIPS, ICML, ASPLOS, IEEE, arXiv など。
- **コード・コマンド・数式・変数**:`` `kubectl patch` ``、`µ(s)=...`、`$T_{max}$`、`pass@1`、`top-5 recall`、`M_C` など。
- **参照語**:`§3.6` はそのまま。`Table N` → **表N**、`Figure N` → **図N**、` vs ` → **「対」**(または「〜と〜」)。

## 2. カタカナに直す(一般 CS 英単語の既定)

和文に混ざる一般語はカタカナにする。代表例:

agent→エージェント、multi-agent→マルチエージェント、telemetry→テレメトリ、observability→オブザーバビリティ、overhead→オーバーヘッド、network→ネットワーク、benchmark→ベンチマーク、baseline→ベースライン、metric(s)→メトリクス、noise→ノイズ、playbook→プレイブック、reflection→リフレクション、ablation→アブレーション、framework→フレームワーク、prototype→プロトタイプ、node→ノード、host→ホスト、storage→ストレージ、traffic→トラフィック、heartbeat→ハートビート、straggler→ストラグラー、attention→アテンション、context window→コンテキストウィンドウ、data→データ、flow→フロー、oracle→オラクル、journey→ジャーニー、segment(ation)→セグメント(セグメンテーション)、actuation→アクチュエーション、chaos→カオス、alert→アラート、operator→オペレータ、probe/probing→プローブ/プロービング、guardrail→ガードレール、whitepaper→ホワイトペーパー、rollback→ロールバック、microservice→マイクロサービス、workload→ワークロード、checkpoint→チェックポイント、interconnect→インターコネクト、accelerator→アクセラレータ。

句の例:state machine→状態機械、control plane→コントロールプレーン、action space→行動空間、reward hacking→報酬ハッキング、zero-shot→ゼロショット、one-shot→ワンショット、end-to-end→エンドツーエンド、safe exploration→安全な探索、undo-and-retry→巻き戻しと再試行、saturate→頭打ちになる(飽和する)。

## 3. 漢語に訳す(定着した標準語。カタカナにしない)

- fault→**障害**(fault localization→**障害箇所特定**、faulty→**障害のある/故障した**、fault injection→**障害注入**)
- localization→**箇所特定**、detection→**検知**(anomaly detection→**異常検知**、change point detection→**変化点検知**)
- failure→**障害/故障**、anomaly→**異常**、mitigation→**緩和**、remediation→**修復/復旧**、root cause→**根本原因**、cause→**原因**
- parallelism→**並列化/並列性**、communication→**通信**(collective communication→**集団通信**)、reduction(次元・データ)→**削減**、co-design→**協調設計**
- precision→**適合率**、recall→**再現率**、similarity→**類似度**、continuity→**連続性**、non-intrusive→**非侵入(的)**、instrumentation→**計装**、production→**本番**、change point→**変化点**、self-repair→**自己修復**、autonomous→**自律(的)**、congestion→**輻輳**、proactive→**先回り型(予防的)**、reactive→**事後対応型**
- false positive→**偽陽性**、false negative→**偽陰性**

## 4. 例外:残してよい英語

- **原語併記の括弧書き**:`障害箇所特定(fault localization)` のように、漢語・カタカナに続けて括弧で原語を添えるのは推奨。初出の専門用語に有効。括弧内の原語は消さない。
- **太字で定義した専門用語**:`**normality reduction**`・`**redundancy reduction**`・`**3D parallelism**`・`**Hybrid Parallelism**` のように、その場で定義する term-of-art は原語のまま太字で示してよい。
- **ハイフン複合の定着語**:OP-level / sub-OP-level / group-level / machine-level / socket-based / metric-based / path-oriented / time-oriented / cross-variate / hypothesis-driven など。無理に分解せず原語で残す。
- **列挙ラベル**:並列化次元の `data/tensor/pipeline/sequence/expert` のような短い列挙は原語のままでよい。

## 5. 直訳調・不自然な表現を避ける

語彙だけでなく言い回しも自然にする。
- 「〜する営み」→「〜する取り組み/作業/こと」
- 「被覆する段」→「カバーする段階」、「攻める段が分かれた」→「注力する段階が分かれた」
- 英文の語順を引きずった硬い表現は、和文として自然な語順・助詞に直す。過度な体言止めの連続を避ける。

## 6. やってはいけないこと

- 出典・引用・数値の削除や改変。
- wikilink `[[...]]` の中身(リンク先・表示名)の置換(リンク切れになる)。`(Source: [[...]])` 表記も保持。
- frontmatter の言語スタイル変更(title/aliases などは原語規約に従う)。
- 段落・箇条書きの情報量の増減(整文は表現の調整にとどめる)。

## 迷ったとき

過剰変換より取りこぼしを許容する。判断に迷う専門用語は「原語+括弧での和訳併記」にして両立させる。
