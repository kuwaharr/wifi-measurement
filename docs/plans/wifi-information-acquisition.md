# Windows Native Wi-Fi APIによるWi-Fi情報の取得

## 状態

`completed`

実装・自動テストに加え、利用者によるWindows実機での確認完了の報告を受け、状態を `completed` とする。自動テストと利用者による実機確認の結果は分けて記録する。

## 目的と今回の範囲

計測するWindows PCが接続しているWi-FiのSSID、BSSID、信号品質、RSSI、チャネル、周波数を取得し、既存の一括取得結果へ追加する。

Python標準ライブラリのctypesからWindows Native Wi-Fi APIを呼ぶ。netshの出力解析、PowerShellによる代替取得、外部のWi-Fiパッケージは使用しない。既存のOokla Speedtest CLI、スマホ位置情報受信、Cloudflare Tunnelの方式は維持する。

今回は接続状態のスナップショットを取得する一機能に限定する。SQLite保存、場所名・階数の入力、再試行・試行回数の記録、連続監視、Wi-Fi接続の変更、Windows測位コードの削除は対象外。README.mdと既存の計画書は編集しない。

## 前提と提案する決定事項

基準となる実装は `0410121` 時点の一括取得機能。`scripts/measurement_probe.py` は位置情報を一件受理してスマホへの応答処理を終えてから、Speedtestを一回実行し、後片付け成功後に統合JSONを表示する。

以下を本機能の提案仕様とし、`approved` への変更で承認対象とする。

| 項目           | 仕様                                                                                        |
| -------------- | ------------------------------------------------------------------------------------------- |
| 取得対象       | スマホではなく、Speedtestを実行するPCの接続中Wi-Fi                                          |
| アダプター選択 | 接続中が一つなら自動選択。ゼロまたは二つ以上ならエラー。先頭・最強信号を勝手に選ばない      |
| 取得時点       | スマホの位置情報受理・応答処理完了後、Speedtest直前                                         |
| 必要な値       | SSIDの生バイト列、BSSID、信号品質%、RSSI dBm、チャネル、中心周波数kHz                       |
| 値の欠損       | 必要な値を取得できない場合は失敗終了。0・推定値・別APの値で埋めない                         |
| 自動スキャン   | WlanScanは呼ばず、Windowsが保持しているBSS情報を参照する                                    |
| 接続変化       | Wi-Fi取得処理の前後で接続先を照合し、途中の切断・接続変更を検出したら失敗。自動再取得しない |
| 既存コマンド   | 位置情報単独・Speedtest単独のコマンドを維持する                                             |
| 追加依存       | なし。ctypes、datetimeなど標準ライブラリのみ                                                |

BSS情報はWindows・ドライバーが保持する観測値で、関数呼び出し時点に新たに測った値とは限らない。アクティブスキャンや鮮度の保証は今回含めない。この制約を実機確認記録にも残す。

Wi-Fiに接続しているだけでは、Speedtestがその回線を通る保証はない。今回、ルーティング判定、VPN検出、Speedtestのインターフェース固定は実装しない。実機で通信性能を比較する際は、利用者が有線・VPN等の別経路を無効にし、対象Wi-Fiを使用する環境を用意する。出力を通信経路確認済みの証明として扱わない。

## 利用者が確認する動作

既存の一括取得コマンドを引き続き使う。引数やスマホのショートカットの変更は不要。

```powershell
.\.venv\Scripts\python.exe scripts\measurement_probe.py
```

1. 従来どおり受信サーバーとトンネルが起動し、QRを表示する。
2. スマホが位置情報を送信し、PCが成功応答を返す。
3. PCはWi-Fi情報取得中であることを表示する。
4. Wi-Fi情報取得に成功した場合だけ、公式Ookla CLIを一回実行する。
5. 後片付けが正常に終了した後、`location`、`wifi`、`speedtest` の三項目を持つ統合JSONを一度表示し、自動終了する。
6. Wi-Fi未接続・権限不足・取得失敗の場合、原因を表示し、Speedtestと統合成功JSONの出力は行わない。受信サーバーとトンネルは片付ける。

