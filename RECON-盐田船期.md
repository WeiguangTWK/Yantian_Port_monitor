> 历史记录：部分接口与功能描述已过时。当前用法以 README.md 和现行源码为准。

# 盐田码头船期抓取 — 前期侦察报告

> 目标：`https://www.156yt.cn/pqs_revision/pages/jsp/voyQuery.jsp?loginVerifyCode=`
> 侦察日期：2026-09-14　状态：**已完成端到端协议验证并取到真实数据**（只读查询，未写入任何生产数据）

---

## 一、结论速览

| 问题 | 结论 |
|---|---|
| 空 token 能用吗？ | ❌ 被弹回「公共信息服务」首页 |
| `loginVerifyCode` 能自己造吗？ | ❌ 登录态产物，由服务端注入页面 |
| 它是登录凭证吗？ | ⚠️ 是**可复用的查询令牌**，**不需要账号密码即可做公众查询** |
| 一次能用多久 | 已验证**跨 20+ 分钟、多次会话复用仍有效**（真实有效期待测） |
| 最大障碍 | **腾讯 EdgeOne JS 挑战**（纯 HTTP 客户端会被全站降级） |
| 破解方式 | ✅ **真实浏览器内核自动通过**，落地 `EO-Bot-Js-Token` |
| 查询接口 | `POST voyQuery.jsp?modify=query`（表单，非 AJAX） |
| 返回格式 | **GB2312 编码的 HTML 表格**，每页 50 条，6 列 |
| 数据视野 | 只发布**约 1 个月**内的船期；无长期历史 |

---

## 二、鉴权机制

### 2.1 token 是登录态产物

未登录时首页把 token 渲染成**字面量 `null`**：

```html
<input type="hidden" value="false" id="isLogin">
<form action="/homepage/publicInquiry/shipSchedule.action" name="shipForm" method="post"
      onsubmit="if(null==null){openDiv();return false;}else{return true;}">
```

登录后渲染成 `if('XXXXXXXX'==null)` → 放行。同页 `id="isLogin"` 由 `false` 变 `true`。

### 2.2 实测：token 直接用就通

**全新浏览器会话 + `voyQuery.jsp?loginVerifyCode=<TOKEN>` → 直接返回「船期公众查询」页。**
（若会话已登录为 member，反而会被弹回公共信息服务首页 —— 所以要用**干净的未登录会话 + token**。）

token 结构：`<opaque base64>==` + **base64("1782270")** ← 尾部是账号/会员 ID，可解码校验。

### 2.3 登录接口（备用，走正门）

```
POST /passport/login!verify        j_username / j_password / _by_ajax
```
- 明文提交，**无验证码、无前端加密**（`upperLogin()` 只做非空校验 + 用户名转小写）
- AJAX 返回 JSON，**响应里直接带回新 token**：
  ```json
  {"loginMessege":null,
   "redirectUrl":"https://www.156yt.cn/member/index.action?loginVerifyCode=<新token>",
   "result":1}
  ```
  所以续期**不需要再爬页面抠 token**（原先的实现多此一举，而且它的登录态检查还会误报）。
- 另有扫码登录 `POST /passport/login!verifyQrcode`（`uuid=...`）

### 2.4 ⚠️ 重要更正：token 是否真的被校验，与我们原先的假设不符

**经过（我自己踩的坑）：**

第一版实现有 `check_token()` —— 对 `voyQuery.jsp?loginVerifyCode=<token>` 发一次 **GET**，
看到标题是「船期公众查询」就判为有效。并据此做了一个"token 寿命观测器"。

后来为验证自动续期，我故意用一个**伪造 token**（`BOGUS-NOT-A-REAL-TOKEN-12345`）跑监控，
结果**一切正常，还查回了真实数据**。于是追查。

**已确认的事实（干净对照实验，每个条件都用全新浏览器档案）：**

