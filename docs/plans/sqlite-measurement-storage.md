# SQLiteへの一回分の計測結果の保存

## 状態

`completed`

実装・自動テストは完了。利用者により実機でのDB保存、二行目の追加と一行目の保持を確認済み。

## 目的と今回の範囲

既存の一括取得で得た位置情報・場所の説明・Wi-Fi情報・Speedtest結果を、Python標準のsqlite3を使って保存する。プロジェクト直下の `measurements.db` に、一回の成功計測を一行として保存し、各取得項目を個別の列に対応させる。

保存対象項目の欠落・None・不正値は計測失敗とし、保存しない。NULLによる補完や、失敗した取得結果の保存は行わない。成功までの試行回数は今回は1固定とし、自動再試行は実装しない。

今回の範囲は検証・保存・成功失敗のPC表示まで。履歴画面、場所候補の保存・選択、集計、CSV出力、DB閲覧用コマンド、クラウドDB、ORM、汎用マイグレーション基盤は追加しない。スマホの受信成功は引き続き位置情報の受理だけを意味し、保存完了通知は追加しない。

本計画書作成時は本ファイルだけを追加する。以下のコード変更は実装担当が行う作業を示す。

## 現在の実装と前提

- 基準：ローカルの `1b29a46`。未使用のWindows測位コードは削除済み。
- `scripts/measurement_probe.py` は位置情報受理・応答完了、Wi-Fi取得、Speedtestの順に処理し、通信関連の後片付け成功後に統合JSONを表示する。
- `location` はlatitude、longitude、sent_at、received_at、location_type、location_name、location_detailの七項目。
- `wifi` はssid、ssid_hex、bssid、signal_quality_percent、rssi_dbm、channel、frequency_khz、captured_atの八項目。
- `src/speedtest.py` は完了したCLI結果から必要な値を抽出するが、個別項目の欠損はNoneとして返す。
- `src/wifi.py` はSSIDをUTF-8で復号できない場合、ssidをNoneとしてssid_hexを保持する。
- 現状の統合テストには省略されたSpeedtest・Wi-Fiのモック値がある。今回、保存成功のfixtureは全項目を含む値に更新する必要がある。
- 永続保存処理・SQLite用の依存関係はまだない。sqlite3は標準ライブラリのため追加インストールしない。

欠損を返す取得関数自体の契約は今回変更しない。一括取得の保存対象を検証する処理でNone等を拒否する。位置情報単独・Speedtest単独コマンドは従来どおりで、DB保存を追加しない。

## 確定方針

| 項目       | 仕様                                                                                   |
| ---------- | -------------------------------------------------------------------------------------- |
| 保存先     | プロジェクト直下のmeasurements.db                                                      |
| パス解決   | src/storage.pyの__file__からプロジェクトルートを求める。作業ディレクトリに依存させない |
| テーブル   | measurements、一回につきINSERTを一回実行                                               |
| データ構成 | 各生値を個別列に保存。集計値・JSON一列だけの保存にはしない                             |
| 必須性     | 下表の取得項目はすべて必須。DB列もNOT NULL                                             |
| 試行回数   | attempt_countは整数1固定                                                               |
| 取得失敗   | 行を保存せず、段階と理由をPCへ表示                                                     |
| 保存失敗   | 未コミットならロールバック。取得済みの完全なJSONをPCへ出し、未保存と非ゼロ終了を明示   |
| 正常終了   | コミット後に保存先・行IDを案内し、計測JSONを一度表示、終了コード0                      |
| スマホ画面 | 変更なし。DB結果は表示しない                                                           |

DB準備でファイルと空テーブルが作られても、成功計測がない限り行は追加しない。「失敗時は保存しない」は失敗した計測の行を保存しない意味とする。

## データベースと列定義

テーブル名は `measurements`。以下の40列とする。idを含めてすべてNOT NULL、idは `INTEGER PRIMARY KEY NOT NULL` とし、INSERT時には指定せずSQLiteに採番させる。AUTOINCREMENTは不要。

表中のパスは既存の統合辞書を起点とする。名前を変えて列へ対応させるだけで、Speedtestの数値・単位を変換しない。