スマホの成功表示は位置情報の受理のみを意味し、その後のWi-Fi取得やSpeedtestの成功を保証しない。結果はPC側で確認する。

## 使用技術とモジュール構成

`src/wifi.py` を新設し、公開関数を `get_wifi_info() -> dict` とする。成功時にスナップショットを返し、取得できない場合は説明付きのRuntimeErrorを送出する。関数内で表示や永続保存を行わない。Windows固有のAPIと構造体定義をこのファイルに閉じ込める。

- `ctypes.WinDLL("wlanapi.dll")` によりAPIを読み込む。Windows以外では呼び出し時に明示的なRuntimeErrorとする。
- モジュールをimportしただけではDLLをロードしない。既存のテストや非Windowsでの静的確認を妨げない。
- APIごとにargtypes・restypeを明示する。HANDLE・ポインターを32ビット整数へ切り詰めない。
- WindowsのDWORD・ULONGは32ビット符号なし、LONG・BOOL・列挙型は32ビット、BOOLEANは8ビット、WCHARは16ビットであることに合わせる。非WindowsホストのC型サイズをWindows ABIと同一と仮定しない。
- 必要なGUID、DOT11_SSID、WLAN_INTERFACE_INFO、WLAN_CONNECTION_ATTRIBUTES、WLAN_ASSOCIATION_ATTRIBUTES、WLAN_SECURITY_ATTRIBUTES、WLAN_BSS_ENTRY、WLAN_RATE_SETとリストのヘッダーを公式定義に合わせる。構造体の途中を省略したり、理由なく `_pack_=1` にしたりしない。
- DLLをモックで差し替えられる小さな内部ロード関数を設ける。汎用的なAPIラッパー基盤や新たなクラス階層は作らない。

## API呼び出しと取得手順

1. `WlanOpenHandle` をクライアントバージョン2、予約引数NULLで呼び、ハンドルを取得する。
2. `WlanEnumInterfaces` でインターフェースを列挙し、状態が `wlan_interface_state_connected` のものを抽出する。接続中が一つであることを確認する。無効なリストや不正なバッファは失敗とする。
3. 選択したGUIDに対して `WlanQueryInterface(..., wlan_intf_opcode_current_connection, ...)` を呼ぶ。接続中であること、infrastructure接続であること、SSID長、BSSID、信号品質を検証する。ad hoc等は今回未対応として明示的に失敗させる。
4. 同じGUIDに対して `wlan_intf_opcode_channel_number` を問い合わせ、ULONGのチャネル番号を取得する。
5. 同じGUIDに対して `WlanGetNetworkBssList` を呼ぶ。pDot11SsidはNULLとして一覧を取得し、同じBSSID・SSID生バイト列・BSS種別のエントリーだけを内部で選別する。一覧そのものは出力しない。
6. 同一接続の一致エントリーから `lRssi` と `ulChCenterFrequency` を取得する。同じ条件のエントリーが複数ある場合は最大の `ullHostTimestamp` のものを選ぶ。同時刻で値が異なるなど一意に決められなければ失敗とする。一致がなければ失敗させ、同じSSIDの別BSSIDから代用しない。
7. 選択したGUIDの現在接続を再取得する。GUID・SSID生バイト列・BSSIDが同一で接続中であることを確認する。チャネルも再問い合わせし、途中で変わっていないことを確認する。これは取得処理内の変化検出であり、Speedtest中の接続維持を保証するものではない。
8. 値をPythonの文字列・整数へコピーし、UTCの取得処理完了時刻を付与する。
9. APIの確保メモリとハンドルを解放してから辞書を返す。

リストは可変長であるため、ヘッダーの件数と正しい要素開始オフセットに基づき読み取る。BSSリストのdwTotalSizeとdwNumberOfItemsから、必要な固定長エントリー領域がバッファ内に収まることを確認する。IE領域を解析する必要はない。WlanQueryInterfaceは戻りデータサイズが期待する構造体・数値のサイズ以上であることを確認してから参照する。

