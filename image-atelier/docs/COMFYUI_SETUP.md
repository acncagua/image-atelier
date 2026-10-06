# ComfyUI版の準備と検証範囲

## StrataとのVRAM共有（2026-10-06）

アプリ側に、Strataのexpertキャッシュを縮小して画像生成の空きを作り、ComfyUIモデルを手動解放してからStrataへ戻す機能を追加しました。Strata連携は既定で無効です。他の生成バックエンドにStrataの変更要求を適用しません。ComfyUI用の操作は「画像生成用VRAMを確保」「画像生成用VRAMを解放」の2つで、読み取り専用の「状態を再確認」も用意しています。

### 事前設定と操作

1. 使用するStrataの `strata-<model>.json` の最上位に `"vram_elastic": true` を追加し、利用者がStrataを再起動します。アプリは設定変更・サーバー起動終了・ドライバー変更を行いません。
2. Atelierの「設定」でStrata連携、同じPCのURL、対象GPU、目標空き容量を保存します。APIキーが必要な場合は、既存の `config.local.json` に `"strata_api_key": "..."` を追加するか、環境変数 `STRATA_API_KEY` を設定します。キーをチャットへ貼る必要はありません。
3. 初回、再起動後、保持状態が未確認のときは、先に「画像生成用VRAMを解放」を押します。キューが空であることと、ComfyUI側の解放完了を確認します。
4. 「画像生成用VRAMを確保」で変更要求の完了と実際の空きを確認し、画像を続けて生成します。同じ確保状態では再要求しません。保持量を推測して容量を差し引く処理はありません。
5. 終了後は「画像生成用VRAMを解放」を押します。ComfyUIの解放を確認してから、Strataへ `{"reserve_mib":null}` を送ります。Strata復帰だけ失敗した場合は、解放ボタンで復帰を再試行できます。

| 設定 | 既定値 |
|---|---|
| Strata連携 | 無効 |
| StrataサーバーURL | `http://127.0.0.1:8080`。保存時に末尾の`/v1`を除去 |
| 対象GPU | 同じPCの単一NVIDIA GPUを照合。UUIDを明示指定することも可能 |
| 目標空きVRAM | 18GiB。`ceil(GiB × 1024)`でMiBへ変換 |
| 疎通待ち | 5秒（1〜30秒） |
| VRAM変更待ち | 900秒（750〜7200秒） |

Strata 0.1.39の実装は、LLMのFIFO待ち300秒、並列制御ロック待ち300秒、エンジン応答待ち120秒の経路を持つため、変更要求には短い疎通タイムアウトを使いません。UIは待機状態を表示し、確保・解放中の画像生成や多重操作を止めます。

### 確認方法と保持の扱い

Strataには推論を起こさない `/health` と認証付き `/v1/status` を使用します。未起動、モデル未ロード、稼働、elastic未有効、認証失敗、未知レスポンスを区別します。正常なHTTP応答だけで確保済みにせず、応答の空きと、対象GPUの`nvidia-smi`による実測の小さい値を目標と比較します。2026-10-06の利用者指定により、目標からの不足が1GiB（1024MiB）以内なら確保済みとして生成を許可します。1GiBを超える不足は拒否し、タイムアウト後の適用確認にも同じ基準を使います。要求値自体は変更せず、要求18GiBならAPIには18432MiBを送ります。不足を許容した場合は要求・実際・不足量を表示します。GPUを一意に特定できない場合や値を取得できない場合は「取得不可」とし、架空のプロセスVRAMを使いません。

初期対応は同じPC・単一NVIDIA GPUです。ComfyUIとStrataのAPIが返すGPU名・容量と、ローカルGPUのUUID・容量を照合します。リモートホスト、複数GPU、GPUを照合できない構成には変更要求を送りません。low-RAMモード等のAPI拒否は理由を表示し、連携を無効にした通常運用を選べます。

ComfyUI 0.38.0の`/free`は、処理フラグを設定してすぐ応答します。履歴への完了登録の後にフラグを処理するため、アプリはサーバー識別用の1件と、`/free`後の2件の標準`EmptyImage → PreviewImage`確認ジョブを使い、フラグ処理が実行される順序を確認します。これらは1×1pxでモデル推論をせず、ComfyUIの一時プレビューと内部履歴だけに残ります。Atelierの採用PNG保存先には書き出しません。この完了確認方式は0.38.0で検証しており、未検証版では復帰へ進みません。

確認後に別クライアントのComfyUI処理が入った場合は、再度解放を案内します。実行中・待機中のキューがある場合は操作を拒否し、他のジョブは取り消しません。共通のGPU排他制御でSwinIR・PE等のGPU処理とも競合を防ぎます。連携中は、生成ごとの解放やSwinIR前のComfyUI自動解放を止め、設定した容量を自動的に加算・推定しません。

