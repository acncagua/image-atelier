# Qwen開発版の画面統合

`Dev-qwen`ブランチでは、既存のモデル選択に「Qwen-Image-2.1（ローカル）」を追加した。新規生成・ブラッシュアップの画面構成、変更・維持指示、プリセット、送信指示文の確認、履歴・PNG保存・次の編集対象を共用する。

- 初期設定: 40ステップ、seed 42、model CPU offload、VAE512/stride384、BF16、1枚PNG。
- Qwen専用欄でステップ数・seed・CPUオフロード・VAEタイルを設定。出力各辺128〜2048、32の倍数。形式と枚数は固定。
- 元画像と参照資料は既存の順序で渡す（最大8枚）。指示文は既存の合成結果をそのまま渡し、追加のプロンプト書換えは行わない。
- 部分修正は未対応。UIで実行を抑止し、サーバーでも拒否する。ブラシを通常編集として黙って扱うことはしない。
- 専用環境・モデルフォルダーは `data/qwen-settings.json`。今回のPCは `.venv-qwen` と `K:\Qwen-Image-2.1` を設定済み。画面の「Qwenの詳細・環境設定」から変更可能。
- Qwenジョブは既存SQLite履歴・同一ID登録に対応し、API料金・予約・利用額へ加算しない。OpenAIへは送信しない。
- SwinIRとGPU実行ロックを共有し、順番に処理する。Qwen待機中・実行中の取消に対応。モデルはジョブごとに別プロセスで読み込み、終了時に解放する。
- Windows Job Objectで子孫を管理。再起動では自動再推論せず、完成PNGが残る場合は既存の再処理操作で登録・保存を再開する。

## 統合検証（2026-09-21）

Python回帰105件とJavaScript28件が成功。その後追加した再起動回収・GPU共有の2件を含むQwen接続テスト10件も成功（現在のテスト構成はPython107件・JS28件）。ビルド成功。

ブラウザーのモック環境でモデル切替、ステップ・seed、プリセットの送信文への反映、編集・新規生成の履歴登録、料金なし表示、下書き復元、部分修正の実行抑止を確認。

実GPUの統合試験: テスト専用DBで元画像と既存形式の日本語指示文を使用。512×512・8ステップ・VAE512/stride384の編集が58.01秒でcompleted、履歴・PNG保存まで成功。試験ID `e1eb8db3-7111-4920-ad3c-c258d8983f12`、PyTorch最大CUDA確保量18,879,136,768 bytes。API課金・本番履歴への追加なし。

未検証: 統合経路での2048出力、複数参照の実GPU品質、Qwen統合中の実機強制終了（共通Job Objectの強制終了は既存試験あり）、全入力でのメモリ安定性。単独試験の実測は以下を参照。

---

# Qwen-Image-2.1 実装試験の準備

2026-09-21 / 開発ブランチ `Dev-qwen`。Atelierの本番画面・APIジョブ・SwinIRキューにはまだ接続していない。準備と推論試験を分ける。

## 確認済み

- ローカルモデル: `K:\Qwen-Image-2.1`
- 配布元: `https://huggingface.co/Qwen/Qwen-Image-2.1`
- モデルのGitコミット: `790c92633540aa0cb11d9abf19eb46d861714758`
- 重み: 7ファイル、33,115,613,408 bytes。LFSポインターではない。必要ファイル、インデックスの参照先、safetensorsヘッダー・オフセット・ファイル長が整合。
- 全ファイルのSHA256照合・モデルの実際のロード・生成品質は未検証。
- RAM: 137,191,849,984 bytes（約128GiB）。初回確認時の空きRAMは約56GiB。
- GPU: NVIDIA GeForce RTX 5090、約32GiB、ドライバー576.80。CUDA利用可能・BF16対応。空きVRAMは初回約18GiB、後の診断では約30GiBに変化しており、実行時に確認する。
- `.venv-qwen` を新設。ReForge、`.venv`、`.venv-swinir`、モデル本体は変更していない。
- Torch 2.7.1+cu128 / torchvision 0.22.1+cu128 / Transformers 5.17.0 / Accelerate 1.15.0。
- DiffusersのPyPI 0.40.0には `QwenImage21Pipeline` がなかったため、公式Gitの `80c7ed262aeffbeb43ef13ae04baeb9b84515a69`（0.41.0.dev0）に固定した。この構成ではクラスのimportが成功。
- `pip check` 成功。モデル検査の単体テスト4件成功。試験コマンドの準備モードを実行済み。

主要依存は `requirements-qwen.txt`、全依存の取得済み構成は `requirements-qwen-lock.txt`。再作成は `setup-qwen.ps1` を使う。診断結果と試験設定はGit除外の `.tmp/qwen/` に保存。

## 起動前診断

以下は `image-atelier` フォルダー内で実行する。

```powershell
.\.venv-qwen\Scripts\python.exe qwen_probe.py --model K:\Qwen-Image-2.1 --environment --report .tmp/qwen/preflight.json
```

診断はモデルの全重みをRAM/GPUへロードしない。依存クラスのimport、CUDA・BF16の検出、ファイル構造確認まで行う。