### 位置情報・計測管理

| 列名                 | SQLite型 | 元データ・内容                              |
| -------------------- | -------- | ------------------------------------------- |
| id                   | INTEGER  | 主キー。SQLiteによる採番                    |
| latitude             | REAL     | location.latitude                           |
| longitude            | REAL     | location.longitude                          |
| location_sent_at     | TEXT     | location.sent_at、UTC                       |
| location_received_at | TEXT     | location.received_at、UTC                   |
| location_type        | TEXT     | location.location_type、insideまたはoutside |
| location_name        | TEXT     | location.location_name                      |
| location_detail      | TEXT     | location.location_detail                    |
| attempt_count        | INTEGER  | アプリが付与する1                           |

### Wi-Fi

| 列名                        | SQLite型 | 元データ・単位                      |
| --------------------------- | -------- | ----------------------------------- |
| wifi_ssid                   | TEXT     | wifi.ssid                           |
| wifi_ssid_hex               | TEXT     | wifi.ssid_hex、生バイト列の16進表記 |
| wifi_bssid                  | TEXT     | wifi.bssid                          |
| wifi_signal_quality_percent | INTEGER  | wifi.signal_quality_percent、%      |
| wifi_rssi_dbm               | INTEGER  | wifi.rssi_dbm、dBm                  |
| wifi_channel                | INTEGER  | wifi.channel                        |
| wifi_frequency_khz          | INTEGER  | wifi.frequency_khz、kHz             |
| wifi_captured_at            | TEXT     | wifi.captured_at、UTC               |

### Speedtest

| 列名                    | SQLite型 | 元データ・単位                        |
| ----------------------- | -------- | ------------------------------------- |
| speedtest_timestamp     | TEXT     | speedtest.timestamp、UTC              |
| idle_latency            | REAL     | speedtest.ping.latency、ms            |
| idle_jitter             | REAL     | speedtest.ping.jitter、ms             |
| idle_latency_low        | REAL     | speedtest.ping.low、ms                |
| idle_latency_high       | REAL     | speedtest.ping.high、ms               |
| download_bandwidth      | INTEGER  | speedtest.download.bandwidth、bytes/s |
| download_bytes          | INTEGER  | speedtest.download.bytes、bytes       |
| download_elapsed        | INTEGER  | speedtest.download.elapsed、ms        |
| download_loaded_latency | REAL     | speedtest.download.latency.iqm、ms    |
| download_loaded_jitter  | REAL     | speedtest.download.latency.jitter、ms |
| download_latency_low    | REAL     | speedtest.download.latency.low、ms    |
| download_latency_high   | REAL     | speedtest.download.latency.high、ms   |
| upload_bandwidth        | INTEGER  | speedtest.upload.bandwidth、bytes/s   |
| upload_bytes            | INTEGER  | speedtest.upload.bytes、bytes         |
| upload_elapsed          | INTEGER  | speedtest.upload.elapsed、ms          |
| upload_loaded_latency   | REAL     | speedtest.upload.latency.iqm、ms      |
| upload_loaded_jitter    | REAL     | speedtest.upload.latency.jitter、ms   |
| upload_latency_low      | REAL     | speedtest.upload.latency.low、ms      |
| upload_latency_high     | REAL     | speedtest.upload.latency.high、ms     |
| packet_loss             | REAL     | speedtest.packetLoss、%               |
| server_id               | INTEGER  | speedtest.server.id                   |
| server_name             | TEXT     | speedtest.server.name                 |
| server_location         | TEXT     | speedtest.server.location             |

転送速度をMbpsへ、時間を秒へ、周波数をMHzへ換算しない。loaded latencyには既存のiqmを対応させる。PythonのfloatをSQLiteのREALとして保持し、丸めや表示用文字列への変換をしない。整数値の遅延がREALとして0.0等で読み出されることは、数値・単位の変更とは扱わない。

SQLiteのINTEGERに収まらない値は保存前に拒否し、文字列化で回避しない。浮動小数点を含む計測値は、元からPythonで保持している数値精度を超えて保証するものではない。