列挙結果のインターフェース名・GUID、SSID以外の周辺ネットワーク情報、IP、プロファイル、セキュリティ詳細、リンク速度は結果へ追加しない。GUIDはAPI問い合わせと接続照合の内部利用に限定する。

## wifiの出力仕様

| キー                   | 型・単位         | 内容                                                                           |
| ---------------------- | ---------------- | ------------------------------------------------------------------------------ |
| ssid                   | stringまたはnull | SSIDを厳密にUTF-8として復号できる場合の文字列。復号できない場合だけnull        |
| ssid_hex               | string           | SSIDの生バイト列を小文字の16進表記で保持。SSIDの欠落・破壊を避けるため常に出力 |
| bssid                  | string           | 6バイトを `aa:bb:cc:dd:ee:ff` 形式へ整形                                       |
| signal_quality_percent | integer、%       | 現在接続のwlanSignalQuality。0〜100                                            |
| rssi_dbm               | integer、dBm     | 一致するBSSエントリーのlRssi。%からの推定値ではない                            |
| channel                | integer          | 問い合わせた現在のチャネル番号。正の整数                                       |
| frequency_khz          | integer、kHz     | 一致するBSSエントリーの中心周波数。正の整数                                    |
| captured_at            | string、UTC      | 取得処理完了時刻。タイムゾーン付きISO 8601、末尾Z                              |

RSSIは符号付きLONGの報告値をそのまま保持する。独自の電波強度ランク、平均化、%からdBmへの換算、dBmから%への換算は行わない。周波数はkHzの原値を保持し、MHzへ変換しない。READMEの「frequency or band」は周波数で満たし、今回は派生値のbandを追加しない。

SSIDはNUL終端文字列ではない。uSSIDLength（0〜32）の長さで生バイトを読む。途中のNUL・空白を削除しない。長さ0は空のSSIDとして保持し、勝手にネットワーク名を補完しない。UTF-8以外のSSIDをOSの表示言語に依存して推測復号しない。ssidがnullでもssid_hexで原データを保持できていれば、SSID取得失敗とは扱わない。

API戻り値・バッファ・信号品質範囲・チャネル・周波数が不正ならRuntimeErrorとする。APIが返した値をそのまま保存することと、壊れた応答を有効な測定値として扱うことは区別する。

## メモリ・エラー・権限

- 各APIのDWORD戻り値を検査し、ERROR_SUCCESS以外を黙って通さない。API名とエラーコードが分かるRuntimeErrorにする。戻り値を使い、GetLastErrorだけから成否を判断しない。
- ERROR_ACCESS_DENIEDでは、Windowsの「設定 > プライバシーとセキュリティ > 位置情報」の許可を確認するよう案内する。Windowsの位置情報サービスを測位に使わなくても、SSID・BSSIDへのアクセスに許可が必要になる場合がある。
- 設定、レジストリ、管理者権限、接続先を自動変更しない。別方式に黙ってフォールバックしない。
- Wi-Fiアダプターなし、未接続、接続中が複数、途中の切断、WLANサービス停止、BSS一致なし、未対応API・ドライバーは区別できる説明を返す。
- APIが確保した各バッファは、参照値をPythonへコピー後、finallyで `WlanFreeMemory` を一回呼ぶ。中間段階の失敗・例外・中断でも解放する。
- ハンドルは最後に `WlanCloseHandle` で閉じる。ハンドル取得失敗時は閉じない。Pythonやctypesが確保したテスト用メモリをWlanFreeMemoryへ渡さない。
- クローズ失敗を成功として扱わない。取得の失敗とクローズ失敗が同時に起きた場合、元の失敗情報も失わないよう例外の連鎖等で保持する。
- APIが返したポインターを解放後に参照しない。返り値へctypes構造体やポインターを残さない。

