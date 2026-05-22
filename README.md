# 工商业储充场站成本收益测算工具



基于 **Streamlit** 的单页应用，用于工商业场景下 **储能 + 充电站** 的 **投资成本、年收入/运维、静态回本、IRR（可选）** 及可选 **利润表/现金流量表/财务评价** 的快速测算。适合可研/初判，**不替代**详细财务模型与税务/折旧/融资条款。



## 功能概览



- **五步录入**：① 场站 → ② 充电桩（A 自动规划 / B 自定义）→ ③ 工商业储能 → ④ 施工成本 → ⑤ 运营与测算。

- **主区分左右两栏（宽屏）**：左侧分步填写后点击 **确认测算**；右侧展示 KPI、投资分项（按工程块虚线分隔）、年收入与现金流图、方案对比。

- **第四步施工（四块并列）**：**充电桩工程** → **微网控制器** → **储能工程** → **相关物料**（含 **变压器增容投资**，与第三部分目标容量/单价联动）。

- **第五步运营**：**充电桩运营** / **储能运营**（安装储能时显示装机、**年化收入计算过程** 可展开核对）。

- **经营指标（可选）**：勾选后输出利润表、现金流量表、NPV/XIRR/PI/静态回收期（不含税口径，SOH 衰减，三免三减半）。

- **配电**：同时使用率含 **1 (满载同时)**（Kt=1 无需微网控制器）；确认测算时若已填 **目标容量 ≥ 基准+缺口** 则不因「相对剩余可用缺口」阻断。

- **侧栏已隐藏**；**浅色工业极简**界面。

- **默认参数**：`config/cost_defaults.json`；页面底部可在线编辑并保存。



## 环境要求



- **Python 3.10+**（建议 3.10–3.12）

- 网络：首次 `pip install` 需能访问 PyPI

- 推荐使用虚拟环境



## 快速开始（3 步）



在项目根目录打开终端：



```powershell

python -m venv venv

.\venv\Scripts\Activate.ps1

pip install -r requirements.txt

streamlit run app.py

```



浏览器访问终端提示的地址（通常 `http://localhost:8501`）。左侧填写后点击 **「确认测算」**，右侧查看结果。



---



## 安装与运行（详细）



### 进入项目目录



```powershell

cd "C:\Users\joelle.qiu\Documents\Envision\CusorPath\Station_cost_estimation"

```



### 虚拟环境与依赖



```powershell

python -m venv venv

.\venv\Scripts\Activate.ps1

pip install -r requirements.txt

streamlit run app.py

```



主要依赖：`streamlit`、`plotly`、`pandas`、`numpy`、`numpy-financial`（IRR/XIRR；未安装时仍可测算，仅财务评价部分受限）。



### 停止服务



在运行 Streamlit 的终端按 **Ctrl + C**。



---



## 使用说明（页面内）



1. **第一步**确认场站（剩余可用变压器容量必填等）。

2. **第二步**选 A/B 模式配置桩数；B 模式在「配电增容」填写 **目标变压器总容量** 与 **增容单价**。

3. **第三步**选择是否安装储能及主要用途。

4. **第四步**填写各施工分项；**相关物料** 会汇总车位土建、杂项与 **变压器增容**。

5. **第五步**运营假设；可展开 **经营指标与财务报表**。

6. 点击 **「确认测算」**；右侧 **投资成本分项** 按四块工程展示（勿与旧版「其他设备」混淆，现为 **微网控制器** 独立一块）。

7. 长期默认：页面底部 **「默认成本参数」** → 保存到 `config/cost_defaults.json`。



业务口径见 **`MEMORY.md`**；AI 协作见 **`.cursorrules`**。



---



## 常见问题



| 现象 | 处理 |

|------|------|

| `streamlit` 不是内部或外部命令 | 激活 `venv` 并 `pip install -r requirements.txt` |

| 确认测算后红字「相对剩余可用缺口」 | 将 **目标变压器总容量** 提高到 ≥ **基准+缺口**（第二步/第三部分），或减桩 |

| 右侧「测算结果渲染失败」 | 查看展开的错误详情；多为代码问题，请反馈或拉最新 `app.py` |

| 微网控制器在结果里出现多次 | 已修复：仅出现在「微网控制器」工程块 |

| IRR / XIRR 不显示 | `pip install numpy-financial` |

| 占用车位超过场站车位 | 黄字 **warning**，不阻止测算 |



---



## 项目结构



| 文件 | 说明 |

|------|------|

| `app.py` | 应用入口：UI、`compute_projection()`、`compute_financial_statements()`、图表 |

| `requirements.txt` | Python 依赖 |

| `config/cost_defaults.json` | 默认成本与运营参数（含微网、财报、SOH 等键） |

| `MEMORY.md` | 业务口径与五步流程备忘 |

| `.cursorrules` | Cursor 项目约定 |

| `CHANGELOG.md` | 版本与功能更新摘要 |



## 维护说明



- 调整公式：优先 `compute_projection()`、`compute_transformer_upgrade_capex()`、`_annual_revenue_by_usage()`。

- 与人确认过的业务结论：同步 `MEMORY.md` 与 `CHANGELOG.md`。



## 作者



应用模块说明中默认维护者：**jiali.qiu**。



## 许可证



未包含许可证文件时，默认 **内部/私有使用**。