ISP、IP、Speedtest結果ID・URL、interface情報、トークン、公開URLは保存しない。保存日時、派生band、成功フラグ、失敗ログ用テーブルも今回追加しない。

## 全項目の検証と欠損判定

`src/storage.py` に、統合辞書を検証して上表の39個の挿入値へ対応させる純粋関数 `prepare_measurement(measurement) -> dict` を設ける。戻り値のキーはid以外の列名とし、DBアクセスや表示は行わない。元の辞書を変更しない。

- location、wifi、speedtestと必要な入れ子が辞書であることを確認する。
- 表の各元パスに値が存在することを確認する。キー欠落とNoneはどちらも失敗とし、例外には `speedtest.download.latency.iqm` 等のパスを含める。
- 0、0.0、緯度・経度0、信号品質0、パケット損失0は欠損ではない。`if not value` だけで欠損を判定しない。
- 数値項目のboolは拒否する。INTEGER項目はintのみ、REAL項目は有限のintまたはfloatのみを受け付ける。数値文字列・NaN・Infinity・不正型は拒否し、勝手にfloat/intへ補正しない。
- 転送量・bandwidth・elapsed・遅延・ジッターは0以上、packet_lossは0〜100、緯度は-90〜90、経度は-180〜180、信号品質は整数0〜100、channel・frequencyは正の整数、server_idは正の整数とする。RSSIは報告された符号付き整数を保持し、正負から独自の換算をしない。
- location_typeはinside/outside、location_nameは前後空白除去後1〜200文字、location_detailは同1〜500文字、NULなしとする。既存の正規化済み文字列を保持し、保存時に別の補正・切り詰めをしない。
- server_name・server_locationは空白だけではない文字列を要求する。正常な原文字列の前後空白を勝手に削除して保存しない。
- wifi.ssidがNoneなら、ssid_hexがあっても今回の全項目必須方針では失敗。非UTF-8のSSIDを代替文字列へ変換して成功させない。
- Wi-Fi取得が許容している長さ0のSSIDは、空文字として実際に取得された値なのでNoneとは区別する。ssidとssid_hexがともに空なら許可する。その他はssidのUTF-8生バイトとssid_hexの一致、最大32バイト、BSSIDの6バイト表記を確認する。
- 四種類の時刻はタイムゾーン付きISO 8601として検証し、UTCの末尾Z形式で列へ渡す。時差付きは同じ瞬間のUTCへ正規化し、小数秒を不要に落とさない。時刻なし・タイムゾーンなしは現在時刻で補完しない。
- attempt_countはここで1を付与する。クライアントや元辞書の任意フィールドから受け取らない。
- 定義済みの列だけを明示的に対応させ、元辞書を丸ごと保存しない。

既存のSpeedtestがpacketLoss等を返さない環境では、その計測は今後失敗になる。オプション項目だからといってNULL保存を許可しない。現状と比べて成功件数が減る可能性は、全項目必須という合意済み方針による動作として記録する。

## DB側の制約と初期化

`src/storage.py` に次の公開関数を設ける。

- `initialize_database(path=DEFAULT_DB_PATH) -> None`：DB準備確認・初回テーブル作成。
- `prepare_measurement(measurement) -> dict`：上記の完全性検証・列への対応付け。
- `save_measurement(record, path=DEFAULT_DB_PATH) -> int`：一件保存し、コミット後の行IDを返す。

DEFAULT_DB_PATHは `Path(__file__).resolve().parents[1] / "measurements.db"`。テストでは必ず一時パスを渡すか、この定数・入口側の参照を適切に差し替える。起動引数でDBを切り替える機能は今回追加しない。

テーブルは全列NOT NULLに加え、少なくともlocation_typeの選択値、latitude・longitudeの範囲、場所名・詳細の長さ、attempt_countの1以上をCHECKで制約する。attempt_countはアプリでは1固定だが、将来の再試行に備えてDB制約は1以上とする。通常のテーブルとPython側の型検証を使い、STRICTテーブルは今回必須にしない。

初期化では以下を行う。