タイムアウトや接続切断では、要求が後から適用される可能性があるため結果不明を保持します。状態APIの適用値・サーバー識別・GPU・ComfyUIの確認履歴を照合するまで、反対方向の要求や新しいGPU処理を開始しません。確保状態はアプリ再起動後に復元せず、再確認を求めます。接続先・GPU・容量の変更には先に解放が必要です。

連携を無効にしても、Strataの縮小状態は自動では戻りません。アプリ終了時もStrataへ自動復帰しません。ComfyUIの解放は同じサーバーの全モデルに作用し、他のアプリの保持モデルにも影響します。GPU全体の使用量0や、元のLLM使用量・速度への完全復帰は保証しません。

### 検証結果と残る実機確認

対象ソースはStrata 0.1.39、コミット `6f32ec070f23ced9f50e704d854d775da52591ab` の`serve/server.py`、実稼働ComfyUIは0.38.0です。追加のpip/npm依存はありません。GPU取得は既存ドライバーの`nvidia-smi`、Windowsの接続先識別は標準APIを`ctypes`から読み取ります。キーの保存は既存ローカル設定を拡張し、ログ・UIには返しません。

モックでは、無効時の非通信、18GiB→18432MiB、復帰のJSON null、LLM待機、容量不足、多重操作、連続生成で要求を繰り返さないこと、失敗・取消時の保持、キュー拒否、解放失敗時の復帰禁止、復帰だけの再試行、タイムアウト照合、再起動・設定変更・別クライアントのモデル保持を確認しました。Python全202件、JavaScript全42件が成功しています。途中の全試験で既存の並列PNG書き出しに一時的なWindowsアクセス拒否が1件出ましたが、該当34件の再試験と最終全202件は成功しました。

RTX 5090で、Strataの既定URLが接続拒否の状態のまま実GPU試験を実施しました。Strataプロセスは起動・終了せず、Strata設定も変更していません。検証中だけアプリ連携を有効にし、最後に無効へ戻しました。

| 実機試験 | 結果 |
|---|---|
| SDXL 512×512・8ステップを同条件で2回 | 7.81秒／3.14秒で完了。両方の終了後にCUDA空きが同じ約24,186MiBで、読み込み前より少ない状態を維持 |
| SDXL I2I | 512×512、4.16秒で完了 |
| SDXL Inpaint・Only masked | 512×512、4.16秒で完了 |
| SDXL HiresFix | 768×768、5.36秒で完了 |
| Qwen-Image-2.1・既存GGUF構成 | 512×512、30.70秒で完了 |
| SwinIR | 64×64から128×128の実GPU拡大が完了 |
| 手動解放 | 0.38.0のフラグ処理完了を確認。GPU空きは約21,312MiBへ戻る。OS・他アプリ使用量は残る |

ブラウザーでは既定無効、18GiB、待機時間、GPU表示、保持チェックの連携管理、待機中の生成・ボタン禁止、部分成功と再試行を確認しました。待機・部分成功のUI検証は明示したHTTPモック応答です。BrowserプラグインがないためPlaywright／Edgeを使用し、1440px／390px幅で確認しました。

以下に、初期の厳密判定で不足となった履歴と、その後の1GiB不足許容での実機試験を記録します。容量18GiBと指示書の参考値は保証値ではありません。バッチ・追加モデル・VAE・タイル条件を記録して調整します。アプリがStrataを停止したり、モデルをロードしたりする機能はありません。

操作記録は`data/vram-operations.jsonl`、未確認操作・セッション記録は`data/vram-session.json`に保存します。実機結果は`.tmp/strata-live/gpu-results.json`に保持しています。

2026-10-06のStrata起動後の追加確認では、0.1.39の`qwen3.8-flash-next-iq3_xxs`が同じRTX 5090を使用していることを確認しました。ただし、状態APIは`vram.elastic: false`を返しており、キャッシュ縮小・復帰の実機試験は有効化待ちです。実際のAPIとブラウザーで、未有効の理由表示、確保操作の拒否、SDXL生成のジョブ登録前の拒否を確認しました。検証中に変更したAtelier設定は復元済みで、Strata設定・サーバープロセスは変更していません。結果は`.tmp/strata-live/started-results.json`に保存しています。

その後、利用者による再起動で`qwen3.8-flash-next-iq3_s`の`vram.elastic: true`を確認しました。18GiB（18432MiB）の要求でキャッシュが8286から2870 expert slotsへ縮小しましたが、Strata応答の空きは18276MiB（17.85GiB）で不足と判定しました。解放操作で8286 slotsへ戻り、Strataの起動識別値は変わりませんでした。追加の16GiB試験でも不足となり、Strata応答16228MiB、アプリのGPU実測を合わせた判定は15.69GiBでした。両試験とも生成ジョブは登録せず、最後に解放・復帰を確認してAtelier設定を既定無効・18GiBに復元しました。

