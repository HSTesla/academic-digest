# OpenAlex 學術簡報

以 Python 定時查詢 OpenAlex，挑選政治學與國際關係、經濟學、法學、社會學領域的期刊論文，再用 Crossref 按 DOI 補查摘要。將標題、作者、期刊、日期、DOI 和選用的摘要寫入 Obsidian Markdown。排程由 macOS `launchd` 管理。

此工具只收集及整理 OpenAlex 與 Crossref 的書目資料和摘要，不使用 LLM 分類或改寫內容，也不包含推送、SQLite 或期刊白名單。

## 工作流程

每天執行時，`digest.py` 會：

1. 查詢執行當日及之前六天內發表的論文。
2. 篩選 OpenAlex 標記為期刊文章、有 DOI，語言為英文或中文，且主要主題子領域符合設定的記錄。摘要可以缺失，之後會向 Crossref 查詢。
3. 讀取 `seen.json`，略過已處理的 DOI。
4. 按期刊兩年平均被引次數排序；若相同，再比較論文被引次數、出版日期及 DOI。整體最多選取 `MAX_PAPERS` 篇。
5. 對選出的論文逐篇以 DOI 查詢 Crossref。比較 OpenAlex 與 Crossref 的摘要文字，保留詞數較多的一份；若兩者詞數相同，保留 OpenAlex。Crossref 摘要可能包含 JATS/HTML 標記，寫檔前會移除標記並正規化空白。
6. 沒有任何來源摘要的論文不寫入，也不標記為已處理。其餘論文按主題子領域分組，新增至當日 Obsidian Markdown 文件；每篇會標示實際採用的摘要來源。
7. 將成功寫入的 DOI 加入 `seen.json`。

期刊排序指標來自 OpenAlex `summary_stats.2yr_mean_citedness`，不是期刊白名單或質量保證。若來源沒有此指標，排序分數以 0 計。

Crossref 是摘要補充來源，摘要是否存在取決於出版社提交的中繼資料。Crossref 查詢不需要 API key；程式會在各 DOI 查詢間隔至少 1.1 秒。若單筆 Crossref 查詢失敗，會記錄警告並繼續使用 OpenAlex 摘要；若兩邊皆沒有摘要，該論文略過。

## 檔案與設定

```text
~/Apps-Dev/academic-digest/
├── README.md
├── digest.py
├── requirements.txt
├── .gitignore
└── (local runtime files: `seen.json`, `.venv/`, `logs/`)

~/Library/LaunchAgents/com.hst.es.academic-digest.plist
```

`digest.py` 開頭的設定區：

| 設定 | 目前值 | 說明 |
|---|---:|---|
| `OPENALEX_API_KEY` | 環境變數 | OpenAlex API key。請勿把有效金鑰寫入原始碼或提交到版本控制。 |
| `OPENALEX_EMAIL` | 環境變數 | 選填的聯絡電子郵件，隨 API 請求傳送。 |
| `MAX_PAPERS` | `20` | 每次執行選取的篇數上限，四個領域合計，而非每個領域各 20 篇。 |
| `WINDOW_DAYS` | `7` | 查詢視窗天數，包含執行當日。 |
| `OUTPUT_DIR` | `~/Library/Mobile Documents/com~apple~CloudDocs/Obsidian/21_簡報` | iCloud Drive 中的 Obsidian Markdown 輸出目錄。 |
| `SUBFIELDS` | 四個 OpenAlex 子領域 | 子領域 ID 對應 Political Science and International Relations、Economics and Econometrics、Law、Sociology and Political Science。 |

