# 江苏高职招聘监控 · 金融专业硕士

每天自动检查 **江苏全部 97 所高职院校**（公办专科 70 所、民办专科 20 所、中外合作 1 所、职业本科 6 所）的招聘信息，筛选适合**金融专业硕士**的岗位（专任教师、辅导员、行政岗都算），汇总后推送到你的手机。

完全免费，运行在 GitHub 的服务器上，不用开电脑。

**结果是一个网页**：部署好以后会得到一个网址（形如 `https://你的用户名.github.io/jiangsu-jobwatch/`）。任何人用手机或电脑的浏览器打开就能看，不用安装、不用登录，在微信里也能直接打开。网页每天早上自动更新，可以按城市、岗位类型、公办/民办筛选，还会标出报名截止日期和“还剩几天”。

> 只需要**一个人部署一次**（下面 6 步，约 15 分钟）。之后把网址发给谁，谁就能用。

---

## 它是怎么尽量做到“不漏”的

**1. 五路数据来源，相互补漏**

| 来源 | 说明 |
|---|---|
| 97 所学校官网 | 自动找到每所学校的人事处“招聘信息”“人才引进”和“通知公告”栏目，每天检查 |
| 江苏省人社厅 | 省属事业单位统一招聘的岗位表（大表里有很多高职的行政、财务岗） |
| 13 个设区市人社局 | 市属高职的统一招聘 |
| 高校人才网（江苏栏目 + 高校招聘栏目） | 聚合网站，用来补漏 |
| 搜索引擎 | 每天全网搜最近一周的结果，另外每天轮换搜 15 所学校，大约一周把所有学校搜一遍 |

**2. 会读附件里的岗位表**：很多公告的专业要求只写在附件里。程序会下载并读取 Excel、Word、WPS、PDF（包括没有框线的表格）和 zip 压缩包，逐个岗位判断。

**3. 分四级，全部列出**

| 级别 | 含义 |
|---|---|
| 🔴 重点 | 岗位专业要求里有“金融”或“经济类”（按江苏的专业目录，金融学和金融硕士都属于经济类），且接受硕士 |
| 🟡 可能 | 专业不限 / 相近专业（经济、财会、工商管理等）/ 金融岗位但只招博士 / **附件读不出来，需要你看一眼** |
| ⚪ 其他 | 江苏高职的其他招聘公告，**全部列出**，防止被误判漏掉 |
| 🔵 进展 | 笔试、面试、公示、拟聘用等后续通知 |

**4. 健康检查**：每份日报末尾都会列出哪些学校官网打不开、哪些附件没读出来，并附上一键搜索链接。不会悄悄漏掉。

---

## 部署步骤（约 15 分钟，只需要浏览器）

### 第 1 步：注册 GitHub 并新建仓库
1. 打开 https://github.com 注册账号（免费）。
2. 点右上角 **＋ → New repository**。
   - Repository name 填 `jiangsu-jobwatch`。
   - 选 **Public**（公开）。免费账号只有公开仓库才能开网页版（GitHub Pages）。仓库里只有公开的招聘信息，没有任何个人信息。
   - 其他选项不用动，点 **Create repository**。

### 第 2 步：上传文件
1. 解压下载的 `jiangsu-jobwatch.zip`。
2. 在新仓库页面点 **uploading an existing file**，把解压后文件夹里的**所有内容**拖进去，点 **Commit changes**。
3. **重要**：Mac 和 Windows 默认会隐藏 `.github` 文件夹，它可能没传上去。检查仓库里是否有 `.github/workflows/daily.yml`。如果没有：
   - 点 **Add file → Create new file**。
   - 文件名一栏输入 `.github/workflows/daily.yml`（输入斜杠时会自动变成文件夹）。
   - 用记事本打开压缩包里的 `setup/daily.yml`，把内容全部复制粘贴进去，点 **Commit changes**。

### 第 3 步：允许自动运行
1. 仓库页面 → **Settings → Actions → General**。
2. 最下方 **Workflow permissions** 选 **Read and write permissions**，点 **Save**。
3. 点上方 **Actions** 标签页。如果提示要启用 workflows，点绿色按钮同意。

### 第 4 步：打开网页版
1. 仓库 **Settings → Pages**。
2. **Source** 选 **Deploy from a branch**，**Branch** 选 `main`，右边文件夹选 `/docs`，点 **Save**。
3. 仓库 **Settings → Secrets and variables → Actions → Variables** 标签 → **New repository variable**，Name 填 `PAGES_ENABLED`，Value 填 `true`，保存。这样每天的推送消息里会带上网页链接。