ローカルStrataソースの`ExpertCache::shrink`は残すキャッシュをセグメント単位で切り上げており、要求した空きに届かない可能性があります。これは観測結果に対する原因の推定です。アプリ側は不足を成功扱いせず、Strata本体も変更していません。結果は`.tmp/strata-elastic/gpu-results.json`と`.tmp/strata-elastic-16/gpu-results.json`に保存しています。これらの`final_state`はクリーンアップ前の状態で、最後の`operations`に解放成功、`settings_restored`に設定復元結果を記録しています。

### 1GiB不足許容と生成中の実GPU計測（2026-10-06）

利用者指定で不足1GiB以内を許可する判定へ変更後、Python全204件、JavaScript全42件、画面ビルドが成功しました。15件のVRAM管理テストには、1024MiB不足の許可、1025MiB不足の拒否、API応答とGPU実測の小さい値での判定、応答不明からの復旧にも同じ基準を適用する試験を含みます。実ブラウザーのデスクトップ・モバイルで許容条件の説明と状態更新を確認しました。

RTX 5090、Strata 0.1.39の`qwen3.8-flash-next-iq3_s`、ComfyUI 0.38.0で試験しました。Strataの起動識別値は全試験で同じです。目標18GiBに対して実際17.64GiBの空きで確保済みとなり、SDXLの512×512をシード42・43で2回生成しました。2回目はチェックポイントのキャッシュが使用され、KSamplerは実行されました。生成間にStrataの再要求や自動解放はありませんでした。

GPU計測は約0.25秒の待ち間隔に`nvidia-smi`の実行時間を加えたサンプリングです。下表の使用量増加は「ComfyUI解放・Strata確保後のGPU全体の空き−生成中に観測した最低空き」で、OSや別アプリの変動も含みます。厳密な瞬間ピークやプロセス別の割当量ではありません。Strataを停止した基準試験は今回行っていません。CUDAの空きも記録しましたが、WindowsのGPU全体の空きと一致しないため、容量判断は`nvidia-smi`に基づきます。

| 処理・条件 | 完了時間 | 最低空き | 確保後からの使用量増加 |
|---|---:|---:|---:|
| SDXL T2I 1920×1088・8ステップ | 3.65秒 | 5.10GiB | 12.54GiB |
| SDXL I2I 1920×1088・8ステップ・denoise 0.7 | 4.56秒 | 5.07GiB | 12.57GiB |
| SDXL Inpaint Whole picture 1920×1088・8ステップ | 5.25秒 | 5.69GiB | 11.95GiB |
| SDXL HiresFix 960×544→1920×1088・8+4ステップ・Lanczos | 4.70秒 | 7.88GiB | 9.76GiB |
| SwinIR 1920×1088→3840×2176・tile 192/overlap 8・SDXL保持 | 完了 | 8.87GiB | 8.77GiB（保持モデル込み） |
| Qwen既存GGUF T2I 512×512・8ステップ・目標21GiB | 30.49秒 | 3.54GiB | 17.31GiB |
| Qwen既存GGUF T2I 1920×1088・8ステップ・目標21GiB | 11.05秒 | 4.34GiB | 16.51GiB |
| SwinIR単独 1920×1088→3840×2176・tile 192/overlap 8・目標6GiB | 完了 | 11.51GiB | 1.60GiB |

SDXLは`animagineXL40_v40.safetensors`、内蔵VAE、CLIP skip 2、CFG 7、DPM++ 2M/Karras、バッチ1です。I2I/InpaintのHiresFixは無効です。目標16GiBでも30ステップのT2I/I2I/Inpaintと30+15ステップのHiresFixが完了し、観測した使用量増加は最大11.91GiBでした。ただし、この回はStrataが約20.9GiBの空きを作ったため、実際の空き16GiBでの最低容量試験とは扱いません。SDXLの通常設定は実際17.64GiBでも一連の処理が成功した18GiBを推奨し、16GiBは観測値約12.6GiBに余裕を加えた節約候補です。

Qwenは現在設定済みの`qwen-image-2.1-Q8_0.gguf`、`qwen3vl_8b_heretic-Q8_0.gguf`、`qwen_image_2.1_vae_bf16.safetensors`です。目標21GiBでは実際20.85GiBで確保し、約3.5GiB以上の空きを残して上記T2Iが完了しました。今回の構成・バッチ1・1920×1088までのT2Iは21GiBを目安にします。QwenのI2I/Inpaint、追加モデル、多枚数、別量子化はこの試験の対象外です。22GiBの確保試験では実際20.97GiBで不足が1GiBを超え、正しく生成開始を拒否しました。

