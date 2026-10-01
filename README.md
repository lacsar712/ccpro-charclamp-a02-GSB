# CharClamp-01 · 炭窑焖烧志

窑场炭窑与焖烧班次台账基线项目（Litestar + SQLAlchemy 2 + Jinja2 + HTMX）。

## 技术栈

| 层 | 技术 |
| --- | --- |
| Web | Litestar · Jinja2 · HTMX CDN · Session 认证 |
| 数据 | SQLAlchemy 2（async） · PostgreSQL 15 |
| 部署 | Docker Compose · Uvicorn |
| 结构 | `domain/` · `infra/` · `web/` 分层（非 Django apps） |

## 路径与端口

- **项目路径**：`d:\work\document\bytecode\claudeCodePro\CharClamp\CharClamp-01`
- **Web**：http://localhost:4750
- **PostgreSQL**：localhost:6150

## 演示账号

| 用户名 | 密码 | 角色 |
| --- | --- | --- |
| `admin` | `123456` | 管理员 |
| `worker` | `123456` | 操作工 |

登录页已预填 `admin` / `123456`。entrypoint 会建表并写入种子数据（窑场 **乌石岗焖烧坞**，窑号如 **坞东-甲 / 坞东-乙 / 河沿-丙**）。

## 主界面：焖烧时间轴

登录后进入全宽 **焖烧时间轴**（不再使用侧栏 + 双 CRUD 列表）：

1. **顶部窑剪影行**：每座炭窑以 SVG 剪影展示；点击某窑用 HTMX 局部刷新下方时间轴，并更新地址栏 `?clamp_id=`；「全部窑」取消筛选。
2. **纵向时间轴**：按开始时间倒序列出 `BurnShift`；每条卡片带窑号徽章（再点可开抽屉）、峰值温度、炭品与当前窑态。
3. **侧抽屉（非独立编辑页）**：「登记班次」写入新班次；点窑徽章打开操作抽屉，可标记「已出炭」（受峰值规则与当日过磅联双重约束）。
4. **过磅联专页**：顶栏「过磅联」进入 `/weighing`，含联列表、新建表单与管理员作废；窑剪影标注今日合格联并与专页对账。

## 业务规则

炭窑状态不可设为「已出炭」（`drawn`），除非**在同一个出炭入口函数内同时**满足：

1. 该窑**最近一条** `BurnShift` 的 `peakTempC` 已记录且 **≥ 400℃**（旧门槛，继续生效）；
2. 该窑**当日**存在一张**合格且未作废**的出炭过磅联（`WeighSlip`）。

出炭校验（峰值门槛 + 过磅联）统一收在 `assert_can_set_clamp_status` → `can_mark_clamp_drawn`，出炭入口（`POST /clamps/{id}/status`）禁止旁路。

### 出炭过磅联（专页「过磅联」）

已出炭前，须先在顶栏「过磅联」专页落下一张过磅联。联字段：炭窑、过磅日、毛重千克、皮重千克、司秤人、是否合格。

- **净重 = 毛重 − 皮重**：毛重与皮重都须 **> 0**，净重 **不少于 50 千克**，否则合格联无法保存。
- **自然日**：以「过磅日」的日历日期为准（与具体时刻无关）。同一炭窑**同一自然日最多保存一张未作废的合格联**；数据库部分唯一索引兜底，两名司秤并发抢建时只落下一张，第二张得到中文「已有联」提示。
- 点「已出炭」时读取该窑当日**最新一张合格且未作废**联；没有则中文拒绝。
- **操作工可新建**过磅联；**作废仅管理员**。作废后该联不得再算出炭（需另落新联）。
- 窑剪影会标注「今日是否有合格联」，并与过磅专页的未作废合格联数**对账（差为 0）**。

规则实现：`src/charclamp/domain/rules.py`，模型：`src/charclamp/domain/models.py`（`WeighSlip`）

## 快速启动

```bash
cd d:\work\document\bytecode\claudeCodePro\CharClamp\CharClamp-01
docker compose up --build
```

浏览器打开 http://localhost:4750

## 目录结构

```
CharClamp-01/
├── docker-compose.yml
├── Dockerfile
├── entrypoint.sh
└── src/charclamp/
    ├── main.py
    ├── domain/          # models + rules
    ├── infra/           # db + seed + security
    └── web/             # controllers + templates + static
```