### 第 5 步：手动运行第一次
1. **Actions** → 左侧 **每日抓取江苏高职招聘** → 右侧 **Run workflow** → **Run workflow**。
2. 第一次要探测所有学校、补抓近期公告，大约 20–60 分钟。之后每天北京时间 7 点多自动运行，一般 10–20 分钟。
3. 运行完成后再等一两分钟，打开 `https://你的用户名.github.io/jiangsu-jobwatch/` 就能看到网页。把这个网址发给需要的人即可。
   - 同时，**Issues** 标签页里会出现一份日报，仓库首页的 **REPORT.md** 是表格版报告。

### 第 6 步：每天推送到手机（可选）

**方式 A：GitHub App（不用配置）**
手机安装 GitHub App 并登录，在仓库页面点 **Watch → All Activity**。每天的日报 Issue 会推送到手机。

**方式 B：微信推送（推荐）**
1. 打开 https://www.pushplus.plus，用微信扫码登录，复制你的 **token**。
2. 仓库 **Settings → Secrets and variables → Actions → New repository secret**。
3. Name 填 `PUSHPLUS_TOKEN`，Secret 粘贴 token，保存。

也支持 Server酱（Secret 名 `SERVERCHAN_KEY`）。

**方式 C：邮件**
添加这几个 Secret：`SMTP_HOST`（如 `smtp.qq.com`）、`SMTP_PORT`（`465`）、`SMTP_USER`（你的邮箱）、`SMTP_PASS`（邮箱的 SMTP 授权码，不是登录密码）、`MAIL_TO`（收件邮箱，多个用英文逗号隔开）。

**把网页放到手机桌面**
用手机浏览器打开网页 →“分享”或菜单 →“添加到主屏幕”。以后就像 App 一样点开即看。在微信里打开的话，可以点右上角“…”→“收藏”。

---

## 日常维护

- **看健康检查**：日报末尾的“需要关注的学校”如果一直出现某所学校，点后面的“搜索”链接找到它的人事处招聘栏目，把网址填进 `schools.yaml` 对应学校的 `recruit_pages`。例如：
  ```yaml
  - {name: 江苏财会职业学院, city: 连云港, type: 公办专科, homepage: "http://www.jscfa.edu.cn/", recruit_pages: ["https://某某/zpxx/list.htm"]}
  ```
  在 GitHub 网页上点文件 → 右上角铅笔图标即可编辑。
- **调整关键词**：编辑 `config.yaml` 里的 `finance_strong`、`finance_weak`。
- **改运行时间**：编辑 `.github/workflows/daily.yml` 里的 `cron`（使用 UTC 时间，北京时间减 8 小时）。
- **没有新公告也想收到日报**：默认每天都推送；想只在有新增时推送，把 `config.yaml` 里的 `only_when_new` 改成 `true`。
- **停用提醒**：如果 GitHub 发邮件说定时任务因长期无活动被停用，去 Actions 页面点一下启用即可。程序每天都会提交数据，一般不会触发。

## 在自己电脑上运行（可选）

如果 GitHub 的海外服务器访问某些学校网站很慢或被拦截，可以在国内电脑上运行同一套程序：

```bash
pip install -r requirements.txt
python -m jobwatch run --no-notify      # 抓取并生成 REPORT.md 和 docs/index.html
python -m jobwatch run --school 财会     # 只测试名字含“财会”的学校
python -m pytest -q                      # 运行自带测试
```

---

## 需要知道的局限

- **无法保证 100% 不漏**。这套程序用多来源交叉覆盖，并把所有拿不准的情况列出来请你人工确认，尽量逼近不漏，但仍可能漏掉：只发在微信公众号的招聘、整篇是图片的公告、需要登录才能看的页面。报名季（每年 3–5 月、9–12 月）建议每周再手动看一眼江苏省人社厅官网和目标学校官网。
- **首次运行会有几所学校显示异常**：`schools.yaml` 里部分学校的官网地址留空了，程序会通过搜索自动找；找不到或找错的会在健康检查里列出，按上面的方法补一次即可。
- GitHub 的服务器在海外，少数政府或学校网站可能限制海外访问。健康检查会显示出来，这种情况可以改用自己的电脑运行。
- `github.io` 网页在国内一般能直接打开，但个别网络下可能偏慢。打不开时可以换个网络，或看每天推送到微信、邮箱里的日报。
- 没有做成微信小程序：发布小程序需要注册主体、服务器和域名备案，招聘类内容还需要相关资质并通过审核，个人很难办下来。网页版在微信里也能直接打开，效果接近。
- 信息仅供参考，**报名条件以学校和人社部门发布的官方公告为准**。