SwinIR単独は目標6GiB、ComfyUI解放済みで同じ1920×1088→3840×2176を試験し、使用量増加は1.60GiBでした。実際の確保後の空きは13.11GiBと目標より多かったため、6GiBそのものを最低必要容量として実証したものではありません。今回のタイル条件では増分に余裕を加えた6GiBを単独運用の目安にし、生成モデルを保持したまま使う場合はSDXL/Qwen用の確保量を維持します。単独試験は`.tmp/strata-tolerance/swinir-alone-results.json`に保存しました。

各試験の終了後は明示的な解放操作でComfyUI解放完了を確認し、StrataへJSON nullを送り、expert slotsが8286へ戻ることを確認しました。Atelier設定は元の連携無効・18GiBへ復元しています。結果と時系列計測値は`.tmp/strata-tolerance/gpu-results.json`、`.tmp/strata-tolerance-16/gpu-results.json`、`.tmp/strata-tolerance-qwen21/gpu-results.json`に保存しました。追加依存関係はありません。

参照: [StrataのGPU共有](https://github.com/Niko1221/Strata/blob/main/docs/DETAILS.md)、[確認したStrataサーバー](https://github.com/Niko1221/Strata/blob/6f32ec070f23ced9f50e704d854d775da52591ab/serve/server.py)、[ComfyUI 0.38.0のフラグ処理](https://github.com/Comfy-Org/ComfyUI/blob/v0.38.0/main.py)、[ComfyUI API](https://github.com/Comfy-Org/ComfyUI/blob/v0.38.0/server.py)。

## モデル選択（2026-09-27追加）

モデル欄からQwen-Image-2.1、FLUX.1-dev、FLUX.1-schnell、FLUX.1-Kontext-dev、Anima、SDXLを選択できます。「接続確認・モデル一覧を取得」で選択モデル用の候補を取得し、必要ファイルを選んで保存してください。設定はモデル別に保持します。旧Qwen設定は最初の保存時に引き継ぎます。

| 選択モデル | テキストエンコーダー | VAE | 追加の参照資料 |
|---|---|---|---|
| Qwen-Image-2.1 | qwen3vl_8b系 | qwen_image_2.1_vae系 | 対応（元画像・マスク込み10枚） |
| FLUX.1-dev / schnell | t5xxl系とclip_lの2個 | ae.safetensors | 非対応 |
| FLUX.1-Kontext-dev | t5xxl系とclip_lの2個 | ae.safetensors | 新規生成で1枚。編集時は元画像を参照 |
| Anima | qwen_3_06b_base系 | qwen_image_vae系（2.1用とは別） | 非対応 |
| SDXL | チェックポイント内蔵 | 内蔵または選択した外部VAE | 非対応 |

## SDXLの設定と操作（2026-10-04追加）

画面のモデルを「SDXL（ローカル）」に切り替え、「接続確認・モデル一覧を取得」を押します。SDXLチェックポイントを選んで「ComfyUI設定を保存」してください。チェックポイント・VAE・Hiresチェックポイントの変更は保存後に実行できます。生成条件は下書きに保存し、履歴の条件復元では当時のチェックポイント選択も復元します。待機中・実行中のジョブは登録時のモデル設定で処理します。

| 項目 | 既定値・動作 |
|---|---|
| モデル | ComfyUIのCheckpointLoaderSimpleにあるチェックポイントを選択 |
| VAE | 未指定はチェックポイント内蔵。外部VAEも選択可能 |
| サンプリング | DPM++ 2M。ComfyUIのKSamplerから候補を取得 |
| スケジューラー | karras。ComfyUIの候補から選択 |
| HiresFix | 新規生成のみ。既定OFF。ONで拡大と2段目のサンプリングを実行 |
| Step数 | 30、範囲1〜150 |
| デノイズ強度 | 0.7、範囲0〜1。img2img・部分修正・Hiresの2段目に使用。新規生成の1段目は1.0 |
| Hiresアップスケーラー | Latent · bislerp。Latent補間、画像補間、配置済みのアップスケールモデルから選択 |
| Hiresチェックポイント | 未指定は元のモデル。指定時は2段目のMODELとCLIPを切り替える |
| CFGスケール | 7、範囲1〜30 |
| CLIP skip | 2、範囲1〜24。ComfyUIのCLIPSetLastLayerへ負の層番号を渡す |
| Inpaint area | Whole picture / Only masked。既定はWhole picture |
| Hires倍率・Step数 | 倍率2。Step数0は1段目と同じ回数 |
| Only maskedの余白 | 32px、範囲0〜256px |

上部の幅・高さは1段目の生成寸法です。各辺128〜2048px、32pxの倍数で指定します。HiresFixの倍率は1より大きく4以下で、拡大後の各辺は4096pxまでです。拡大後の寸法は32px単位に丸め、実行確認と履歴には最終出力寸法を表示します。

HiresFixは単なる保存画像の拡大ではありません。1段目の結果を選んだ方式で拡大し、指定デノイズ強度で再サンプリングします。別チェックポイントへ切り替えると、VAE未指定時は2段目にそのモデルの内蔵VAEを使用します。VAEが変わるLatent拡大では、1段目を画像へデコードしてから2段目のVAEで再エンコードします。外部VAEを指定した場合は両段で同じVAEを使います。

Whole pictureは全体を指定寸法へ合わせて、マスク付きLatentを処理します。「変更範囲だけ合成」を選ぶと、出力寸法に合わせた作業用元画像と合成して範囲外を保護します。Only maskedはマスクと余白を含む領域を切り出し、指定寸法で処理して、塗った範囲だけ元画像へ戻します。全体の画像寸法とマスク外の画素は元画像を維持します。Only maskedの結果は範囲内合成済みの1枚です。合成チェックがONでも同じ境界処理を再適用しません。

ブラッシュアップ（I2I）と部分修正（Inpaint）ではHiresFixを使用しません。チェックは無効・OFF表示になり、Hires倍率・アップスケーラー・チェックポイントの欄も表示しません。古い下書きや履歴にHiresFix ONが残っていても、新規登録時にOFFへ補正し、ComfyUIのワークフローに拡大・2段目を追加しません。新規生成へ戻すと、新規生成用のHires選択を使えます。

この制限変更はSDXLのPythonテスト9件、JavaScriptテスト39件、Viteビルドで確認しました。再起動後の実画面でも、新規生成→ブラッシュアップ→部分修正、ON設定を保持した下書きの再読み込みで、編集時のチェックが無効・OFF表示になり、新規生成へ戻ると有効になることをPlaywright／Edgeで確認しました。

サンプラー名とHires・Inpaintの項目は[Forge Classic neoのUI](https://github.com/Haoming02/sd-webui-forge-classic/blob/neo/modules/ui.py)、[サンプラー定義](https://github.com/Haoming02/sd-webui-forge-classic/blob/neo/modules/sd_samplers_kdiffusion.py)、[2段階生成・マスク領域処理](https://github.com/Haoming02/sd-webui-forge-classic/blob/neo/modules/processing.py)を参照しました。実行にはComfyUI標準ノードを使います。Forge固有の方式をそのまま移植する機能ではなく、画面には接続先ComfyUIで使える候補を表示します。同じシードでもForgeとの画像一致は保証しません。

ComfyUIのチェックポイント一覧には、SDXL以外の重みも含まれることがあります。通常のSDXL用チェックポイントを選んでください。SDXL Refiner単体・専用Inpaintチェックポイント・LoRAは今回の対応範囲に含めません。ネガティブプロンプトとシードを指定できます。追加の参照資料と指示補強PEはSDXLには送りません。

### このPCでのモデル共有と検証

ComfyUI 0.38.0の `K:\ComfyUI\ComfyUI\extra_model_paths.yaml` に、既存の `K:\sd-webui-forge-classic\models\Stable-diffusion` と `models\VAE`、`K:\stable-diffusion-webui\models\ESRGAN` を追加しました。モデルをコピーせずに共有しています。SDXLの初期プロファイルは `animagineXL40_v40.safetensors`、内蔵VAE、Hiresチェックポイント未指定です。既存QwenのGGUF設定も保持しています。

RTX 5090で、Atelierの実HTTP経路から次を検証しました。512×512、CFG 7、CLIP skip 2、seed 42、DPM++ 2M／karrasを基本条件とし、Hiresは1.5倍・2段目6ステップです。外部画像APIは使用していません。

| 試験 | 最終寸法・結果 | 経過時間 |
|---|---|---|
| Animagine XL・内蔵VAEで新規生成 | 512×512、保存・履歴成功 | 7.96秒 |
| SDXL fp16-fixの外部VAEで新規生成 | 512×512、保存・履歴成功 | 5.43秒 |
| Latent · bislerpのHiresFix | 768×768、保存・履歴成功 | 6.51秒 |
| 画像補間 · lanczosのHiresFix | 768×768、保存・履歴成功 | 6.04秒 |
| 4x-AnimeSharp＋Illustrious XLへ切替えるHiresFix | 768×768、保存・履歴成功 | 11.23秒 |
| img2img | 512×512、保存・履歴成功 | 5.05秒 |
| Whole picture＋範囲内合成 | 512×512、マスク外が元画像と一致 | 5.09秒 |
| Only masked＋HiresFix（新規生成限定に変更する前の試験） | 元画像の641×479を維持、マスク外も一致 | 6.63秒 |
| ブラウザーからHiresFix付き生成 | 768×768、履歴からチェックポイント・条件を復元 | 成功 |
| 初期実装のOnly masked＋合成ON | 641×479、出力1枚、マスク外が元画像と一致 | 6.04秒 |

Python全181件、JavaScript全38件、Viteビルドが成功しました。ブラウザー検証はBrowserプラグインがないためPlaywright／Edgeを使用し、1440pxと390px幅で行いました。CLIP skip・デノイズの既定値、未保存モデル設定での実行阻止、下書き再読み込み、最終寸法表示、モデル選択を含む履歴復元を確認しました。画面例外・関連するコンソールエラー・横方向のはみ出しはありません。既存のfavicon未配置による404は動作に影響しません。

実GPU試験は低ステップで実行経路を確認したもので、モデルの画質比較ではありません。4096px級、候補の全サンプラー・全アップスケーラー、その他のSDXLチェックポイントは未検証です。テスト記録と画像はAtelierの `.tmp/sdxl-live/`、通常の保存画像と履歴にも残しています。

参照ノード: [CheckpointLoaderSimple・KSampler・CLIPSetLastLayer](https://github.com/Comfy-Org/ComfyUI/blob/v0.38.0/nodes.py)、[SDXLテキストエンコード](https://github.com/Comfy-Org/ComfyUI/blob/v0.38.0/comfy_extras/nodes_clip_sdxl.py)、[モデルによる拡大](https://github.com/Comfy-Org/ComfyUI/blob/v0.38.0/comfy_extras/nodes_upscale_model.py)。

FLUX／Animaはブラッシュアップでimg2img、部分修正でlatentのノイズマスクを使います。「変更強度」で元画像を変える強さを調整します。未編集の画素を保護するには「変更範囲だけ合成」を有効にしてください。FLUX Fill専用モデルはこの実装の対象外です。Qwenの部分修正は従来のマスク画像参照方式です。

参照資料欄は、選択モデルとモードで追加参照が使える場合に表示します。非対応モデルへ切り替えたとき、添付済みの資料は下書きに残し、生成には送りません。元のモデルへ戻すと再表示します。指示補強PEはQwenでのみ表示します。

候補の絞り込みはComfyUIの一覧とファイル名に基づきます。独自に改名したモデルや未対応派生モデルは表示されない場合があります。重みの内容まで検証する仕組みではありません。GGUFは対応するComfyUI-GGUFローダーが必要です。モデルファイルの自動ダウンロードは行いません。

実装の参照元: [Anima公式ワークフロー](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_anima_base_v1.json)、[ComfyUIのFLUXノード](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_flux.py)。モデル定義は`local_models.json`をPythonとReactで共有しています。旧履歴との互換性のため、保存済みパラメータの`qwen_*`名と旧API経路は残しています。

FLUX／Animaのワークフロー構築、HTTPモックでの生成・結果保存、部分修正の範囲外合成、モデル別設定、参照欄の切替を検証しています。今回確認した実機にはQwen用の重みのみ配置されており、FLUX／Animaの実GPU推論と画質は未検証です。

v0.4.0からQwenの画像生成をComfyUI接続へ変更しました。旧Diffusers版はv0.3.1に保存しています。

## 現在の環境

初期調査時の旧ComfyUIは2023年版でした。その後、利用者が新しいPortable版を K:\ComfyUI に配置し、ComfyUI 0.37.0で公式モデルとGGUF生成を確認しました。2026-10-01にこのPortable版を0.38.0へ更新し、下記の実GPU試験を実施しました。ReForge環境は変更していません。

## 利用者側で用意するもの

1. [公式ComfyUI v0.38.0](https://github.com/Comfy-Org/ComfyUI/releases/tag/v0.38.0)からNVIDIA Windows portable版を取得してください。既存のv0.37.0でもQwen 2.1を利用できます。
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
- 終了後はキューが空なら `/free` にモデル解放を要求します。保持チェックがONならモデルを保持し、OFFまたはGPT Image選択時は解放を要求します。起動スクリプトは標準キャッシュを使用します。
- PE-T2I／PE-I2Iは既存の独立した補強処理を利用します。

## 未完了・未検証

- 公式INT8生成モデル＋公式INT8テキストエンコーダー＋BF16 VAEで、512×512・8ステップの実GPU生成を確認済み。参照画像付き・部分修正・高解像度は実機未検証。
- NVFP4の速度・画質・メモリ量、VAE差し替え、実GPU取消は未検証。標準構成では終了後のGPU全体使用量が1461 MiBまで低下したことを確認。
- ステップ単位の時間計測・進捗イベントは未対応。現在は経過時間と結果到着を表示。旧設定で時間計測が有効なら画面で解除してください。
- 旧Diffusersコードの完全撤去は、ComfyUIの実GPU試験が済んでから実施する移行作業として残しています。

検証はtests/test_comfy.pyのHTTPモックで、生成要求、画像回収、シード、参照の順序、アップロード中の取消、応答不明からの再送なしの復旧を確認します。ComfyUIの実機検証とは区別してください。

ブラウザーのモック試験では、一覧取得→モデル3種の選択→設定保存→指示確認→登録→結果回収→履歴の完了表示まで成功。ComfyUI用テスト8件、JavaScript33件成功。全Python137件の実行では既存の並列PNG書き出し試験で一時的なPermissionErrorが1件発生し、該当スイート34件の再実行は全件成功しました。実ComfyUIでの検証とは区別しています。

## 実機の初回確認（2026-09-22）
ComfyUI 0.37.0 / PyTorch 2.13.0+cu130 / RTX 5090。qwen_image_2.1_int8_convrot、qwen3vl_8b_int8_convrot、qwen_image_2.1_vae_bf16を使用。512×512・8ステップ・CFG 1・seed 42、青い陶器のカップを生成。Atelierのジョブ経過6.25秒で完了、PNGの寸法と絵を確認。画像保存・履歴登録成功。終了後のキューは空、GPU全体使用量1461 MiBを確認。コールドスタート条件を固定した速度比較ではない。

## GGUF生成モデル（2026-09-22）
leejet/ComfyUI-GGUFとgguf 0.19.0をComfyUI専用環境へ導入。拡張子.ggufはUnetLoaderGGUFへ自動切替え、モデル一覧は標準とGGUFローダーの一覧を統合。GGUFノード未導入・選択モデルが専用一覧にない場合は送信前に拒否する。Qwen-Image-2.1-Q8_0＋公式INT8テキストエンコーダー＋公式BF16 VAEで512×512・8ステップ・CFG 1・seed 42の実GPU生成成功（Atelier経過9.80秒）。PNG保存・履歴登録・青いカップの画像内容を確認。異なる量子化や高解像度、参照・部分修正のGGUF実機試験は未実施。ComfyUI接続テスト9件成功。

## ComfyUI 0.38.0対応（2026-10-01）

公式v0.38.0のHTTP APIとノード定義を確認しました。既存の生成・アップロード・画像回収・対象IDによる取消・メモリ解放の経路を引き続き使います。「接続確認・モデル一覧を取得」でComfyUIのバージョンを表示します。バージョン情報を取得できない場合も、ノードとモデルの一覧が取得できれば接続確認は続行します。

Qwen 2.1の軽量VAE `taeqi2_1` を選択できるようにしました。ComfyUI側の `models/vae_approx/` に対応する `taeqi2_1_encoder` と `taeqi2_1_decoder` の重みが両方あると、VAELoaderの一覧にこの名前が現れます。Atelierでは一覧に現れた候補だけを選択できます。FLUX／Anima用の候補には含めません。通常のBF16 VAEも引き続き使えます。軽量VAEは近似モデルなので、画質と速度は実際の画像で確認してください。

本体の更新はComfyUIを停止してから、Portable版に同梱された公式の更新手段を使うか、新しいPortable版を別フォルダーへ展開してください。依存関係も更新し、GGUF等の追加ノードを使う場合は対応状況を確認します。再起動後、Atelier側で一覧を再取得し、モデル設定を保存してください。公式v0.38.0で追加されたMing-Imageや外部APIのPartner Nodesは、Atelierのモデル選択には追加していません。

最初のアプリ対応では、HTTPモックによる軽量VAEの選択・生成要求・結果回収、参照画像とマスクの順序、従来モデルの回帰テストを行いました。ComfyUI・モデル別Pythonテスト24件、JavaScriptテスト37件、Viteビルドが成功しました。BrowserプラグインがないためPlaywright／Edgeを使用し、ローカル検証画面（127.0.0.1:18794、API応答はモック）でバージョン表示→軽量VAE選択→設定保存、バージョン不明時の接続成功を確認しました。1280pxと390px幅で表示を確認し、画面例外・コンソールエラー・Viteエラー表示はありませんでした。この段階では本体は0.37.0でした。その後の本体更新と実GPU試験は次節に記録しています。

参照: [v0.38.0 HTTP API](https://github.com/Comfy-Org/ComfyUI/blob/v0.38.0/server.py)、[Qwenノード](https://github.com/Comfy-Org/ComfyUI/blob/v0.38.0/comfy_extras/nodes_qwen.py)、[VAELoader](https://github.com/Comfy-Org/ComfyUI/blob/v0.38.0/nodes.py)。

## このPCの本体更新手順と実機確認（2026-10-01）

`K:\ComfyUI` の本体を公式タグ `v0.38.0`（`6b747c0`）へ更新しました。起動中だった旧ComfyUIとAtelierは、キューと処理中ジョブが空であることを確認してから停止・再起動しています。現在はComfyUI `http://127.0.0.1:8188`、Atelier `http://127.0.0.1:18791` で起動しています。

同じ更新を手動で行う場合は、ComfyUIとAtelierを停止した状態でPowerShellから次を実行します。`switch` は未保存のコード変更がないことを確認してから実行してください。

```powershell
git -C K:\ComfyUI\ComfyUI status --short
git -C K:\ComfyUI\ComfyUI fetch origin tag v0.38.0
git -C K:\ComfyUI\ComfyUI switch --detach v0.38.0
& K:\ComfyUI\python_embeded\python.exe -s -m pip install -r K:\ComfyUI\ComfyUI\requirements.txt
& K:\ComfyUI\python_embeded\python.exe -s -m pip check
```

その後、通常の `start-qwen.bat` と `start-atelier.bat` で起動し、ブラウザーを再読み込みして接続確認を行います。今回はCodexが上記更新と再起動を実施済みなので、再実行は不要です。新しいバージョンへ進む際は指定タグを改めて確認してください。Portable同梱の `update\update_comfyui_stable.bat` も安定版更新に使えますが、実行時点の最新安定版へ進みます。

更新前のコードはGitブランチ `codex/backup-comfyui-0370-20261001`（`73c9bad4d21e7addbe1d13bc92eee0f1431b017d`）に保存しました。Pythonの更新前後のパッケージ一覧と起動ログはAtelierの `.tmp/comfy038-update/` に保持しています。これはコードとパッケージ構成の記録であり、環境全体のバックアップではありません。本体起動時にComfyUIの内部DBは0007から0008へ移行しています。

フロントエンドは1.53.6、公式テンプレートは0.11.70、comfy-kitchenは0.2.36へ更新しました。PyTorch 2.13.0+cu130、既存のComfyUI-GGUF・ComfyUI-GGUF-Qwen3VL-TEはそのまま使用し、`pip check` は成功しています。

RTX 5090、512×512、CFG 1、seed 42、Euler／simpleで、Atelierの実際のHTTP経路から次を確認しました。外部画像APIは使用していません。

| 試験 | 結果 | Atelier経過時間 |
|---|---|---|
| 公式INT8生成モデル＋公式INT8テキストエンコーダー＋BF16 VAE、8ステップ | 生成・PNG保存・履歴登録成功 | 9.92秒 |
| Q8_0生成モデル＋Heretic Q8_0テキストエンコーダー＋BF16 VAE、8ステップ | 同上 | 32.91秒 |
| 元画像＋参照資料1枚でブラッシュアップ、4ステップ | 生成・画像回収・保存成功 | 25.10秒 |
| 部分修正＋範囲内合成、4ステップ | 保存成功。塗っていない画素が元画像と一致 | 25.06秒 |
| 軽量VAE `taeqi2_1`、4ステップ | 生成・画像回収・保存成功 | 22.25秒 |
| 軽量VAE `taeqi2_1`、8ステップ | 生成・画像回収・保存成功。通常VAEの8ステップ出力と同じ構図を確認 | 19.10秒 |
| 実行中ジョブの対象IDによる取消 | cancelledを確認。リモートキューも空 | 13.96秒 |

終了後のキューは空、Atelierワーカーは正常、ComfyUIが報告するPyTorch予約メモリは64 MiBでした。元のGGUF生成モデル・GGUFテキストエンコーダー・BF16 VAEの設定を復元しています。上記時間は起動・モデル読込み・解放を含む個別試験であり、速度比較の結果ではありません。高解像度、FLUX／Animaの実GPU推論、顔や人物の同一性は今回の試験範囲に含めません。

実際のAtelier画面でも、モデル選択→接続確認→ComfyUI 0.38.0表示を確認しました。軽量VAEが候補に現れ、元のGGUF／BF16設定が選択されることを確認しています。BrowserプラグインがないためPlaywright／Edgeを使用しました。画面例外・関連するコンソールエラーはありません。既存のfavicon未配置による404はアプリの動作に影響しません。

軽量VAEの重みは[ComfyUI公式PRの案内](https://github.com/Comfy-Org/ComfyUI/pull/16552)に従い、madebyollin/taesdからencoder・decoderを各約15MB追加しました。取得時のコミットとSHA256も `.tmp/comfy038-update/` に記録しています。4ステップ出力では像の重なりが見られましたが、通常VAEと同じ8ステップ条件では解消しました。画質を定量評価した試験ではなく、通常設定には従来のBF16 VAEを残しています。

## GGUFテキストエンコーダー

.ggufのテキストエンコーダーはCLIPLoaderGGUFへ切り替える。ComfyUI-GGUF-Qwen3VL-TE追加ノードと、対応するmmprojを同じtext_encodersフォルダーに置く。mmprojは一覧から除外する。Q8_0生成モデル＋Q8_0テキストエンコーダー＋公式BF16 VAEで新規生成を再確認済み。

## 保持切替

Qwen選択かつ保持ONで生成後も保持。OFFまたはGPT Image切替時にはチェックにかかわらず解放。実行中は完了後に適用し、PE／SwinIR前も解放する。保持状態は下書きに保存。サーバー全体には最後の画面の指定が適用される。