## 生成の最小試験

まず準備のみ。ネットワークや実APIは使用しない。

```powershell
.\.venv-qwen\Scripts\python.exe qwen_trial.py --model K:\Qwen-Image-2.1 --width 512 --height 512 --steps 8
```

GPUを使うアプリの状況を確認してから、実推論を明示的に実行する。

```powershell
.\.venv-qwen\Scripts\python.exe qwen_trial.py --model K:\Qwen-Image-2.1 --width 512 --height 512 --steps 8 --run
```

既定はBF16、model CPU offload、1枚、seed 42、簡単な英文の生成指示。`--offload sequential` も選択できるが、速度とメモリは未測定。量子化、追加のプロンプト書換えモデル、最適化カーネルは初期試験に含めない。

出力は `qwen-test-output/<試験ID>.png`、設定・進捗・結果は `.tmp/qwen/trials/<試験ID>/`。既存画像を上書きしない。実推論の子プロセスにはAPIキー等を渡さず、ローカルファイルのみでロードする。通信を禁止し、自動再試行しない。Windowsの既存Job Object起動処理を使い、Ctrl+C・30分タイムアウトで子プロセスツリーを終了する。試験ハーネス自体の実推論・取消試験は今後行う。

## 単画像編集の試験

UTF-8の指示ファイルと、使用してよい入力画像を用意する。

```powershell
.\.venv-qwen\Scripts\python.exe qwen_trial.py --model K:\Qwen-Image-2.1 --input "入力画像の絶対パス.png" --prompt-file "編集指示.txt" --width 512 --height 512 --steps 8 --run
```

初期は背景を単色へ変える等、成否を確認しやすい編集で試す。出力寸法・透過・seed再現性・入力不変を確認後、1024、2048、参照画像追加へ段階的に進める。低ステップ試験は画質評価用ではない。

## 次の実装順序

1. モデルのロード、512生成・単画像編集。実行時間、GPUピーク使用量、RAM使用量、OOM時の状態を確認。
2. 1024→2048の段階試験、RGBA・日本語指示・複数参照の確認。
3. マスク編集方式を確認。取得したDiffusersの呼出しシグネチャには `mask_image` がなく、現在のAtelierマスクをそのまま渡せるとは扱わない。公式のマスク・注釈画像の入力方式と、範囲外合成を別途調査する。
4. SwinIRとGPUを共有するキュー、永続登録・取消・異常終了・保存復旧の接続。API料金には計上しない。
5. 実行先選択、モデル別寸法・参照枚数・seed・steps、履歴・下書きの対応。
6. Windowsの強制終了と再起動、既存OpenAI/SwinIRの回帰。検証結果を分けて記録する。

## 利用条件と未検証事項

ローカルの `LICENSE` はQwen Research Licenseで、研究・評価目的の非商用利用に限定される。商用利用には別契約が必要。モデルはAtelierのGitやリリースへ同梱しない。

準備完了時点では実推論は未検証だった。後続の実測は下記に追記し、そこで確認した条件と未検証項目を区別する。