1. sqlite3.connectへtimeout=5.0を指定して開く。既存のDBファイルを削除・置換しない。
2. 新規DBなら明示的なトランザクション内でCREATE TABLEとPRAGMA user_version=1を実行し、コミットする。DDLが暗黙に保護されると仮定しない。
3. 既存DBはuser_version=1、measurementsの列名・宣言型・NOT NULL・主キーが本書と一致することをPRAGMA table_info等で確認する。新規以外の不明なversionや、別構成の既存テーブルは準備失敗とする。勝手にDROP・ALTER・データ移し替えをしない。
4. user_version=0で利用者テーブルがない空のSQLiteファイルは新規として初期化してよい。既存テーブルがあるversion=0のDBを勝手に本機能のDBへ変換しない。
5. BEGIN IMMEDIATEと終了処理により書き込みトランザクションを開始できることを確認し、行を追加せずコミットする。破損、読み取り専用、ロック等を準備失敗として示す。
6. 接続を必ず閉じて戻る。測位・Speedtest中に書き込みロックや接続を保持しない。起動時に成功しても、後の保存成功までは保証されない。

スキーマの汎用移行や並列計測の調整、WALモードへの変更は追加しない。標準のジャーナルを使う。

## 保存トランザクション

- save_measurementは元辞書を受けず、prepare_measurementが返す列辞書を受ける。キー集合・None・型等を再確認し、直接呼ばれても不完全な行を保存しない。共通の検証定義を利用し、列対応を二重管理しない。
- 新しい接続で既存スキーマを確認し、明示的にトランザクションを開始する。保存時はSQLite URIのmode=rw等を使用して既存ファイルのみを開き、計測中にDBが削除されても新しい空DBを作らない。URIはPath.as_uri等で生成し、Windowsのパスや空白を正しく扱う。テーブル削除・変更も失敗とし、自動修復しない。
- 固定の列名を明示した一つのINSERTとプレースホルダーで値を渡す。文字列連結によるSQL、INSERT OR REPLACE、更新・上書きは使用しない。
- INSERTとCOMMITが成功した後だけ保存成功とする。lastrowidを得ただけで成功表示しない。
- コミット前のエラー・KeyboardInterruptではロールバックし、接続を閉じる。元のエラーとrollback/closeのエラーが重なる場合も元の原因を保持する。
- 明示的に接続をcloseする。`with connection` だけで接続まで閉じると仮定しない。
- コミット済みの行を、後の表示失敗・クローズ失敗を理由に「ロールバックした」と報告しない。保存確定後の障害は保存済みか未確定かを区別して表示する。
- 自動再INSERT・自動再計測は行わない。コミット結果が不明な深刻なI/O障害では、未保存を断定せずDB確認を促す。重複防止用の新しい識別子や再送プロトコルは今回追加しない。

## 一括取得への組み込み

処理順は次のとおり。

1. 引数を検証し、DB準備確認。失敗ならHTTPサーバー・トンネル・Speedtestを起動せず終了コード1。
2. 従来どおり位置情報を一件受理し、応答処理を完了する。
3. Wi-Fi情報を取得し、Speedtestを一回実行する。
4. location・wifi・speedtestの統合辞書を作り、prepare_measurementで全項目を検証する。
5. 有効な場合だけ、トップレベルへ `attempt_count: 1` を付けたPC出力用JSONをallow_nan=Falseで文字列化して保持する。location・wifi・speedtest内部の構造は変えない。
6. 既存のfinallyで受信サーバー・トンネル・スレッドを片付ける。取得・検証・JSON化・後片付けが失敗したら保存しない。
7. ここまで成功した場合だけsave_measurementを一回呼ぶ。
8. コミット成功なら保存先の絶対パスと行IDを標準エラー出力へ案内し、PCのJSONを標準出力へ一度表示して0で終了する。

統合辞書は取得処理から受け取った値を保持する。DB用のUTC正規化等は列辞書側で行う。attempt_countは表示とDBでともに1とし、DBの行IDや保存先を計測JSONへ混ぜない。

完全性検証はSpeedtest終了後に行うため、例えばssidがNoneであることを検証する前にSpeedtestが実行される場合はある。ただしその取得結果を成功計測として保存・出力しない。今回は取得関数の契約変更や複数段階の事前検証を追加しない。