この機能は同期API呼び出しとして実装する。別プロセスによる強制タイムアウトやDLL呼び出しの強制中断は追加しない。Ctrl+Cは既存の入口で130として扱い、Pythonへ制御が戻った時点でfinallyによりリソースを解放する。ドライバー内の長時間停止を即時中断できる保証はしない。

## 既存の一括取得への統合

`scripts/measurement_probe.py` に `from src.wifi import get_wifi_info` を追加する。スマホへの応答完了待ちを抜けた後、Speedtest開始前に一回呼び出す。

実行順は、位置情報受理・応答処理完了 → Wi-Fi情報取得 → Speedtest → 後片付け → 統合JSON表示とする。HTTPハンドラーやトンネルのスレッドでWi-Fi APIを実行しない。

統合結果のトップレベルを `location`、`wifi`、`speedtest` とし、既存二項目の構造・値・単位は変更しない。失敗時の終了コード1、中断130、正常0を維持する。Wi-Fi取得失敗時にSpeedtestを開始せず、位置情報だけ・Wi-Fi情報だけの成功JSONも出力しない。既存の後片付けと、後片付け成功後にだけ結果を表示する動作を維持する。

Wi-Fi情報はSpeedtest直前のスナップショットとする。Speedtest終了後の追加取得、計測中のローミング監視、複数リンク（Wi-Fi 7 MLO等）の一覧化は行わない。従来APIで一つの接続として整合する値が取得できない環境は取得失敗とし、別リンクの値を混ぜて成功させない。

## 実装担当が変更するファイル

| ファイル                                   | 区分 | 内容                                                                                    |
| ------------------------------------------ | ---- | --------------------------------------------------------------------------------------- |
| src/wifi.py                                | 新規 | ctypes定義、API呼び出し、接続選択・照合、出力整形、解放処理、get_wifi_info              |
| scripts/measurement_probe.py               | 修正 | Wi-Fi取得の呼び出し、進捗表示、統合JSONのwifi追加                                       |
| tests/test_wifi.py                         | 新規 | ABI、API引数、バッファ解析、出力、異常系、解放処理                                      |
| tests/test_measurement_probe.py            | 修正 | Wi-Fiのモック、呼び出し順・失敗・統合結果の検証。既存テストの期待値を必要な範囲だけ更新 |
| docs/plans/wifi-information-acquisition.md | 更新 | 承認状態、実装内容、自動テスト・実機確認・未確認事項を記録                              |

README.md、AGENTS.md、既存の計画書、pyproject.toml、.gitignore、位置情報・Cloudflare・Speedtest取得の各実装とそれらの単独コマンドは変更しない。新たな単独probe、設定ファイル、保存ファイルも追加しない。変更するテキストはLFとする。依頼なしにコミット・プッシュしない。

この計画書の作成段階では、本ファイルだけを追加する。上表のコード・テストへの変更は、承認後に実装担当が行う。

## 実装する自動テスト

### tests/test_wifi.py：API境界とデータ取得

- WindowsのABIに対する型サイズ、GUID・主要構造体のサイズとフィールドオフセットを公式定義と比較する。少なくとも利用者のWindows x64環境で確認する。非Windowsでの構造体テストをWindows実機でのABI確認の代用としない。
- モックDLLに対し、ハンドル、GUID、opcode、予約引数、出力ポインター、呼び出し順が適切であることを検査する。
- 接続中一つで正しい辞書を返し、数値・単位が変わっていないことを確認する。返り値にctypesオブジェクト、GUID、IPや他APの情報がないことも確認する。
- 同じSSIDの別BSSID、別SSIDのエントリー、複数BSSエントリーから正しい接続を選ぶ。最新選択と同時刻の曖昧さによる失敗も確認する。
- SSIDの日本語UTF-8、32バイト、長さ0、途中のNUL、前後空白、不正UTF-8、32超の不正長を検証する。生バイトの完全保持を確認する。
- 信号品質0・100は有効、範囲外は失敗。負のRSSIを正の強度や%へ変換しない。チャネルと周波数は正の整数として保持する。
- アダプターなし、未接続、複数接続、非infrastructure、BSSなし・一致なし、途中のBSSID・SSID・チャネル変更・切断を検証する。
- 問い合わせサイズ不足、NULLポインター、不正なBSS件数・総サイズを検証し、範囲外を読まないことを確認する。危険な実メモリ読み取りではなく安全なテスト用バッファを使う。
- 各APIのアクセス拒否・サービス停止・その他失敗で、呼び出し名とコードが分かるエラーになることを確認する。
- 各段階の失敗・例外・KeyboardInterruptで、確保済みバッファだけが一回解放され、ハンドルが閉じることを検査する。OpenHandle失敗、CloseHandle失敗、元の取得エラーとの重複も確認する。
- モジュールimport時にDLLをロードせず、非Windowsでのget_wifi_infoは明示的に失敗することを確認する。
- WlanScan、netsh、PowerShell、外部パッケージを呼ばないことを確認する。

