# 跨境电池合作核算

项目按来源版本保存业务资料，已有 materials、factories、contribution 环节。现有货物流向、工厂和地区汇总只按加工地加总，尚未区分跨境加工链上的已实现贡献与未来承诺。

运行与检查：

    python -m unittest discover -s tests -v
    python -m compileall -q battery_cooperation tests
    python -m battery_cooperation.cli data/example.json
