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

1. **顶部窑剪影行**：每座炭窑以 SVG 剪影展示；点击某窑用 HTMX 局部刷新下方时间轴，并更新地址栏 `?clamp_id=`；「全部窑」取消筛选。今日已有合格未作废过磅联的窑带 **磅✓** 标记（与过磅联专页同源数据，对账差恒为 0）。
2. **纵向时间轴**：按开始时间倒序列出 `BurnShift`；每条卡片带窑号徽章（再点可开抽屉）、峰值温度、炭品与当前窑态。
3. **侧抽屉（非独立编辑页）**：「登记班次」写入新班次；点窑徽章打开操作抽屉，可标记「已出炭」（受峰值规则与过磅联双重约束）。
4. **顶栏导航**：可在「时间轴」与「过磅联」专页间切换；即使落联被挡，两页仍可正常点开。

## 出炭过磅联（专页 `/weigh-slips`）

已出炭前须先在过磅联专页落下一张**出炭过磅联**并通过。

- **联字段**：炭窑、过磅日（自然日 `YYYY-MM-DD`）、毛重千克、皮重千克、司秤人、是否合格。
- **数值规则**：毛重、皮重均须大于 0；**净重 = 毛重 − 皮重，不得少于 50 千克**。
- **自然日唯一**：同一炭窑同一自然日最多保存一张**未作废合格联**。两名司秤并发抢落时，由数据库部分唯一索引兜底，第二张被中文拒绝（已有联，不落第二张）。
- **权限**：操作工可新建联；**作废仅管理员**。作废后该联不得再算出炭。
- **出炭判定**：点「已出炭」时现场读取该窑**当日最新一张合格且未作废联**；没有则中文拒绝。峰值 ≥ 400℃ 的旧门槛与过磅联检查收进 `can_mark_clamp_drawn` / `assert_can_set_clamp_status` 同一函数（`src/charclamp/domain/rules.py`），禁止旁路。

## 业务规则

炭窑状态不可设为「已出炭」（`drawn`），除非同时满足：

1. 该窑**最近一条** `BurnShift` 的 `peakTempC` 已记录且 **≥ 400℃**；
2. 该窑**当日（自然日）**存在一张合格且未作废的出炭过磅联（净重 ≥ 50kg）。

规则实现：`src/charclamp/domain/rules.py`；过磅联模型：`DrawWeighSlip`（`src/charclamp/domain/models.py`）。

种子数据中 **坞东-甲** 即「焖烧中、峰值已达标（455℃）但无过磅联」的典型待办窑。

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
