# ACM / IEEE の PDF をブラウザで取得する

`wiki-ingest-paper` の従属資料。`scripts/fetch-paper-pdf.sh` が**終了コード 5** を返したとき(ACM DL / IEEE Xplore で curl が PDF を取れなかったとき)に読む。`wiki-ingest-thesis` で IEEE / ACM の長編サーベイを取るときも同じ手順で PDF を手元に落としてから `fetch-book.sh` にローカルパスを渡す。

## なぜブラウザか

- ACM DL は curl を Cloudflare で弾く(403 `Just a moment...`)。ブラウザなら Open Access 論文の PDF が取れる。
- IEEE Xplore は Open Access 論文なら curl で取れる(ヘルパーが先に試す)。購読論文はログイン済みセッションが要る。利用者が購読している機関・個人のアカウントを使う。パスワードは利用者が管理し、エージェントは知らない。

## ブラウザの選び方

**Claude in Chrome 拡張(`mcp__claude-in-chrome__*`)を入れた Dia を既定にする。** 各経路を試した結果:

| 経路 | ACM | IEEE(ログイン後) | 保存 |
|---|---|---|---|
| **Dia + 拡張** | `?download=true` への遷移で保存できる。ページ内 `fetch` は 403 | ページ内 `fetch` で PDF | ダイアログなしで `~/Downloads` |
| Chrome + 拡張 | ページ内 `fetch` でも遷移でも可 | 同左 | 「保存場所を確認する」設定がオンだと Save ダイアログで止まる。拡張からは押せない |
| 内蔵ブラウザ(`mcp__Claude_Browser__*`) | `fetch` は通る | 同左 | 保存ダイアログで止まる。`127.0.0.1` への送信も遮断。下見専用 |
| Computer Use | — | — | ブラウザは read 層。クリック・入力不可。使わない |

拡張に複数のブラウザがつながっていることがある。`list_connected_browsers` で一覧し、Dia を `select_browser` で選ぶ。名前が `Browser 1` のように区別できないときは、タブで `navigator.userAgentData.brands` を見る。`Google Chrome` を含まず `Chromium` だけなら Dia である。

## ヘルパーの引き継ぎ出力

```text
browser_fetch=https://ieeexplore.ieee.org/document/10172776       # 開くページ
browser_pdf_path=/stampPDF/getPDF.jsp?tp=&arnumber=10172776&ref=  # そのオリジン上の PDF パス(ページ内 fetch 用)
browser_download=                                                 # 遷移だけで保存が始まる URL(ACM のみ)
publisher=ieee                                                    # acm | ieee
slug=ieee-10172776                                                # .raw の slug
origin_ref=https://doi.org/10.1109/ICSE48619.2023.00010           # 既存 source 照合用
existing_sources=                                                 # 空でなければ既存ページの更新に回る
```

ACM は `browser_download=https://dl.acm.org/doi/pdf/<doi>?download=true`、slug は `acm-<DOI の後半>`。IEEE の DOI は doi.org のリダイレクト先から arnumber を引くので、サンドボックス下では doi.org を許可して呼ぶ。

## 手順

### 1. ツールをまとめて読み込み、Dia を選ぶ

```text
ToolSearch select:mcp__claude-in-chrome__list_connected_browsers,mcp__claude-in-chrome__select_browser,mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__tabs_close_mcp,mcp__claude-in-chrome__navigate,mcp__claude-in-chrome__javascript_tool,mcp__claude-in-chrome__browser_batch,mcp__claude-in-chrome__computer,mcp__claude-in-chrome__find,mcp__claude-in-chrome__request_credentials
```