### エラーの段階と出力

| 段階                                               | 動作・出力                                                                                  |
| -------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| Database preparation                               | DBパスと原因を表示。計測未実行、行追加なし、終了1                                           |
| Location reception / Wi-Fi acquisition / Speedtest | 失敗段階と既存の原因を表示。保存なし、成功JSONなし、終了1                                   |
| Measurement validation                             | 欠損・不正のパスを表示。保存なし、成功JSONなし、終了1                                       |
| Cleanup                                            | 後片付けの失敗を表示。保存なし、成功JSONなし、終了1                                         |
| Database save                                      | ロールバック確認後は「計測成功・DB未保存」と表示。完全な取得済みJSONを一度出して終了1       |
| コミット後の障害・保存確定不明                     | 保存済み・確定不明を正確に案内し、DB確認を促す。自動再INSERTしない                          |
| Ctrl+C                                             | 中断を表示し、未コミットならロールバック。原則終了130。保存前の中断では行追加・成功JSONなし |

単一のstage変数等で段階を管理し、既存の「Measurement failed」だけではどこで失敗したか分からない状態を改善する。予想される失敗では不要なトレースバックを出さない。トークンや公開URLをエラーへ含めない。

DB保存だけ失敗した場合のJSONは「DB保存済みの成功結果」ではなく、取得済みデータの救済出力である。保存エラーの案内を必ず伴わせる。保存開始前の取得・検証・後片付け失敗とは区別する。保存処理中の中断は通常の救済出力経路にせず、中断として扱う。

## Git管理と変更対象

DBは利用者のローカルデータとしてGit管理から除外する。.gitignoreへルート限定で以下を追加する。

```gitignore
/measurements.db
/measurements.db-journal
/measurements.db-wal
/measurements.db-shm
```

WALは今回有効化しないが、SQLiteの関連ファイルも誤追加しない。既存の.venv等の除外は維持する。実データやテスト用DBをリポジトリへ追加しない。

| ファイル                                 | 区分・変更内容                                                                   |
| ---------------------------------------- | -------------------------------------------------------------------------------- |
| src/storage.py                           | 新規。既定パス、列定義、完全性検証・対応付け、初期化、保存、トランザクション管理 |
| scripts/measurement_probe.py             | DB準備、全項目検証、後片付け後の保存、attempt_count、段階別エラー・救済出力      |
| tests/test_storage.py                    | 新規。実SQLiteの一時DBでスキーマ・保存・制約・ロールバック・検証を確認           |
| tests/test_measurement_probe.py          | 完全なfixture、DBパスの分離、保存順・成功失敗・中断・表示の統合検証              |
| .gitignore                               | DB本体・関連ファイルのルート限定除外                                             |
| docs/plans/sqlite-measurement-storage.md | 状態、実装内容、検証結果・未確認事項を記録                                       |

README、AGENTS.md、他の計画書、pyproject.toml、Webファイル、HTTP受信・Cloudflare・Wi-Fi・Speedtestの取得実装と単独コマンドは変更しない。テスト用fixtureの共通化は必要最小限にし、無関係なテストの再編は行わない。テキストはLF、コミット・プッシュは依頼なしに行わない。

## 実装するテスト

### tests/test_storage.py：実際のSQLiteによる検証

TemporaryDirectory内のDBを使い、テスト終了時に接続をすべて閉じる。実際のプロジェクト直下のmeasurements.dbは作成・読み書きしない。