### tests/test_measurement_probe.py：既存フローへの結合

- 既存の正常系モックへWi-Fi情報を追加し、三項目の統合結果が取得関数の返り値と一致することを確認する。
- 位置情報の応答処理完了後にWi-Fi取得、続いてSpeedtestが各一回実行されることを呼び出し順で検証する。
- 位置情報待ち・不正入力・受信タイムアウト・起動失敗ではWi-Fi取得もSpeedtestも行わない。
- Wi-Fi取得失敗・複数接続・権限不足ではSpeedtestと統合JSON出力を行わず、終了コード1とし、サーバーとトンネルを解放する。
- Wi-Fi取得中のKeyboardInterruptは130となり、統合JSONを出さず後片付けを行う。
- Speedtest失敗と後片付け失敗では、Wi-Fi情報が取得済みでも成功JSONを出さない。
- ローカルHTTPでの位置情報送信とモックWi-Fi・Speedtestを組み合わせ、既存の重複拒否・応答後の開始・ポート再利用の検証を維持する。

テストで実際のWi-Fi設定・権限・接続を変更しない。自動テストではAPIをモックし、実際のOokla・Cloudflareへの接続は行わない。モックで構造体の読み取りが成功しただけで、Windows APIの実機動作を確認済みとしない。

## 実装順序と検証方法

1. 本書を確認し、承認後に `approved` にする。
2. src/wifi.pyにWindows固有処理を実装し、API境界・バッファ・解放処理のテストを追加する。
3. 一括取得コマンドへ組み込み、統合テストを更新する。
4. プロジェクトの.venvで既存分を含むテストと依存整合性確認を行う。

    ```powershell
    .\.venv\Scripts\python.exe -m unittest discover -s tests -v
    .\.venv\Scripts\python.exe -m pip check
    ```

5. 利用者のWindows実機で、同じPC・対象Wi-Fiに接続して一括取得を実行する。必要なWindowsの位置情報許可は利用者が設定する。
6. 下表に従い実機の結果を記録する。成功した環境のWindowsバージョン・Pythonのビット数・アダプターの種類は検証記録へ必要な範囲で記載し、計測結果のフィールドへは追加しない。
7. 必須条件を満たしたら `completed` にする。未実施項目は未確認と記録する。

## 完成条件と利用者による実機確認

| 確認                       | 確認する動作・成果                                                                                                                         |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| 必須：正常取得             | QRで位置情報を送信した後、Wi-Fi情報、Speedtestの順に取得され、三項目の統合JSONが一度表示され自動終了する                                   |
| 必須：値の対応             | 接続中SSID・BSSID・チャネルがWindowsの接続情報やAP管理画面等と整合する。確認用netshを使っても、実装の取得方式には組み込まない              |
| 必須：値と単位             | 信号品質%とRSSI dBmが別項目で、周波数kHzとUTC取得時刻が表示される。瞬間値・BSSキャッシュの差により、別時点の比較値との完全一致は要求しない |
| 必須：正常終了後           | 再起動して再取得でき、ポート・トンネルが残らない                                                                                           |
| 条件付き：権限拒否         | 該当環境がある場合、説明付きの失敗となり、成功JSONやSpeedtestを実行しない                                                                  |
| 条件付き：未接続・複数接続 | 安全に用意できる環境で、勝手なアダプター選択や架空値による成功がないことを確認する                                                         |
| 条件付き：帯域差           | 利用可能な2.4GHz・5GHz・6GHzで取得し、API値を維持する。利用できない帯域は未確認とする                                                      |
| 条件付き：中断             | Ctrl+C後に既存のサーバー・トンネルが片付き、再起動できる                                                                                   |

