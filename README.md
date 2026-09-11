# 工商业储充场站成本收益测算工具

一套 Streamlit：① 摸底表筛高价值站 → ② 手填曲线结论做配储评分 → ③ 原五步单站精算。充电运营商和厂区/园区负荷走同一套流程，分型规则不同。可研级简化，不替代详细财务账。

**发给客户的表**：`doc/场站资源摸底收资表.xlsx`（蓝列 VPP 组盘 + 绿列储能投资；充电运营商和产业园/厂区共用，客户只填一次）。

## 目录

```
Station_cost_estimation/
  app.py                 # 入口：三页签 + ③精算逻辑
  ui_pipeline.py         # ①筛站 / ②评分页
  intake_screening.py    # 摸底导入与红黄绿
  storage_scoring.py     # 手填评分与粗投资
  tariff_lib.py          # 2026 电价库匹配
  reverse_analysis.py    # 逆向补贴分析
  requirements.txt
  install.bat            # 首次装依赖
  启动测算工具.bat
  config/cost_defaults.json
  config/tariff_library.json
  ref/                   # 对照材料（不进主公式）
  doc/                   # 口径、摸底表、安装说明
  output/                # 本工具导出/生成结果
```

| 路径 | 说明 |
|------|------|
| `app.py` | 页签 + ③ `compute_projection()` |
| `doc/场站资源摸底收资表.xlsx` | 对客统一摸底（VPP 资源清单 + 配储/EGC 列） |
| `config/tariff_library.json` | 代理购电 vs 入市分行。⭐=动态分时不可用库内价；【政策即将变动需复核】=上会前对文件 |
| `doc/MEMORY.md` | 已确认业务口径 |
| `doc/CHANGELOG.md` | 功能更新摘要 |
| `doc/同事安装说明.md` | Windows 双击安装与启动 |
| `ref/` | 包头 V2G 表、重卡超充 IRR、中恒报价摘录 |
| `output/` | 测算导出放这里 |

## 启动

```powershell
cd "C:\Users\joelle.qiu\Documents\Envision\CusorPath\Station_cost_estimation"
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

或双击 `install.bat`（首次），再双击 `启动测算工具.bat`。浏览器一般是 `http://localhost:8501`。

## 三个页签

1. **① 高价值筛站**：导入统一摸底表，同时出 VPP 粗可调和配储红黄绿灰。不算 IRR。
2. **② 配储评分测算**：手填峰/时长/空窗（不上传曲线）。柜型 125kW/262kWh。粗回本闸门 5 年。
3. **③ 单站精算**：原五步。

黄/绿站可在①勾选「带入②」。

## 页面五步（③）

1. 场站（剩余可用变压器容量必填）
2. 充电桩 A/B 配置；配电增容填目标容量与单价
3. 是否装储能及主要用途
4. 施工四块：充电桩工程 / 微网控制器 / 储能工程 / 相关物料
5. 运营假设；可选经营指标与财务报表

点 **确认测算**。长期默认在页面底部写入 `config/cost_defaults.json`。

## 改码

- 公式：`compute_projection()`、`compute_transformer_upgrade_capex()`、`_annual_revenue_by_usage()`
- 确认过的口径：同步 `doc/MEMORY.md` 与 `doc/CHANGELOG.md`
- 作者：jiali.qiu
