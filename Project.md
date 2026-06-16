# BitoAlliance 專案規格文件
> 最後更新：2026年6月16日（v6）
> 貼給 Claude 使用，作為每次對話的起點

---

## 品牌架構

| 層級 | 名稱 | 定位 |
|------|------|------|
| 大品牌 | 比特聯盟 BitoAlliance | 整體團隊品牌 |
| 主理人 | 毛哥 | 直播主、選幣人 |
| 指標品牌 | BitoCat | TradingView 指標系列 |
| 交易所夥伴 | Bitunix | 推廣對象，毛哥為代理商 |
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

### Railway 環境變數
- `SUPABASE_URL`：`https://kldumnxdlgovaxtiszyn.supabase.co`
- `SUPABASE_KEY`：service_role key

### 後端路由
| 路由 | 方法 | 功能 |
|------|------|------|
| `/health` | GET | 健康檢查 |
| `/live-data` | GET | 所有直播數據 |
| `/kline` | GET | Bitunix K線 |
| `/webhook` | POST | TradingView 快訊 |

---

## Supabase

### anon key（前端用）
```
eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImtsZHVtbnhkbGdvdmF4dGlzenluIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzgxMjIyNDgsImV4cCI6MjA5MzY5ODI0OH0.P_anfYpSV2tgCPIQ7uZDVBjUFY3UhfEXk9u3XjlzBHI
```

### profiles 表完整欄位
```sql
id, email, display_name, plan, is_direct_client, bitunix_uid,
team_name, master_id, parent_id, master_status
```

| 欄位 | 說明 |
|------|------|
| plan | free/tw/us/all/direct/master/admin |
| is_direct_client | boolean |
| bitunix_uid | Bitunix 用戶 ID |
| team_name | 盟主團隊名稱 |
| master_id | 所屬盟主的 user_id |
| parent_id | 上級盟主的 user_id |
| master_status | null/pending/recommended/approved/rejected |

### member_ranks 表
```sql
id, user_id, rank, granted_by, expires_at, note,
screenshot_path, status, created_at, updated_at
```

- rank 值：miner/knight/wargod/wizard/king/elder/master/supreme
- status 值：pending/approved/rejected

### Storage bucket
- `vip-screenshots`（私有）：存放會員 VIP 截圖

### Trigger
- `on_auth_user_created`：新用戶自動寫入 profiles（含 email）

---

## 會員等級體系

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

- 有效期：1個月，到期需重新提交截圖
- 升級方式：會員上傳 Bitunix VIP 截圖 → 盟主審核截圖 → 核准後生效

## 盟主體系

```
毛哥（superadmin）
└── 盟主（plan=master）
      └── 直客會員
      └── 下級盟主申請（推薦給毛哥核准）
```

- 盟主只管自己的直客
- 盟主推薦新盟主人選（填入當事人資料），毛哥最終核准
- master_status 流程：null → pending → recommended → approved/rejected

---

## TradingView Webhook

URL：`https://web-production-f4735.up.railway.app/webhook`

四個快訊 JSON（純英文）：
```json
{"type":"tunnel","ticker":"BTCUSDT","timeframe":"15m","signal":"upper","price":"{{close}}","message":"15m upper"}
{"type":"tunnel","ticker":"BTCUSDT","timeframe":"15m","signal":"lower","price":"{{close}}","message":"15m lower"}
{"type":"tunnel","ticker":"BTCUSDT","timeframe":"1h","signal":"upper","price":"{{close}}","message":"H1 upper"}
{"type":"tunnel","ticker":"BTCUSDT","timeframe":"1h","signal":"lower","price":"{{close}}","message":"H1 lower"}
```
✅ 已設定完成，Webhook 正常

---

## 頁面狀態

| 檔案 | 狀態 | 說明 |
|------|------|------|
| `live.html` | ✅ 完成 | 直播儀表板 1920×1080 |
| `login.html` | ✅ 完成 | Email+Google登入、忘記密碼 |
| `index.html` | ✅ 完成 | 用戶儀表板 |
| `profile.html` | ✅ 完成 | 帳號設定、UID綁定、選擇盟主、VIP截圖上傳 |
| `admin.html` | 🔄 進行中 | 盟主後台 |
| `superadmin.html` | ⏳ 待開發 | 毛哥超級管理員後台 |

---

## live.html 版面結構

```
標題列（全寬）
├── 左側面板（270px，全高）     右側欄
│   BTC現價大字                  ├── Header（Logo+時鐘+LIVE）
│   1h Sparkline                 ├── 數據橫列（多空比/OI/費率/熱門/恐懼貪婪）
│   資金費率 Gauge                ├── TradingView（flex:1）
│   費率倒數環                    └── 加入會員說明｜今日直播主題(金色)｜QR code×3
│   買賣深度 Bar
│   日內高低
│   貓咪隧道快訊
│   Technical Analysis Widget（scale 0.60）
警語列（全寬）
```

今日直播主題：找 `id="liveTitle"` 改文字，金色 #FFD700

---

## admin.html 待完成功能

- [ ] 設定團隊名稱
- [ ] 管理自己的直客（審核 UID 申請）
- [ ] 審核直客 VIP 截圖（授予稱號）
- [ ] 推薦新盟主人選給毛哥（填入當事人資料）
- [ ] 直客列表（可刪除/修改）

## superadmin.html 待完成功能

- [ ] 所有會員申請總覽（屬於哪位盟主、核准/未核准）
- [ ] 盟主申請審核（核准/拒絕推薦）
- [ ] 活動紀錄時間序（提交/核准）
- [ ] 各盟主統計

---

## Google OAuth
- Supabase：已啟用 Google provider
- Google Cloud Console redirect URI：
  - `https://kldumnxdlgovaxtiszyn.supabase.co/auth/v1/callback`
  - `https://bitocat.github.io/bitoalliance`
  - `https://bitocat.github.io/bitoalliance/index.html`

## 待辦
- [ ] admin.html 完成
- [ ] superadmin.html 完成
- [ ] signals.html 幣圈掃描
- [ ] @Boss1688Bot /direct 指令