必須項目と自動テストの成功をcompletedの条件とする。条件付き項目は環境がなければ未確認と記録し、モック検証結果と分ける。MLOや計測中ローミング、ルーティングを検証した機能として報告しない。

## 参考資料

- [Microsoft：WlanOpenHandle](https://learn.microsoft.com/en-us/windows/win32/api/wlanapi/nf-wlanapi-wlanopenhandle)
- [Microsoft：WlanEnumInterfaces](https://learn.microsoft.com/en-us/windows/win32/api/wlanapi/nf-wlanapi-wlanenuminterfaces)
- [Microsoft：WlanQueryInterface](https://learn.microsoft.com/en-us/windows/win32/api/wlanapi/nf-wlanapi-wlanqueryinterface)
- [Microsoft：WLAN_ASSOCIATION_ATTRIBUTES](https://learn.microsoft.com/en-us/windows/win32/api/wlanapi/ns-wlanapi-wlan_association_attributes)
- [Microsoft：WlanGetNetworkBssList](https://learn.microsoft.com/en-us/windows/win32/api/wlanapi/nf-wlanapi-wlangetnetworkbsslist)
- [Microsoft：WLAN_BSS_ENTRY](https://learn.microsoft.com/en-us/windows/win32/api/wlanapi/ns-wlanapi-wlan_bss_entry)
- [Microsoft：WlanFreeMemory](https://learn.microsoft.com/en-us/windows/win32/api/wlanapi/nf-wlanapi-wlanfreememory)
- [Microsoft：WlanCloseHandle](https://learn.microsoft.com/en-us/windows/win32/api/wlanapi/nf-wlanapi-wlanclosehandle)
- [Microsoft：Wi-Fi APIと位置情報アクセス許可](https://learn.microsoft.com/en-us/windows/win32/nativewifi/wi-fi-access-location-changes)

## 実装・検証状況

- 状態：completed。PC側の実装・自動テストと利用者による実機確認が完了。
- src/wifi.py：固定幅ctypes型と構造体、DLLの遅延ロード、接続中アダプターの選択、現在接続・チャネル・BSSの照合、生値のコピー、APIバッファとハンドルの解放を実装。
- measurement_probe.py：位置情報応答完了後にWi-Fiを一回取得し、成功時だけSpeedtestを開始。統合JSONにwifiを追加。
- 自動テスト：既存分を含む71件成功。Windows・64ビットPython上で構造体サイズと主要オフセット、モックAPIの出力・失敗・解放処理、既存一括取得への統合を確認。
- APIモックではSSIDの日本語・32バイト・空・NUL・空白・非UTF-8、BSS最新選択・曖昧さ・別APの拒否、信号品質境界、未接続・複数接続・接続変化、不正バッファ、APIエラー、割り込み時の解放、クローズエラーの例外連鎖を確認。
- 統合テストでは応答完了→Wi-Fi→Speedtestの順序、Wi-Fi失敗時のSpeedtest未実行、中断時の130、三項目の結果と後片付けを検証。実際のWi-Fi API・Ookla・Cloudflareへの接続や設定変更は行っていない。
- pip check：依存関係の不整合なし。git diff --check：問題なし。
- 利用者よりWindows実機での確認完了の報告を受領。条件付きの異常系・帯域別検証の個別報告はないため、自動テスト結果と区別して未確認として扱う。BSSはキャッシュ観測値であり鮮度を保証しない。Speedtestの通信経路も検証していない。
- README、既存計画書、依存関係、単独コマンドは変更していない。