参考: [モデル配布元](https://huggingface.co/Qwen/Qwen-Image-2.1)、[公式実装案内](https://github.com/QwenLM/Qwen-Image-2.1)。


## 単画像編集の実測（2026-09-21）

利用者による512角の背景変更成功後、同じ入力画像・日本語の背景変更指示で1024角を試験した。どちらもBF16・model CPU offload・8ステップ・seed 42。

| 試験 | 結果 | 時間 | PyTorch GPU最大確保量 |
|---|---|---|---|
| 512×512（利用者実行、記録確認） | completed、RGBA PNG保存 | 72.953秒 | 20,530,417,664 bytes（約19.1GiB） |
| 1024×1024（エージェント実行） | completed、RGBA PNG検証成功 | 50.688秒 | 20,530,417,664 bytes（約19.1GiB） |

1024角の出力を目視し、背景が青色へ変わっていることを確認した。細部は再描画されており、指定範囲外の厳密な画素保持を示す試験ではない。入力は縦長だが試験指定は正方形であり、元の縦横比を保持した編集試験は別途必要。処理時間はOSキャッシュや他アプリの状況が異なるので、解像度による速度比較には使わない。

1024試験ID: `68138c244a324e3fab92903c35d5110a`。記録は `.tmp/qwen/trials/`、出力は `qwen-test-output/` に保存。モデルロードと単画像編集は今回の条件で成功したが、2048角、40ステップの画質比較、複数参照、透過指示、マスク、取消・強制終了、UI統合は未検証。

`--diagnose` で失敗段階・traceback（error.txt）とメモリ推移（memory.jsonl / phases.jsonl）を残せる。両試験でWindowsコミット余裕が一時的に小さくなっているため、全条件でのメモリ余裕を保証する記録ではない。ページファイル設定は変更していない。


## 2048角の編集試験（2026-09-21）

同じ画像・指示・8ステップ・seed 42で、次の2条件を実行した。

| 条件 | 結果 | 時間 | GPU最大確保量 |
|---|---|---|---|
| VAE分割なし | 8ステップ後の画像変換が長時間継続し、試験担当が中止。PNG未保存 | 約352秒を観測 | 完了時統計なし。途中のGPU全体使用量は約30GB |
| `--vae-tiling` あり | completed、2048×2048 RGBA PNGの検証成功 | 81.640秒 | PyTorch 20,197,726,720 bytes（約18.8GiB） |

分割なし試験ID: `19d66022799447c4ae3d2ffd468c760b`。明示的なOOM例外は出ておらず、停止理由は高負荷と最終変換の長時間継続。キャンセル経緯はsupervisor.jsonに記録。

分割あり試験ID: `145499a5b6744911a1ca13b06fd7dc46`。空きRAM最小約15.1GiB、Windowsコミット余裕最小約17.4GiB。背景の青色化は確認できたが、縮小プレビューで背景に薄い格子状の濃淡が見える。タイル分割の影響が候補であり、実行成功と画質合格は分けて扱う。40ステップやタイル設定の比較はまだ行っていない。

今回追加した `--vae-tiling` は公式 `pipe.vae.enable_tiling()` を呼び、エンコード・デコードの両方に影響する。診断表示にもdecoding段階を追加した。メモリ・処理時間は他アプリやOSキャッシュ・ページファイルの状態にも左右されるため、差をすべてVAE分割の効果と断定しない。

再現コマンド（image-atelier内、入力・指示パスは試験用ファイル）:

```powershell
.\.venv-qwen\Scripts\python.exe qwen_trial.py --model K:\Qwen-Image-2.1 --input "R:\ChibiAlice.png" --prompt-file "R:\ChibiAlice.txt" --width 2048 --height 2048 --steps 8 --vae-tiling --diagnose --timeout 600 --run
```


## タイル・ステップ数の比較（2026-09-21）

入力・背景変更指示・seed 42・BF16・model CPU offload・2048×2048を固定。VAE分割はエンコード・デコード両方に適用されるため、タイル変更は入力条件画像の表現にも影響する。

| タイル / stride（重なり） | ステップ | 時間 | PyTorch GPU最大確保量 | 観察 |
|---|---:|---:|---:|---|
| 256 / 192（64） | 8 | 81.640秒 | 18.81GiB | 背景に細かな格子状ムラ |
| 512 / 384（128） | 8 | 84.016秒 | 18.81GiB | 縮小表示では細かな格子が目立ちにくい |
| 512 / 384（128） | 40 | 215.563秒 | 18.81GiB | 髪と衣装の輪郭・細部がより明瞭。今回の品質重視候補 |

追加試験IDは順に `2412bd0a91344cbc8506e5d6a424236a`、`bc253b49289c4fe394bc0fae67662b7e`。全試験でPNG保存と2048角RGBAを確認。比較画像は `.tmp/qwen/comparison-2048.jpg`、集計値は同名JSON。

目視評価は縮小比較画像による今回の1入力・1指示・1seedの結果であり、あらゆる画像での優位性やタイル継ぎ目の完全消失を保証しない。40ステップでも背景は完全に均一な単色ではなく濃淡がある。元の人物・文字も再描画され、厳密な画素保持ではない。

512タイル8ステップのWindowsコミット余裕最小は約0.008GiB、40ステップでは約0.90GiB。どちらも成功したが、GPU確保量だけでPC全体の余裕を判断しない。システム設定や他アプリは変更していない。

品質重視の再現コマンド:

```powershell
.\.venv-qwen\Scripts\python.exe qwen_trial.py --model K:\Qwen-Image-2.1 --input "R:\ChibiAlice.png" --prompt-file "R:\ChibiAlice.txt" --width 2048 --height 2048 --steps 40 --vae-tiling --vae-tile-size 512 --vae-tile-stride 384 --diagnose --timeout 900 --run
```

`--vae-tile-size` と `--vae-tile-stride` を追加。両方32の倍数、タイル128〜1024、strideはタイル未満を検証する。試験用の既定値は従来通り256/192であり、今回の候補は明示オプションで指定する。


## テキストからの新規生成（2026-09-21）

`R:\Qwen_Gene.txt` の指示をそのまま使用し、入力画像なしで生成した。指示は「2Dイラスト」「ドレスを着た少女が城のダンスホールで和やかな表情で立っている」。2048×2048、40ステップ、seed 42、BF16、model CPU offload、VAE512/stride384。

試験ID: `66e87726c837439ebe4c5738114c3e43`。completed、2048×2048 RGBA PNG検証成功。所要199.484秒（約3分20秒）、PyTorch GPU最大確保量17,592,641,024 bytes（約16.4GiB）。Windowsコミット余裕の最小値は約0.030GiB。画像を確認し、ドレス姿の人物・城のホール・2Dイラスト表現を認めた。ポーズには動きがあり、細かな指示追従の精度を保証する試験ではない。

出力は `qwen-test-output/66e87726c837439ebe4c5738114c3e43.png`。プロンプト書換えモデル、外部API、参照画像は使用していない。
