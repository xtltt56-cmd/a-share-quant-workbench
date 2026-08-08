# RiceQuant Skills 适配说明

已通过 Codex Skill Installer 安装到本机用户 Skill 目录：

- `idea-generation`
- `report-renderer`

来源：[ricequant/ricequant-skills](https://github.com/ricequant/ricequant-skills)。Skill 文件没有复制进本仓库，因此不会与项目源代码混在一起，也不会改变项目许可证边界。

## 适配边界

RiceQuant research Skill 的量化筛选命令依赖 RQData CLI、Bash 和账号/许可证配置。项目 V1 不把这些依赖设为必需，而是采用以下替换：

| Skill 能力 | V1 数据入口 | 适配方式 |
|---|---|---|
| idea generation 的候选筛选 | `AKShareDataProvider` | Windows wrapper 调用本项目 DataProvider，输出带 `data_version`/`as_of` 的候选表 |
| report renderer 的报告组织 | Top20/Top5 信号与 QuantStats | 复用 Markdown/HTML 报告结构，输入来自统一 Signal Schema |
| RQData/网页补充 | 暂不启用 | 只有显式配置 RQData 后才允许使用；不能覆盖 PIT 和数据质量门禁 |

## 使用规则

1. Skill 只能生成研究候选、解释和报告，不直接调用任何券商 API。
2. 量化筛选数据必须来自带版本的 DataProvider；web search/网页内容不能替代价格、财务或估值数据。
3. 生成结果必须包含策略版本、模型版本、信号日期、数据版本和风险标记。
4. Skill 依赖缺失时，免费 AKShare + 本地报告路径继续运行。
