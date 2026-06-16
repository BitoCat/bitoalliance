# BitoAlliance 專案規格文件
> 最後更新：2026年6月17日（v7）
> 貼給 Claude 使用，作為每次對話的起點

---

## 品牌架構

| 層級 | 名稱 | 定位 |
|------|------|------|
| 大品牌 | 比特聯盟 BitoAlliance | 整體團隊品牌 |
| 主理人 | 系統管理員（對外不提個人） | 直播主、選幣人 |
| 指標品牌 | BitoCat | TradingView 指標系列 |
| 交易所夥伴 | Bitunix | 推廣對象 |
| Bitunix 推薦碼 | `Pooc` | https://www.bitunix.com/register?vipCode=Pooc |

---

## 技術架構

### 前端
- 純靜態 HTML + JS，GitHub Pages：`BitoCat/bitoalliance`
- 網址：`https://bitocat.github.io/bitoalliance`
- 色系：暖米咖啡館，底色 `#edd4be`

### 後端 bito-api
- Flask，Railway：`https://web-production-f4735.up.railway.app`
- repo：`BitoCat/bito-api`

### Railway 環境變數（bito-api）
- `SUPABASE_URL`：`https://kldumnxdlgovaxtiszyn.supabase.co`
- `SUPABASE_KEY`：service_role key
- `ECPAY_MERCHANT_ID`：`3189236`（與交易大師共用）
- `ECPAY_HASH_KEY` / `ECPAY_HASH_IV`：已設定（與交易大師相同）
- `TG_BOT_URL`：`https://web-production-9ba5b.up.railway.app`
- `TG_BOT_SECRET`：已設定

### 後端路由（bito-api）
| 路由 | 方法 | 功能 |
|------|------|------|
| `/health` | GET | 健康檢查 |
| `/live-data` | GET | 所有直播數據 |
| `/kline` | GET | Bitunix K線 |
| `/webhook` | POST | TradingView 快訊 |
| `/market/tickers` | GET | 所有幣種 ticker |
| `/scanner/crypto` | GET | 幣種型態掃描（即時） |
| `/indicator/checkout` | POST | 遊俠方案綠界付款 |
| `/indicator/notify` | POST | 綠界回傳 |
| `/indicator/submit` | POST | 填寫 TradingView 帳號 |
| `/indicator/check` | GET | 查詢訂單狀態 |
| `/indicator/verify-code` | POST | 驗證優惠碼 |
| `/bito/add-member` | POST | 新增 Telegram 會員（中繼） |
| `/bito/members` | GET | 查詢 Telegram 會員（中繼） |
| `/debug/kline` | GET | 除錯用 K線格式查看 |

---

### ECPay 注意事項
- 商家代號 `3189236` 與交易大師共用同一組憑證
- 綠界後台 ReturnURL：`https://web-production-9ba5b.up.railway.app/ecpay/notify`
- 比特聯盟指標付款 ReturnURL：`https://web-production-9ba5b.up.railway.app/indicator/notify`
- `9ba5b`（tg-member-bot）收到後轉發給 `f4735`（bito-api）處理

---

## tg-member-bot（Telegram 會員管理）
- repo：`BitoCat/tg-member-bot`（main.py）
- Railway：`https://web-production-9ba5b.up.railway.app`
- Bot：`@Boss1688Bot`

### 比特聯盟頻道
- `-1003936945873`：交易高手 Pro Trader
- `-1003778531725`：BTC PHL 價格高低

### memberships 表欄位（含 source）
- `source`：`trader`（交易大師）/ `bitoalliance`（比特聯盟）
- Bot 依 source 發對應頻道邀請

---

## Supabase

### anon key（前端用）
```
eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImtsZHVtbnhkbGdvdmF4dGlzenluIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzgxMjIyNDgsImV4cCI6MjA5MzY5ODI0OH0.P_anfYpSV2tgCPIQ7uZDVBjUFY3UhfEXk9u3XjlzBHI
```

### profiles 表完整欄位
```sql
id, email, display_name, plan, is_direct_client, bitunix_uid,
team_name, master_id, parent_id, master_status,
referral_code, indicator_expires_at, groq_api_key
```

| 欄位 | 說明 |
|------|------|
| plan | free/ranger/direct/master/admin |
| is_direct_client | boolean |
| bitunix_uid | Bitunix 用戶 ID |
| team_name | 盟主團隊名稱 |
| master_id | 所屬盟主的 user_id |
| parent_id | 上級盟主的 user_id |
| master_status | null/pending/recommended/approved/rejected |
| referral_code | 盟主優惠碼（格式：BITO-XXXX），核准時自動配發 |
| indicator_expires_at | 遊俠指標授權到期日 |
| groq_api_key | 會員自填的 Groq API Key，用於 AI 技術分析 |

### member_ranks 表
```sql
id, user_id, rank, granted_by, expires_at, note,
screenshot_path, status, created_at, updated_at
```
- rank 值：miner/knight/wargod/wizard/king/elder/master/supreme
- status 值：pending/approved/rejected