| 条件（全新档案） | 引导后落地 | 标题 | 拿到 cookie | POST 查询 |
|---|---|---|---|---|
| 真实 token | 查询页 | 船期公众查询 | EO + JSESSIONID | ✅ 总记录数=1，命中真实数据 |
| **伪造 token** | 查询页 | 船期公众查询 | EO + JSESSIONID | ✅ **总记录数=1，命中真实数据** |
| **空 token** | 查询页 | 船期公众查询 | EO + JSESSIONID | ✅ **总记录数=1，命中真实数据** |
| **完全不带该参数** | 查询页 | 船期公众查询 | EO + JSESSIONID | ✅ **总记录数=1，命中真实数据** |

**结论（确定）：**

1. **`loginVerifyCode` 对「船期公众查询」完全不必要。**
   连"不带这个参数"都能正常查询。它不是访问控制，只是平台侧的会话/统计标记。
2. **真正的门槛只有一个：EdgeOne 的 JS 挑战** —— 由真实浏览器内核自动通过，
   落地 `EO-Bot-Js-Token` 后即可查询。
3. **`check_token()` 判不出 token 是否有效**（伪造/空 token 一样拿到查询页），
   因此"token 寿命观测器"测的其实是 **cookie/会话寿命**，不是 token TTL。

**为什么之前会得出"空 token 会被弹回首页"的错误结论：**
那次观测是在**还没有有效 cookie** 的情况下做的，看到的其实是 **WAF 拦截**，
被误归因到了 token 上。这正是"变量没控制住"的典型后果。

**对设计的重大影响：**

| 原设计 | 现在 |
|---|---|
| 需要账号 + token | **不需要账号，也不需要 token** |
| 需要 token 续期 | **不需要**（整个 `auth.py` / `renew_token.py` 在本场景是多余的） |
| 需要测 token TTL | **不存在这个问题** |
| 需要保管账号密码 | **不需要**（凭证可以完全不落任何地方） |
| 唯一门槛 | 能过 EdgeOne 挑战的浏览器（拿到 `EO-Bot-Js-Token`） |

**⚠️ 另一个必须记住的运维事实：GET 不能作为判据**

用 `requests`（带缓存 cookie）对 `voyQuery.jsp` 发 **GET**，**会稳定落到
「公共信息服务」首页**；而**同一个会话的 POST 查询却完全正常**。

这导致预检查在两个方向上都会误导：

| 判据 | 后果 |
|---|---|
|  `'船期公众查询' in 正文` （旧实现） | 首页也有这个链接文本 → **永远报"有效"** |
| `查询表单字段存在` （改对之后） | GET 本来就落首页 → **永远报"失效"，直接放弃查询** |

**所以：唯一可信的信号是查询本身。** 预检查只在"必然失败"的两类问题上提前中止
（`waf` / `network`），其余情况一律继续实际查询，由查询结果来判定。

**实测（无 token，连续三轮）**：全部成功，首轮含引导 9.4s，之后约 2s。

⚠️ **仍需注意**：公众查询是公开服务，但**限流是真实存在的**（实测触发过 HTTP 567）。
无论是否登录，都应保持低频率、单并发，并遵守站点条款。
若将来需要高频或需要更多字段，正路是申请官方的**船期信息订阅接口**。

---


## 三、EdgeOne JS 挑战（已解决）

**现象**：非浏览器客户端请求任意路径 → 同一个 29179 字节混淆 JS 挑战页。
`Server: TencentEdgeOne`，通过后落地 cookie **`EO-Bot-Js-Token`**（域 `.156yt.cn`，非 HttpOnly）。

**已验证可行**：无头 Edge 打开页面即自动通过，拿到真实 DOM。

**关键坑**：每次 `--dump-dom` 都是**独立会话**，而站点是**多实例粘性会话**（JSESSIONID 后缀 `portal1`/`portal2`/`pqs2`），且 `EO-Bot-Js-Token` 是唯一持久化的 cookie。因此必须用**同一浏览器会话**连续操作（已产品化在 `ytmon/cdp.py`）。

---

## 四、查询协议（端到端已验证 ✅）

