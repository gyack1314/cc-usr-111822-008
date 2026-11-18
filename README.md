# 电池合作项目指标分析

项目保存电池合作项目的来源版次，按资料取得时间比较单期指标并给出区域规则判断。evidence 保存来源，metrics 计算同口径偏离，context 解释当前区域规则，report 保存报告。现有实现尚不处理跨期与跨空间的综合分析。样本是合成数据。

Python 3.11 以上，在项目根目录执行：

    python -m unittest discover -s tests -v
    python -m compileall -q apec_analysis tests
    python -m apec_analysis.cli inspect data/example.json

Dockerfile 可在 Linux 容器运行：docker build -t apec-analysis-008 .；docker run --rm apec-analysis-008。