`autofill_credential` / `enter_verification_code` / `release_credentials` も読めるなら同じ呼び出しに足す。ただし Claude Code のセッション(デスクトップアプリの Code タブを含む)では、`autofill_credential` は公開されないか、公開されても `tab_unavailable` / `agenticModeNotEnabled` で必ず失敗する(anthropics/claude-code#87365、not planned で閉じられた)。自動入力が動くのは Cowork / claude.ai 側で、対応ブラウザは Chrome に限られる。Claude Code から ingest するときは、IEEE のログインを利用者に頼む前提で進める。拡張が「not connected」を返したら、エラー文中のリンクを示して Dia(または Chrome)の Claude パネルでサインインしてもらう。待てないなら、利用者に PDF を手で落としてもらい、そのパスで 4 へ進む。

### 2. ダウンロードの承認を取る(省略しない)

ファイルのダウンロードは利用者の明示承認が要る。取得する全論文を 1 回の質問にまとめ、保存名、取得元(`browser_fetch`)、保存先(`~/Downloads`)を示す。IEEE の購読論文ならログインすることも同じ質問で告げる。

### 3. PDF を `~/Downloads` に保存させる

**ACM**: `browser_download` へ `navigate` するだけでよい。保存名は ACM が決める(`<DOI の後半>.pdf`、同名があると ` (1)` 等が付く)。`Just a moment...` が数秒出ることがある。**CAPTCHA やチェックボックスが出たら自分で解かず、利用者に頼む。**

**ACM DL に載る VLDB(DOI `10.14778/`)は、この経路では保存されない。** `www.vldb.org` の公開 PDF を curl で取る([`../SKILL.md`](../SKILL.md) の「VLDB」節)。

**IEEE**: `browser_fetch` を開き、ページ上で次を実行する(`PDF_PATH` と `SLUG` を置き換える)。同一オリジンの `fetch` なのでログイン状態がそのまま効く。

```js
const PDF_PATH = '<browser_pdf_path>', SLUG = '<slug>';
const r = await fetch(PDF_PATH, {credentials: 'include'});
const blob = await r.blob();
const head = new TextDecoder().decode(await blob.slice(0, 5).arrayBuffer());
if (head === '%PDF-') {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = SLUG + '.pdf';
  document.body.appendChild(a); a.click(); a.remove();
}
({signedIn: /Sign Out/.test(document.body.innerText), status: r.status, size: blob.size, head})
```

- `head` が `%PDF-` なら保存が始まっている。4 へ。
- `<!DOC` なら権限が無い。`signedIn` が false ならログインする。右上の **Personal Sign In** を開く。`autofill_credential` が使えるなら `request_credentials`(`website: https://ieee.org`、`keywords` に `IEEE`, `IEEE Xplore`, 購読アカウントの手掛かり)のあとでサインイン欄に対して呼ぶ。**ブラウザが欄を先に埋めることがあるが、購読アカウントでなければ送信しない**(購読の無いアカウントでは論文が取れない)。`autofill_credential` が使えないときは利用者にサインインしてもらう。**パスワードやワンタイムコードを自分で入力しない。チャットで尋ねない。**
- `signedIn` が true でも `<!DOC` なら購読範囲外。arXiv 版など別版を探し、利用者に報告する。購入はしない。
- `status` が 202 で空なら、ページの読み込み途中である。数秒おいて再実行する。

### 4. 手元の PDF を `.raw/papers/` に取り込む

```bash
ls -t ~/Downloads/*.pdf | head -n3   # 直前に保存された PDF を確かめる(ACM は保存名が slug と異なる)
FETCH_ORIGIN_REF="<origin_ref>" bash scripts/fetch-paper-pdf.sh "$HOME/Downloads/<保存されたファイル名>" "<slug>"
```

`FETCH_ORIGIN_REF` を付けると、ローカル PDF でも DOI で既存 source を照合する(`existing_sources=`)。`~/Downloads` に無ければ、Chrome の保存ダイアログで止まっている(`~/Downloads/.com.google.Chrome.*` という一時ファイルが残る)か、保存先が既定と違う。利用者に確かめる。`~/Downloads` の元ファイルは利用者の領域なので消さない。

### 5. 後片付け

作ったタブを `tabs_close_mcp` で閉じる。`request_credentials` を使ったなら、全論文の取得後に `release_credentials` を呼ぶ。IEEE からサインアウトはしない(次回も使う)。

## バッチ並行 ingest との分担

ブラウザは 1 つなので、この手順は**オーケストレータが subagent 起動前に直列で**行う。全論文をまず `fetch-paper-pdf.sh` に通し、終了コード 5 の論文だけ 2〜4 を順に済ませてから、subagent にはローカルパスと `FETCH_ORIGIN_REF` を渡す。subagent はブラウザを操作しない。subagent が終了コード 5 を受けたら `Deferred: browser fetch (<browser_fetch>)` と報告して止まる。

## frontmatter

`url:` には出版社の論文ページ(`browser_fetch`)、`doi:` には DOI を入れる。`sources:` は `.raw/papers/<slug>.pdf` 以下、arXiv 論文と同じ。