OpenAlex API key 可在 [OpenAlex 設定頁](https://openalex.org/settings/api)建立。若金鑰曾被公開或提交到版本控制，請在 OpenAlex 撤銷並更換。

`seen.json` 是 DOI 字串陣列。刪除其中單一 DOI 會允許該論文在查詢視窗內再次被選取；清空整個檔案會允許所有仍在視窗內的論文重新入選。重設前請先備份。

## 安裝與配置

需要可執行 Python 3 的 macOS 環境及網路連線。

```bash
cd ~/Apps-Dev/academic-digest
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

設定環境變數 `OPENALEX_API_KEY` 後再執行：

```bash
read -s OPENALEX_API_KEY
export OPENALEX_API_KEY
```

如需向 OpenAlex 提供聯絡方式，也可設定 `OPENALEX_EMAIL` 環境變數。

使用 `launchd` 時，請在本機的 `com.hst.es.academic-digest.plist` 之 `EnvironmentVariables` 中設定 `OPENALEX_API_KEY`；該 plist 僅供本機使用，不要提交到版本控制。其餘設定可按需調整聯絡郵件、篇數、查詢天數和 Obsidian 輸出路徑。

請勿在 `requirements.txt` 以外安裝不需要的套件；目前唯一的 Python 依賴是 `requests`。

## 手動執行與檢查

```bash
~/Apps-Dev/academic-digest/.venv/bin/python ~/Apps-Dev/academic-digest/digest.py
```

成功取得新論文時，日誌會顯示寫入篇數和輸出路徑。檢查當日文件及計數：

```bash
ls -l "$HOME/Library/Mobile Documents/com~apple~CloudDocs/Obsidian/21_簡報"
grep '^count:' "$HOME/Library/Mobile Documents/com~apple~CloudDocs/Obsidian/21_簡報/$(date +%F) 學術簡報.md"
```

若沒有新的論文，腳本只會記錄訊息，不會建立空白文件。已成功處理的 DOI 會被去重；因此，對同一天再次執行時，不會重新抓取或重建已完成的文件。

## 每日 launchd 排程

LaunchAgent 設定在 `~/Library/LaunchAgents/com.hst.es.academic-digest.plist`，預定每天本機時間 **12:00** 執行，並在載入時立即執行一次。plist 使用目前使用者的絕對路徑；若搬動專案或更換 macOS 帳戶，必須同步更新 `ProgramArguments` 和日誌路徑。

手動載入或套用修改後的設定：

```bash
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/com.hst.es.academic-digest.plist"
```

查看服務狀態：

```bash
launchctl print "gui/$(id -u)/com.hst.es.academic-digest"
```

停止並卸載：

```bash
launchctl bootout "gui/$(id -u)/com.hst.es.academic-digest"
```

標準輸出與錯誤分別記錄在 `~/Apps-Dev/academic-digest/logs/launchd.log` 和 `~/Apps-Dev/academic-digest/logs/launchd-error.log`。`launchd` 需要能存取專案、虛擬環境、網路和 iCloud Drive 中的 Obsidian 目錄；首次使用前請確認該目錄已在本機下載，並授予相應的檔案存取權限。

電腦睡眠時，排程可能延遲至喚醒後執行；關機期間錯過的執行不保證會補跑。七日查詢視窗可以涵蓋近期發表的記錄，但不是無漏資料的保證：若某篇論文因每日篇數上限而未入選，且在之後執行前已超出查詢視窗，它就不會被處理。

## 輸出格式

每個有新論文的執行日建立或更新：

```text
YYYY-MM-DD 學術簡報.md
```

文件包含 Obsidian frontmatter（日期、標籤及篇數）、每日標題、按領域分組的論文標題，以及期刊、作者、出版日期、DOI 連結和摘要。每篇會註明摘要來自 OpenAlex 或 Crossref；作者超過五人時，只顯示前五位及 `et al.`。

語言篩選是 OpenAlex 的 `en`/`zh` 欄位，不是繁簡體判定；因此中文結果可能使用簡體字。領域分類依 OpenAlex 的主要主題子領域，也可能與讀者的學科判斷不同。

## 常見問題排查

| 現象 | 檢查方式 |
|---|---|
| `401` 或 `403` | 確認 `OPENALEX_API_KEY` 已設定且有效；若金鑰已撤銷，換成新金鑰。 |
| Crossref 查詢失敗 | 查看日誌中的 DOI 與錯誤訊息。單筆失敗不會中止整份簡報；若 OpenAlex 有摘要，仍會使用 OpenAlex 版本。 |
| 沒有產生當日文件 | 查看日誌，並確認七日範圍內有符合條件且未記錄在 `seen.json` 的論文。沒有新論文時不會建立空文件。 |
| 手動執行成功、排程失敗 | 查看 `launchd-error.log`，確認 plist 中 Python 和腳本的絕對路徑、虛擬環境，以及目前登入使用者的 LaunchAgent 是否已載入。 |
| 無法寫入 Obsidian 目錄 | 確認 `OUTPUT_DIR` 正確，並檢查 macOS 對執行 Python 程序的檔案存取權限。 |
| 安裝依賴失敗 | 在專案目錄執行 `.venv/bin/python -m pip install -r requirements.txt`，並確認網路及 Python 環境正常。 |

OpenAlex 的 HTTP 錯誤會使當次執行失敗，不會以成功狀態掩蓋錯誤。遇到 API 配額或服務問題時，查看錯誤日誌；修復原因後可手動重跑。
