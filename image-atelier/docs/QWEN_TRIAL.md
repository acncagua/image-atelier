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

現段階では画像生成・編集・32GBでの実測・モデルロード・速度・最大解像度・画質・日本語追従・透過・マスク・実行中取消・UI統合はいずれも未検証。診断成功は実推論成功を意味しない。

参考: [モデル配布元](https://huggingface.co/Qwen/Qwen-Image-2.1)、[公式実装案内](https://github.com/QwenLM/Qwen-Image-2.1)。
