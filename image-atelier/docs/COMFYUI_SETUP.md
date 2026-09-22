# dev-comfyui の準備と検証範囲

安定版はGitHub Release v0.3.1。ComfyUI接続はdev-comfyuiの試作です。

## 現在の環境

確認した既存の K:\ComfyUI は2023-07-26の版で、Qwen 2.1ノードがありません。既存ComfyUI・ReForge・モデルフォルダーは変更していません。8188番ポートへの接続もできませんでした。

## 利用者側で用意するもの

1. [公式ComfyUIリリース](https://github.com/Comfy-Org/ComfyUI/releases)から、Qwen-Image-2.1対応の新しいNVIDIA Windows portable版を取得してください。調査時点のv0.37.0にQwen 2.1対応が含まれます。
2. 既存環境と別の `K:\ComfyUI-Atelier` 等へ展開します。この直下に `python_embeded` と `ComfyUI` が並ぶ構成を想定しています。
3. 以下の3種類を配置します。[公式の配置説明](https://huggingface.co/Comfy-Org/Qwen-Image-2.1)

| 種類 | 配布ファイル例 | 展開先の配置場所 |
|---|---|---|
| 画像生成モデル | [qwen_image_2.1_int8_convrot.safetensors](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/tree/main/diffusion_models) | ComfyUI/models/diffusion_models/ |
| 標準テキストエンコーダー | [qwen3vl_8b_int8_convrot.safetensors](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/tree/main/text_encoders) | ComfyUI/models/text_encoders/ |
| VAE | [qwen_image_2.1_vae_bf16.safetensors](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/tree/main/vae) | ComfyUI/models/vae/ |

差し替えを試す場合は [qwen3vl_8b_nvfp4_heretic.safetensors](https://huggingface.co/pottokao/Qwen-Image-2.1-Text-Encoder-Heretic-NVFP4/tree/main) も `text_encoders` に置きます。標準と差し替え版を画面の一覧から選べます。モデル名がQwen 2.1用でも量子化方式の実行可否はComfyUI環境に依存します。NVFP4の実GPU動作はまだ未検証です。

既存の `K:\Qwen-Image-2.1` はDiffusers形式であり、そのフォルダーをComfyUIの単体safetensors用ローダーへそのまま指定することはできません。

## 起動と接続

Atelierのフォルダーで実行します。

```powershell
.\start-comfyui.ps1 -PortableRoot 'K:\ComfyUI-Atelier'
```

通常のAtelierも `start.cmd` で起動し、Qwenを選択します。

1. 接続URLを `http://127.0.0.1:8188` にする。
2. 「接続確認・モデル一覧を取得」を押す。不足ノードがあればComfyUIの更新が必要。
3. Qwen-Image-2.1用の生成モデル・テキストエンコーダー・VAEを選び、「ComfyUI設定を保存」。
4. 最初は512×512・8ステップで確認してから通常条件へ進める。

ComfyUIはAtelier専用インスタンスにしてください。他のComfyUI作業とGPUを共用しません。追加カスタムノードは必須ではありません。設定は無視対象のdata/comfy-settings.jsonへ保存します。モデルファイルはGitへ追加しません。

## 実装した範囲

- Qwenの本番実行経路はComfyUI HTTP APIへ変更。画面からDiffusersへ切り替える選択肢・自動フォールバックはありません。従来コードは既存記録の復旧とテスト用としてまだ残しています。
- 公式ワークフローを基にUNETLoader／CLIPLoader／VAELoader／TextEncodeQwenImage21／KSampler／VAEDecode／SaveImageを構成します。
- 作成指示・ネガティブ・CFG・実行シード・要求寸法を渡す。Euler／simple、denoise 1.0。標準Diffusersと数値的に同一の生成を保証しません。
- 元画像は編集時のみ先頭、参照画像は登録順、部分修正マスクは最後。合計10枚まで。部分修正は従来同様、必要ならAtelier側で範囲内合成します。ネイティブのノイズマスク指定ではありません。
- 画像のアップロード、ジョブIDの保存、結果回収、履歴、自動保存、対象IDを指定した取消。
- `/prompt` の応答不明時は再送しません。「中断ジョブを照合・残処理を取消」で保存済み結果の回収または対象ジョブの取消を行います。未確認中はローカルGPUの次の処理を止めます。
- 終了後はキューが空なら `/free` にモデル解放を要求します。起動スクリプトは `--cache-none` を指定します。
- PE-T2I／PE-I2Iは既存の独立した補強処理を利用します。

## 未完了・未検証

- 新しいComfyUI・3種類のモデルが未配置のため、実ComfyUIでのAPI互換確認と実GPU生成は未実施。
- NVFP4の速度・画質・メモリ量、VAE差し替え、実際のモデル解放、実GPU取消は未検証。
- ステップ単位の時間計測・進捗イベントは未対応。現在は経過時間と結果到着を表示。旧設定で時間計測が有効なら画面で解除してください。
- 旧Diffusersコードの完全撤去は、ComfyUIの実GPU試験が済んでから実施する移行作業として残しています。

検証はtests/test_comfy.pyのHTTPモックで、生成要求、画像回収、シード、参照の順序、アップロード中の取消、応答不明からの再送なしの復旧を確認します。ComfyUIの実機検証とは区別してください。

ブラウザーのモック試験では、一覧取得→モデル3種の選択→設定保存→指示確認→登録→結果回収→履歴の完了表示まで成功。ComfyUI用テスト8件、JavaScript33件成功。全Python137件の実行では既存の並列PNG書き出し試験で一時的なPermissionErrorが1件発生し、該当スイート34件の再実行は全件成功しました。実ComfyUIでの検証とは区別しています。