### 4.1 请求

```
POST https://www.156yt.cn/pqs_revision/pages/jsp/voyQuery.jsp?modify=query
Content-Type: application/x-www-form-urlencoded

pageCurrent=1
etb_time=20260914        起始日期 YYYYMMDD，必填
ship_name=MSC            船名，模糊匹配，≥2 字符
voyage_code=             码头航次，非空时 ≥2 字符
Submit1=查询
```

**客户端校验规则**（服务端亦生效）：
- `etb_time` 必填，必须 `YYYYMMDD`
- `ship_name` 与 `voyage_code` **不可同时为空**
- `voyage_code` 非空时长度 ≥2
- `ship_name` **≥2 字符**；1 字符 / `*` / 空格 → **0 条**（不是报错）

### 4.2 响应

- `Content-Type: text/html;charset=gb2312` → **必须按 `gb18030` 解码**
  （浏览器 `fetch().text()` 会按 UTF-8 解坏成 U+FFFD，必须走 `arrayBuffer`）
- 6 列：

| 码头航次 | 船名 | 闸口 | 预计停靠（ETB） | 预计离港（ETD） | 船代 |
|---|---|---|---|---|---|
| KN637A | MSC SOMYA III | A | 2026-09-14 21:00 | 2026-09-16 04:00 | 外运 |
| UX637A | MSC DANIT | B | 2026-09-18 | 2026-09-20 | 外运 |

- **日期有两种格式**：`YYYY-MM-DD` 和 `YYYY-MM-DD HH:MM` → 解析器必须兼容
- 分页区：`总记录数 N` / `当前页 N` / `总页数 N`，**每页 50 条**
- 翻页：同一 POST，改 `pageCurrent`（页面 JS `ddPage(pageCurrent,a,b,c)`）

### 4.3 日期窗口语义（实测）

`etb_time` = **起始日**，返回其后 **≈30 天**窗口：

| etb_time | 总记录数 | 实际 ETB 跨度 |
|---|---|---|
| 20260901 | 76 | 2026-09-07 → 2026-10-04 |
| 20260914 | 69 | 2026-09-14 → 2026-10-14 |
| 20261014 | 4 | 2026-10-14 |
| 20261015 | 0 | — |
| 20261231 | 0 | — |

→ **站点只公布约 1 个月内的船期，无法长期回溯历史。**
→ 有一次返回 `HTTP 567`（非标准码）属**瞬时限流**，不是日期错误；需退避重试。

---

## 五、⚠️ 最大的设计约束：无法"查全部"

`ship_name` 为空 → **0 条**。没有"列出全部"的查询方式。所以要覆盖全量船期，只有两条路：

| 方案 | 做法 | 代价 |
|---|---|---|
| **白名单**（推荐） | 只抓关注的船公司/船代/航线，如 `MSC` / `EVER` / `COSCO` … | 覆盖可控，但会漏掉未列入的船 |
| **穷举** | 用 ≥2 字符子串扫描（36×36=1296 次）建船舶字典，之后按发现的船名增量更新 | 首次成本高，对站点压力大 |

> `ship_name` 是**子串匹配**（查 `EVER` 会命中 `LEVERKUSEN EXPRESS`），所以穷举需要设计好覆盖策略。

---

## 六、本机环境

| 项 | 状态 |
|---|---|
| 真实浏览器 | **Edge 152** `C:\Program Files (x86)\Microsoft\EdgeCore\152.0.4191.66\msedge.exe` ✅ |
| | WebView2 152、Chromium 146、Firefox ✅；❌ 无 Chrome（请在 App Paths 外按绝对路径调用） |
| Python | 3.14.7 + `requests` / `httpx` / `aiohttp` / `bs4` / `lxml` / `openpyxl` / `APScheduler` |
| 未装 | ❌ `playwright` / `selenium` / `pandas` |
| 网络 | PyPI ✅ npm ✅；系统 `curl` ❌（Schannel 凭证错，调试用 Python） |
| **可行方案** | 用已装 `aiohttp` 直连 CDP 驱动 Edge → **零安装**（已产品化在 `ytmon/cdp.py`） |