- 新規初期化でmeasurementsとuser_version=1が作られ、40列の型・NOT NULL・主キー・必要なCHECKが定義どおりであることを確認する。
- 再初期化で既存行を失わず、空DB初期化・不正SQLite・互換性のない列・不明version・別の既存テーブルを区別する。
- 完全な入力を一件保存して別接続から読み、39個の保存値が期待する元の値・UTC時刻・1固定の試行回数と一致することを確認する。全列にNULLがないことを確認する。
- 二回の保存で二行が増え、同じ値でも勝手に上書き・集約・重複排除しない。
- 表の各元パスについて、欠落とNoneを一つずつ発生させ、パス付きエラーで一行も増えないことを検証する。途中の辞書の欠落・不正型も確認する。
- 0・0.0、負のRSSI、Unicode・引用符・SQLに見える文字列が正しく保持されることを確認する。
- bool、数値文字列、NaN、Infinity、範囲外、SQLite整数範囲超過、欠損時刻・タイムゾーンなしを拒否する。
- 時差付き日時のUTC変換と小数秒保持を確認する。異なる取得時刻を同じ時刻へ上書きしない。
- ssid=Noneは失敗、正しい空SSIDと空hexは成功、SSID/hex不一致は失敗とする。
- prepare_measurementが元辞書を変更せず、不要なメタデータを保存対象へ含めないことを確認する。
- SQLを直接使ったNULL挿入やCHECK違反もDB側で拒否する。save_measurementの直接呼び出しでも不完全な列辞書を拒否する。
- 別接続の書き込みロックで保存が失敗し、既存行が保持されることを確認する。短いテスト用timeout等で長時間待たせない。
- INSERT後・COMMIT前の例外を注入し、別接続から行が増えていないことと再保存できることを確認する。単にrollbackメソッドが呼ばれたというモックassertionだけで済ませない。
- コミット失敗・KeyboardInterrupt・接続終了を検証する。モックでのみ再現したI/O障害は、その範囲を記録する。

### tests/test_measurement_probe.py：フローと障害

- 正常fixtureを全項目ありに更新する。現在のpacketLoss=Noneや省略Wi-Fiのまま正常成功にしない。
- すべてのテストでDBパスを一時ディレクトリへ差し替える。default引数が定数を事前束縛する点も考慮し、実DBに触れない。
- DB準備がHTTPサーバー・トンネル起動前に行われ、失敗時にはどちらもSpeedtestも起動しない。
- 応答完了→Wi-Fi→Speedtest→完全性検証→通信の後片付け→保存の順序と、INSERTが一回だけであることを確認する。
- ローカルHTTP、モックした取得・トンネル、実一時SQLiteを組み合わせ、一回の正常実行で一行保存し、ターミナルJSONにattempt_count=1を含むことを確認する。
- 正常時の保存先・行IDの案内と、JSON出力が一回であることを確認する。
- 位置情報・Wi-Fi・Speedtest・完全性検証・JSON化・後片付けの各失敗で行が増えない。
- 保存だけの失敗で未保存案内と完全なJSONの救済出力があり、終了1となる。取得・検証失敗では救済JSONを出さない。
- コミット確定後の表示失敗を未保存と誤報せず、自動再INSERTしない。
- 中断・重複POST・期限・ポート再利用・トンネル後片付けの既存テストを維持する。
- 別作業ディレクトリからの起動でも既定パス解決がプロジェクト直下になることを、実DBへ書かず検証する。

## 実装順序・検証方法

1. 列定義・prepare_measurement・初期化・保存をsrc/storage.pyへ実装する。
2. 一時DBを使う保存・完全性検証・トランザクションのテストを実装する。
3. 一括取得へ組み込み、fixtureと統合テストを更新する。.gitignoreへ除外を追加する。
4. プロジェクトのWindows用.venvで以下を実行する。

    ```powershell
    .\.venv\Scripts\python.exe -m unittest discover -s tests -v
    .\.venv\Scripts\python.exe -m pip check
    git diff --check
    git check-ignore measurements.db measurements.db-journal measurements.db-wal measurements.db-shm
    ```

5. 利用者がWindows・iPhoneで一括取得を行い、保存成功の表示、PCのJSON、実DBの一行追加、全列の値・NULLなし・attempt_count=1を確認する。読み出し確認にはPythonのsqlite3を使えばよく、DB閲覧ツールを新規導入しない。
6. 二回目の正常取得で二行目が増え、以前の行が残ることを確認する。
7. 自動テストの結果と実機確認、未確認のロック・I/O障害等を本書に分けて記録する。テスト失敗は修正して再実行する。実行できなかった確認は成功と扱わない。

