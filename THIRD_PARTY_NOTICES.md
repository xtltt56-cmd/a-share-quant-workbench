# Third-party notices

Python 第三方组件通过包、公开 API 或明确的 Adapter 接入。前端随项目提供 Apache ECharts 5.6.0 官方发布的压缩构建，用于本地 K 线和成交量图，未修改该文件。来源：https://cdn.jsdelivr.net/npm/echarts@5.6.0/dist/echarts.min.js ，上游：https://github.com/apache/echarts 。适用 Apache-2.0 及上游列明的子组件许可证；完整许可证见 src/a_share_quant/workbench/static/ECHARTS-LICENSE.txt，声明见同目录 ECHARTS-NOTICE.txt。其他许可证和用途记录在 [DEPENDENCIES.md](DEPENDENCIES.md) 与 [docs/OPEN_SOURCE_EVALUATION.md](docs/OPEN_SOURCE_EVALUATION.md)。

主要来源：

- [AKShare](https://github.com/akfamily/akshare)
- [BaoStock](https://www.baostock.com/)
- [Qlib](https://github.com/microsoft/qlib)
- [DuckDB](https://duckdb.org/)
- [Optuna](https://github.com/optuna/optuna)
- [QuantStats](https://github.com/ranaroussi/quantstats)
- [RQAlpha](https://github.com/ricequant/rqalpha)（可选，个人研究）
- [VectorBT](https://github.com/polakowo/vectorbt)（可选，Commons Clause）
- [PyPortfolioOpt](https://github.com/PyPortfolio/PyPortfolioOpt)（可选）
- [vn.py](https://github.com/vnpy/vnpy)（未来 Adapter 参考）
- [RiceQuant Skills](https://github.com/ricequant/ricequant-skills)（可选 Skill/工作流参考）
- [Ollama Python SDK](https://github.com/ollama/ollama-python) 0.6.3（可选，MIT；本地只读 Agent）
- [OpenAI Python SDK](https://github.com/openai/openai-python) 3.24.0（可选，Apache-2.0；DeepSeek 官方兼容接口，不表示已授权 OpenAI API）
- [DDGS](https://github.com/deedy5/ddgs) 9.16.0（可选，MIT；公开信息检索）
- [Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/)（MIT；公开网页正文提取）

如果后续将任何第三方文件复制进仓库，必须在复制同一提交中补充该项目的 LICENSE、版本、来源、变更说明和适用范围，并重新执行许可证审查。