---

## 七、前期准备清单

> ⚠️ **本节是侦察阶段的原始清单，保留作为决策留痕。**
> 其中大部分问题后来已经解决或被实测推翻（例如"token 有效期"整条 —— token 本就不需要）。
> **要看当前结论请看本文第 2.4 节、`README.md`，以及 OV 里的
> `viking://user/default/resources/projects/Port_Auto_Fetch/`。**

### A. 业务口径（需你确认）
- [ ] **抓取范围**：白名单船公司/船代，还是穷举？（见第五节，**这是第一个必须定的**）
- [ ] 抓取频率与时间点（建议每天 2–4 次）
- [ ] 产物形态：Excel / CSV / SQLite / JSON
- [ ] 是否累积入库，重点看 **ETB/ETD 的变动**（延误/改期才是真需求）
- [ ] 是否并入现有 `XML_FastMerge` / 库存仪表盘生态（统一 SQLite）

### B. 令牌与账号
- [ ] **token 有效期**：需要观察多久过期、如何自动续期
- [ ] 续期方案：是否用账号自动登录 `POST /passport/login!verify` 后从页面取新 token
- [ ] 凭证保管：不落明文，走环境变量 / Windows 凭据管理器
- [ ] 合规确认：用该账号做自动化查询已获授权（建议留痕）

### C. 工程约束
- [ ] 节流：**并发=1**，间隔 ≥3–5s（实测 6 次连续请求后出现 `567` 限流）
- [ ] `567` / 非 200 一律**退避重试**，不要当空结果
- [ ] 每页 50 条：**必须翻页到底**，否则静默丢数据
- [ ] 唯一键去重：`码头航次 + 船名 + ETB`（航次可能跨日重复）
- [ ] 编码：一律 `gb18030`；日期兼容无时分格式
- [ ] 每次抓取留**原始 HTML 快照**（复核 + 解析器回归）
- [ ] 数据校验：行数突变、日期越界、船名/航次为空 → **报警而非静默**

### D. 验收准备
- [x] golden file 回归集 —— **已落地到 `tests/fixtures/`**（不再是种子，是用起来的固定装置）：
  `voy_result_ever.html`(26,061 B，即原 `.recon/res_EVER.html`)、`voy_result_empty.html`(14,704 B)、
  `voy_query_page.html`(13,994 B)、`public_info_page.html`(20,390 B)
- [ ] 边界样本：0 条、多页、日期无时分、超长船名

---

## 八、侦察留档（已清理）

> **2026-09-14：`.recon/` 已整体删除**（约 495 MB，主要是 12 份浏览器档案副本）。
> 原因：它是初期侦察的现场，长期留在工作区只是噪声；而且里面 `token.txt`、
> `final_voy.html`、`state/token_watch*.jsonl` **含真实凭证**，不应长期驻留磁盘。
>
> **结论都已保留**，需要时看这里，不要去找 `.recon`：

| 原本在 `.recon` 的东西 | 现在在哪 |
|---|---|
| 最小 CDP 驱动器 | `ytmon/cdp.py`（已产品化，不是那个原型了） |
| 真实结果样本 / 查询页 / 边界样本 | `tests/fixtures/*.html`（已提取，字节数一致） |
| EdgeOne 挑战页原始响应 | 特征串在 `ytmon/http_client.py` 的 `_WAF_MARKERS`，回归测试守着 |
| 各阶段侦察脚本（probe5~8 等约 90 个） | 已删 —— 都是一次性探针；结论见本文与 `protocol.md`/`dead-ends.md` |
| ⚠️ 你的令牌 `token.txt` | **已删除**。而且实测证明 token 本来就不需要（见 2.4 节） |

**如果你想重新验证某个结论**，结论正文里都写了当时的控制变量和观测结果，
重写一个探针脚本是几分钟的事 —— 比维护一个 500 MB 的现场便宜得多。