自動テストで実Ookla・Cloudflareへ接続せず、実DBや利用者のデータを削除しない。新規依存や外部サービスは不要。

## 完成条件

- プロジェクト直下のmeasurements.dbを準備し、成功した一回分だけを一行として保存できる。
- 全取得項目を個別列に保持し、保存済み行のどの列にもNULLがない。生値・単位・別々のUTC時刻が保持される。
- 一項目でも欠損・不正なら失敗で、計測行を追加しない。試行回数は1。
- 保存失敗は段階が分かり、未コミットの行は残らない。取得済み完全データは保存失敗時にPCへ救済表示する。
- 保存の成功と位置情報受理の成功を混同せず、成功時はコミット後の保存先・行IDを案内する。
- 既存のWeb受信・Wi-Fi・Speedtest・終了処理が保たれ、DBをGit管理へ含めない。
- 自動テスト成功に加え、実機で一回の保存と再実行後の追記を確認する。個別の異常系の実機未確認は明記する。

## 参考資料

- [Python：sqlite3](https://docs.python.org/3/library/sqlite3.html)：プレースホルダー、トランザクション、接続の終了。
- [SQLite：Transactions](https://www.sqlite.org/lang_transaction.html)：BEGIN・COMMIT・ROLLBACKとロック。
- [SQLite：Datatypes](https://www.sqlite.org/datatype3.html)：INTEGER・REAL・TEXTと型アフィニティ。

## 実装・検証状況

- 2026-11-10：src/storage.pyに40列のスキーマ、39挿入値の検証・対応付け、初期化と一件保存を実装。共通の列定義でprepare_measurementとsave_measurementの検証を行う。生値・単位を保持し、時刻はDB列側だけUTCへ正規化する。
- measurement_probeへ起動前のDB準備、全項目検証、attempt_count=1、通信の後片付け後の保存、段階別エラー、保存失敗時の完全JSON救済出力を追加。コミット結果が不明な場合は未保存を断定せず、コミット後の障害は保存済みとして案内する。依存関係・取得実装・Web画面・単独コマンドは変更していない。
- .gitignoreにDBと関連ファイルのルート限定除外を追加。テストは一時ディレクトリ内のSQLiteのみを使用し、プロジェクト直下の実DBにはアクセスしていない。

### 自動検証

- Windows用 `.venv` で `python -m unittest discover -s tests -v`：107件成功、失敗0件、スキップ0件（2026-11-13再確認）。
- `python -m pip check`：成功（No broken requirements found）。
- `git diff --check`：成功。
- `git check-ignore measurements.db measurements.db-journal measurements.db-wal measurements.db-shm`：四ファイルすべて除外されることを確認。
- 実SQLiteで、全列の保存値・NULLなし・追記・再初期化、欠損・不正型・範囲、UTCと小数秒、空SSID、DB制約、不正スキーマ・破損・削除、別接続のロック、例外・中断後の行数と接続解放を検証。
- ローカルHTTP・モック取得・実一時DBで、準備失敗時の未起動、応答後の取得、検証・通信終了後の一回保存、救済出力、保存中断、コミット後の表示失敗を検証。既存の重複POST・期限・ポート再利用等のテストも成功。
- 初期化のDDL中断、INSERT後の例外、COMMIT・rollback・close障害は実SQLite接続のサブクラスによる障害注入で検証。実際のディスクI/O障害の再現ではない。

### 実機確認・未確認事項

- 2026-11-13：利用者から実機でのDB保存、二行目の追加と一行目の保持を確認した旨の報告を受領。利用者による `completed` への変更に合わせ、実機未確認としていた記述を更新した。
- 全列の値・NULLなし・attempt_count=1は上記自動テストで確認済み。実機での各列の個別照合については報告を受けていない。
- エージェントの自動検証では実Ookla・Cloudflareへ接続せず、Windows設定の変更も行っていない。実環境の読み取り専用権限・ストレージ障害・コミット確定不明となるI/O障害は未確認。
- packetLoss等の欠損やssid=Noneを成功として扱わないため、従来より成功件数が減る可能性がある。計画の全項目必須方針に沿う動作である。