### indicator_orders 表
```sql
id, merchant_trade_no, user_id, referred_by, referral_code,
amount, tv_username, payment_status, indicator_status,
paid_at, granted_at, created_at, updated_at
```
- payment_status：pending / paid
- indicator_status：pending / granted / rejected

### memberships 表（tg-member-bot 用）
- 新增 `source` 欄位：`trader` / `bitoalliance`

### Storage bucket
- `vip-screenshots`（私有）：VIP 截圖

---

## 會員身份體系

| 身份 | 進入方式 | plan 值 | 說明 |
|------|---------|---------|------|
| 布衣 | 剛註冊 | `free` | 少許功能，吸引升級 |
| 遊俠 | 付年費 $3,600 租指標 | `ranger` | 自由身，不屬任何門派 |
| 會員 | 加入盟主 + Bitunix | `direct` | 師徒體系 |
| 盟主 | 系統管理員核准 | `master` | 管理會員 |
| 系統管理員 | — | `admin` | |

## Bitunix VIP 稱號（獨立於 plan）

| Bitunix VIP | 比特聯盟稱號 |
|-------------|------------|
| VIP 0 | ⛏️ 礦工（miner）|
| VIP 1 | ⚔️ 騎士（knight）|
| VIP 2 | ⚡ 戰神（wargod）|
| VIP 3 | 🔮 術士（wizard）|
| VIP 4 | 👑 天王（king）|
| VIP 5 | 🏯 長老（elder）|
| VIP 6 | 🐉 宗師（master）|
| VIP 7 | ✨ 至尊（supreme）|

稱號在 `member_ranks` 表，不影響 plan，有效期 1 個月。

## 盟主體系

```
系統管理員（plan=admin）
└── 盟主（plan=master）
      └── 會員（直客）
      └── 下級盟主（推薦 → 系統管理員核准）
```

- 盟主核准時自動配發優惠碼（BITO-XXXX）
- 優惠碼折抵指標年費 $1,200（$3,600 → $2,400）
- 盟主可在 superadmin 重新配發優惠碼

---

## 遊俠指標申請流程

1. `indicator.html` 填優惠碼（選填）→ 付綠界 $3,600（有碼 $2,400）
2. 綠界回傳 → `indicator_orders` 寫入 paid
3. 填訂單編號 + TradingView 帳號
4. superadmin 審核 → 標記 granted → 手動去 TradingView 授權
5. 付款後 → `@Boss1688Bot /start` → 輸入 Gmail → 進兩個頻道

---

## TradingView Webhook

URL：`https://web-production-f4735.up.railway.app/webhook`

四個快訊 JSON：
```json
{"type":"tunnel","ticker":"BTCUSDT","timeframe":"15m","signal":"upper","price":"{{close}}","message":"15m upper"}
{"type":"tunnel","ticker":"BTCUSDT","timeframe":"15m","signal":"lower","price":"{{close}}","message":"15m lower"}
{"type":"tunnel","ticker":"BTCUSDT","timeframe":"1h","signal":"upper","price":"{{close}}","message":"H1 upper"}
{"type":"tunnel","ticker":"BTCUSDT","timeframe":"1h","signal":"lower","price":"{{close}}","message":"H1 lower"}
```

---

## 頁面狀態

| 檔案 | 狀態 | 說明 |
|------|------|------|
| `login.html` | ✅ 完成 | Email+Google登入、忘記密碼 |
| `index.html` | ✅ 完成 | 用戶儀表板，布衣看到升級 CTA |
| `profile.html` | ✅ 完成 | 帳號設定、UID綁定、VIP截圖、Groq Key |
| `indicator.html` | ✅ 完成 | 遊俠指標申請、付款流程 |
| `signals.html` | ✅ 完成 | 市場儀表板（TradingView + 多空比 + OI）|
| `scanner.html` | ✅ 完成 | 幣種型態掃描器（即時，4大類15種型態）|
| `whale.html` | ✅ 完成 | 鯨魚追蹤（OI/大戶持倉/爆倉/大單）|
| `concepts.html` | ✅ 完成 | 概念板塊排行（8板塊/熱力圖/輪動）|
| `diagnosis.html` | ✅ 完成 | 幣種技術診斷（多時框+AI解讀）|
| `groq-guide.html` | ✅ 完成 | Groq Key 申請教學 |
| `admin.html` | ✅ 完成 | 盟主後台 |
| `superadmin.html` | ✅ 完成 | 系統管理員後台 |

## Navbar 導覽（所有功能頁共用）
首頁 · 市場儀表板 · 幣種掃描 · 鯨魚追蹤 · 板塊排行 · 技術診斷

---

## 待修清單
- [ ] 各頁面「直客」→「會員」
- [ ] 其他個人色彩文字清理

## Google OAuth
- Supabase：已啟用 Google provider
- Google Cloud Console redirect URI：
  - `https://kldumnxdlgovaxtiszyn.supabase.co/auth/v1/callback`
  - `https://bitocat.github.io/bitoalliance`
  - `https://bitocat.github.io/bitoalliance/index.html`
