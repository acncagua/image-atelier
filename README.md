# Image Atelier

Windows向けのローカル画像生成・編集UIです。OpenAI Images API、ComfyUI経由のSDXL・Qwen等の生成・編集、タグ補完、StrataとのVRAM共有、役割付き参照資料、マスクと局所合成、比較、履歴、予算管理に対応します。

v0.5.0はComfyUI 0.38.0とStrata 0.1.39で検証しています。更新内容と試験条件は[ComfyUI準備・検証記録](image-atelier/docs/COMFYUI_SETUP.md)を参照してください。

導入・操作方法は [アプリのREADME](image-atelier/README.md) を参照してください。

APIキーは `image-atelier/config.local.json` または環境変数 `OPENAI_API_KEY` でローカルに設定します。APIキー設定、画像、履歴DB、出力、ローカル試験スクリプトはリポジトリに含めません。

初期設定は外部APIを呼ばないモックです。実APIの利用には別途API料金が発生します。
